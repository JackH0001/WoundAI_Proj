# -*- coding: utf-8 -*-
"""契約測試：**從時間軸補送標註，不必重新上傳影像、也不必重測一遍**。

這條路徑是 App v3「重簽同意 → 回頭補送」的後端側保證：
classify 當下已把影像存在 `flywheel/images/<image_id>.jpg`，
之後任何時間只要帶著同一個 `image_id`（外加本機留存的 gt_polygon 與尺寸）
就能把標註補進再訓練佇列——**請求裡沒有任何影像位元組**。

同時鎖住幾條不可回歸的守門線：
  - 缺 image_id（孤兒 GT）必須擋 —— 這是 2026-07-28 稽核發現 8/8 筆不可訓練的根因
  - image_id 路徑穿越必須擋
  - 撤回同意後補送必須擋，且**拒絕理由要是「已撤回同意」而不是「查無影像」**
    （撤回會把影像移進 quarantine，若先查檔案存在性，audit.jsonl 會記成錯的理由，
      IRB 稽核就看不出這筆是因撤回而被排除）
  - 重新簽署（restore）後補送必須恢復

不需要模型、不需要真的起 server（用 Flask test_client），跑得動就代表契約成立。

    python engineering/phase2/test_resubmit_from_timeline.py
"""
import os
import sys
import json
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


def make_jpeg(w=1200, h=900):
    """產一張最小可解碼 JPEG。沒有 Pillow 就退回一段固定位元組（守門邏輯不看畫面內容）。"""
    try:
        from PIL import Image
        import io
        buf = io.BytesIO()
        Image.new("RGB", (w, h), (180, 90, 80)).save(buf, "JPEG")
        return buf.getvalue()
    except Exception:
        return b"\xff\xd8\xff\xe0" + b"woundai-test-image" * 64 + b"\xff\xd9"


