# -*- coding: utf-8 -*-
"""權限分層（RBAC S1）契約測試。設計見 `docs/rbac_design.md`。

## 這支測試在防什麼

**「UI 隱藏」不是存取控制。** App 可以被改、APK 可以被反編譯、HTTP 請求可以直接偽造。
所以這裡完全不模擬 App 的行為——每一條都是**直接打 API**，帶著各種角色的 JWT，
驗證伺服器自己擋得住。

最關鍵的一條是 §3：**護理師偽造 `doctor_verified=true` 必須被伺服器否決**。
那正是 App 端閘門擋不住的攻擊面，也是整批訓練資料方法學上站得住的前提。

    python engineering/phase2/test_rbac.py
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
    tmp = tempfile.mkdtemp(prefix="woundai_rbac_")
    os.environ["WOUNDAI_FLYWHEEL_DIR"] = tmp
    os.environ.pop("WOUNDAI_STORE", None)
    for d in ("images", "quarantine"):
        os.makedirs(os.path.join(tmp, d), exist_ok=True)
    for f in ("retrain_queue.jsonl", "withdrawn.jsonl", "audit.jsonl", "users.jsonl"):
        open(os.path.join(tmp, f), "w").close()

    sys.path.insert(0, BACKEND)
    import store as st
    st.reset_store(None)
    import importlib
    import api_flywheel as fw
    importlib.reload(fw)
    import auth_users
    importlib.reload(auth_users)
    st.reset_store(None)

    from flask import Flask
    from flask_jwt_extended import JWTManager, create_access_token
    app = Flask(__name__)
    app.config["JWT_SECRET_KEY"] = "rbac-test-only-key-not-for-production"
    JWTManager(app)
    app.register_blueprint(fw.flywheel_bp)
    from api_users import users_bp
    app.register_blueprint(users_bp)
    cli = app.test_client()

    # 每個角色一個帳號
    ROLES = ["physician", "nurse", "assistant", "engineer", "admin"]
    tokens = {}
    with app.app_context():
        for r in ROLES:
            auth_users.upsert_user("default", r + "1", r, "test-password-1234",
                                   display_name=auth_users.ROLES[r], actor="test")
            tokens[r] = create_access_token(
                identity="default:%s1" % r,
                additional_claims={"role": r, "org": "default", "user": r + "1"})

    def H(role):
        return {"Authorization": "Bearer " + tokens[role]}

    # 影像先進儲存（模擬 classify）
    import hashlib
    jpg = b"\xff\xd8" + b"rbac-test" * 200 + b"\xff\xd9"
    iid = hashlib.sha1(jpg).hexdigest()[:16]
    with open(os.path.join(tmp, "images", iid + ".jpg"), "wb") as f:
        f.write(jpg)

    def anno(verified=True, code="WD-RBAC0001"):
        return {"code": code, "gt_polygon": [[10, 10], [200, 10], [200, 200], [10, 200]],
                "exudate": 1, "doctor_verified": verified, "deidentified": True,
                "consent_train": True, "image_id": iid, "image_w": 640, "image_h": 480,
                "source": "clinical"}

    print("── 1. 帳號與密碼 ──")
    check("1  正確密碼可通過", auth_users.authenticate("default", "nurse1", "test-password-1234")[0] is not None)
    check("1b 錯誤密碼被拒", auth_users.authenticate("default", "nurse1", "wrong")[0] is None)
    check("1c 密碼不以明文儲存",
          all("test-password-1234" not in l for l in open(os.path.join(tmp, "users.jsonl"), encoding="utf-8")))
    check("1d 識別碼為 <org>:<user>", auth_users.identity("default", "nurse1") == "default:nurse1")
    auth_users.upsert_user("default", "nurse1", "nurse", disabled=True, actor="test")
    check("1e 停用後即刻失效",
          auth_users.authenticate("default", "nurse1", "test-password-1234")[0] is None)
    auth_users.upsert_user("default", "nurse1", "nurse", disabled=False, actor="test")
    check("1f 可重新啟用",
          auth_users.authenticate("default", "nurse1", "test-password-1234")[0] is not None)

    print("\n── 2. 送訓練標註：只有醫師 ──")
    r = cli.post("/api/v1/annotation", json=anno(), headers=H("physician"))
    check("2  醫師可送出", r.status_code == 200, r.status_code)
    for role in ("nurse", "assistant", "engineer", "admin"):
        r = cli.post("/api/v1/annotation", json=anno(code="WD-RBAC0002"), headers=H(role))
        check("2b %-9s 被擋（403）" % role, r.status_code == 403, r.status_code)

    print("\n── 3. 偽造 doctor_verified：伺服器必須否決 ──")
    # 這是 App 端閘門完全擋不住的攻擊面：直接打 API 送 doctor_verified=true。
    r = cli.post("/api/v1/annotation", json=anno(verified=True, code="WD-RBAC0003"),
                 headers=H("nurse"))
    body = r.get_json() or {}
    check("3  護理師偽造 doctor_verified=true 被伺服器否決", r.status_code == 403, r.status_code)
    check("3b 拒絕理由指出是權限問題而非格式問題",
          any("醫師" in str(i) for i in (body.get("issues") or [])), body.get("issues"))
    # 佇列裡不可出現這筆
    recs = fw.read_jsonl(fw.QUEUE)
    check("3c 佇列中沒有非醫師產生的紀錄",
          all(x.get("role") == "physician" for x in recs), [x.get("role") for x in recs])

    print("\n── 4. 撤回同意：醫師/護理師 ──")
    for role, want in (("physician", 200), ("nurse", 200), ("assistant", 403),
                       ("engineer", 403), ("admin", 403)):
        r = cli.post("/api/v1/consent/withdraw",
                     json={"code": "WD-RBAC0001", "image_ids": [], "reason": "t"}, headers=H(role))
        check("4  %-9s → %d" % (role, want), r.status_code == want, r.status_code)

    print("\n── 5. 佇列健康度：助理不可 ──")
    for role, want in (("physician", 200), ("nurse", 200), ("assistant", 403),
                       ("engineer", 200), ("admin", 200)):
        r = cli.get("/api/v1/flywheel/stats", headers=H(role))
        check("5  %-9s → %d" % (role, want), r.status_code == want, r.status_code)

    print("\n── 6. 帳號管理：只有管理者 ──")
    for role, want in (("admin", 200), ("physician", 403), ("nurse", 403),
                       ("assistant", 403), ("engineer", 403)):
        r = cli.get("/api/v1/users", headers=H(role))
        check("6  %-9s → %d" % (role, want), r.status_code == want, r.status_code)
    r = cli.post("/api/v1/users",
                 json={"user": "newdoc", "role": "physician", "password": "another-pw-9876"},
                 headers=H("admin"))
    check("6b 管理者可新增帳號", r.status_code == 200, r.status_code)
    r = cli.post("/api/v1/users",
                 json={"user": "hacker", "role": "admin", "password": "x-pw-1234567"},
                 headers=H("physician"))
    check("6c 醫師不可自行升級為管理者", r.status_code == 403, r.status_code)
    r = cli.post("/api/v1/users", json={"user": "weak", "role": "nurse", "password": "123"},
                 headers=H("admin"))
    check("6d 弱密碼被拒", r.status_code == 400, r.status_code)

    print("\n── 7. 稽核歸屬 ──")
    audits = fw.read_jsonl(fw.AUDIT)
    check("7  稽核記錄真實身分而非 admin",
          any(a.get("actor", "").startswith("default:physician") for a in audits))
    check("7b 稽核帶角色", any(a.get("role") == "physician" for a in audits))
    check("7c 稽核帶機構（S2 前向相容）", all(a.get("org") == "default" for a in audits if a.get("actor")))
    ok, issues, stats = fw.verify_audit_chain()
    check("7d 加了 role/org 之後雜湊鏈仍完整", ok, stats.get("issues"))

    print("\n── 8. 缺角色的舊 token：fail-closed ──")
    with app.app_context():
        legacy = create_access_token(identity="admin")   # 沒有 role claim
    r = cli.post("/api/v1/annotation", json=anno(code="WD-RBAC0009"),
                 headers={"Authorization": "Bearer " + legacy})
    check("8  無 role 的舊 token 一律拒絕（不是預設放行）", r.status_code == 403, r.status_code)

    st.reset_store(None)
    shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FAILED:
        print("FAILED %d 項：%s" % (len(FAILED), "; ".join(FAILED)))
        return 1
    print("全部通過：權限由伺服器執行，偽造 doctor_verified 擋得住，稽核可歸屬到人。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
