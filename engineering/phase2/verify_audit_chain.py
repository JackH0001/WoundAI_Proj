# -*- coding: utf-8 -*-
"""稽核軌跡完整性驗證 —— 供 IRB／院方稽核時出示。

## 這支工具在回答什麼問題

稽核員問的是：「你怎麼證明這份操作紀錄沒有被事後修改過？」

「我們的程式只做 append」不是答案——那是承諾，不是證據。雜湊鏈是證據：
每一筆紀錄都攜帶前一筆的 SHA-256，任何一筆被改動、刪除或調換順序，
後續每一筆的鏈結都會對不上，而且這支工具**指得出第一個斷點在第幾筆、什麼時間**。

## 誠實邊界（請一併向稽核員說明，不要假裝這是萬能的）

雜湊鏈能**偵測**竄改，不能**阻止**。有寫入權限的人可以把整條鏈重算成一致的樣子。
要達到真正的不可竄改，需要物件儲存的保留政策（WORM），見 `Backend/Flask/harden_bucket.ps1`。
兩者是互補的層次：WORM 讓人改不動，雜湊鏈讓「有沒有被改」變成可以當場驗證的事。

實務上再加一層：定期把**鏈頭雜湊**抄寫到一個獨立、只增不減的地方（紙本值班紀錄、
另一個帳號的 WORM 桶）。之後即使整條鏈被重算，也對不上那個時間點抄下來的鏈頭。

## 用法

    # 本機
    python engineering/phase2/verify_audit_chain.py

    # 雲端（Cloud Run 的 GCS 儲存）。稽核鍵走獨立稽核桶,缺 WOUNDAI_AUDIT_BUCKET 會
    # 明確報錯(store._target 拒絕),不會靜默讀到錯的地方。
    $env:WOUNDAI_STORE="gcs"; $env:WOUNDAI_GCS_BUCKET="woundai-flywheel-jackh001"
    $env:WOUNDAI_AUDIT_BUCKET="woundai-flywheel-jackh001-audit"
    python engineering/phase2/verify_audit_chain.py

    # 要把結果當成 WORM 證據時，必須明確要求並讀回雲端保留政策
    python engineering/phase2/verify_audit_chain.py --require-worm

    # 記下鏈頭（建議每次稽核後抄到程式碰不到的地方）
    python engineering/phase2/verify_audit_chain.py --head-only

退出碼 0＝完整，1＝發現問題，2＝無法讀取。
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, "..", "..", "Backend", "Flask"))
sys.path.insert(0, BACKEND)


def worm_evidence(store):
    """Return `(passes, evidence)` from a live store retention readback.

    This deliberately accepts a store object rather than environment strings:
    a configured bucket name is not proof that the bucket is locked.  The GCS
    implementation reloads bucket metadata inside `retention_info()`.
    """
    from store import AUDIT_RETENTION_SECONDS, GcsStore

    if not isinstance(store, GcsStore):
        return False, {
            "backend": type(store).__name__,
            "required_retention_seconds": AUDIT_RETENTION_SECONDS,
            "reason": "--require-worm is valid only for a live GCS audit bucket",
        }
    try:
        info = store.retention_info()
    except Exception as exc:
        info = {"verified": False, "locked": False,
                "reason": "retention_info raised: %s" % exc}
    if not isinstance(info, dict):
        info = {"verified": False, "locked": False,
                "reason": "retention_info returned a non-object"}
    try:
        retention = int(info.get("retention_seconds") or 0)
    except (TypeError, ValueError):
        retention = -1
    passed = (info.get("verified") is True and info.get("locked") is True
              and retention == AUDIT_RETENTION_SECONDS)
    return passed, {
        "backend": "GcsStore",
        "required_retention_seconds": AUDIT_RETENTION_SECONDS,
        "retention_info": info,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit-path", default=None, help="自訂稽核檔路徑（預設用飛輪目錄）")
    ap.add_argument("--head-only", action="store_true", help="只印鏈頭雜湊（供抄寫存證）")
    ap.add_argument("--show", type=int, default=10, help="最多列出幾筆問題")
    ap.add_argument("--require-worm", action="store_true",
                    help="要求即時讀回的 GCS 稽核桶為已鎖定、精確 7 年保留")
    a = ap.parse_args()

    if a.require_worm and a.audit_path is not None:
        print("拒絕：--require-worm 不得與 --audit-path 併用；保留證據必須綁定預設 GCS 稽核鍵")
        return 2

    try:
        import api_flywheel as fw
    except Exception as e:
        print("無法載入 api_flywheel：%s" % e)
        return 2

    try:
        ok, issues, stats = fw.verify_audit_chain(a.audit_path)
    except Exception as e:
        print("讀取稽核軌跡失敗：%s" % e)
        return 2

    try:
        active_store = fw._store()
    except Exception as exc:
        print("無法載入儲存後端：%s" % exc)
        return 2

    worm_ok, worm = True, None
    if a.require_worm:
        worm_ok, worm = worm_evidence(active_store)

    if a.head_only:
        print(stats["head"])
        if a.require_worm:
            print("WORM retention_info: %s" %
                  json.dumps(worm, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 0 if ok and worm_ok else 1

    try:
        store_desc = active_store.describe()
    except Exception:
        store_desc = "?"

    print("稽核軌跡完整性驗證")
    print("  儲存後端 : %s" % store_desc)
    print("  紀錄筆數 : %d" % stats["total"])
    print("  鏈頭雜湊 : %s" % stats["head"])
    if a.require_worm:
        print("  WORM 要求 : %s" % ("PASS" if worm_ok else "FAIL"))
        print("  retention_info : %s" % json.dumps(worm, ensure_ascii=False, sort_keys=True))
    print()

    if ok:
        print("✅ 鏈結計算完整：沒有發現竄改、刪除或順序異動的跡象。")
        if not worm_ok:
            print("❌ WORM 要求未成立：本次結果不能宣稱為已鎖定的不可變更證據。")
            return 1
        print()
        print("   建議把上面的鏈頭雜湊抄到程式碰不到的地方（紙本值班紀錄或另一個帳號），")
        print("   下次驗證時比對——即使整條鏈被重算，也對不上先前抄下的鏈頭。")
        return 0

    KIND_ZH = {
        "hash_mismatch": "內容被改過",
        "broken_link": "前一筆被刪除或順序被調換",
        "fork": "兩筆指向同一個前驅（並行寫入或被補塞）",
        "invalid_record": "JSON 無法唯一、標準地解析",
        "schema_invalid": "v4 紀錄不符合不可變 slot 合約",
        "legacy_no_hash": "雜湊鏈導入前的舊紀錄（無法驗證，非異常）",
        "legacy_formula": "以歷史欄位組重算吻合（不足以單獨證明未被竄改）",
        "seq_discontinuity": "hashed 紀錄的 seq 不連續或不符合軌跡位置",
        "legacy_after_hashed": "無 hash 紀錄出現在 hashed 軌跡之後",
        "version_regression": "chain_v 沿軌跡倒退",
    }
    INFORMATIONAL = ("legacy_no_hash", "legacy_formula")
    real = [i for i in issues if i["kind"] not in INFORMATIONAL]
    legacy = len(issues) - len(real)

    if not real:
        n_nohash = sum(1 for i in issues if i["kind"] == "legacy_no_hash")
        n_formula = sum(1 for i in issues if i["kind"] == "legacy_formula")
        print("✅ 鏈結計算未見結構性異常（另有 %d 筆歷史證據限制）。" % legacy)
        if n_nohash:
            print("   %d 筆為雜湊鏈導入前的舊紀錄，無法回溯驗證——" % n_nohash)
            print("   可信度僅止於「當時的程式只做 append」這個承諾。")
        if n_formula:
            print("   %d 筆以歷史欄位組重算吻合，這只能辨識使用過的公式。" % n_formula)
            print("   它不能單獨證明紀錄未被竄改；須再對照當時外部抄寫的鏈頭或 WORM 證據。")
        if not worm_ok:
            print("❌ WORM 要求未成立：本次結果不能宣稱為已鎖定的不可變更證據。")
            return 1
        return 0

    print("❌ 發現 %d 處異常：" % len(real))
    for k, n in stats["kinds"].items():
        if k in INFORMATIONAL:
            continue
        print("   %-16s %d 筆  — %s" % (k, n, KIND_ZH.get(k, k)))
    print()
    print("前 %d 筆明細：" % min(a.show, len(real)))
    for i in real[:a.show]:
        print("   #%-5d %s  [%s]" % (i["index"], i.get("ts") or "?", i["kind"]))
        print("          %s" % i["detail"])
    if legacy:
        print("\n   （另有 %d 筆資訊性標記未計入：導入前舊紀錄，或已被取代的公式）" % legacy)
    print()
    print("下一步：這不是可以自行「修好」的東西。稽核軌跡出現斷點本身就是要通報的事件，")
    print("        請保留現況、記錄發現時間，並依 SOP 通報。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
