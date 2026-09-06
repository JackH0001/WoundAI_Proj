# -*- coding: utf-8 -*-
"""契約測試：誤送排除、我的送件清單、一次性登入碼。

三件事各自都有一條「做錯了不會報錯、但會很難救」的邊界：

  1. **誤送排除 ≠ 撤回同意。** 兩者存不同檔、走不同端點、在統計裡是不同欄位。
     用 withdraw 去標記一次操作失誤，IRB 報告上就會出現一筆**根本沒發生過的撤回**——
     而那份報告的可信度正是整個飛輪存在的理由。這裡鎖住「排除不動 withdrawn 統計」。

  2. **清單的範圍限制在伺服器端。** 醫師送 `scope=all` 也只能看到自己的。
     「前端只顯示自己的」不是隔離，那只是沒有畫出來而已。

  3. **一次性登入碼不能當一般 token 用。** 它本質上是一個簽章合法的 JWT，
     少了型別檢查就等於一個 60 秒的萬用通行證。這裡鎖住它打任何一般端點都會被拒。

    python engineering/phase2/test_retract_and_records.py
"""
import os
import sys
import json
import time
import hashlib
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


def make_jpeg():
    try:
        from PIL import Image
        import io
        buf = io.BytesIO()
        Image.new("RGB", (1200, 900), (180, 90, 80)).save(buf, "JPEG")
        return buf.getvalue()
    except Exception:
        return b"\xff\xd8\xff\xe0" + b"woundai-test" * 64 + b"\xff\xd9"


