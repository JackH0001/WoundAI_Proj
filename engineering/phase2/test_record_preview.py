# -*- coding: utf-8 -*-
"""契約測試：**送件清單的複核資訊，與它刻意不含的東西**。

    python engineering/phase2/test_record_preview.py

## 這條路徑要解決什麼

複核者（或送件的醫師自己）要判斷「這一筆對不對、該不該排除」。
原本清單只有代碼／面積／狀態——而一筆 74 cm² 的紀錄看起來完全正常，
即使它**根本沒有組織標註**，或組織遮罩是 AI 自己的輸出從沒被人改過。
那兩件事決定它進不進得了組織分割訓練集，而清單上看不出來。

## 為什麼預覽是向量圖而不是照片

複核要回答的問題不需要看到病患皮膚：輪廓形狀合不合理、組織分區有沒有、
比例分布是否荒謬——從幾何與類別就看得出來。主控台目前刻意不顯示任何影像；
為了複核便利把傷口照片放進瀏覽器，代價是快取、截圖、旁人目視，
而那條界線一旦破了就補不回來。

所以這支測試**同時鎖住兩件事**：
  1. 複核需要的欄位與圖確實出得來
  2. 出來的東西裡**沒有任何原始影像位元組**——這一條比第一條重要
"""
import os
import sys
import json
import base64
import hashlib
import re
import shutil
import tempfile
import importlib

HERE = os.path.dirname(os.path.abspath(__file__))
FLASK_DIR = os.path.abspath(os.path.join(HERE, "..", "..", "Backend", "Flask"))

FAILED = []
TOTAL = [0]


def check(name, ok, detail=""):
    TOTAL[0] += 1
    print(("PASS  " if ok else "FAIL  ") + name + (("   " + str(detail)) if detail else ""))
    if not ok:
        FAILED.append(name)


def make_jpeg(w=1200, h=900):
    from PIL import Image
    import io
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (180, 90, 80)).save(buf, "JPEG")
    return buf.getvalue()


