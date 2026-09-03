# -*- coding: utf-8 -*-
"""稽核鏈序列化的**真實 GCS** 煙霧測試。

假件能證明邏輯,證明不了 google-cloud-storage 在真桶上對 `if_generation_match=0` 的行為
(412 → PreconditionFailed)。這支腳本對一個**拋棄式**桶做最小驗證。

⚠ 它會寫入物件。請指向專供煙霧測試的桶(無保留政策、事後可刪),
   **不要**指向即將鎖定的正式稽核桶——寫進去的測試紀錄會跟著凍 7 年。

    $env:WOUNDAI_STORE="gcs"
    $env:WOUNDAI_GCS_BUCKET="<主桶>"                 # 只用來建構 GcsStore,不寫入
    $env:WOUNDAI_AUDIT_BUCKET="<拋棄式煙霧桶>"       # 實際寫入的地方
    $env:WOUNDAI_AUDIT_SMOKE="1"                    # 明確承認這是可刪的測試桶
    python engineering/phase2/smoke_audit_chain_gcs.py

退出碼 0＝全部通過;1＝有項目失敗;2＝環境不對(拒跑)。
"""
import json
import os
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, "..", "..", "Backend", "Flask"))
sys.path.insert(0, BACKEND)

FAILED = []


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + (("   " + str(detail)) if detail else ""))
    if not ok:
        FAILED.append(name)


def main():
    if os.environ.get("WOUNDAI_STORE") != "gcs":
        print("拒跑:WOUNDAI_STORE 必須是 gcs(這支只對真 GCS 有意義)")
        return 2
    audit_bucket = os.environ.get("WOUNDAI_AUDIT_BUCKET", "")
    main_bucket = os.environ.get("WOUNDAI_GCS_BUCKET", "")
    if os.environ.get("WOUNDAI_AUDIT_SMOKE") != "1":
        print("拒跑:必須明確設定 WOUNDAI_AUDIT_SMOKE=1（此工具會寫入可刪測試桶）")
        return 2
    if not audit_bucket or "smoke" not in audit_bucket:
        # 硬性要求桶名含 smoke:防止手滑對正式稽核桶寫測試紀錄
        print("拒跑:WOUNDAI_AUDIT_BUCKET 必須是名稱含 'smoke' 的拋棄式桶(目前=%r)" % audit_bucket)
        return 2
    if not main_bucket or audit_bucket == main_bucket:
        print("拒跑:WOUNDAI_AUDIT_BUCKET 必須與 WOUNDAI_GCS_BUCKET 不同")
        return 2

    import store as st
    import api_flywheel as fw
    g = fw._store()
    check("0  後端是 GcsStore", isinstance(g, st.GcsStore), type(g).__name__)
    print("   稽核桶:gs://%s" % audit_bucket)
    if FAILED:
        return 1

    info = g.retention_info()
    if not info.get("verified") or info.get("locked") or int(info.get("retention_seconds") or 0) != 0:
        print("拒跑:煙霧桶必須可讀、未鎖且無 retention policy（目前=%s）" % info)
        return 2

    key = "audit.jsonl"
    tail_seq, _ = g.chain_tail(key)
    if tail_seq != -1:
        print("拒跑:煙霧桶必須為空，避免測試混入既有稽核資料（tail=%d）" % tail_seq)
        return 2
    check("1  起始尾端為空(seq=-1)", True)

    # 2. 連寫三筆,鏈必須乾淨
    recs = [fw.audit("smoke:tester", "smoke_test", "SMOKE-%d" % i, "ok", "engineer", "smoke")
            for i in range(3)]
    check("2  三筆 seq 連續", [r["seq"] for r in recs] == [tail_seq + 1, tail_seq + 2, tail_seq + 3],
          [r["seq"] for r in recs])
    check("2b prev 正確鏈接", recs[1]["prev"] == recs[0]["hash"] and recs[2]["prev"] == recs[1]["hash"])
    ok, issues, stats = fw.verify_audit_chain()
    check("2c verify_audit_chain real_issues=0", stats["real_issues"] == 0, stats["kinds"])

    # 3. 真 412:對已存在的格寫不同位元組 → ChainConflict(證明 PreconditionFailed 真的會來)
    taken = recs[0]["seq"]
    line = json.dumps(recs[0], ensure_ascii=False)
    try:
        g.append_chained(key, taken, line.replace("SMOKE-0", "SMOKE-EVIL"))
        check("3  已存在格 + 不同位元組 → ChainConflict(真 412)", False, "沒有拋例外")
    except st.ChainConflict:
        check("3  已存在格 + 不同位元組 → ChainConflict(真 412)", True)
    except Exception as e:
        check("3  已存在格 + 不同位元組 → ChainConflict(真 412)", False, repr(e))

    # 4. 真 412 + 相同位元組 → False(自己的重試)
    try:
        r = g.append_chained(key, taken, line)
        check("4  已存在格 + 相同位元組 → False", r is False, r)
    except Exception as e:
        check("4  已存在格 + 相同位元組 → False", False, repr(e))
    _, tail_after = g.chain_tail(key)
    check("4b 3/4 兩步都沒有改動鏈(尾端仍是第三筆)", tail_after["hash"] == recs[2]["hash"])

    # 5. 真並行:8 執行緒 × 10,對真 GCS 搶格
    N_T, N_E = 8, 10
    barrier = threading.Barrier(N_T)
    errors = []

    def worker(t):
        try:
            barrier.wait()
            for i in range(N_E):
                fw.audit("smoke:t%d" % t, "smoke_concurrent", "SMOKE-C%d-%d" % (t, i), "ok",
                         "engineer", "smoke")
        except Exception as e:
            errors.append(repr(e))

    ths = [threading.Thread(target=worker, args=(t,)) for t in range(N_T)]
    [x.start() for x in ths]
    [x.join() for x in ths]
    check("5  並行寫入無例外", not errors, errors[:3])
    all_recs = fw.read_jsonl(fw.AUDIT)
    seqs = [r["seq"] for r in all_recs]
    check("5b seq 無重複", len(seqs) == len(set(seqs)), (len(seqs), len(set(seqs))))
    check("5c 筆數增加恰為 %d" % (N_T * N_E), len(all_recs) == tail_seq + 1 + 3 + N_T * N_E, len(all_recs))
    ok, issues, stats = fw.verify_audit_chain()
    check("5d 真 GCS 並行後 fork=0 broken_link=0",
          stats["kinds"].get("fork", 0) == 0 and stats["kinds"].get("broken_link", 0) == 0, stats["kinds"])
    check("5e 整條鏈 real_issues=0", stats["real_issues"] == 0)

    print()
    if FAILED:
        print("FAILED %d 項:%s" % (len(FAILED), "; ".join(FAILED)))
        return 1
    print("全部通過:真 GCS 上的條件建立、412 仲裁、並行序列化皆如設計。")
    print("(煙霧桶內的測試物件可整桶刪除;它沒有保留政策。)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
