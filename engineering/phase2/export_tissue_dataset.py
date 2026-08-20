# -*- coding: utf-8 -*-
"""把飛輪的組織標註匯出成 `train_tissue_seg.py` 吃得下的資料夾。

    # 從後端匯出（需要工程師／管理者帳號）
    python export_tissue_dataset.py --url https://…run.app --user eng01 --out D:/woundai_ds

    # 從本機 flywheel 目錄匯出（離線／測試）
    python export_tissue_dataset.py --flywheel Backend/Flask/flywheel --out D:/ds

    # 只檢查不寫檔
    python export_tissue_dataset.py --flywheel … --out D:/ds --dry-run

產出：

    <out>/images/<code>__<image_id>.jpg      裁到 ROI 的影像
    <out>/masks/<code>__<image_id>.png       同尺寸的組織碼遮罩（R 通道，0..5）
    <out>/dataset_report.json                每筆的類別像素數、來源、品質指標
    <out>/manifest_snapshot.json             匯出當下的 manifest 原文（可追溯）

## 這支腳本存在的唯一理由：ROI 幾何

**組織遮罩不是整張影像。** 它是修邊畫面的柵格，只覆蓋「傷口外框 ＋ 各 60% 邊距」
那一塊 ROI，而 `tissue_raster` 的 rx0/ry0/mw/mh/m_scale 就是為了把它擺回去。

而 `train_tissue_seg.py` 的 `__getitem__` 是這樣做的：

    img = imread(影像);  img = resize(img, 512)
    m   = imread(遮罩);  m   = resize(m,   512)

它假設兩者**已經在同一個座標空間**。所以如果匯出時照直覺寫
（下載影像、下載遮罩、各存一份），每一對訓練資料的幾何都是錯的——
遮罩會被拉伸到整張照片，而**訓練照樣會跑完、loss 照樣會下降**。

這個錯誤在 2026-08-07 的主控台預覽上真實發生過一次
（`_raster_rect` 的註解記著），當時的症狀是醫師回報「標註超出傷口邊界」。
在訓練資料上發生的話不會有任何人回報——它只會讓模型學到錯的東西。

所以：**裁切在這裡做，而且有契約測試盯著**。

## 不做的事

  · 不做任何「修飾」（平滑、填洞、重新取樣類別）。匯出是搬運，不是加工；
    要改標註請回到修邊畫面改，那裡有醫師在看。
  · 不自己決定訓練/驗證切分。切分要避免同一位標註者跨切分（資料洩漏），
    那是訓練腳本的事，而它需要 `actor`——report 裡有。
"""
import argparse
import base64
import io
import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

CLASSES = {0: "背景/未標註", 1: "肉芽", 2: "腐肉", 3: "壞死", 4: "上皮", 5: "其他"}
MAX_CODE = 5


# ── 幾何：與後端 `_raster_rect` 同一套換算 ─────────────────────────
def raster_rect(tr):
    """組織遮罩在**影像座標**中佔的矩形 (x0, y0, rw, rh)。取不到回 None。

    與 `Backend/Flask/api_flywheel.py::_raster_rect` **必須一致**——
    兩邊各寫一份遲早會分岔，而分岔的症狀是「主控台預覽看起來對、
    訓練資料卻是歪的」，那種不一致沒有人查得出來。
    契約測試 `test_tissue_export.py` 會比對兩邊對同一組輸入的結果。
    """
    if not tr:
        return None
    try:
        mw, mh = int(tr["mw"]), int(tr["mh"])
        # ⚠ 不可以寫 `tr.get("m_scale") or 1.0`。
        # `0 or 1.0` 在 Python 是 1.0——一筆損毀的 `m_scale: 0` 會**靜默變成 1.0**，
        # 於是 ROI 的尺度整個錯掉，而下面的 `ms <= 0` 守衛永遠等不到那個 0。
        # 損毀的資料要被擋下來，不是被猜一個預設值。
        ms_raw = tr.get("m_scale")
        ms = float(ms_raw) if ms_raw is not None else 1.0
        x0 = float(tr.get("rx0") if tr.get("rx0") is not None else 0.0)
        y0 = float(tr.get("ry0") if tr.get("ry0") is not None else 0.0)
        if mw <= 0 or mh <= 0 or ms <= 0:
            return None
        return x0, y0, mw / ms, mh / ms
    except (KeyError, TypeError, ValueError):
        return None