def make_tissue_png(w=200, h=150):
    """組織遮罩：值放 R 通道（與 App 的 TissueMaskCodec 一致）。

    刻意讓左半是肉芽(1)、右半是壞死(3)，而且**面積差很多**——
    降採樣若用中心取樣而非眾數，小的那一類會時有時無，這裡看得出來。
    """
    from PIL import Image
    import io
    im = Image.new("RGB", (w, h), (0, 0, 0))
    px = im.load()
    for y in range(h):
        for x in range(w):
            if (x - w / 2) ** 2 + (y - h / 2) ** 2 < (min(w, h) / 3) ** 2:
                px[x, y] = ((1 if x < w * 0.7 else 3), 0, 0)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def main():
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        print("需要 Pillow：pip install pillow")
        return 1

    tmp = tempfile.mkdtemp(prefix="woundai_preview_")
    os.environ["WOUNDAI_FLYWHEEL_DIR"] = tmp
    os.environ["JWT_SECRET_KEY"] = "test-secret-key-please-ignore-0000"
    for sub in ("images", "quarantine", "tissue_masks"):
        os.makedirs(os.path.join(tmp, sub), exist_ok=True)
    for f in ("retrain_queue.jsonl", "withdrawn.jsonl", "audit.jsonl",
              "users.jsonl", "retracted.jsonl"):
        open(os.path.join(tmp, f), "w").close()

    sys.path.insert(0, FLASK_DIR)
    fw = importlib.import_module("api_flywheel")

    # 只掛藍圖，不匯入 app.py：後者會在**當前工作目錄**建 SQLite 與 uploads/，
    # 在網路掛載上會得到 "disk I/O error"，而那與本測試無關。
    from flask import Flask
    from flask_jwt_extended import JWTManager, create_access_token
    flask_app = Flask(__name__)
    flask_app.config["JWT_SECRET_KEY"] = "test-only"
    JWTManager(flask_app)
    flask_app.register_blueprint(fw.flywheel_bp)
    cli = flask_app.test_client()

    def bearer(user, role):
        with flask_app.app_context():
            return {"Authorization": "Bearer " + create_access_token(
                identity="default:%s" % user,
                additional_claims={"role": role, "org": "default", "user": user})}

    H = bearer("dr01", "physician")

    # classify 的產物：影像落在 images/<sha1[:16]>.jpg。這裡直接建，
    # 免得為了拿一個 image_id 而把整個 app.py 拉進來。
    jpg = make_jpeg()
    iid = hashlib.sha1(jpg).hexdigest()[:16]
    with open(os.path.join(tmp, "images", iid + ".jpg"), "wb") as f:
        f.write(jpg)

    poly = [[300, 200], [800, 210], [820, 640], [310, 630]]
    body = {
        "code": "WD-PREVIEW1", "gt_polygon": poly, "exudate": 1,
        "image_id": iid, "image_w": 1200, "image_h": 900, "mm_per_px": 0.25,
        "doctor_verified": True, "deidentified": True, "consent_train": True,
        "route": "cloud", "source": "sample",
        "tissue_frac": {"granulation": 0.62, "necrosis": 0.38},
        "tissue_mask_png": base64.b64encode(make_tissue_png()).decode(),
        "tissue_edited": True, "tissue_edit_px": 12483, "tissue_edit_ratio": 0.21,
        "tissue_raster": {"mw": 200, "mh": 150, "rx0": 300.0, "ry0": 200.0, "sx": 2.6, "sy": 2.93},
        "quality": {"focus_lapvar": 42.0, "clipped_frac": 0.01,
                    "roi_short_px": 430, "marker_frac": 0.004, "marker_skew": 0.05},
        "capture_device": "Samsung SM-S918",
    }
    r = cli.post("/api/v1/annotation", headers=H, json=body)
    check("標註送出成功", r.status_code == 200, r.status_code)

    # ── 1. 清單欄位 ──
    r = cli.get("/api/v1/flywheel/records", headers=H)
    recs = (r.get_json() or {}).get("records") or []
    rec = next((x for x in recs if x.get("image_id") == iid), None)
    check("清單找得到這一筆", rec is not None)
    if rec:
        check("清單有 tissue_mask 旗標", rec.get("tissue_mask") is True)
        check("清單有 tissue_edited 旗標", rec.get("tissue_edited") is True)
        check("清單有醫師修正像素數", rec.get("tissue_edit_px") == 12483, rec.get("tissue_edit_px"))
        check("清單有組織比例", (rec.get("tissue_frac") or {}).get("granulation") == 0.62)
        # depth_source 缺席時要回 "none" 而不是 null——
        # 日後要分得出「沒拍」與「這個欄位還不存在」。
        check("深度來源明確標為 none", rec.get("depth_source") == "none", rec.get("depth_source"))
        check("清單有拍攝裝置", rec.get("capture_device") == "Samsung SM-S918")
        check("清單有品質資訊", (rec.get("quality") or {}).get("focus_lapvar") == 42.0)
        check("清單標示可預覽", rec.get("has_preview") is True)

    # ── 2. 預覽圖 ──
    r = cli.get("/api/v1/flywheel/record/%s/preview.svg" % iid, headers=H)
    check("預覽回 200", r.status_code == 200, r.status_code)
    svg = r.data.decode("utf-8")
    check("是 SVG", svg.startswith("<svg") and svg.rstrip().endswith("</svg>"))
    check("content-type 是 image/svg+xml", "image/svg+xml" in r.headers.get("Content-Type", ""))
    check("畫出了 GT 輪廓", "<polygon" in svg and "300.0,200.0" in svg)
    check("畫出了組織分區", '"#1d9e75"' in svg and '"#4a3f3a"' in svg,
          "肉芽綠與壞死深褐都要出現")
    check("標示醫師已修正像素數", "12483" in svg)
    check("標示組織比例", "肉芽62%" in svg and "壞死38%" in svg)
    check("圖上寫明不含原始影像", "不含任何原始影像像素" in svg)

    # ⚠ 最重要的一條：預覽裡不得有任何原始影像位元組。
    jpg = make_jpeg()
    check("預覽不含 JPEG 標頭", b"\xff\xd8\xff" not in r.data)
    check("預覽不含 base64 內嵌影像", "data:image" not in svg)
    check("預覽不含原圖位元組", jpg[:64] not in r.data)
    check("預覽不含腳本", "<script" not in svg.lower() and "onload" not in svg.lower())

    # ── 2b. 組織遮罩必須畫在**它真正的位置**，不是拉滿整張影像 ──────
    #
    # 2026-08-07 實測回報：「雲端的組織標註邊緣超過傷口邊界，正常嗎？會不會污染訓練？」
    # 遮罩本身完全正確——錯的是這張複核圖：它把 mw×mh 的 ROI 柵格直接拉滿 w×h，
    # 於是組織色塊大幅溢出傷口輪廓。
    #
    # **一張畫錯的複核圖比沒有複核圖危險**：它會讓人排除掉正確的紀錄。
    print("\n── 2b 組織遮罩的定位 ──")
    tr = body["tissue_raster"]
    rx, ry = tr["rx0"], tr["ry0"]
    rw = tr["mw"] / tr["sx"] if "sx" in tr else None
    # 本測試的 body 用的是 m_scale 的舊鍵名，統一改成後端實際讀的那個
    check("測試資料含 tissue_raster 定位資訊", all(k in tr for k in ("rx0", "ry0", "mw", "mh")))
    xs = [float(m) for m in re.findall(r'<rect x="([\d.]+)" y="[\d.]+" width="[\d.]+" height="[\d.]+" fill="#', svg)]
    ys = [float(m) for m in re.findall(r'<rect x="[\d.]+" y="([\d.]+)" width="[\d.]+" height="[\d.]+" fill="#', svg)]
    check("有畫出組織色塊", len(xs) > 10, "%d 塊" % len(xs))
    if xs:
        # 色塊必須落在 tissue_raster 宣告的矩形內（容一點邊界誤差）
        x1 = rx + tr["mw"] / float(tr.get("m_scale") or 1.0)
        y1 = ry + tr["mh"] / float(tr.get("m_scale") or 1.0)
        check("組織色塊全部落在 tissue_raster 宣告的範圍內",
              min(xs) >= rx - 2 and max(xs) <= x1 + 2 and min(ys) >= ry - 2 and max(ys) <= y1 + 2,
              "色塊 x∈[%.0f,%.0f] y∈[%.0f,%.0f]；柵格 x∈[%.0f,%.0f] y∈[%.0f,%.0f]"
              % (min(xs), max(xs), min(ys), max(ys), rx, x1, ry, y1))
        # 這一條才是真正抓到 bug 的：拉滿整張影像的話，色塊會從 0 開始
        check("組織色塊沒有從影像原點開始（＝沒有被拉滿整張圖）", min(xs) > 1.0,
              "最小 x = %.1f（rx0 = %.1f）" % (min(xs), rx))

    # ── 3. 未修正的遮罩要**在圖上**講清楚 ──
    jpg2 = make_jpeg(800, 600)
    iid2 = hashlib.sha1(jpg2).hexdigest()[:16]
    with open(os.path.join(tmp, "images", iid2 + ".jpg"), "wb") as f:
        f.write(jpg2)
    # ⚠ 輪廓要重畫成 800×600 之內。沿用上一筆的座標會有點落在畫面外，
    # 後端會以「座標空間不符」400 擋下——那個守門是對的（見 api_flywheel 第 148 行），
    # 但會讓這一條測試因為錯誤的理由而失敗。
    b2 = dict(body, code="WD-PREVIEW2", image_id=iid2, image_w=800, image_h=600,
              gt_polygon=[[100, 100], [600, 110], [610, 450], [110, 440]],
              tissue_edited=False, tissue_edit_px=0, tissue_edit_ratio=0.0)
    r2 = cli.post("/api/v1/annotation", headers=H, json=b2)
    check("第二筆標註送出成功", r2.status_code == 200,
          "%s %s" % (r2.status_code, (r2.get_json() or {}).get("issues") or ""))
    s2 = cli.get("/api/v1/flywheel/record/%s/preview.svg" % iid2, headers=H).data.decode()
    check("未修正的遮罩在圖上明講不進訓練集", "未經醫師修正" in s2 and "不會進入訓練集" in s2)

    # ── 3b. 只改組織、不改邊界，必須被視為**新的標註**而非重複 ──────
    #
    # 2026-08-06 實測的 bug：去重鍵是 image_id::poly_sig(gt_polygon)，
    # 組織遮罩不在鍵裡。醫師從時間軸重新修邊、只改組織分區沒動邊界時，
    # poly_sig 完全相同 → 判定重複 → **新的組織遮罩根本沒存**，
    # 而畫面顯示「相同影像的相同遮罩已在佇列（去重）」，聽起來像正常行為。
    #
    # 傷口輪廓與組織分區是兩份獨立的 GT，任一份變了就是一筆新的標註。
    print("\n── 3b 只改組織不改邊界 ──")
    b3 = dict(body, code="WD-TISSUEONLY",
              tissue_mask_png=base64.b64encode(make_tissue_png(200, 150)).decode(),
              tissue_edit_px=99999)
    r3 = cli.post("/api/v1/annotation", headers=H, json=b3)
    check("相同影像＋相同輪廓＋相同組織遮罩 → 判定重複",
          (r3.get_json() or {}).get("status") == "duplicate_skipped",
          (r3.get_json() or {}).get("status"))

    # 換一張不同的組織遮罩（邊界完全不動）
    import numpy as _np
    from PIL import Image as _Im
    import io as _io
    _im = _Im.new("RGB", (200, 150), (0, 0, 0))
    _px = _im.load()
    for _y in range(150):
        for _x in range(200):
            if (_x - 100) ** 2 + (_y - 75) ** 2 < 50 ** 2:
                _px[_x, _y] = ((2 if _x < 100 else 4), 0, 0)   # 換成腐肉／上皮
    _buf = _io.BytesIO(); _im.save(_buf, "PNG")
    b4 = dict(body, code="WD-TISSUENEW",
              tissue_mask_png=base64.b64encode(_buf.getvalue()).decode(),
              tissue_edit_px=54321)
    r4 = cli.post("/api/v1/annotation", headers=H, json=b4)
    j4 = r4.get_json() or {}
    check("相同輪廓但**組織遮罩不同** → 收下（不是重複，是修訂）",
          j4.get("status") != "duplicate_skipped", j4.get("status"))
    recs4 = [x for x in fw.read_jsonl(fw.QUEUE) if x.get("code") == "WD-TISSUENEW"]
    check("新的組織遮罩確實入列", len(recs4) == 1, "%d 筆" % len(recs4))
    if recs4:
        check("紀錄帶有 tissue_sig（下次比對才拿得到）", bool(recs4[0].get("tissue_sig")))
        check("新遮罩的醫師修正像素數有落盤", recs4[0].get("tissue_edit_px") == 54321)
    # 覆蓋後的遮罩檔要是新的那一份
    _stored = open(os.path.join(tmp, "tissue_masks", iid + ".png"), "rb").read()
    check("落盤的遮罩已更新為最新送出的那一份",
          hashlib.sha1(_stored).hexdigest() == hashlib.sha1(_buf.getvalue()).hexdigest())

    # ── 4. 守門 ──
    r = cli.get("/api/v1/flywheel/record/..%2Fetc%2Fpasswd/preview.svg", headers=H)
    check("路徑穿越擋下", r.status_code in (400, 404), r.status_code)
    r = cli.get("/api/v1/flywheel/record/%s/preview.svg" % ("z" * 16), headers=H)
    check("格式不合法的 image_id 回 400", r.status_code == 400, r.status_code)
    # 格式合法但不存在 → 404。要用 16 碼十六進位，否則擋在格式檢查而測不到這條。
    r = cli.get("/api/v1/flywheel/record/%s/preview.svg" % ("a" * 16), headers=H)
    check("不存在的 image_id 回 404", r.status_code == 404, r.status_code)
    r = cli.get("/api/v1/flywheel/record/%s/preview.svg" % iid)
    check("未帶 token 擋下", r.status_code == 401, r.status_code)

    # 別人的紀錄：回 404 而非 403——後者會洩漏「這個 image_id 存在」，
    # 而 image_id 是 16 碼十六進位，可以枚舉。
    H2 = bearer("dr02", "physician")
    r = cli.get("/api/v1/flywheel/record/%s/preview.svg" % iid, headers=H2)
    check("看不到別人送的紀錄，且回 404 不洩漏存在性", r.status_code == 404, r.status_code)
    rr = cli.get("/api/v1/flywheel/records", headers=H2).get_json() or {}
    check("別人的清單看不到這一筆", not any(
        x.get("image_id") == iid for x in (rr.get("records") or [])))
    # 管理者／工程師（有 audit.read）則看得到——範圍由伺服器端判定，不看客戶端參數。
    HE = bearer("eng01", "engineer")
    r = cli.get("/api/v1/flywheel/record/%s/preview.svg" % iid, headers=HE)
    check("工程師看得到（audit.read）", r.status_code == 200, r.status_code)

    shutil.rmtree(tmp, ignore_errors=True)
    print("\n%d 項檢查，%d 項失敗" % (TOTAL[0], len(FAILED)))
    if FAILED:
        print("失敗：")
        for f in FAILED:
            print("  · " + f)
        return 1
    print("全部通過：複核所需資訊出得來，且預覽不含任何原始影像位元組；範圍與存在性由伺服器端把關。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
