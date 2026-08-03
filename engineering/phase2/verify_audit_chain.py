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

    # 雲端（Cloud Run 的 GCS 儲存）
    $env:WOUNDAI_STORE="gcs"; $env:WOUNDAI_GCS_BUCKET="woundai-flywheel-jackh001"
    python engineering/phase2/verify_audit_chain.py

    # 記下鏈頭（建議每次稽核後抄到程式碰不到的地方）
    python engineering/phase2/verify_audit_chain.py --head-only

退出碼 0＝完整，1＝發現問題，2＝無法讀取。
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, "..", "..", "Backend", "Flask"))
sys.path.insert(0, BACKEND)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit-path", default=None, help="自訂稽核檔路徑（預設用飛輪目錄）")
    ap.add_argument("--head-only", action="store_true", help="只印鏈頭雜湊（供抄寫存證）")
    ap.add_argument("--show", type=int, default=10, help="最多列出幾筆問題")
    a = ap.parse_args()

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

    if a.head_only:
        print(stats["head"])
        return 0 if ok else 1

    try:
        store_desc = fw._store().describe()
    except Exception:
        store_desc = "?"

    print("稽核軌跡完整性驗證")
    print("  儲存後端 : %s" % store_desc)
    print("  紀錄筆數 : %d" % stats["total"])
    print("  鏈頭雜湊 : %s" % stats["head"])
    print()

    if ok:
        print("✅ 鏈結完整：沒有發現竄改、刪除或順序異動的跡象。")
        print()
        print("   建議把上面的鏈頭雜湊抄到程式碰不到的地方（紙本值班紀錄或另一個帳號），")
        print("   下次驗證時比對——即使整條鏈被重算，也對不上先前抄下的鏈頭。")
        return 0

    KIND_ZH = {
        "hash_mismatch": "內容被改過",
        "broken_link": "前一筆被刪除或順序被調換",
        "fork": "兩筆指向同一個前驅（並行寫入或被補塞）",
        "legacy_no_hash": "雜湊鏈導入前的舊紀錄（無法驗證，非異常）",
    }
    real = [i for i in issues if i["kind"] != "legacy_no_hash"]
    legacy = len(issues) - len(real)

    if not real:
        print("✅ 鏈結完整（另有 %d 筆為雜湊鏈導入前的舊紀錄，無法回溯驗證）。" % legacy)
        print("   那些紀錄的可信度僅止於「當時的程式只做 append」這個承諾。")
        return 0

    print("❌ 發現 %d 處異常：" % len(real))
    for k, n in stats["kinds"].items():
        if k == "legacy_no_hash":
            continue
        print("   %-16s %d 筆  — %s" % (k, n, KIND_ZH.get(k, k)))
    print()
    print("前 %d 筆明細：" % min(a.show, len(real)))
    for i in real[:a.show]:
        print("   #%-5d %s  [%s]" % (i["index"], i.get("ts") or "?", i["kind"]))
        print("          %s" % i["detail"])
    if legacy:
        print("\n   （另有 %d 筆導入前的舊紀錄未計入）" % legacy)
    print()
    print("下一步：這不是可以自行「修好」的東西。稽核軌跡出現斷點本身就是要通報的事件，")
    print("        請保留現況、記錄發現時間，並依 SOP 通報。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