def crop_to_roi(img_bgr, mask_codes, tr):
    """回 (裁好的影像, 對齊的遮罩, 診斷字串)。任何一步不確定就回 (None, None, 原因)。

    影像裁到 ROI、遮罩以**最近鄰**放大到裁切後的尺寸——
    遮罩存的是類別碼，任何插值都會生出不存在的類別（1 和 3 之間插出 2）。
    """
    import cv2
    import numpy as np

    rect = raster_rect(tr)
    if rect is None:
        return None, None, "tissue_raster 缺 mw/mh/m_scale"
    x0, y0, rw, rh = rect
    H, W = img_bgr.shape[:2]

    # ROI 可能因為擴張而超出影像邊界（修邊畫面允許往外畫）。
    # 夾在影像範圍內，並**同步**裁掉遮罩對應的部分——只夾一邊就是位移。
    ix0, iy0 = int(round(x0)), int(round(y0))
    ix1, iy1 = int(round(x0 + rw)), int(round(y0 + rh))
    cx0, cy0 = max(0, ix0), max(0, iy0)
    cx1, cy1 = min(W, ix1), min(H, iy1)
    if cx1 - cx0 < 8 or cy1 - cy0 < 8:
        return None, None, "ROI 與影像的交集過小（%dx%d）" % (cx1 - cx0, cy1 - cy0)

    img = img_bgr[cy0:cy1, cx0:cx1]

    mh, mw = mask_codes.shape[:2]
    # 遮罩先放大到**完整 ROI**的尺寸，再依同樣的夾取切掉溢出部分。
    # 先切後放大會讓比例改變——那是最容易寫錯的一步。
    full_w, full_h = max(1, ix1 - ix0), max(1, iy1 - iy0)
    m_full = cv2.resize(mask_codes, (full_w, full_h), interpolation=cv2.INTER_NEAREST)
    m = m_full[cy0 - iy0: cy0 - iy0 + (cy1 - cy0),
               cx0 - ix0: cx0 - ix0 + (cx1 - cx0)]
    if m.shape[:2] != img.shape[:2]:
        return None, None, "裁切後尺寸不符：影像 %s vs 遮罩 %s" % (img.shape[:2], m.shape[:2])
    return img, m, ""


# ── 取得資料：後端 或 本機 flywheel 目錄 ───────────────────────────
def fetch_backend(url, user, pw, kind, source, allow_unedited):
    def post(path, body):
        req = urllib.request.Request(
            url.rstrip("/") + path, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())

    tok = post("/api/auth/login", {"username": user, "password": pw}).get("access_token")
    if not tok:
        raise SystemExit("登入失敗")

    def get(path):
        req = urllib.request.Request(url.rstrip("/") + path,
                                     headers={"Authorization": "Bearer " + tok})
        with urllib.request.urlopen(req, timeout=180) as r:
            return r.read()

    q = "/api/v1/dataset/manifest?kind=%s" % kind
    if source:
        q += "&source=" + source
    if allow_unedited:
        q += "&require_edited=0"
    man = json.loads(get(q).decode())

    # ⚠ 走**具名端點**，不是通用的 `?key=` blob。
    # 通用端點會開出「任意鍵讀取」的攻擊面——一個參數之差就能讀到 audit.jsonl。
    # 這裡只認 image_id，沒有路徑可以被操縱。
    def blob(kind, image_id):
        path = "/api/v1/flywheel/record/%s/%s" % (
            image_id, "image.jpg" if kind == "image" else "tissue_mask.png")
        try:
            return get(path)
        except urllib.error.HTTPError as e:
            # 410＝已依撤回同意隔離。那不是錯誤，是**正確地拒絕**——
            # 要與「取不到」分開講，否則報告上看不出資料為什麼變少。
            raise RuntimeError("HTTP %d%s" % (e.code, "（已撤回同意）" if e.code == 410 else ""))

    return man, blob


