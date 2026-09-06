# -*- coding: utf-8 -*-
"""契約測試：**匯出的影像與遮罩必須逐像素對齊**。

    python engineering/phase2/test_tissue_export.py

## 為什麼這支測試是必要的

組織遮罩不是整張影像。它是修邊畫面的柵格，只覆蓋「傷口外框 ＋ 各 60% 邊距」
那塊 ROI，位置記在 `tissue_raster`（rx0/ry0/mw/mh/m_scale）。

而 `train_tissue_seg.py` 的 `__getitem__` 假設兩者**已經在同一個座標空間**：

    img = imread(影像);  img = resize(img, 512)
    m   = imread(遮罩);  m   = resize(m,   512)

所以匯出時若照直覺寫（下載影像、下載遮罩、各存一份），每一對訓練資料的幾何
都是錯的——遮罩被拉伸到整張照片。

**而訓練照樣會跑完、loss 照樣會下降、指標照樣會有數字。**

這個錯誤在 2026-08-07 的主控台預覽上真實發生過（症狀是醫師回報「標註超出傷口
邊界」）。在訓練資料上發生則不會有任何人回報：它只會讓模型學到錯的東西，
而所有指標看起來都正常。

## 驗法：合成一組已知答案

在已知位置放一個純色方塊，遮罩在對應位置放同樣的方塊。
匯出之後，**遮罩非零的區域必須落在影像那個方塊上**——
只要幾何錯了（拉伸、位移、比例不對），重疊率就會掉下來。

用合成資料而不是真實照片，是因為真實照片沒有「正確答案」可比；
而幾何錯誤在真實照片上看起來仍然像個合理的遮罩。
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FLASK_DIR = os.path.abspath(os.path.join(HERE, "..", "..", "Backend", "Flask"))

FAILED = []
TOTAL = [0]


def check(name, ok, detail=""):
    TOTAL[0] += 1
    print(("PASS  " if ok else "FAIL  ") + name + (("   " + str(detail)) if (detail and not ok) else ""))
    if not ok:
        FAILED.append(name)


def main():
    try:
        import cv2
        import numpy as np
    except ImportError:
        print("需要 opencv-python 與 numpy")
        return 1

    sys.path.insert(0, HERE)
    import export_tissue_dataset as EX

    # ── 1 幾何換算與後端一致 ──────────────────────────────────────
    print("── 1 幾何換算 ──")
    sys.path.insert(0, FLASK_DIR)
    tmp0 = tempfile.mkdtemp(prefix="woundai_exp_")
    os.environ["WOUNDAI_FLYWHEEL_DIR"] = tmp0
    for s in ("images", "quarantine", "tissue_masks"):
        os.makedirs(os.path.join(tmp0, s), exist_ok=True)
    for f in ("retrain_queue.jsonl", "withdrawn.jsonl", "audit.jsonl",
              "users.jsonl", "retracted.jsonl"):
        open(os.path.join(tmp0, f), "w").close()
    for m in list(sys.modules):
        if m.startswith("api_flywheel"):
            del sys.modules[m]
    import importlib
    fw = importlib.import_module("api_flywheel")

    # 兩邊各寫一份換算遲早會分岔，而分岔的症狀是「預覽看起來對、訓練資料是歪的」。
    for tr in ({"rx0": 100.0, "ry0": 50.0, "mw": 400, "mh": 300, "m_scale": 0.5},
               {"rx0": 0.0, "ry0": 0.0, "mw": 64, "mh": 64, "m_scale": 1.0},
               {"rx0": -20.0, "ry0": 5.0, "mw": 200, "mh": 100, "m_scale": 0.25}):
        a = EX.raster_rect(tr)
        b = fw._raster_rect({"tissue_raster": tr}, 0, 0)
        check("raster_rect 與後端一致 %s" % tr, a == b, "%s vs %s" % (a, b))
    check("缺欄位回 None", EX.raster_rect({"mw": 10}) is None)
    check("m_scale=0 回 None（不可除以零）",
          EX.raster_rect({"rx0": 0, "ry0": 0, "mw": 10, "mh": 10, "m_scale": 0}) is None)

    # ── 2 裁切後逐像素對齊 ────────────────────────────────────────
    print("\n── 2 裁切對齊（合成已知答案）──")
    W, H = 800, 600
    img = np.zeros((H, W, 3), np.uint8)
    # 影像上的「傷口」：一個明確的方塊
    img[200:400, 300:500] = (0, 0, 255)

    # ROI = 傷口外框 + 邊距，柵格縮放 0.5
    rx0, ry0, ms = 250.0, 150.0, 0.5
    roi_w, roi_h = 300, 300                      # 影像座標中的 ROI 大小
    mw, mh = int(roi_w * ms), int(roi_h * ms)    # 150 × 150
    mask = np.zeros((mh, mw), np.uint8)
    # 遮罩上對應的方塊：影像座標 (300..500, 200..400) → ROI 內 (50..250, 50..250)
    #                                                 → 柵格 ×0.5 → (25..125, 25..125)
    mask[25:125, 25:125] = 1

    tr = {"rx0": rx0, "ry0": ry0, "mw": mw, "mh": mh, "m_scale": ms}
    cimg, cmask, why = EX.crop_to_roi(img, mask, tr)
    check("裁切成功", cimg is not None, why)
    if cimg is None:
        return 1
    check("影像與遮罩尺寸相同", cimg.shape[:2] == cmask.shape[:2],
          "%s vs %s" % (cimg.shape[:2], cmask.shape[:2]))
    check("裁切尺寸＝ROI 大小", cimg.shape[:2] == (roi_h, roi_w), cimg.shape[:2])

    # 核心：遮罩標到的地方，影像上必須真的是那個方塊
    red = (cimg[:, :, 2] > 200)
    lab = (cmask > 0)
    inter = float(np.logical_and(red, lab).sum())
    union = float(np.logical_or(red, lab).sum())
    iou = inter / union if union else 0.0
    check("遮罩與影像上的目標重疊 IoU > 0.95（幾何正確）", iou > 0.95, "IoU=%.3f" % iou)

    # ── 3 ROI 超出影像邊界時要同步夾取 ────────────────────────────
    print("\n── 3 ROI 溢出邊界 ──")
    # 修邊畫面允許往外擴張，所以 ROI 可能有一部分在影像外。
    # 只夾影像不夾遮罩（或反過來）就是位移——而位移後的遮罩看起來仍然合理。
    tr2 = {"rx0": -50.0, "ry0": -40.0, "mw": 200, "mh": 200, "m_scale": 0.5}
    img2 = np.zeros((H, W, 3), np.uint8)
    img2[0:60, 0:100] = (0, 0, 255)          # 左上角的目標
    # ⚠ 遮罩尺寸**必須等於 tr 宣告的 mw×mh**。第一版寫成 100×100 而 tr 說 200×200，
    # 測試因此報紅（IoU 0.034）而程式碼是對的——測試自己的前提不自洽。
    # 這正是本測試要防的錯誤的鏡像：尺寸宣告與實際資料不符。
    m2 = np.zeros((tr2["mh"], tr2["mw"]), np.uint8)
    # ROI 影像座標 (-50..350, -40..360)；目標 (0..100, 0..60)
    # → ROI 內 (50..150, 40..100) → 柵格 ×0.5 → (25..75, 20..50)
    m2[20:50, 25:75] = 2
    c2i, c2m, why2 = EX.crop_to_roi(img2, m2, tr2)
    check("溢出邊界仍裁得出來", c2i is not None, why2)
    if c2i is not None:
        check("尺寸仍相同", c2i.shape[:2] == c2m.shape[:2],
              "%s vs %s" % (c2i.shape[:2], c2m.shape[:2]))
        r2 = (c2i[:, :, 2] > 200)
        l2 = (c2m > 0)
        i2 = float(np.logical_and(r2, l2).sum())
        u2 = float(np.logical_or(r2, l2).sum())
        check("溢出情況下仍對得準（IoU > 0.9）", (i2 / u2 if u2 else 0) > 0.9,
              "IoU=%.3f" % (i2 / u2 if u2 else 0))

    # ── 4 類別碼不可被插值破壞 ────────────────────────────────────
    print("\n── 4 類別碼完整性 ──")
    # 遮罩存的是**類別碼**不是灰階值。任何插值都會在 1 和 3 之間插出 2——
    # 一個從未被醫師標註過的類別會憑空出現在訓練資料裡。
    m3 = np.zeros((100, 100), np.uint8)
    m3[:, :50] = 1
    m3[:, 50:] = 3
    tr3 = {"rx0": 0.0, "ry0": 0.0, "mw": 100, "mh": 100, "m_scale": 0.25}
    _, c3m, _ = EX.crop_to_roi(np.zeros((400, 400, 3), np.uint8), m3, tr3)
    vals = sorted(set(np.unique(c3m).tolist()))
    check("放大後只剩原本的碼（沒有插值產生的中間值）", vals == [1, 3], vals)

    # ── 5 端到端：離線匯出一次 ────────────────────────────────────
    print("\n── 5 端到端 ──")
    from flask import Flask
    from flask_jwt_extended import JWTManager, create_access_token
    from PIL import Image
    import hashlib
    import io as _io
    app = Flask(__name__)
    app.config["JWT_SECRET_KEY"] = "t" * 32
    JWTManager(app)
    app.register_blueprint(fw.flywheel_bp)
    cli = app.test_client()
    with app.app_context():
        H_DR = {"Authorization": "Bearer " + create_access_token(
            identity="default:dr01",
            additional_claims={"role": "physician", "org": "default", "user": "dr01"})}

    buf = _io.BytesIO()
    Image.fromarray(img[:, :, ::-1]).save(buf, "JPEG")
    jpg = buf.getvalue()
    iid = hashlib.sha1(jpg).hexdigest()[:16]
    open(os.path.join(tmp0, "images", iid + ".jpg"), "wb").write(jpg)
    from _synthetic_receipts import ratify_synthetic
    ratify_synthetic(tmp0, [iid])

    mbuf = _io.BytesIO()
    m_rgb = np.zeros((mh, mw, 3), np.uint8)
    m_rgb[:, :, 0] = mask                       # PIL 是 RGB，碼放 R
    Image.fromarray(m_rgb).save(mbuf, "PNG")
    import base64
    r = cli.post("/api/v1/annotation", headers=H_DR, json={
        "code": "WD-EXPORT", "gt_polygon": [[300, 200], [500, 200], [500, 400], [300, 400]],
        "exudate": 1, "image_id": iid, "image_w": W, "image_h": H, "mm_per_px": 0.5,
        "doctor_verified": True, "deidentified": True, "consent_train": True,
        "route": "cloud", "source": "sample",
        "tissue_mask_png": base64.b64encode(mbuf.getvalue()).decode(),
        "tissue_edited": True, "tissue_edit_px": 500,
        "tissue_raster": tr,
    })
    check("前置：帶組織遮罩的送件成功", r.status_code == 200,
          "%s %s" % (r.status_code, (r.get_json() or {}).get("issues")))

    out = tempfile.mkdtemp(prefix="woundai_ds_")
    rc = subprocess.run(
        [sys.executable, os.path.join(HERE, "export_tissue_dataset.py"),
         "--flywheel", tmp0, "--out", out],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=dict(os.environ, WOUNDAI_FLYWHEEL_DIR=tmp0))
    check("匯出腳本執行成功", rc.returncode == 0, (rc.stdout or "")[-400:] + (rc.stderr or "")[-400:])
    imgs = os.listdir(os.path.join(out, "images")) if os.path.isdir(os.path.join(out, "images")) else []
    msks = os.listdir(os.path.join(out, "masks")) if os.path.isdir(os.path.join(out, "masks")) else []
    check("產出 images/ 與 masks/ 各一筆", len(imgs) == 1 and len(msks) == 1,
          "%s / %s" % (imgs, msks))
    if imgs and msks:
        ei = cv2.imread(os.path.join(out, "images", imgs[0]))
        em = cv2.imread(os.path.join(out, "masks", msks[0]))
        check("匯出的影像與遮罩尺寸相同", ei.shape[:2] == em.shape[:2],
              "%s vs %s" % (ei.shape[:2], em.shape[:2]))
        check("遮罩的碼在 R 通道（訓練腳本讀 [:, :, 2]）", int(em[:, :, 2].max()) > 0,
              "R 通道最大值 %d" % int(em[:, :, 2].max()))
        er = (ei[:, :, 2] > 200)
        el = (em[:, :, 2] > 0)
        ii = float(np.logical_and(er, el).sum())
        uu = float(np.logical_or(er, el).sum())
        check("端到端幾何正確（IoU > 0.9）", (ii / uu if uu else 0) > 0.9,
              "IoU=%.3f" % (ii / uu if uu else 0))
    rp = os.path.join(out, "dataset_report.json")
    check("產出 dataset_report.json", os.path.isfile(rp))
    if os.path.isfile(rp):
        rep = json.load(open(rp, encoding="utf-8"))
        check("報告帶每類別像素數（收案要看得到缺口）",
              bool(rep.get("class_px")), rep.get("class_px"))
        check("報告帶 actor（切分時要避免同一標註者跨訓練/驗證）",
              "actor" in (rep.get("items") or [{}])[0])

    print("\n%d 項檢查，%d 項失敗" % (TOTAL[0], len(FAILED)))
    if FAILED:
        print("失敗：")
        for x in FAILED:
            print("  · " + x)
        print("\n⚠ 幾何錯誤不會讓訓練失敗——loss 照樣下降、指標照樣有數字。")
        print("  它只會讓模型學到錯的東西，而沒有任何一步會報錯。")
        return 1
    print("全部通過：匯出的影像與遮罩逐像素對齊，類別碼未被插值破壞。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
