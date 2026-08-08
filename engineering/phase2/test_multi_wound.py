# -*- coding: utf-8 -*-
"""契約測試：**同一張影像多處傷口，不可以只留最大的那一個**。

    python engineering/phase2/test_multi_wound.py

## 為什麼

同一肢體多處傷口是臨床常態（小腿同時有兩處潰瘍）。而舊版整條鏈都只取
最大連通元件——後端 classify 的 `wound_polygon`、App 完成修邊的輪廓追蹤、
難例集成的比對，三處各自獨立地丟掉其餘元件。

2026-08-07 實測：醫師在修邊畫面明明把兩個傷口都標了，回到結果頁只看到一個，
他回報的是「參照圖沒更新」。但真正的問題比畫面嚴重得多：

  · **送進訓練集的 GT 是錯的**——第二個傷口被標成背景，
    等於在教模型「那不是傷口」。比少收一筆資料糟得多。
  · 面積（遮罩，涵蓋兩個）與輪廓（只有一個）不一致，
    而 App 顯示 52.93 cm²、主控台由多邊形反算只有左邊那個——
    **兩個數字都合理、都沒有警告**。

## 這支測試守什麼

  1. `gt_polygons` 收得下、存得住、驗得到（每個輪廓都要檢查座標空間）
  2. 面積以 App 送來的 `area_cm2` 為真值，不由多邊形反算
  3. 沒有 `area_cm2` 時退回多邊形，且**多輪廓要合計**
  4. 預覽 SVG 畫出每一個輪廓
  5. 舊格式（只有 gt_polygon）完全不受影響
"""
import base64
import hashlib
import importlib
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FLASK_DIR = os.path.abspath(os.path.join(HERE, "..", "..", "Backend", "Flask"))

FAILED = []
TOTAL = [0]


def check(name, ok, detail=""):
    TOTAL[0] += 1
    print(("PASS  " if ok else "FAIL  ") + name + (("   " + str(detail)) if detail else ""))
    if not ok:
        FAILED.append(name)


def make_jpeg(w=1200, h=900, salt=b""):
    from PIL import Image
    import io
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (180, 90, 80)).save(buf, "JPEG")
    return buf.getvalue() + salt


