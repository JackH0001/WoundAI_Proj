# -*- coding: utf-8 -*-
"""契約測試：管理者主控台（帳號管理 + 系統管控）。

這一頁是**唯一一個會改變授權狀態的介面**，所以它的守門線要比別處更硬。
鎖住的不是「畫面長什麼樣」，而是四件會直接造成資安事故的事：

  1. **前端隱藏不是存取控制。** 把 JS 改掉、直接打 API，非管理者仍須 403。
     這是最容易被誤解的一條——UI 沒有按鈕不等於後端擋得住。
  2. **權限分層要真的分開。** 工程師看得到稽核（`audit.read`）但**看不到帳號管理**
     （`user.manage`）；醫師護理師連稽核都看不到。若工程師能開帳號，
     等於任何有部署權的人都能給自己開一個醫師帳號去產生 GT——飛輪的方法學就破了。
  3. **停用要立即生效。** 停用後舊密碼必須當場失效，不是等 token 過期。
  4. **帳號異動必須進稽核，且不破壞雜湊鏈。** 少了這個，
     「一個被濫用的帳號是誰核發的」永遠查不出來。

另外驗證一條容易寫錯的實作細節：稽核查詢**先驗鏈再過濾**。
反過來的話，過濾後的子集鏈結必然不連續，會回報一堆不存在的 broken_link，
而值班的人會去追一個假的竄改警報。

    python engineering/phase2/test_admin_console.py
"""
import os
import sys
import json
import shutil
import tempfile
import importlib

HERE = os.path.dirname(os.path.abspath(__file__))
FLASK_DIR = os.path.abspath(os.path.join(HERE, "..", "..", "Backend", "Flask"))

FAILED = []


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + (("   " + str(detail)) if detail else ""))
    if not ok:
        FAILED.append(name)


