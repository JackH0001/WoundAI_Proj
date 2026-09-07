# -*- coding: utf-8 -*-
"""稽核鏈「壞了沒」這個問題,只能有一個答案。

`verify_audit_chain` 的第一個回傳值 `ok` 是 `len(issues) == 0`,而 issues 含
`legacy_formula` / `legacy_no_hash` 這兩個資訊性標記。曾經發生的事:寫入路徑用
`stats["real_issues"]` 判定、照常延伸鏈,主控台卻只看 `ok`,於是同一條鏈在畫面上
被標成 `chain_integrity_failure`,而且那次實際通過的驗證不會留下 `audit_verify`。

方向要講清楚:這是 fail-closed 的誤報,不是 fail-open——因為 real_issues 一旦大於 0,
len(issues) 必然大於 0,`ok` 必為 False。本檔把兩件事鎖住:

  1. `fw.chain_integrity_ok` 與寫入路徑原本寫死的判準完全等價(逐一枚舉驗證)。
  2. 主控台只帶資訊性標記的鏈,**不得**回報 chain_integrity_failure;
     真的被竄改時仍然要回報。

    python engineering/phase2/test_audit_console_verdict.py
"""
import os
import sys
import json
import hashlib
import tempfile
import importlib
import itertools

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, "..", "..", "Backend", "Flask"))

FAILED = []


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + (("   " + str(detail)) if detail else ""))
    if not ok:
        FAILED.append(name)