def main():
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        print("需要 Pillow：pip install pillow")
        return 1

    tmp = tempfile.mkdtemp(prefix="woundai_multi_")
    os.environ["WOUNDAI_FLYWHEEL_DIR"] = tmp
    os.environ["JWT_SECRET_KEY"] = "test-secret-key-please-ignore-0000"
    for sub in ("images", "quarantine", "tissue_masks", "depth_maps"):
        os.makedirs(os.path.join(tmp, sub), exist_ok=True)
    for f in ("retrain_queue.jsonl", "withdrawn.jsonl", "audit.jsonl",
              "users.jsonl", "retracted.jsonl"):
        open(os.path.join(tmp, f), "w").close()

    sys.path.insert(0, FLASK_DIR)
    for m in list(sys.modules):
        if m.startswith("api_flywheel"):
            del sys.modules[m]
    fw = importlib.import_module("api_flywheel")
    from flask import Flask
    from flask_jwt_extended import JWTManager, create_access_token
    app = Flask(__name__)
    app.config["JWT_SECRET_KEY"] = "test-only-please-ignore"
    JWTManager(app)
    app.register_blueprint(fw.flywheel_bp)
    cli = app.test_client()
    with app.app_context():
        H = {"Authorization": "Bearer " + create_access_token(
            identity="default:dr01",
            additional_claims={"role": "physician", "org": "default", "user": "dr01"})}

    # 左傷口 200×400、右傷口 150×150。刻意讓兩者面積差很多——
    # 只取最大的那一個時，漏掉的比例才看得出來。
    LEFT = [[100, 100], [300, 100], [300, 500], [100, 500]]      # 200×400 = 80000 px
    RIGHT = [[700, 300], [850, 300], [850, 450], [700, 450]]     # 150×150 = 22500 px
    MM = 0.5                                                     # → 80000*0.25/100 = 200 cm²
    #                                                              → 22500*0.25/100 = 56.25 cm²

    def submit(code, salt, **over):
        jpg = make_jpeg(salt=salt)
        iid = hashlib.sha1(jpg).hexdigest()[:16]
        open(os.path.join(tmp, "images", iid + ".jpg"), "wb").write(jpg)
        body = {
            "code": code, "gt_polygon": LEFT, "exudate": 1,
            "image_id": iid, "image_w": 1200, "image_h": 900, "mm_per_px": MM,
            "doctor_verified": True, "deidentified": True, "consent_train": True,
            "route": "cloud", "source": "sample",
        }
        body.update(over)
        return cli.post("/api/v1/annotation", headers=H, json=body), iid

    # ── 1. 純函式：面積合計 ────────────────────────────────────
    print("── 1 面積計算 ──")
    a1 = fw.poly_area_cm2(LEFT, MM)
    a2 = fw.poly_area_cm2(LEFT, MM, [LEFT, RIGHT])
    check("單一輪廓面積正確", abs(a1 - 200.0) < 0.5, "%.2f cm²" % a1)
    check("多輪廓面積是**合計**（不是只算最大的）", abs(a2 - 256.25) < 0.5,
          "%.2f cm²（左 200 ＋ 右 56.25）" % a2)
    check("多輪廓面積確實大於單一輪廓", a2 > a1 * 1.2, "%.2f vs %.2f" % (a2, a1))

    # App 送來的 area_cm2 才是真值（來自遮罩像素數）
    rec_fake = {"gt_polygon": LEFT, "gt_polygons": [LEFT, RIGHT], "mm_per_px": MM,
                "area_cm2": 258.9}
    check("有 area_cm2 時以它為準（遮罩才是醫師畫的東西）",
          abs(fw.record_area_cm2(rec_fake) - 258.9) < 0.01,
          fw.record_area_cm2(rec_fake))
    rec_fake2 = dict(rec_fake); rec_fake2.pop("area_cm2")
    check("沒有 area_cm2 時退回多邊形合計",
          abs(fw.record_area_cm2(rec_fake2) - 256.25) < 0.5,
          fw.record_area_cm2(rec_fake2))

    # ── 2. 收下多輪廓 ─────────────────────────────────────────
    print("\n── 2 送出與落盤 ──")
    r, iid = submit("WD-MULTI1", b"m1", gt_polygons=[LEFT, RIGHT], area_cm2=258.9)
    check("多輪廓標註送出成功", r.status_code == 200,
          "%s %s" % (r.status_code, (r.get_json() or {}).get("issues") or ""))
    rec = [x for x in fw.read_jsonl(fw.QUEUE) if x.get("image_id") == iid][-1]
    check("gt_polygons 有落盤", len(rec.get("gt_polygons") or []) == 2,
          len(rec.get("gt_polygons") or []))
    check("area_cm2 有落盤", abs((rec.get("area_cm2") or 0) - 258.9) < 0.01)

    j = cli.get("/api/v1/flywheel/records", headers=H).get_json() or {}
    row = next((x for x in j.get("records", []) if x["image_id"] == iid), None)
    check("清單找得到這一筆", row is not None)
    if row:
        check("清單面積用 App 的真值（不是由多邊形反算）",
              abs(row["area_cm2"] - 258.9) < 0.01, row["area_cm2"])
        check("清單標示輪廓數", row.get("polygon_count") == 2, row.get("polygon_count"))

    # ── 3. 預覽畫出每一個輪廓 ─────────────────────────────────
    print("\n── 3 預覽 ──")
    svg = cli.get("/api/v1/flywheel/record/%s/preview.svg" % iid, headers=H).data.decode()
    n_poly = len(re.findall(r'<polygon points="[^"]+" fill="none" stroke="#00e5ff"', svg))
    check("預覽畫出兩個輪廓（不是只有最大的那一個）", n_poly == 2, "找到 %d 個" % n_poly)
    check("右邊傷口的座標出現在預覽裡", "700.0,300.0" in svg)
    check("預覽標示傷口輪廓數", "2 個傷口輪廓" in svg)

    # ── 4. 每個輪廓都要驗座標空間 ─────────────────────────────
    print("\n── 4 守門 ──")
    BAD = [[700, 300], [1400, 300], [1400, 450], [700, 450]]   # x=1400 超出 1200
    r, _ = submit("WD-MULTIBAD", b"m2", gt_polygons=[LEFT, BAD])
    body = r.get_json() or {}
    check("第二個輪廓越界被擋下", r.status_code == 400, r.status_code)
    check("錯誤訊息指出是**第幾個**輪廓",
          any("第 2 個輪廓" in str(i) for i in (body.get("issues") or [])),
          body.get("issues"))
    r, _ = submit("WD-MULTIEMPTY", b"m3", gt_polygons=[])
    check("gt_polygons 為空清單被擋下", r.status_code == 400, r.status_code)

    # ── 5. 舊格式不受影響 ─────────────────────────────────────
    print("\n── 5 舊格式相容 ──")
    r, iid5 = submit("WD-LEGACY1", b"m5")          # 只有 gt_polygon，沒有 gt_polygons
    check("只有 gt_polygon 的舊格式仍收得下", r.status_code == 200, r.status_code)
    j5 = cli.get("/api/v1/flywheel/records", headers=H).get_json() or {}
    row5 = next((x for x in j5.get("records", []) if x["image_id"] == iid5), None)
    check("舊格式面積由單一多邊形算出", row5 and abs(row5["area_cm2"] - 200.0) < 0.5,
          row5 and row5["area_cm2"])
    check("舊格式輪廓數為 1", row5 and row5.get("polygon_count") == 1)
    svg5 = cli.get("/api/v1/flywheel/record/%s/preview.svg" % iid5, headers=H).data.decode()
    check("舊格式預覽仍畫得出輪廓",
          len(re.findall(r'stroke="#00e5ff"', svg5)) == 1)

    shutil.rmtree(tmp, ignore_errors=True)
    print("\n%d 項檢查，%d 項失敗" % (TOTAL[0], len(FAILED)))
    if FAILED:
        print("失敗：")
        for f in FAILED:
            print("  · " + f)
        return 1
    print("全部通過：多處傷口的每一個輪廓都收得下、驗得到、畫得出來；"
          "面積以遮罩為真值；舊格式不受影響。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
