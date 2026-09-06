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
import time

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

    # ── 6 帳號異動有進稽核，且可歸屬 ────────────────────────────
    acts = [e["action"] for e in a["entries"]]
    check("6  帳號異動寫入稽核（user_upsert）", "user_upsert" in acts, sorted(set(acts)))
    check("6b 稽核記到「誰」開的帳號（actor 可歸屬）",
          all(e.get("actor", "").startswith("default:") for e in a["entries"]
              if e["action"] == "user_upsert"))
    check("6c 稽核記到密碼是否被變更（pw_changed）",
          any("pw_changed=True" in (e.get("result") or "") for e in a["entries"]))
    check("6d 新到舊排序（最新的在最前面）",
          len(a["entries"]) > 1 and a["entries"][0]["seq"] > a["entries"][-1]["seq"])

    # ── 7 鏈驗證是「選用」而非每次都跑 ────────────────────────────
    #
    # 驗證是 O(n) 且沒有取巧空間。若跟著每次開頁跑，紀錄累積後主控台會慢到沒人想開，
    # 而不開的主控台等於沒有稽核。所以預設不驗，只回 O(1) 的 head。
    check("7  預設不做鏈驗證（verified 為 null）", a.get("verified") is None, a.get("verified"))
    check("7b 預設仍回 head（O(1)，最後一筆的 hash）", len(a.get("head") or "") == 64)

    s, v = get("/api/v1/audit?verify=1", "admin")
    ver = v.get("verified") or {}
    check("7c verify=1 才驗鏈且通過", s == 200 and ver.get("ok") is True, ver.get("issues"))
    check("7d 驗證結果可錨定（head + 時間 + 驗證者）",
          len(ver.get("head") or "") == 64 and ver.get("verified_at")
          and (ver.get("verified_by") or "").startswith("default:"), ver.get("verified_at"))
    _, a2 = get("/api/v1/audit?limit=5", "admin")
    check("7e 驗證動作本身也進稽核（audit_verify）",
          any(e["action"] == "audit_verify" for e in a2["entries"]),
          [e["action"] for e in a2["entries"]])

    # verify=1 必須只讀一次 strict snapshot。若先用 read_jsonl 畫表、再另讀一次驗鏈，
    # 兩次讀取中間的併發 append 會讓畫面與證明分屬不同鏈頭。
    original_snapshot = fw.read_verified_audit_snapshot
    original_read_jsonl = fw.read_jsonl
    snapshot_calls = {"strict": 0, "generic": 0}

    def counted_snapshot(*args, **kwargs):
        snapshot_calls["strict"] += 1
        return original_snapshot(*args, **kwargs)

    def counted_read_jsonl(*args, **kwargs):
        snapshot_calls["generic"] += 1
        return original_read_jsonl(*args, **kwargs)

    fw.read_verified_audit_snapshot = counted_snapshot
    fw.read_jsonl = counted_read_jsonl
    try:
        s_same, same = get("/api/v1/audit?verify=1&limit=3", "admin")
    finally:
        fw.read_verified_audit_snapshot = original_snapshot
        fw.read_jsonl = original_read_jsonl
    check("7f verify 列表與證明共用一次 strict snapshot（不混入 generic read）",
          s_same == 200 and snapshot_calls == {"strict": 1, "generic": 0}
          and same.get("verified", {}).get("ok") is True,
          snapshot_calls)

    # ── 8 分頁與篩選 ──────────────────────────────────────────────
    _, p1 = get("/api/v1/audit?limit=2&offset=0", "admin")
    _, p2 = get("/api/v1/audit?limit=2&offset=2", "admin")
    check("8  分頁：limit 生效", len(p1["entries"]) == 2, len(p1["entries"]))
    check("8b offset 從**最新**往回數（第二頁的 seq 全部小於第一頁）",
          min(e["seq"] for e in p1["entries"]) > max(e["seq"] for e in p2["entries"]),
          ([e["seq"] for e in p1["entries"]], [e["seq"] for e in p2["entries"]]))
    check("8c limit 有上限（防止一次拉爆記憶體）",
          get("/api/v1/audit?limit=99999", "admin")[1]["limit"] == 500)

    # ⚠ 基準要**當下重取**。稽核是 append-only 且這支測試自己也在寫紀錄
    # （驗證、匯出都會留痕），拿前面某一次的回應當基準必然對不上——
    # 那會測出一個假的失敗，然後有人跑去「修」一個沒有壞的功能。
    _, base = get("/api/v1/audit?limit=1", "admin")
    _, f = get("/api/v1/audit?action=user_upsert", "admin")
    check("8d 依動作篩選", f["entries"] and {e["action"] for e in f["entries"]} == {"user_upsert"})
    check("8e matched 是篩選後、total 是全量（兩者要分得開，否則分頁會算錯頁數）",
          f["matched"] < f["total"], (f["matched"], f["total"]))
    check("8f 選單選項從**全量**算，不隨篩選縮小（否則選了就再也切不回去）",
          set(f["actions"]) == set(base["actions"]) and len(f["actions"]) > 1, f["actions"])

    _, fa = get("/api/v1/audit?actor=default:t_admin", "admin")
    check("8g 依操作者篩選", fa["entries"]
          and {e["actor"] for e in fa["entries"]} == {"default:t_admin"})
    _, fr = get("/api/v1/audit?role=admin", "admin")
    check("8h 依角色篩選", fr["entries"] and {e["role"] for e in fr["entries"]} == {"admin"})

    today = time.strftime("%Y-%m-%d", time.gmtime())
    _, d1 = get("/api/v1/audit?since=%s&until=%s" % (today, today), "admin")
    _, d0 = get("/api/v1/audit?until=2000-01-01", "admin")
    check("8i 日期區間：今日有紀錄", d1["matched"] > 0, d1["matched"])
    check("8j 日期區間：迄日在紀錄之前 → 0 筆（until 不可被忽略）", d0["matched"] == 0, d0["matched"])

    # ── 9 CSV 匯出 ────────────────────────────────────────────────
    r = cli.get("/api/v1/audit?format=csv&action=user_upsert", headers=HDR["admin"])
    raw = r.get_data()
    check("9  CSV 匯出", r.status_code == 200 and "text/csv" in r.headers.get("Content-Type", ""))
    check("9b 帶 attachment 檔名", "attachment" in r.headers.get("Content-Disposition", ""))
    check("9c 有 UTF-8 BOM —— 少了它 Excel 會用系統字碼頁開，中文全變亂碼",
          raw.startswith(b"\xef\xbb\xbf"))
    body = raw.decode("utf-8-sig")
    check("9d 欄位齊全且含 hash（沒有 hash 的匯出無法離線驗鏈）",
          body.splitlines()[0].split(",")[:3] == ["seq", "ts", "actor"] and "hash" in body.splitlines()[0])
    check("9e 只匯出符合篩選的紀錄", all("user_upsert" in ln for ln in body.splitlines()[1:] if ln.strip()))
    _, a3 = get("/api/v1/audit?limit=5", "admin")
    check("9f 匯出動作本身進稽核（誰把稽核帶走了，和誰做了什麼一樣重要）",
          any(e["action"] == "audit_export" for e in a3["entries"]),
          [e["action"] for e in a3["entries"]])

    # ── 10 竄改偵測仍然有效（確認上面的通過不是因為驗證被關掉） ──
    p = os.path.join(tmp, "audit.jsonl")
    lines = open(p, encoding="utf-8").read().splitlines()
    d = json.loads(lines[1]); d["result"] = "被竄改的內容"
    lines[1] = json.dumps(d, ensure_ascii=False)
    open(p, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    status_t, t = get("/api/v1/audit?verify=1", "admin")
    tv = t.get("verified") or {}
    check("10 竄改任一筆內容 → 驗證轉 False 並指出位置",
          status_t == 200 and tv.get("ok") is False
          and any(i["kind"] == "hash_mismatch" for i in tv["issues"]),
          tv.get("issues"))
    after_failed_verify = open(p, encoding="utf-8").read().splitlines()
    check("10b 損壞鏈只回報證據、不嘗試追加 audit_verify",
          len(after_failed_verify) == len(lines)
          and tv.get("verification_event_recorded") is False
          and tv.get("recording_reason") == "chain_integrity_failure",
          {"before": len(lines), "after": len(after_failed_verify),
           "recorded": tv.get("verification_event_recorded")})

    # ── 11 主控台頁面本身不含資料 ────────────────────────────────
    page = cli.get("/console")
    html = page.get_data(as_text=True)
    check("11 /console 可開（不需 token，頁面是靜態的）", page.status_code == 200)
    check("11b Content-Type 只有一組 charset",
          page.headers.get("Content-Type", "").count("charset") == 1,
          page.headers.get("Content-Type"))
    check("11c 四個頁籤區塊都在",
          all(('id="tab-%s"' % t) in html for t in ("dash", "sys", "audit", "users")))
    check("11d 側欄四個項目都在",
          all(('data-tab="%s"' % t) in html for t in ("dash", "sys", "audit", "users")))
    check("11e 頁籤預設隱藏（未登入不展開）", html.count('class="hide"') >= 4)
    check("11f 頁面原始碼不含任何帳號或密碼（資料一律靠 token 取）",
          pw2 not in html and "ns09" not in html)
    check("11g 前端 perms 來自後端回傳，不自行推導角色能力", "perms = j.perms" in html)
    # 不比對整串字面值——那種測試每次加頁籤都要跟著改，而且改的人會直接複製新字串進來，
    # 等於沒有驗到任何東西。改成把前端的對照表抽出來，逐一確認那個權限**真的存在於後端**。
    # 打錯字（"audit.reads"）不會有任何錯誤訊息，只會讓那個頁籤對所有人永久隱藏。
    import re as _re
    m = _re.search(r"const TABS = \{(.*?)\};", html, _re.S)
    tabs = dict(_re.findall(r"(\w+)\s*:\s*\"([\w.]+)\"", m.group(1))) if m else {}
    unknown = {t: p for t, p in tabs.items() if p not in au.PERMS}
    check("11h 每個頁籤宣告的權限都存在於後端權限矩陣（打錯字會讓頁籤永久隱藏且不報錯）",
          bool(tabs) and not unknown, unknown or tabs)
    check("11i 四個管理頁籤都在對照表裡",
          {"dash", "sys", "audit", "users"} <= set(tabs), sorted(tabs))

    # ── 12 參數防呆 ───────────────────────────────────────────────
    bad = [
        ({"user": "NS10", "role": "nurse", "password": "x" * 12}, "大寫帳號"),
        ({"user": "ns10", "role": "boss", "password": "x" * 12}, "不存在的角色"),
        ({"user": "ns10", "role": "nurse", "password": "short"}, "密碼過短"),
        ({"user": "../etc", "role": "nurse", "password": "x" * 12}, "路徑穿越字元"),
        ({"role": "nurse", "password": "x" * 12}, "缺帳號"),
        ({"user": ["ns10"], "role": "nurse", "password": "x" * 12}, "帳號型別非字串"),
    ]
    got = {}
    for body, why2 in bad:
        got[why2] = post("/api/v1/users", "admin", body)[0]
    check("12 不合法的帳號參數全部擋下（400）", set(got.values()) == {400}, got)

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
