# -*- coding: utf-8 -*-
"""稽核軌跡雜湊鏈的回歸測試。

四種竄改手法都要被抓到，而**整條重算要「通過」** —— 那不是缺陷，是這個機制的邊界，
必須用測試把它釘住，免得日後有人以為雜湊鏈等於不可竄改而省略 WORM 與鏈頭抄寫。

    python engineering/phase2/test_audit_chain.py
"""
import ast
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
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
        fw.audit("test:dr_%d" % i, "annotation_enqueued",
                 "WD-TEST%04d" % i, "ok", "test", "test")
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

    # v4 admission and offline verification must share the same strict schema:
    # a self-hash-valid extra field cannot become invisible simply because the
    # verifier reads a local JSONL rather than an immutable GCS slot.
    r = load()
    schema_bad = dict(r[2], reviewer_note="unhashed extra field")
    schema_bad["hash"] = fw._audit_hash(schema_bad)
    save(r[:2] + [schema_bad] + r[3:])
    ok, iss, _ = fw.verify_audit_chain()
    check("8  自雜湊正確但 v4 額外欄位 → schema_invalid",
          not ok and any(i["kind"] == "schema_invalid" for i in iss))
    restore()

    # `seq` is evidence, not decorative metadata.  Re-hash the whole chain so
    # hash/prev remain valid; the dedicated sequence invariant must still catch
    # a gap at the exact trajectory position.
    r, out, prev = load(), [], "GENESIS"
    for i, rec in enumerate(r):
        rec = dict(rec, seq=(7 if i == 2 else i), prev=prev)
        rec["hash"] = fw._audit_hash(rec)
        prev = rec["hash"]
        out.append(rec)
    save(out)
    ok, iss, _ = fw.verify_audit_chain()
    check("9  整條重算仍不能掩蓋 hashed seq 不連續",
          not ok and any(x["kind"] == "seq_discontinuity" and x["index"] == 2 for x in iss))
    restore()

    # Hashless history is supported only as a prefix.  Give the following v4
    # records their historical physical positions and rebuild the links: this
    # prefix is informational, not a real structural issue.
    legacy = {"ts": "2026-07-28T00:00:00Z", "actor": "old", "action": "x",
              "code": "-", "result": "舊紀錄"}
    out, prev = [legacy], "GENESIS"
    for i, rec in enumerate(load(), 1):
        rec = dict(rec, seq=i, prev=prev)
        rec["hash"] = fw._audit_hash(rec)
        prev = rec["hash"]
        out.append(rec)
    save(out)
    ok, iss, stats = fw.verify_audit_chain()
    check("10 hashless prefix 只是資訊性標記",
          not ok and stats["real_issues"] == 0
          and sum(x["kind"] == "legacy_no_hash" for x in iss) == 1,
          stats["kinds"])
    restore()

    # The same record inserted after hashing has begun is not historical
    # preface.  It would create an unauthenticated hole and must be a real issue.
    r = load()
    save([r[0], legacy])
    ok, iss, stats = fw.verify_audit_chain()
    check("11 hashed 之後的 hashless 紀錄是 real issue",
          not ok and stats["real_issues"] >= 1
          and any(x["kind"] == "legacy_after_hashed" for x in iss), stats["kinds"])
    restore()

    # The command-line verifier must not lose duplicate JSON keys via the
    # standard decoder's last-key-wins behavior.  Exercise the actual CLI and
    # require its non-zero result, not merely the in-process helper.
    raw = open(P, encoding="utf-8").readlines()
    raw[2] = raw[2].rstrip("\n")[:-1] + ',"code":"WD-DUPLICATE"}\n'
    with open(P, "w", encoding="utf-8") as f:
        f.writelines(raw)
    cli = subprocess.run([sys.executable, os.path.join(HERE, "verify_audit_chain.py"),
                          "--head-only"], cwd=os.path.join(HERE, "..", ".."),
                         env=os.environ.copy(), capture_output=True, text=True)
    check("12 CLI 拒絕重複 JSON key", cli.returncode == 1,
          {"returncode": cli.returncode, "stderr": cli.stderr[-300:]})
    restore()

    # --require-worm is an evidence gate, not a label.  Local verification must
    # fail, while a GcsStore helper must call retention_info and require the
    # exact locked seven-year policy.
    cli = subprocess.run([sys.executable, os.path.join(HERE, "verify_audit_chain.py"),
                          "--head-only", "--require-worm"],
                         cwd=os.path.join(HERE, "..", ".."), env=os.environ.copy(),
                         capture_output=True, text=True, encoding="utf-8")
    check("13 Local --require-worm fail-closed 且輸出證據", cli.returncode == 1
          and "WORM retention_info" in cli.stderr and "LocalStore" in cli.stderr,
          {"returncode": cli.returncode, "stderr": cli.stderr[-300:]})

    tool = runpy.run_path(os.path.join(HERE, "verify_audit_chain.py"))
    fake_gcs = st.GcsStore.__new__(st.GcsStore)
    calls = []
    fake_gcs.retention_info = lambda: calls.append("read") or {
        "verified": True, "locked": True,
        "retention_seconds": st.AUDIT_RETENTION_SECONDS,
        "bucket": "synthetic-audit",
    }
    worm_ok, evidence = tool["worm_evidence"](fake_gcs)
    check("13b GCS WORM 必須實讀 retention_info 且精確 7 年",
          worm_ok and calls == ["read"]
          and evidence["retention_info"]["bucket"] == "synthetic-audit", evidence)
    fake_gcs.retention_info = lambda: {
        "verified": True, "locked": True,
        "retention_seconds": st.AUDIT_RETENTION_SECONDS - 1,
    }
    wrong_retention_ok, _ = tool["worm_evidence"](fake_gcs)
    check("13c 已鎖但保留秒數不精確仍 fail", not wrong_retention_ok)
    fake_gcs.retention_info = lambda: {
        "verified": True, "locked": False,
        "retention_seconds": st.AUDIT_RETENTION_SECONDS,
    }
    unlocked_ok, _ = tool["worm_evidence"](fake_gcs)
    check("13d 精確 7 年但未鎖仍 fail", not unlocked_ok)
    fake_gcs.retention_info = lambda: {
        "verified": False, "locked": True,
        "retention_seconds": st.AUDIT_RETENTION_SECONDS,
    }
    unverified_ok, _ = tool["worm_evidence"](fake_gcs)
    check("13e metadata 未經即時驗證仍 fail", not unverified_ok)

    # Production audit calls must carry the historical role/org facts.  Scan
    # the three modules that directly call api_flywheel.audit; consent_staging
    # intentionally receives a different callback signature and is excluded.
    root = Path(HERE).parents[1]
    missing, observed = [], 0
    for rel in ("Backend/Flask/app.py", "Backend/Flask/api_flywheel.py",
                "Backend/Flask/api_users.py"):
        source = (root / rel).read_text(encoding="utf-8-sig")
        tree = ast.parse(source, filename=rel)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            target = node.func
            direct = isinstance(target, ast.Name) and target.id == "audit"
            qualified = (isinstance(target, ast.Attribute) and target.attr == "audit"
                         and isinstance(target.value, ast.Name)
                         and target.value.id in {"fw", "_fw", "api_flywheel"})
            if not (direct or qualified):
                continue
            observed += 1
            keyword_names = {kw.arg for kw in node.keywords if kw.arg}
            if len(node.args) < 6 and not {"role", "org"} <= keyword_names:
                missing.append("%s:%d" % (rel, node.lineno))
    check("14 production audit calls 皆明確傳 role/org",
          observed > 0 and not missing, {"observed": observed, "missing": missing})

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