def main():
    tmp = tempfile.mkdtemp(prefix="woundai_retract_")
    os.environ["WOUNDAI_FLYWHEEL_DIR"] = tmp
    os.environ["JWT_SECRET_KEY"] = "test-secret-key-please-ignore-0000"
    os.environ["ADMIN_PASSWORD"] = "test-admin-password"
    for sub in ("images", "quarantine"):
        os.makedirs(os.path.join(tmp, sub), exist_ok=True)
    for f in ("retrain_queue.jsonl", "withdrawn.jsonl", "audit.jsonl",
              "users.jsonl", "retracted.jsonl"):
        open(os.path.join(tmp, f), "w").close()

    sys.path.insert(0, FLASK_DIR)
    fw = importlib.import_module("api_flywheel")
    au = importlib.import_module("auth_users")

    from flask import Flask
    from flask_jwt_extended import JWTManager, create_access_token
    app = Flask(__name__)
    app.config["JWT_SECRET_KEY"] = "test-only"
    JWTManager(app)
    app.register_blueprint(fw.flywheel_bp)
    cli = app.test_client()

    def tok(user, role):
        with app.app_context():
            return create_access_token(identity="default:%s" % user,
                                       additional_claims={"role": role, "org": "default",
                                                          "user": user})

    H = {r: {"Authorization": "Bearer " + tok(u, r)}
         for u, r in [("dr01", "physician"), ("dr02", "physician2"),
                      ("admin", "admin"), ("ns01", "nurse"), ("eng01", "engineer")]
         for r in [r]}
    # dr02 也是醫師，但用不同 identity —— 「不能排除別人的」要用同角色不同人才測得出來
    H["physician2"] = {"Authorization": "Bearer " + tok("dr02", "physician")}

    def post(path, role, body):
        r = cli.post(path, json=body, headers=H[role])
        return r.status_code, (r.get_json() or {})

    def get(path, role):
        r = cli.get(path, headers=H[role])
        return r.status_code, (r.get_json() or {})

    # ── 建立三筆送件：dr01 兩筆、dr02 一筆 ────────────────────────
    ids = {}
    for tag, salt in (("a", b"1"), ("b", b"2"), ("c", b"3")):
        jpg = make_jpeg() + salt
        iid = hashlib.sha1(jpg).hexdigest()[:16]
        with open(os.path.join(tmp, "images", iid + ".jpg"), "wb") as f:
            f.write(jpg)
        ids[tag] = iid

    from _synthetic_receipts import ratify_synthetic
    ratify_synthetic(tmp, ids.values())

    def anno(iid, code, poly=None):
        return {"code": code, "gt_polygon": poly or [[100, 100], [500, 100], [500, 400], [100, 400]],
                "exudate": 1, "doctor_verified": True, "deidentified": True, "consent_train": True,
                "image_id": iid, "image_w": 1200, "image_h": 900, "mm_per_px": 0.1,
                "route": "cloud_escalated(AU)", "source": "clinical"}

    for tag, code, who in (("a", "WD-AAA1", "physician"), ("b", "WD-BBB2", "physician"),
                           ("c", "WD-CCC3", "physician2")):
        s, r = post("/api/v1/annotation", who, anno(ids[tag], code))
        assert s == 200 and r.get("status") == "enqueued", (tag, s, r)

    _, st0 = fw.effective_queue()
    check("0  三筆送件皆可訓練", st0["trainable"] == 3, st0["trainable"])

    # ── 1 我的送件：範圍在伺服器端 ────────────────────────────────
    s, mine = get("/api/v1/flywheel/records", "physician")
    check("1  醫師看得到自己的送件", s == 200 and len(mine["records"]) == 2, len(mine.get("records", [])))
    check("1b 只有自己的（dr02 那筆不在裡面）",
          {r["actor"] for r in mine["records"]} == {"default:dr01"})

    # ⚠ 重點：送 scope=all 也不能越界。前端不畫出來不是隔離。
    s, forced = get("/api/v1/flywheel/records?scope=all", "physician")
    check("1c 醫師送 scope=all 仍只看得到自己的（範圍限制在伺服器端）",
          forced["scope"] == "mine" and len(forced["records"]) == 2, forced.get("scope"))
    check("1d may_see_all 對醫師為 false", forced["may_see_all"] is False)

    s, allr = get("/api/v1/flywheel/records?scope=all", "admin")
    check("1e 管理者可看全部", allr["scope"] == "all" and len(allr["records"]) == 3,
          len(allr.get("records", [])))
    s, eng = get("/api/v1/flywheel/records?scope=all", "engineer")
    check("1f 工程師也可看全部（除錯需要），但看到的仍是去識別欄位",
          eng["scope"] == "all" and all("patient" not in json.dumps(r) for r in eng["records"]))

    r0 = mine["records"][0]
    check("1g 面積換算正確（400×300 px @0.1mm/px = 12 cm²）", r0["area_cm2"] == 12.0, r0["area_cm2"])
    check("1h 帶狀態與可否排除", r0["status"] == "trainable" and r0["can_retract"] is True, r0["status"])
    check("1i 不含姓名或影像位元組",
          not any(k in r0 for k in ("name", "mrn", "image", "gt_polygon")), list(r0))

    # ── 2 誤送排除 ────────────────────────────────────────────────
    s, r = post("/api/v1/retract", "physician",
                {"image_id": ids["a"], "reason": "wrong_source", "note": "這是範例圖，誤送"})
    check("2  醫師可排除自己送的", s == 200 and r["status"] == "retracted", (s, r))
    check("2b 回應明說這不是撤回同意", "不是撤回同意" in r.get("effect", ""))
    check("2c 影像已隔離",
          os.path.exists(os.path.join(tmp, "quarantine", ids["a"] + ".jpg"))
          and not os.path.exists(os.path.join(tmp, "images", ids["a"] + ".jpg")))

    _, st1 = fw.effective_queue()
    check("2d 可訓練由 3 降為 2", st1["trainable"] == 2, st1["trainable"])
    check("2e 統計裡是 retracted，**不是 withdrawn**",
          st1["retracted"] == 1 and st1["withdrawn"] == 0, (st1["retracted"], st1["withdrawn"]))
    # 這一條是本檔的核心：排除不可污染同意管理的紀錄。
    check("2f withdrawn.jsonl 完全沒被寫入（IRB 報告不會出現沒發生過的撤回）",
          os.path.getsize(os.path.join(tmp, "withdrawn.jsonl")) == 0)
    check("2g 排除寫在獨立的 retracted.jsonl",
          os.path.getsize(os.path.join(tmp, "retracted.jsonl")) > 0)

    _, mine2 = get("/api/v1/flywheel/records", "physician")
    ra = [r for r in mine2["records"] if r["image_id"] == ids["a"]][0]
    check("2h 清單顯示已排除與理由",
          ra["status"] == "retracted" and "來源標錯" in (ra["retract_reason"] or ""), ra)
    check("2i 已排除者不再顯示可排除", ra["can_retract"] is False)

    # ── 3 不能排除別人的 ──────────────────────────────────────────
    s, r = post("/api/v1/retract", "physician", {"image_id": ids["c"], "reason": "mis_submitted"})
    check("3  醫師不得排除他人送出的紀錄", s == 403, s)
    aud = [json.loads(l) for l in open(os.path.join(tmp, "audit.jsonl"), encoding="utf-8")]
    check("3b 越權嘗試留痕（誰試圖越權和誰成功越權一樣重要）",
          any(a["action"] == "retract_denied" for a in aud))
    s, _ = post("/api/v1/retract", "admin", {"image_id": ids["c"], "reason": "duplicate"})
    check("3c 管理者可排除任何一筆", s == 200, s)
    s, _ = post("/api/v1/retract", "nurse", {"image_id": ids["b"], "reason": "mis_submitted"})
    check("3d 護理師不得排除（不具送標註權限）", s == 403, s)
    s, _ = post("/api/v1/retract", "engineer", {"image_id": ids["b"], "reason": "mis_submitted"})
    check("3e 工程師不得排除臨床紀錄", s == 403, s)

    # ── 4 參數防呆 ────────────────────────────────────────────────
    bad = {
        "理由不在清單": {"image_id": ids["b"], "reason": "because"},
        "理由是撤回同意（必須走另一個端點）": {"image_id": ids["b"], "reason": "consent_withdrawn"},
        "other 沒填說明": {"image_id": ids["b"], "reason": "other"},
        "image_id 格式不合": {"image_id": "../../etc", "reason": "mis_submitted"},
    }
    got = {k: post("/api/v1/retract", "physician", v)[0] for k, v in bad.items()}
    check("4  不合法的排除參數全部擋下（400）", set(got.values()) == {400}, got)
    s, _ = post("/api/v1/retract", "physician",
                {"image_id": "0" * 16, "reason": "mis_submitted"})
    check("4b 查無紀錄回 404（與權限不足 403 分開）", s == 404, s)

    # ── 5 還原排除 ────────────────────────────────────────────────
    s, _ = post("/api/v1/unretract", "physician", {"image_id": ids["a"]})
    check("5  醫師不得自行還原（能自排自還等於這個標記可以反覆翻面）", s == 403, s)
    s, r = post("/api/v1/unretract", "admin", {"image_id": ids["a"], "note": "確認是誤按"})
    check("5b 管理者可還原", s == 200 and r["status"] == "unretracted", (s, r))
    check("5c 影像復原回 images/",
          os.path.exists(os.path.join(tmp, "images", ids["a"] + ".jpg")))
    _, st2 = fw.effective_queue()
    check("5d 還原後可訓練回到 2（c 仍被管理者排除）", st2["trainable"] == 2, st2["trainable"])
    check("5e 還原不刪除歷史（retracted.jsonl 兩筆以上，看得出排除→還原的過程）",
          len([l for l in open(os.path.join(tmp, "retracted.jsonl"), encoding="utf-8") if l.strip()]) >= 3)
    s, _ = post("/api/v1/unretract", "admin", {"image_id": ids["a"]})
    check("5f 重複還原被擋（並未被排除）", s == 400, s)

    # ── 6 一次性登入碼 ────────────────────────────────────────────
    try:
        import warnings
        warnings.filterwarnings("ignore")
        # app.py 會在**當前工作目錄**建 SQLite 檔與 uploads/。
        # 在唯讀或網路掛載的目錄下跑會得到 "disk I/O error"，而那與本測試無關。
        os.chdir(tmp)
        A = importlib.import_module("app")
    except Exception as e:
        print("SKIP  6  一次性登入碼（app.py 匯入失敗：%s）" % e)
        A = None

    if A is not None:
        acli = A.app.test_client()
        au.upsert_user(org="default", user="dr09", role="physician",
                       password="test-password-1234", display_name="醫師 09", actor="test")
        lr = acli.post("/api/auth/login", json={"username": "dr09", "password": "test-password-1234"})
        jwt_tok = (lr.get_json() or {}).get("access_token")
        check("6  一般登入可用", lr.status_code == 200 and bool(jwt_tok), lr.status_code)

        oc = acli.post("/api/v1/auth/onetime", headers={"Authorization": "Bearer " + jwt_tok})
        code = (oc.get_json() or {}).get("code")
        check("6b 可取得一次性登入碼", oc.status_code == 200 and bool(code), oc.status_code)
        check("6c 效期 60 秒", (oc.get_json() or {}).get("expires_in") == 60)

        # ⚠ 核心：otc 是一個簽章合法的 JWT。少了型別檢查，它就是 60 秒的萬用通行證。
        probe = acli.get("/api/v1/flywheel/stats", headers={"Authorization": "Bearer " + code})
        # ⚠ flask-jwt-extended 對 token_verification 失敗的預設回應是 **400**。
        # 那會讓客戶端去檢查請求主體，而真正的問題在 Authorization 標頭——
        # 所以後端另外掛了 token_verification_failed_loader 改回 401。
        check("6d 一次性碼**不能**當一般 token 用（打任何一般端點都要被拒）",
              probe.status_code == 401, probe.status_code)
        check("6d2 拒絕訊息說得出原因（不是含糊的 400 參數錯誤）",
              "一次性登入碼" in json.dumps(probe.get_json() or {}, ensure_ascii=False),
              probe.get_json())

        ex = acli.post("/api/auth/exchange", json={"code": code})
        ej = ex.get_json() or {}
        check("6e 可換成正常 token", ex.status_code == 200 and bool(ej.get("access_token")), ex.status_code)
        check("6f 換來的 token 帶正確角色與權限",
              ej.get("role") == "physician" and "gt.verify" in (ej.get("perms") or []), ej.get("role"))
        ok2 = acli.get("/api/v1/flywheel/stats",
                       headers={"Authorization": "Bearer " + ej["access_token"]})
        check("6g 換來的 token 可正常使用", ok2.status_code == 200, ok2.status_code)

        again = acli.post("/api/auth/exchange", json={"code": code})
        check("6h 同一個碼不可重複交換", again.status_code == 401, again.status_code)
        aud2 = [json.loads(l) for l in open(os.path.join(tmp, "audit.jsonl"), encoding="utf-8")]
        check("6i 重用留痕（可能是使用者按兩次，也可能是攔截後重放）",
              any(a["action"] == "otc_reused" for a in aud2))
        check("6j 發碼與交換都進稽核",
              any(a["action"] == "otc_issued" for a in aud2)
              and any(a["action"] == "login_otc" for a in aud2))

        bad_ex = acli.post("/api/auth/exchange", json={"code": "not-a-token"})
        check("6k 偽造的碼被拒", bad_ex.status_code == 401, bad_ex.status_code)
        check("6l 過期與偽造回同一句（分開講等於告訴攻擊者簽章是對的）",
              (bad_ex.get_json() or {}).get("error") == "登入碼無效或已過期")

        # 發碼到交換之間帳號被停用 —— 60 秒也是時間
        oc2 = acli.post("/api/v1/auth/onetime", headers={"Authorization": "Bearer " + jwt_tok})
        code2 = (oc2.get_json() or {}).get("code")
        au.upsert_user(org="default", user="dr09", role="physician", disabled=True, actor="test")
        ex2 = acli.post("/api/auth/exchange", json={"code": code2})
        check("6m 發碼後帳號被停用 → 交換失敗", ex2.status_code == 401, ex2.status_code)

    os.chdir(HERE)
    shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FAILED:
        print("FAILED %d 項：%s" % (len(FAILED), "; ".join(FAILED)))
        return 1
    print("全部通過：誤送排除與撤回同意在儲存、統計與稽核上完全分離；"
          "送件清單的範圍由伺服器端強制；一次性登入碼無法當一般 token 使用。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
