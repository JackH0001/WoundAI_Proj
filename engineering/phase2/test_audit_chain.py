# -*- coding: utf-8 -*-
"""稽核軌跡雜湊鏈的回歸測試。

四種竄改手法都要被抓到，而**整條重算要「通過」** —— 那不是缺陷，是這個機制的邊界，
必須用測試把它釘住，免得日後有人以為雜湊鏈等於不可竄改而省略 WORM 與鏈頭抄寫。

    python engineering/phase2/test_audit_chain.py
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, "..", "..", "Backend", "Flask"))

FAILED = []


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + (("   " + str(detail)) if detail else ""))
    if not ok:
        FAILED.append(name)


def main():
    tmp = tempfile.mkdtemp(prefix="woundai_audit_")
    os.environ["WOUNDAI_FLYWHEEL_DIR"] = tmp
    os.environ.pop("WOUNDAI_STORE", None)
    for d in ("images", "quarantine"):
        os.makedirs(os.path.join(tmp, d), exist_ok=True)
    for f in ("retrain_queue.jsonl", "withdrawn.jsonl", "audit.jsonl"):
        open(os.path.join(tmp, f), "w").close()

    sys.path.insert(0, BACKEND)
    import store as st
    st.reset_store(None)
    import importlib
    import api_flywheel as fw
    importlib.reload(fw)
    st.reset_store(None)

    P = os.path.join(tmp, "audit.jsonl")
    for i in range(5):
        fw.audit("dr_%d" % i, "annotation_enqueued", "WD-TEST%04d" % i, "ok")
    shutil.copy(P, P + ".bak")

    def load():
        return [json.loads(l) for l in open(P, encoding="utf-8") if l.strip()]

    def save(recs):
        with open(P, "w", encoding="utf-8") as f:
            for r in recs:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def restore():
        shutil.copy(P + ".bak", P)

    ok, iss, stats = fw.verify_audit_chain()
    check("1  正常鏈驗證通過", ok and stats["total"] == 5, stats["total"])
    check("1b 鏈頭是 SHA-256", len(stats["head"]) == 64)
    head_before = stats["head"]

    # 竄改內容
    save([dict(x, result="ok(已竄改)") if i == 2 else x for i, x in enumerate(load())])
    ok, iss, _ = fw.verify_audit_chain()
    check("2  竄改內容 → hash_mismatch",
          not ok and any(i["kind"] == "hash_mismatch" and i["index"] == 2 for i in iss))
    restore()

    # 刪除
    save([x for i, x in enumerate(load()) if i != 2])
    ok, iss, _ = fw.verify_audit_chain()
    check("3  刪除紀錄 → broken_link", not ok and any(i["kind"] == "broken_link" for i in iss))
    restore()

    # 調換順序
    r = load()
    save(r[:1] + [r[2], r[1]] + r[3:])
    ok, iss, _ = fw.verify_audit_chain()
    check("4  調換順序 → broken_link", not ok and any(i["kind"] == "broken_link" for i in iss))
    restore()

    # 補塞偽造
    r = load()
    fake = dict(r[2], result="偽造紀錄", actor="attacker")
    save(r[:3] + [fake] + r[3:])
    ok, iss, _ = fw.verify_audit_chain()
    check("5  補塞偽造紀錄被抓到", not ok, sorted({i["kind"] for i in iss}))
    restore()

    # 誠實邊界：整條重算會通過。這是為什麼還需要 WORM 與鏈頭抄寫。
    recs, prev, out = load(), "GENESIS", []
    for i, rec in enumerate(recs):
        rec = dict(rec, seq=i, prev=prev)
        if i == 2:
            rec["result"] = "ok(重算後的竄改)"
        rec["hash"] = fw._audit_hash(rec)
        prev = rec["hash"]
        out.append(rec)
    save(out)
    ok, iss, stats = fw.verify_audit_chain()
    check("6  整條重算「通過」驗證 —— 雜湊鏈只能偵測、不能阻止", ok)
    check("6b 但鏈頭改變了 → 抄寫鏈頭可揭露這種攻擊", stats["head"] != head_before)
    restore()

    # 導入前的舊紀錄不可靜默通過
    save([{"ts": "2026-07-28T00:00:00Z", "actor": "old", "action": "x",
           "code": "-", "result": "舊紀錄"}] + load())
    ok, iss, _ = fw.verify_audit_chain()
    check("7  導入前舊紀錄被標記為 legacy_no_hash",
          any(i["kind"] == "legacy_no_hash" for i in iss))
    restore()

    st.reset_store(None)
    shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FAILED:
        print("FAILED %d 項：%s" % (len(FAILED), "; ".join(FAILED)))
        return 1
    print("全部通過：四種竄改手法皆可偵測；整條重算的邊界已明確釘住。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
