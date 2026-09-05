# -*- coding: utf-8 -*-
"""稽核鏈欄位版本(`chain_v`)的回歸測試。

2026-08-04 `AUDIT_CHAIN_FIELDS` 被就地改過一次(7 欄 → 9 欄,加入 role/org),
結果是變更前寫的每一筆紀錄都在往後每次驗證被標成 `hash_mismatch`——
對外顯示為「內容被改過」。紀錄沒被動過,是公式換了。

這組測試要同時釘住兩件事,少了任何一件這次修補就沒有意義:

  1. 舊公式蓋章的紀錄改標 `legacy_formula`(公式辨識的資訊性結果，
     本身不足以證明未被竄改);
  2. **真正的竄改仍然被抓到**——包含「把 chain_v 改小以換一組公式」的降級手法。

    python engineering/phase2/test_audit_chain_versioning.py
"""
import hashlib
import json
import os
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
    tmp = tempfile.mkdtemp(prefix="woundai_chainv_")
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

    def load():
        return [json.loads(l) for l in open(P, encoding="utf-8") if l.strip()]

    def save(recs):
        with open(P, "w", encoding="utf-8") as f:
            for r in recs:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def stamp(rec, version):
        """用指定版本的欄位組蓋章(模擬當年的程式)。"""
        fields = fw.CHAIN_FIELD_VERSIONS[version]
        payload = json.dumps({k: rec.get(k) for k in fields},
                             ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        rec["hash"] = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return rec

    def base(seq, prev, **kw):
        r = {"seq": seq, "ts": "2026-08-04T0%d:00:00Z" % (seq % 10),
             "actor": "default:admin", "role": "admin", "org": "default",
             "action": "record_preview", "code": "WD-T%04d" % seq,
             "result": "ok", "prev": prev}
        r.update(kw)
        return r

    def kinds_at(iss, idx):
        return sorted(i["kind"] for i in iss if i["index"] == idx)

    # ── 1. 新寫入的紀錄自帶 chain_v,且驗證乾淨 ──────────────────────────
    for i in range(3):
        fw.audit("default:dr_%d" % i, "annotation_enqueued", "WD-NEW%04d" % i, "ok",
                 "physician", "default")
    recs = load()
    check("1  新紀錄自帶 chain_v=%d" % fw.CHAIN_V,
          all(r.get("chain_v") == fw.CHAIN_V for r in recs), [r.get("chain_v") for r in recs])
    ok, iss, stats = fw.verify_audit_chain()
    check("1b 全新鏈驗證乾淨", ok and stats["real_issues"] == 0 and stats["issues"] == 0)

    # ── 2. v1(7 欄)舊紀錄 → legacy_formula,不是 hash_mismatch ──────────
    r0 = stamp(base(0, "GENESIS"), 1)
    r1 = stamp(base(1, r0["hash"]), 2)          # v2(9 欄,無標記)
    r2 = base(2, r1["hash"], chain_v=3)
    r2["hash"] = fw._audit_hash(r2)             # v3(自帶標記)
    save([r0, r1, r2])
    ok, iss, stats = fw.verify_audit_chain()
    check("2  v1 舊公式紀錄 → legacy_formula(不單獨定性竄改與否)",
          kinds_at(iss, 0) == ["legacy_formula"],
          kinds_at(iss, 0))
    check("2b v2 舊公式紀錄 → 完全無標記", kinds_at(iss, 1) == [], kinds_at(iss, 1))
    check("2c v3 紀錄 → 完全無標記", kinds_at(iss, 2) == [], kinds_at(iss, 2))
    check("2d 三版混鏈的 real_issues = 0(僅資訊性標記)",
          stats["real_issues"] == 0 and stats["informational"] == 1,
          (stats["real_issues"], stats["informational"]))
    check("2e ok 維持嚴格語意(有標記即 False),向後相容", ok is False)
    # Historical records are preserved for evidence, but the v4 admission
    # primitive must not silently extend a mixed log or leak an IOError as a
    # server 500.  A reviewed new epoch is the explicit migration route.
    before_historical_append = open(P, encoding="utf-8").read()
    try:
        fw.audit("default:admin", "record_preview", "WD-HISTORICAL-APPEND", "ok",
                 "admin", "default")
        check("2f 歷史鏈 append → AuditChainCorrupt（要求新 v4 epoch）", False, "沒有拋例外")
    except fw.AuditChainCorrupt:
        check("2f 歷史鏈 append → AuditChainCorrupt（要求新 v4 epoch）", True)
    check("2g 歷史鏈被拒後內容不變", open(P, encoding="utf-8").read() == before_historical_append)

    # ── 3. 真竄改仍被抓到,沒有被舊公式漂白 ─────────────────────────────
    bad = dict(r0, result="ok(已竄改)")          # v1 紀錄,改內容不改 hash
    save([bad, r1, r2])
    ok, iss, _ = fw.verify_audit_chain()
    check("3  竄改 v1 紀錄 → hash_mismatch(不會被誤判為 legacy_formula)",
          "hash_mismatch" in kinds_at(iss, 0) and "legacy_formula" not in kinds_at(iss, 0),
          kinds_at(iss, 0))

    bad3 = dict(r2, result="ok(已竄改)")
    save([r0, r1, bad3])
    ok, iss, _ = fw.verify_audit_chain()
    check("3b 竄改 v3 紀錄 → hash_mismatch", "hash_mismatch" in kinds_at(iss, 2), kinds_at(iss, 2))

    # ── 4. 降級攻擊:把 chain_v 改小,想換一組較寬鬆的公式 ────────────────
    downgraded = dict(r2, chain_v=2)
    save([r0, r1, downgraded])
    ok, iss, _ = fw.verify_audit_chain()
    check("4  降級 chain_v 3→2 → hash_mismatch(版本本身在雜湊輸入內)",
          "hash_mismatch" in kinds_at(iss, 2), kinds_at(iss, 2))

    stripped = {k: v for k, v in r2.items() if k != "chain_v"}
    save([r0, r1, stripped])
    ok, iss, _ = fw.verify_audit_chain()
    check("4b 直接拔掉 chain_v → hash_mismatch(不會退回試算而放行)",
          "hash_mismatch" in kinds_at(iss, 2), kinds_at(iss, 2))

    # Version identifiers are exact protocol integers, not Python numeric
    # aliases.  Without this check True/1.0 could index the historical version
    # dictionary as 1 and be reported as a valid old formula.
    float_alias = dict(r2, chain_v=3.0)
    float_alias["hash"] = fw._audit_hash(float_alias, 3)
    bool_alias = dict(r0, chain_v=True)
    bool_alias["hash"] = fw._audit_hash(bool_alias, 1)
    save([float_alias, bool_alias])
    ok, iss, _ = fw.verify_audit_chain()
    check("4c float/bool chain_v alias → hash_mismatch",
          all("hash_mismatch" in kinds_at(iss, i) for i in (0, 1)),
          (kinds_at(iss, 0), kinds_at(iss, 1)))

    # ── 5. 未知版本要明確失敗,不能拋例外把整支驗證打斷 ──────────────────
    future = dict(r2, chain_v=99)
    save([r0, r1, future])
    try:
        ok, iss, _ = fw.verify_audit_chain()
        check("5  未知 chain_v → hash_mismatch 且不中斷驗證",
              "hash_mismatch" in kinds_at(iss, 2), kinds_at(iss, 2))
    except Exception as e:
        check("5  未知 chain_v → hash_mismatch 且不中斷驗證", False, "拋出 %r" % (e,))

    # ── 6. 版本表本身:只可新增,不可就地改 ──────────────────────────────
    check("6  v1/v2 欄位組與歷史一致(改動即回歸失敗)",
          fw.CHAIN_FIELD_VERSIONS[1] == ("seq", "ts", "actor", "action", "code", "result", "prev")
          and fw.CHAIN_FIELD_VERSIONS[2] == ("seq", "ts", "actor", "role", "org",
                                             "action", "code", "result", "prev"))
    check("6b v3 把 chain_v 納入雜湊輸入", "chain_v" in fw.CHAIN_FIELD_VERSIONS[3])
    check("6c AUDIT_CHAIN_FIELDS 指向現行版本",
          fw.AUDIT_CHAIN_FIELDS == fw.CHAIN_FIELD_VERSIONS[fw.CHAIN_V])

    # ── 7. v4:nonce 進雜湊;v3→v4 混鏈合法;降級 v4→v3 被抓 ──────────────────
    r4a = base(3, r2["hash"], chain_v=4, nonce="a" * 32)
    r4a["hash"] = fw._audit_hash(r4a)
    r4b = base(4, r4a["hash"], chain_v=4, nonce="b" * 32)
    r4b["hash"] = fw._audit_hash(r4b)
    save([r0, r1, r2, r4a, r4b])
    ok, iss, stats = fw.verify_audit_chain()
    check("7  v1→v2→v3→v4 混鏈:real_issues = 0", stats["real_issues"] == 0, stats["kinds"])
    check("7b v4 紀錄無任何標記", kinds_at(iss, 3) == [] and kinds_at(iss, 4) == [])

    save([r0, r1, r2, dict(r4a, nonce="c" * 32), r4b])
    ok, iss, _ = fw.verify_audit_chain()
    check("7c 竄改 v4 的 nonce → hash_mismatch(nonce 在雜湊輸入內)",
          "hash_mismatch" in kinds_at(iss, 3), kinds_at(iss, 3))

    downgraded4 = {k: v for k, v in r4a.items() if k != "nonce"}
    downgraded4["chain_v"] = 3
    save([r0, r1, r2, downgraded4, r4b])
    ok, iss, _ = fw.verify_audit_chain()
    check("7d 降級 v4→v3(拔 nonce 改標 3)→ hash_mismatch",
          "hash_mismatch" in kinds_at(iss, 3), kinds_at(iss, 3))

    check("7e v4 欄位組 = v3 + nonce(只新增,未改動 v3)",
          set(fw.CHAIN_FIELD_VERSIONS[4]) == set(fw.CHAIN_FIELD_VERSIONS[3]) | {"nonce"}
          and fw.CHAIN_FIELD_VERSIONS[3] == ("chain_v", "seq", "ts", "actor", "role", "org",
                                             "action", "code", "result", "prev"))

    # 正確的 self-hash/prev 不足以讓版本倒退合法。版本是鏈層協定，
    # 一旦出現新版，其後回到舊公式必須被當作 real issue。
    v4_first = base(0, "GENESIS", chain_v=4, nonce="d" * 32)
    v4_first["hash"] = fw._audit_hash(v4_first)
    v3_after = base(1, v4_first["hash"], chain_v=3)
    v3_after["hash"] = fw._audit_hash(v3_after)
    save([v4_first, v3_after])
    ok, iss, stats = fw.verify_audit_chain()
    check("8  chain_v 4→3 即使 hash/prev 皆正確仍拒絕倒退",
          not ok and stats["real_issues"] >= 1
          and "version_regression" in kinds_at(iss, 1), kinds_at(iss, 1))

    # v4 is the first role/org-bearing locked-epoch format.  Missing identity
    # facts cannot be represented as an otherwise valid immutable slot.
    for field, value in (("role", ""), ("org", None)):
        bad_identity = dict(v4_first, **{field: value})
        bad_identity["hash"] = fw._audit_hash(bad_identity)
        save([bad_identity])
        ok, iss, _ = fw.verify_audit_chain()
        check("9  v4 %s 非空字串守門" % field,
              not ok and "schema_invalid" in kinds_at(iss, 0), kinds_at(iss, 0))

    # Matching a historical formula identifies a formula; it does not prove
    # that bytes were never rewritten and re-stamped.  Pin the CLI wording so a
    # future edit cannot turn an informational classification into overclaim.
    save([r0])
    cli = subprocess.run([sys.executable, os.path.join(HERE, "verify_audit_chain.py")],
                         cwd=os.path.join(HERE, "..", ".."), env=os.environ.copy(),
                         capture_output=True, text=True, encoding="utf-8")
    overclaims = ("紀錄本身沒有被動過", "非竄改",
                  "竄改無法恰好符合")
    check("10 CLI legacy_formula 只說明公式吻合，不宣稱未竄改",
          cli.returncode == 0 and "不能單獨證明" in cli.stdout
          and not any(text in cli.stdout for text in overclaims),
          {"returncode": cli.returncode, "stdout": cli.stdout[-600:]})

    st.reset_store(None)
    shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FAILED:
        print("FAILED %d 項：%s" % (len(FAILED), "; ".join(FAILED)))
        return 1
    print("全部通過：歷史公式辨識不超額宣稱，真竄改與降級手法仍然被抓到。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