def fetch_local(fw_dir, kind, source, allow_unedited):
    """離線路徑：直接讀 flywheel 目錄。測試與斷網時用。"""
    sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "Backend", "Flask")))
    os.environ.setdefault("WOUNDAI_FLYWHEEL_DIR", os.path.abspath(fw_dir))
    import api_flywheel as fw

    recs = [r for r, st in fw.classify_queue(fw.read_jsonl(fw.QUEUE)) if st == "trainable"]
    items = []
    for r in recs:
        if kind == "tissue":
            if not r.get("tissue_mask_key"):
                continue
            if not allow_unedited and not r.get("tissue_edited"):
                continue
        if source and fw.rec_source(r) != source:
            continue
        items.append({
            "image_id": r.get("image_id"), "code": r.get("code"),
            "image_key": "images/%s.jpg" % r.get("image_id"),
            "tissue_mask_key": r.get("tissue_mask_key"),
            "tissue_raster": r.get("tissue_raster"),
            "tissue_edited": bool(r.get("tissue_edited")),
            "image_w": r.get("image_w"), "image_h": r.get("image_h"),
            "quality": r.get("quality"), "actor": r.get("actor"),
            "source": fw.rec_source(r), "received_at": r.get("received_at"),
        })

    def blob(kind_, image_id):
        rec = next((r for r in recs if r.get("image_id") == image_id), None)
        if rec is None:
            return None
        key = ("images/%s.jpg" % image_id) if kind_ == "image" else rec.get("tissue_mask_key")
        return fw._store().get_blob(key) if key else None

    return {"kind": kind, "count": len(items), "items": items}, blob


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", help="後端網址（與 --flywheel 二擇一）")
    ap.add_argument("--user")
    ap.add_argument("--password", help="不給則從 WOUNDAI_PW 讀")
    ap.add_argument("--flywheel", help="本機 flywheel 目錄（離線）")
    ap.add_argument("--out", required=True)
    ap.add_argument("--kind", default="tissue")
    ap.add_argument("--source", default=None, help="clinical / sample / phantom")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--allow-unedited", action="store_true",
                    help="⚠ 收未經醫師修正的遮罩。那些是啟發式的原樣輸出——"
                         "拿它訓練＝用模型自己的輸出訓練自己")
    a = ap.parse_args()

    try:
        import cv2
        import numpy as np
    except ImportError:
        print("需要 opencv-python 與 numpy")
        return 1

    if a.allow_unedited:
        print("⚠⚠ --allow-unedited：收下的遮罩包含**未經醫師修正**的啟發式輸出。")
        print("   用模型自己的輸出訓練自己，指標會漂亮而臨床表現不會動。")
        print("   只有在明確知道自己要做什麼（例如純粹的資料流測試）時才用。\n")

    if a.flywheel:
        man, blob = fetch_local(a.flywheel, a.kind, a.source, a.allow_unedited)
    elif a.url:
        pw = a.password or os.environ.get("WOUNDAI_PW")
        if not pw:
            print("缺密碼：--password 或環境變數 WOUNDAI_PW")
            return 1
        man, blob = fetch_backend(a.url, a.user, pw, a.kind, a.source, a.allow_unedited)
    else:
        print("要嘛 --url 要嘛 --flywheel")
        return 1

    items = man.get("items") or []
    print("manifest：%d 筆（kind=%s%s）" % (len(items), a.kind,
                                          "，source=" + a.source if a.source else ""))
    if not items:
        print("沒有合格樣本。若預期應該有，先用主控台的送件審閱確認狀態。")
        return 1

    img_dir = os.path.join(a.out, "images")
    msk_dir = os.path.join(a.out, "masks")
    if not a.dry_run:
        os.makedirs(img_dir, exist_ok=True)
        os.makedirs(msk_dir, exist_ok=True)

    total = np.zeros(MAX_CODE + 1, dtype=np.int64)
    report, skipped = [], []
    for it in items:
        iid, code = it.get("image_id"), it.get("code") or "NA"
        why = ""
        try:
            raw_img = blob("image", iid)
            raw_msk = blob("mask", iid) if it.get("tissue_mask_key") else None
            if raw_img is None or raw_msk is None:
                why = "影像或遮罩取不到"
            else:
                img = cv2.imdecode(np.frombuffer(raw_img, np.uint8), cv2.IMREAD_COLOR)
                mk = cv2.imdecode(np.frombuffer(raw_msk, np.uint8), cv2.IMREAD_COLOR)
                if img is None or mk is None:
                    why = "影像或遮罩解碼失敗"
                else:
                    # 類別碼在 R 通道（與 App 的 TissueMaskCodec 一致）。
                    # cv2 是 BGR，所以取 index 2。
                    codes = mk[:, :, 2]
                    bad = int((codes > MAX_CODE).sum())
                    if bad:
                        why = "遮罩含 %d 個超出值域(0..%d)的碼——資料損毀" % (bad, MAX_CODE)
                    else:
                        cimg, cmask, why = crop_to_roi(img, codes, it.get("tissue_raster"))
        except Exception as e:                       # noqa: BLE001
            why = "%s: %s" % (type(e).__name__, e)

        if why:
            skipped.append({"image_id": iid, "code": code, "reason": why})
            print("  跳過 %s（%s）：%s" % (code, (iid or "")[:8], why))
            continue

        cnt = np.bincount(cmask.ravel(), minlength=MAX_CODE + 1)[:MAX_CODE + 1]
        total += cnt
        name = "%s__%s" % (code, iid)
        report.append({
            "name": name, "image_id": iid, "code": code,
            "roi_px": [int(cmask.shape[1]), int(cmask.shape[0])],
            "mask_native_px": [int(mk.shape[1]), int(mk.shape[0])],
            "class_px": {CLASSES[i]: int(cnt[i]) for i in range(MAX_CODE + 1)},
            "labelled_px": int(cnt[1:].sum()),
            "tissue_edited": it.get("tissue_edited"),
            "actor": it.get("actor"), "source": it.get("source"),
            "quality": it.get("quality"), "received_at": it.get("received_at"),
        })
        if not a.dry_run:
            cv2.imwrite(os.path.join(img_dir, name + ".jpg"), cimg,
                        [int(cv2.IMWRITE_JPEG_QUALITY), 95])
            # 遮罩寫成三通道 PNG，值放 R——與訓練腳本的讀法一致
            #（它取 `imread(...)[:, :, 2]`）。寫成灰階的話讀出來會是 0。
            out3 = np.zeros((cmask.shape[0], cmask.shape[1], 3), np.uint8)
            out3[:, :, 2] = cmask
            cv2.imwrite(os.path.join(msk_dir, name + ".png"), out3)

    print("\n匯出 %d 筆，跳過 %d 筆" % (len(report), len(skipped)))
    print("類別像素分布：")
    lab = max(1, int(total[1:].sum()))
    for i in range(MAX_CODE + 1):
        bar = "█" * int(40.0 * total[i] / max(1, total.sum()))
        pct = (100.0 * total[i] / lab) if i else 0.0
        print("  %-10s %12d %s%s" % (CLASSES[i], total[i],
                                     ("%5.1f%% " % pct) if i else "      ", bar))
    absent = [CLASSES[i] for i in range(1, MAX_CODE + 1) if total[i] == 0]
    if absent:
        print("\n⚠ 完全沒有這些類別：%s" % "、".join(absent))
        print("  模型不可能學會它們，而驗證集若也沒有，指標**看不出這個缺口**。")
    tiny = [CLASSES[i] for i in range(1, MAX_CODE + 1)
            if 0 < total[i] < lab * 0.01]
    if tiny:
        print("\n⚠ 這些類別不到已標註像素的 1%%：%s" % "、".join(tiny))
        print("  加權損失壓得住比例，壓不住**資訊量**——合成資料實測：")
        print("  加權 CE ＋ Dice 之下，壞死仍只有 0.074。這要靠針對性收案，不是調參。")

    if a.dry_run:
        print("\n--dry-run：沒有寫出任何檔案。")
        return 0

    with open(os.path.join(a.out, "dataset_report.json"), "w", encoding="utf-8") as f:
        json.dump({"exported": len(report), "skipped": skipped,
                   "class_px": {CLASSES[i]: int(total[i]) for i in range(MAX_CODE + 1)},
                   "items": report}, f, ensure_ascii=False, indent=2)
    with open(os.path.join(a.out, "manifest_snapshot.json"), "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=2)
    print("\n報告：%s" % os.path.join(a.out, "dataset_report.json"))
    print("接著：python train_tissue_seg.py --data %s --dry-run" % a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