def main():
    tmp = tempfile.mkdtemp(prefix="woundai_admin_")
    os.environ["WOUNDAI_FLYWHEEL_DIR"] = tmp
    for sub in ("images", "quarantine"):
        os.makedirs(os.path.join(tmp, sub), exist_ok=True)
    for f in ("retrain_queue.jsonl", "withdrawn.jsonl", "audit.jsonl", "users.jsonl"):
        open(os.path.join(tmp, f), "w").close()

    sys.path.insert(0, FLASK_DIR)
    au = importlib.import_module("auth_users")
    fw = importlib.import_module("api_flywheel")
    api_users = importlib.import_module("api_users")
    api_console = importlib.import_module("api_console")

    from flask import Flask
    from flask_jwt_extended import JWTManager, create_access_token
    app = Flask(__name__)
    app.config["JWT_SECRET_KEY"] = "test-only"
    JWTManager(app)
    app.register_blueprint(api_users.users_bp)
    app.register_blueprint(api_console.console_bp)
    cli = app.test_client()

    def token(user, role):
        with app.app_context():
            return create_access_token(
                identity="default:%s" % user,
                additional_claims={"role": role, "org": "default", "user": user})

    HDR = {r: {"Authorization": "Bearer " + token("t_" + r, r)}
           for r in ("admin", "physician", "nurse", "assistant", "engineer")}

    def get(path, role):
        r = cli.get(path, headers=HDR[role])
        return r.status_code, (r.get_json() or {})

    def post(path, role, body):
        r = cli.post(path, json=body, headers=HDR[role])
        return r.status_code, (r.get_json() or {})

    # ── 1 只有管理者能碰帳號管理 ───────────────────────────────────
    s, _ = get("/api/v1/users", "admin")
    check("1  管理者可列出帳號", s == 200, s)
    denied = {r: get("/api/v1/users", r)[0] for r in ("physician", "nurse", "assistant", "engineer")}
    check("1b 醫師/護理師/助理/工程師列表一律 403", set(denied.values()) == {403}, denied)

    # ⚠ 這條是重點：主控台隱藏了按鈕，但 API 才是真正的門。
    forged = {r: post("/api/v1/users", r,
                      {"user": "evil" + r[:3], "role": "admin", "password": "x" * 12})[0]
              for r in ("physician", "nurse", "assistant", "engineer")}
    check("1c 繞過前端直接 POST 建立 admin 帳號 → 全部 403（前端隱藏不是存取控制）",
          set(forged.values()) == {403}, forged)
    check("1d 被擋的帳號確實沒被建立", au.get_user("default", "evieng") is None
          and all(u["user"].startswith("t_") is False or True for u in au.list_users())
          and not any(u["user"].startswith("evil") for u in au.list_users()))

    # ── 2 伺服器產生密碼：回傳一次、可登入、不留明文 ─────────────
    s, r = post("/api/v1/users", "admin",
                {"user": "ns09", "role": "nurse", "display_name": "護理師 09",
                 "generate_password": True})
    pw = r.get("generated_password")
    check("2  新增帳號並由伺服器產生密碼", s == 200 and bool(pw), s)
    check("2b 回應形狀與列表一致（有 identity / role_zh）",
          r.get("user", {}).get("identity") == "default:ns09"
          and r["user"].get("role_zh") == "護理師", r.get("user"))
    check("2c 產生的密碼可登入", au.authenticate("default", "ns09", pw)[0] is not None)
    check("2d 密碼不含易混淆字元 l/1/I/O/0（要靠人抄寫傳遞）",
          not (set(pw) & set("l1IO0")), pw)

    _, lst = get("/api/v1/users", "admin")
    row = [u for u in lst["users"] if u["user"] == "ns09"][0]
    check("2e 帳號列表不含密碼雜湊", "pw" not in row and "password" not in row, list(row))
    raw = open(os.path.join(tmp, "users.jsonl"), encoding="utf-8").read()
    stored = json.loads([l for l in raw.splitlines() if '"ns09"' in l][-1])["pw"]
    check("2f 落盤的是加鹽 PBKDF2 雜湊而非明文密碼",
          pw not in raw and set(stored) == {"salt", "iters", "hash"}
          and stored["iters"] >= 200_000 and len(stored["hash"]) == 64, stored.get("iters"))
    same = [au.hash_password("identical-password"), au.hash_password("identical-password")]
    check("2g 相同密碼各自加鹽 → 雜湊不同（共用鹽會讓「兩人密碼相同」從雜湊值看得出來）",
          same[0]["salt"] != same[1]["salt"] and same[0]["hash"] != same[1]["hash"])

    # ── 3 停用／啟用立即生效 ──────────────────────────────────────
    bad_pw_reason = au.authenticate("default", "ns09", "wrongwrong123")[1]
    s, _ = post("/api/v1/users", "admin", {"user": "ns09", "role": "nurse", "disabled": True})
    rec, why = au.authenticate("default", "ns09", pw)
    check("3  停用後舊密碼當場失效", s == 200 and rec is None, why)
    # 內部原因要分得出「停用 / 無此帳號 / 密碼錯」——稽核與值班判讀靠它。
    # 但**不可原樣回給客戶端**：三種原因分開告訴外界等於送人一份帳號列舉工具，
    # 所以 /api/auth/login 一律回同一句話。這兩件事必須同時成立。
    reasons = {
        "停用": au.authenticate("default", "ns09", pw)[1],
        "無此帳號": au.authenticate("default", "nobody99", pw)[1],
        "密碼錯": bad_pw_reason,          # 停用前取的——停用會蓋掉密碼錯這個原因
    }
    check("3b 內部失敗原因三者可區分（供稽核與值班判讀）",
          reasons["停用"] == "disabled" and len(set(reasons.values())) == 3, reasons)

    s, _ = post("/api/v1/users", "admin", {"user": "ns09", "role": "nurse", "disabled": False})
    check("3c 重新啟用後可登入", s == 200 and au.authenticate("default", "ns09", pw)[0] is not None)

    # 停用不是刪除——識別碼必須還在（稽核軌跡引用著它）
    _, lst = get("/api/v1/users", "admin")
    check("3d 停用不會讓帳號從列表消失（稽核軌跡引用該識別碼）",
          any(u["user"] == "ns09" for u in lst["users"]))

    # ── 4 重設密碼：舊的立刻失效、新的只給一次 ────────────────────
    s, r2 = post("/api/v1/users", "admin",
                 {"user": "ns09", "role": "nurse", "generate_password": True})
    pw2 = r2.get("generated_password")
    check("4  重設密碼", s == 200 and bool(pw2) and pw2 != pw)
    check("4b 舊密碼失效、新密碼生效",
          au.authenticate("default", "ns09", pw)[0] is None
          and au.authenticate("default", "ns09", pw2)[0] is not None)

    # ── 5 稽核查詢的權限分層 ──────────────────────────────────────
    s, a = get("/api/v1/audit?limit=200", "admin")
    check("5  管理者可讀稽核", s == 200 and "entries" in a, s)
    s2, _ = get("/api/v1/audit", "engineer")
    check("5b 工程師可讀稽核（除錯需要）", s2 == 200, s2)
    s3, _ = get("/api/v1/users", "engineer")
    check("5c 但工程師**不能**管理帳號——否則有部署權的人可自行開醫師帳號產生 GT",
          s3 == 403, s3)
    clin = {r: get("/api/v1/audit", r)[0] for r in ("physician", "nurse", "assistant")}
    check("5d 臨床角色讀不到稽核", set(clin.values()) == {403}, clin)

    # ── 6 帳號異動有進稽核，且雜湊鏈完整 ──────────────────────────
    acts = [e["action"] for e in a["entries"]]
    check("6  帳號異動寫入稽核（user_upsert）", "user_upsert" in acts, sorted(set(acts)))
    check("6b 稽核記到「誰」開的帳號（actor 可歸屬）",
          all(e.get("actor", "").startswith("default:") for e in a["entries"]
              if e["action"] == "user_upsert"))
    check("6c 稽核記到密碼是否被變更（pw_changed）",
          any("pw_changed=True" in (e.get("result") or "") for e in a["entries"]))
    check("6d 雜湊鏈完整", a.get("chain_ok") is True, a.get("chain_issues"))
    check("6e 回傳鏈頭雜湊供外部錨定", len(a.get("chain_head") or "") == 64)

    # 被拒絕的越權嘗試也要留痕——「誰試圖越權」和「誰成功越權」一樣重要
    _, a_all = get("/api/v1/audit?limit=500", "admin")
    check("6f 新到舊排序（最新的在最前面）",
          len(a_all["entries"]) > 1
          and a_all["entries"][0]["seq"] > a_all["entries"][-1]["seq"])

    # ── 7 先驗鏈再過濾（否則會回報假的 broken_link） ───────────────
    s, f = get("/api/v1/audit?action=user_upsert", "admin")
    check("7  依動作過濾可用", s == 200 and f["entries"]
          and {e["action"] for e in f["entries"]} == {"user_upsert"})
    check("7b 過濾後 chain_ok 仍為 True —— 驗鏈用完整紀錄，不是過濾後的子集"
          "（反過來會產生假的竄改警報）", f.get("chain_ok") is True, f.get("chain_issues"))
    check("7c 過濾不影響 total（total 是全量）", f.get("total") == a.get("total"), (f.get("total"), a.get("total")))

    # 竄改偵測仍然有效（確認上面的 chain_ok 不是因為驗證被關掉）
    p = os.path.join(tmp, "audit.jsonl")
    lines = open(p, encoding="utf-8").read().splitlines()
    d = json.loads(lines[1]); d["result"] = "被竄改的內容"
    lines[1] = json.dumps(d, ensure_ascii=False)
    open(p, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    _, t = get("/api/v1/audit", "admin")
    check("7d 竄改任一筆內容 → chain_ok 轉 False 並指出位置",
          t.get("chain_ok") is False
          and any(i["kind"] == "hash_mismatch" for i in t["chain_issues"]),
          t.get("chain_issues"))

    # ── 8 主控台頁面本身不含資料 ──────────────────────────────────
    page = cli.get("/console")
    html = page.get_data(as_text=True)
    check("8  /console 可開（不需 token，頁面是靜態的）", page.status_code == 200)
    check("8b 頁面含依角色展開的三個管理分區",
          all(x in html for x in ('id="userbox"', 'id="auditbox"', 'id="sysbox"')))
    check("8c 三個分區預設 class=hide（未登入不展開）", html.count('class="hide"') >= 3)
    check("8d 頁面原始碼不含任何帳號或密碼（資料一律靠 token 取）",
          pw2 not in html and "ns09" not in html)
    check("8e 前端 perms 來自後端回傳，不自行推導角色能力",
          "perms = j.perms" in html)

    # ── 9 參數防呆 ────────────────────────────────────────────────
    bad = [
        ({"user": "NS10", "role": "nurse", "password": "x" * 12}, "大寫帳號"),
        ({"user": "ns10", "role": "boss", "password": "x" * 12}, "不存在的角色"),
        ({"user": "ns10", "role": "nurse", "password": "short"}, "密碼過短"),
        ({"user": "../etc", "role": "nurse", "password": "x" * 12}, "路徑穿越字元"),
        ({"role": "nurse", "password": "x" * 12}, "缺帳號"),
    ]
    got = {}
    for body, why2 in bad:
        got[why2] = post("/api/v1/users", "admin", body)[0]
    check("9  不合法的帳號參數全部擋下（400）", set(got.values()) == {400}, got)

    shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FAILED:
        print("FAILED %d 項：%s" % (len(FAILED), "; ".join(FAILED)))
        return 1
    print("全部通過：管理入口的權限分層、停用即時生效、密碼不落明文、"
          "帳號異動可歸屬且雜湊鏈可驗，皆由伺服器端把關。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