def main():
    tmp = tempfile.mkdtemp(prefix="woundai_fw_")
    os.environ["WOUNDAI_FLYWHEEL_DIR"] = tmp
    for sub in ("images", "quarantine"):
        os.makedirs(os.path.join(tmp, sub), exist_ok=True)
    for f in ("retrain_queue.jsonl", "withdrawn.jsonl", "audit.jsonl"):
        open(os.path.join(tmp, f), "w").close()

    sys.path.insert(0, FLASK_DIR)
    fw = importlib.import_module("api_flywheel")

    from flask import Flask
    from flask_jwt_extended import JWTManager, create_access_token
    app = Flask(__name__)
    app.config["JWT_SECRET_KEY"] = "test-only"
    JWTManager(app)
    app.register_blueprint(fw.flywheel_bp)
    with app.app_context():
        # ⚠ 一定要帶 role。RBAC S1 之後，缺角色的 token 一律 fail-closed（403）——
        # 這條測試曾因此整批失敗，而那正是預期行為：舊 token 不該還能送標註。
        # 補送標註屬醫師權限，所以這裡用 physician。
        tok = create_access_token(
            identity="default:dr_test",
            additional_claims={"role": "physician", "org": "default", "user": "dr_test"})
    cli = app.test_client()
    HDR = {"Authorization": "Bearer " + tok}

    def post(path, body):
        r = cli.post(path, json=body, headers=HDR)
        return r.status_code, r.get_json()

    # ---- classify 當下：影像落盤，App 只留下 image_id ----
    jpg = make_jpeg()
    image_id = hashlib.sha1(jpg).hexdigest()[:16]
    with open(os.path.join(tmp, "images", image_id + ".jpg"), "wb") as f:
        f.write(jpg)

    CODE = "WD-F9DDE995"
    poly1 = [[300, 250], [820, 250], [820, 700], [300, 700]]
    poly2 = [[305, 255], [830, 250], [825, 705], [300, 700], [295, 480]]  # 醫師重新修邊

    from _synthetic_receipts import ratify_synthetic
    ratify_synthetic(tmp, [image_id])

    def anno(poly, code=CODE, iou=1.0, consent=True, src="clinical"):
        """完全對應 BackendClient.submitAnnotation 送出的欄位組合。"""
        return {
            "code": code, "gt_polygon": poly, "exudate": 1,
            "doctor_verified": True, "deidentified": True, "consent_train": consent,
            "image_id": image_id, "image_w": 1200, "image_h": 900,
            "mm_per_px": 0.173913, "route": "cloud_escalated(AU)",
            "correction_iou": iou, "care_note": "resubmit from timeline", "source": src,
        }

    def stats():
        return fw.effective_queue(
            os.path.join(tmp, "retrain_queue.jsonl"),
            os.path.join(tmp, "images"),
            os.path.join(tmp, "withdrawn.jsonl"))

    # 1 只帶 image_id 補送 —— 請求裡沒有任何影像位元組
    body = anno(poly1)
    assert not any(isinstance(v, (bytes, bytearray)) for v in body.values()), "請求不應含影像位元組"
    s, r = post("/api/v1/annotation", body)
    check("1  只帶 image_id 補送標註（不重傳影像）", s == 200 and r.get("status") == "enqueued", r.get("status"))

    # 2 同影像同遮罩 → 去重
    s, r = post("/api/v1/annotation", anno(poly1))
    check("2  同影像同遮罩 → 去重不重複入列", s == 200 and r.get("status") == "duplicate_skipped")

    # 3 重新修邊後補送 → 修訂版，舊筆被 supersede
    s, r = post("/api/v1/annotation", anno(poly2, iou=0.93))
    check("3  重新修邊後補送 → 標為醫師修訂版", s == 200 and bool(r.get("note")), r.get("note"))

    recs, st = stats()
    check("4  佇列 2 筆 → 可訓練 1 筆（同影像取最新）",
          st["total"] == 2 and st["trainable"] == 1 and st["superseded"] == 1, st)
    check("4b 取到的是修訂版（5 點而非 4 點）", len(recs[0]["gt_polygon"]) == 5)

    # 5 孤兒 GT
    d = anno(poly1); d.pop("image_id")
    s, r = post("/api/v1/annotation", d)
    check("5  缺 image_id（孤兒 GT）被擋", s == 400)

    # 6 image_id 指向不存在的影像
    d = anno(poly1); d["image_id"] = "0" * 16
    s, r = post("/api/v1/annotation", d)
    check("6  image_id 查無影像被擋", s == 400)

    # 7 路徑穿越
    for evil in ("../../../etc/passwd", "..%2F..%2Fsecret", "abc"):
        d = anno(poly1); d["image_id"] = evil
        s, _ = post("/api/v1/annotation", d)
        if s != 400:
            check("7  image_id 路徑穿越被擋", False, evil)
            break
    else:
        check("7  image_id 路徑穿越被擋", True)

    # 8 未取得訓練同意
    s, r = post("/api/v1/annotation", anno(poly1, code="WD-NOCONSENT", consent=False))
    check("8  consent_train=false 被擋", s == 400)

    # 9 撤回同意 → 整組失效 + 影像隔離
    s, _ = post("/api/v1/consent/withdraw", {"code": CODE, "image_ids": [image_id], "reason": "病患撤回"})
    _, st = stats()
    check("9  撤回後可訓練歸零", st["trainable"] == 0 and st["withdrawn"] == 2, st["trainable"])
    check("9b 影像已移入 quarantine",
          os.path.exists(os.path.join(tmp, "quarantine", image_id + ".jpg"))
          and not os.path.exists(os.path.join(tmp, "images", image_id + ".jpg")))

    # 10 撤回後補送被擋，且**理由要正確**
    s, r = post("/api/v1/annotation", anno(poly2))
    issue = (r.get("issues") or [""])[0]
    check("10 撤回後補送被擋", s == 400)
    check("10b 拒絕理由是「已撤回同意」而非「查無影像」（IRB 稽核要看得出來）",
          "撤回" in issue and "查無影像" not in issue, issue[:50])
    reasons = [json.loads(l).get("result", "") for l in open(os.path.join(tmp, "audit.jsonl"), encoding="utf-8")]
    check("10c audit.jsonl 也記到撤回理由", any("撤回訓練同意" in x for x in reasons))

    # 11 重新簽署後恢復
    s, r = post("/api/v1/consent/restore", {"code": CODE, "image_ids": [image_id], "reason": "重新簽署"})
    check("11 restore 成功", s == 200 and r.get("status") == "restored")
    s, _ = post("/api/v1/annotation", anno(poly2, iou=0.93))
    _, st = stats()
    check("11b 重新簽署後補送成功、可訓練恢復", s == 200 and st["trainable"] == 1, st["trainable"])

    shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FAILED:
        print("FAILED %d 項：%s" % (len(FAILED), "; ".join(FAILED)))
        return 1
    print("全部通過：時間軸補送標註不需重傳影像，且撤回/重簽同意的閘門與稽核理由皆正確。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