def main():
    tmp = tempfile.mkdtemp(prefix="woundai_verdict_")
    os.environ["WOUNDAI_FLYWHEEL_DIR"] = tmp
    os.environ.pop("WOUNDAI_STORE", None)
    for d in ("images", "quarantine"):
        os.makedirs(os.path.join(tmp, d), exist_ok=True)
    for f in ("retrain_queue.jsonl", "withdrawn.jsonl", "audit.jsonl", "users.jsonl"):
        open(os.path.join(tmp, f), "w").close()

    sys.path.insert(0, BACKEND)
    import store as st
    st.reset_store(None)
    import api_flywheel as fw
    importlib.reload(fw)
    st.reset_store(None)
    import auth_users            # noqa: F401  (blueprint 需要)
    import api_users
    importlib.reload(api_users)

    # ── 1. 判準等價性:逐一枚舉,不是抽樣 ────────────────────────────────
    # 寫入路徑原本寫死的條件,保留在這裡當作對照組。任何一邊改動而另一邊沒跟上,
    # 這個測試就會紅。
    def inline_admission_condition(stats):
        kinds = stats.get("kinds", {})
        return not (stats.get("real_issues", 0) or kinds.get("legacy_no_hash", 0))

    mismatches = []
    for real_issues, no_hash, formula in itertools.product((0, 1, 3), (0, 1, 2), (0, 1, 5)):
        stats = {"real_issues": real_issues,
                 "informational": no_hash + formula,
                 "issues": real_issues + no_hash + formula,
                 "kinds": {k: v for k, v in
                           (("legacy_no_hash", no_hash), ("legacy_formula", formula),
                            ("hash_mismatch", real_issues)) if v}}
        if fw.chain_integrity_ok(stats) != inline_admission_condition(stats):
            mismatches.append(stats)
    check("1  chain_integrity_ok 與寫入路徑原判準在 27 種組合下完全等價",
          not mismatches, mismatches[:3])

    check("1b 只有資訊性 legacy_formula → 可信任",
          fw.chain_integrity_ok({"real_issues": 0, "kinds": {"legacy_formula": 6}}) is True)
    check("1c legacy_no_hash 仍然阻擋(那些紀錄早於雜湊鏈,無法證明未竄改)",
          fw.chain_integrity_ok({"real_issues": 0, "kinds": {"legacy_no_hash": 1}}) is False)
    check("1d 真異常阻擋",
          fw.chain_integrity_ok({"real_issues": 1, "kinds": {"hash_mismatch": 1}}) is False)
    check("1e 缺 kinds 欄位不炸,且不放行 real_issues",
          fw.chain_integrity_ok({"real_issues": 2}) is False
          and fw.chain_integrity_ok({}) is True)

    # ── 2. 主控台:只帶資訊性標記的鏈不得被標成完整性失敗 ────────────────
    from flask import Flask
    from flask_jwt_extended import JWTManager, create_access_token
    app = Flask(__name__)
    app.config["JWT_SECRET_KEY"] = "test-only"
    JWTManager(app)
    app.register_blueprint(api_users.users_bp)
    cli = app.test_client()

    def token(user, role):
        with app.app_context():
            return create_access_token(
                identity="default:%s" % user,
                additional_claims={"role": role, "org": "default", "user": user})

    def verify_as(user, role):
        r = cli.get("/api/v1/audit?verify=1",
                    headers={"Authorization": "Bearer " + token(user, role)})
        return r.status_code, (r.get_json() or {}).get("verified") or {}

    P = os.path.join(tmp, "audit.jsonl")

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

    r0 = stamp(base(0, "GENESIS"), 1)          # v1 七欄舊公式 → legacy_formula
    r1 = stamp(base(1, r0["hash"]), 2)         # v2 九欄舊公式 → legacy_formula
    save([r0, r1])

    ok_flag, issues, stats = fw.verify_audit_chain()
    kinds = stats.get("kinds") or {}
    # v2(九欄)是無標記紀錄的主要公式,不算 legacy;只有 v1 那筆會被標記。
    # 這裡鎖的是性質——「沒有真異常、沒有 legacy_no_hash、至少一個資訊性標記」——
    # 而不是某個會隨版本簿記變動的數字。
    check("2  夾具就是「只有資訊性標記」的鏈",
          stats["real_issues"] == 0 and kinds.get("legacy_formula", 0) >= 1
          and not kinds.get("legacy_no_hash") and stats["informational"] >= 1, kinds)
    check("2b 這種鏈的 ok 仍為 False(維持原語意,不打壞既有呼叫端)", ok_flag is False)

    status, ver = verify_as("admin", "admin")
    check("3  主控台回 200", status == 200, status)
    check("3b ok 照舊為 False", ver.get("ok") is False, ver.get("ok"))
    check("3c integrity_ok 為 True——這條鏈沒有壞", ver.get("integrity_ok") is True, ver)
    check("3d 不得回報 chain_integrity_failure(這正是原本的缺陷)",
          ver.get("recording_reason") != "chain_integrity_failure",
          ver.get("recording_reason"))
    check("3e 把 real_issues 與 informational 攤開給操作者看",
          ver.get("real_issues") == 0
          and ver.get("informational") == stats["informational"]
          and ver.get("informational") >= 1,
          {k: ver.get(k) for k in ("real_issues", "informational", "kinds")})
    # 寫入端拒絕延伸欄位組早於 v4 的鏈,於是 audit_verify 追加失敗。唯讀的驗證端點
    # 必須把它降級成一個具名理由,而不是 500——否則舊鏈根本查不了。
    check("3f 追加失敗被降級成具名理由,不是 500,也不是完整性失敗",
          status == 200
          and ver.get("recording_reason") in (None, "verification_not_appendable"),
          {"status": status, "reason": ver.get("recording_reason"),
           "error": ver.get("recording_error")})

    # ── 3. 真的竄改仍然要被擋(確認上面不是把驗證關掉換來的) ──────────────
    tampered = dict(r1)
    tampered["result"] = "被竄改的內容"
    save([r0, tampered])
    before = open(P, encoding="utf-8").read().splitlines()
    status_t, tv = verify_as("admin", "admin")
    after = open(P, encoding="utf-8").read().splitlines()
    check("4  竄改內容 → integrity_ok 轉 False",
          status_t == 200 and tv.get("integrity_ok") is False
          and any(i["kind"] == "hash_mismatch" for i in tv.get("issues") or []),
          tv.get("kinds"))
    check("4b 仍回報 chain_integrity_failure 且不追加任何紀錄",
          tv.get("recording_reason") == "chain_integrity_failure"
          and tv.get("verification_event_recorded") is False
          and len(after) == len(before),
          {"before": len(before), "after": len(after)})

    # ── 5. 判準只能有一份實作 ──────────────────────────────────────────
    # 上面第 1 節證明的是「helper 的語意正確」,但那不阻止有人把寫入路徑改回
    # 自己寫死一份條件——兩份實作正是這個缺陷的成因。這裡靜態鎖住呼叫關係。
    src = open(os.path.join(BACKEND, "api_flywheel.py"), encoding="utf-8").read()
    start = src.index("def chain_integrity_ok(")
    end = src.index("\ndef ", start + 1)
    rest = src[:start] + src[end:]          # 扣掉 helper 本體
    check("5  寫入路徑透過共用判準提問",
          "if not chain_integrity_ok(stats):" in rest)
    offending = [ln.strip() for ln in rest.splitlines()
                 if "real_issues" in ln
                 and ln.strip().startswith(("if ", "elif ", "return ", "assert "))]
    check("5b 判準沒有在 helper 以外的地方被重寫一份", not offending, offending)

    print()
    if FAILED:
        print("FAILED %d: %s" % (len(FAILED), FAILED))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
