# -*- coding: utf-8 -*-
"""飛輪佇列 → 可訓練資料集(image/mask 對)。

飛輪的消費端:把 `Backend/Flask/flywheel/retrain_queue.jsonl` 的醫師驗證 GT,
配上 classify 當時存下的原始影像(`flywheel/images/<image_id>.jpg`),
柵格化成二值遮罩 PNG,輸出成訓練腳本可直接吃的目錄結構。

排除規則與 `/api/v1/flywheel/stats` 共用 `api_flywheel.effective_queue`(單一真相):
  孤兒(無 image_id) / 欄位格式錯 / 影像檔遺失 / 已撤回同意(code 或 image_id)
  / 三同意不全 / 同影像被更新版取代 → 一律不匯出。

用法:
    python export_flywheel_dataset.py                      # 預設輸出到 flywheel/dataset_<日期>
    python export_flywheel_dataset.py --out D:/ds --min-samples 20
    python export_flywheel_dataset.py --dry-run            # 只看統計不寫檔

輸出:
    <out>/images/<code>__<image_id>.jpg   原始影像(EXIF 方向已正規化)
    <out>/masks/<code>__<image_id>.png    二值遮罩(0/255,尺寸=影像)
    <out>/manifest.json                   每筆的溯源(image_id/面積/mm_per_px/route/修正幅度/滲液)
    <out>/DATASET_CARD.md                 資料卡(來源、同意狀態、限制、排除統計)

離開碼:0=有產出;1=無可訓練樣本/低於 --min-samples/全部略過(CI 可直接判斷)。

【檔名】用 `<code>__<image_id>`:code 對得回同意書、image_id 保證唯一。
App 的 code 是毫秒尾 8 碼(約 27.8 小時循環)會碰撞,單用 code 會靜默覆寫樣本。
"""
import argparse, io, json, os, shutil, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, "..", "..", "Backend", "Flask"))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

import numpy as np  # noqa: E402
try:
    import cv2  # noqa: E402
except ImportError:
    cv2 = None

import api_flywheel as fw  # noqa: E402


def write_bytes(path, data):
    with open(path, "wb") as f:
        f.write(data)


def imwrite_unicode(path, arr):
    """cv2.imwrite 在 Windows 非 ASCII 路徑會回 False 且不拋例外(本專案有前例)→ 一律走 imencode。"""
    if cv2 is not None:
        ok, buf = cv2.imencode(os.path.splitext(path)[1], arr)
        if not ok: raise IOError(f"編碼失敗: {path}")
        write_bytes(path, buf.tobytes())
    else:
        from PIL import Image
        Image.fromarray(arr).save(path)
    if not os.path.exists(path):
        raise IOError(f"寫檔失敗: {path}")


def normalize_image(raw):
    """回 (bgr_or_rgb_array, out_bytes, w, h)。

    EXIF 方向陷阱:cv2.imdecode 會套用 EXIF 旋轉,PIL.open().size 不會 →
    兩種環境算出的尺寸不同,而遮罩是依 cv2 座標畫的 → image/mask 錯位。
    對策:有 EXIF 方向就解碼後重新編碼(剝掉 EXIF,像素即最終方向);
    沒有(Android Bitmap.compress 的常態)就原封複製,不做無謂的重壓縮。"""
    # 偵測 EXIF 需要 PIL;opencv-python 並不相依 Pillow,所以「有 cv2 無 PIL」是真實環境。
    # 那種環境下無法判斷方向 → 一律重新編碼(像素即最終方向),寧可多壓一次也不要 image/mask 錯位。
    oriented = True
    try:
        from PIL import Image
        ex = Image.open(io.BytesIO(raw)).getexif()
        oriented = int(ex.get(274, 1) or 1) != 1 if ex else False
    except ImportError:
        oriented = True
    except Exception:
        oriented = True

    if cv2 is not None:
        arr = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        if arr is None: raise IOError("影像解碼失敗")
        h, w = arr.shape[:2]
    else:
        from PIL import Image, ImageOps
        im = ImageOps.exif_transpose(Image.open(io.BytesIO(raw)).convert("RGB"))
        arr = np.array(im); h, w = arr.shape[:2]

    if oriented:
        if cv2 is not None:
            ok, buf = cv2.imencode(".jpg", arr, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
            out = buf.tobytes() if ok else raw
        else:
            from PIL import Image
            b = io.BytesIO(); Image.fromarray(arr).save(b, "JPEG", quality=95); out = b.getvalue()
    else:
        out = raw
    return arr, out, w, h


def rasterize(poly, w, h):
    """polygon → 二值遮罩(uint8 0/255)。cv2 優先,無 cv2 退回 PIL。"""
    pts = np.array([[int(round(float(p[0]))), int(round(float(p[1])))] for p in poly], dtype=np.int32)
    if cv2 is not None:
        m = np.zeros((h, w), np.uint8)
        cv2.fillPoly(m, [pts], 255)
        return m
    from PIL import Image, ImageDraw
    im = Image.new("L", (w, h), 0)
    ImageDraw.Draw(im).polygon([tuple(p) for p in pts.tolist()], fill=255)
    return np.array(im)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--queue", default=fw.QUEUE)
    ap.add_argument("--images", default=fw.IMAGES_DIR)
    ap.add_argument("--withdrawn", default=fw.WITHDRAWN)
    ap.add_argument("--out", default=os.path.join(fw.FLYWHEEL_DIR, "dataset_" + time.strftime("%Y%m%d")))
    ap.add_argument("--min-samples", type=int, default=0,
                    help="低於此數不產出(避免拿極小樣本訓練並誤以為有效);0=不設限")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="輸出目錄已有內容時清空重寫。預設拒絕,避免舊遮罩與新 manifest 混在一起")
    a = ap.parse_args()

    recs, stats = fw.effective_queue(a.queue, a.images, a.withdrawn)
    print("=== 佇列健康度 ===")
    for k, v in stats.items():
        print(f"  {k:20s}: {v}")

    if stats["orphan_no_image"]:
        print(f"\n⚠ {stats['orphan_no_image']} 筆孤兒 GT(無 image_id)已排除——"
              f"這是 2026-07-28 前舊版標註端點的產物,無影像不可訓練。")
    if not recs:
        print("\n✗ 無可訓練樣本,未產出。")
        return 1
    if a.min_samples and len(recs) < a.min_samples:
        print(f"\n✗ 可訓練樣本 {len(recs)} < --min-samples {a.min_samples},誠實中止(不硬訓)。")
        return 1
    if a.dry_run:
        print(f"\n[dry-run] 會匯出 {len(recs)} 筆。")
        return 0

    # 預設 --out 是 dataset_<日期>,同一天重跑必撞。若不擋,會出現「檔名碰撞全數略過 →
    # 磁碟留著舊遮罩,manifest 還是上一輪的」這種看起來正常、內容過期的資料集。
    if os.path.isdir(a.out) and any(os.scandir(a.out)):
        if not a.force:
            print(f"\n✗ 輸出目錄非空:{a.out}\n  → 加 --force 清空重寫,或改用不同 --out(避免新舊 GT 混在一起)")
            return 1
        for sub in ("images", "masks"):
            p = os.path.join(a.out, sub)
            if os.path.isdir(p): shutil.rmtree(p)
        for f_ in ("manifest.json", "DATASET_CARD.md"):
            p = os.path.join(a.out, f_)
            if os.path.exists(p): os.remove(p)

    img_out = os.path.join(a.out, "images"); msk_out = os.path.join(a.out, "masks")
    os.makedirs(img_out, exist_ok=True); os.makedirs(msk_out, exist_ok=True)

    manifest, skipped = [], []
    for r in recs:
        code, iid = str(r["code"]), str(r["image_id"])
        stem = f"{code}__{iid}"          # code 對得回同意書、image_id 保證唯一(見檔頭說明)
        try:
            with open(os.path.join(a.images, f"{iid}.jpg"), "rb") as f:
                raw = f.read()
            _arr, out_bytes, w_act, h_act = normalize_image(raw)
        except Exception as e:
            skipped.append({"code": code, "reason": f"影像讀取/正規化失敗: {e}"}); continue

        # 影像真實尺寸必須等於標註時記錄的座標空間,否則遮罩會錯位
        w_rec, h_rec = int(r["image_w"]), int(r["image_h"])
        if (w_act, h_act) != (w_rec, h_rec):
            skipped.append({"code": code, "reason": f"尺寸不符 記錄{w_rec}x{h_rec} vs 實際{w_act}x{h_act}"})
            continue

        mask = rasterize(r["gt_polygon"], w_rec, h_rec)
        px = int((mask > 0).sum())
        if px == 0:
            skipped.append({"code": code, "reason": "柵格化後遮罩為空"}); continue

        ip, mp = os.path.join(img_out, stem + ".jpg"), os.path.join(msk_out, stem + ".png")
        if os.path.exists(ip):
            skipped.append({"code": code, "reason": f"檔名碰撞 {stem}(已存在,不覆寫)"}); continue
        write_bytes(ip, out_bytes)
        imwrite_unicode(mp, mask)

        mm = r.get("mm_per_px")
        manifest.append({
            "code": code, "image_id": iid, "image": f"images/{stem}.jpg", "mask": f"masks/{stem}.png",
            "width": w_rec, "height": h_rec, "mask_px": px,
            "area_cm2": (round(px * (float(mm) / 10.0) ** 2, 3) if mm else None),
            "mm_per_px": mm, "route": r.get("route"), "seg_model": r.get("seg_model"),
            "correction_iou": r.get("correction_iou"), "exudate": r.get("exudate"),
            "doctor_verified": r.get("doctor_verified"), "consent_train": r.get("consent_train"),
            "actor": r.get("actor"), "received_at": r.get("received_at"),
        })

    if not manifest:
        print(f"\n✗ 全部 {len(skipped)} 筆在匯出階段被略過,未產出資料集:")
        for s in skipped: print("   -", s["code"], s["reason"])
        return 1

    with open(os.path.join(a.out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"generated_at": fw.utc_now(), "queue_stats": stats,
                   "exported": len(manifest), "skipped": skipped, "samples": manifest},
                  f, ensure_ascii=False, indent=2)

    n_scaled = sum(1 for m in manifest if m["mm_per_px"])
    n_edited = sum(1 for m in manifest if m.get("correction_iou") is not None and m["correction_iou"] < 0.99)
    card = f"""# 飛輪訓練集資料卡（自動產生）

- 產生時間：{time.strftime('%Y-%m-%d %H:%M:%S')}
- 樣本數：**{len(manifest)}**（來源佇列 {stats['total']} 筆）
- 來源：App 醫師確認・送出訓練標註 → `POST /api/v1/annotation`
- GT 定義：醫師於 App 修邊工具產生的傷口輪廓（筆刷塗抹 → 最大外輪廓 → RDP 簡化 → 本腳本柵格化）

## 品質與同意
- 全部樣本 `doctor_verified` / `deidentified` / `consent_train` 皆為 true（端點守門 + 匯出時重驗）
- 有 ArUco 尺度（mm/px）：{n_scaled} / {len(manifest)}
- 醫師有實際修邊（correction_iou < 0.99）：{n_edited} / {len(manifest)}
- 代碼為去識別 `WD-*`；不含姓名／病歷號／院所等 PII
- 已撤回同意者（含同影像的其他標註）一律不在此資料集，影像已移入 `quarantine/`

## 排除統計（未進入本資料集）
| 原因 | 筆數 |
|---|---|
| 孤兒 GT（無 image_id，2026-07-28 前舊版） | {stats['orphan_no_image']} |
| 欄位／格式不合 | {stats['malformed']} |
| 影像檔遺失 | {stats['image_file_missing']} |
| 已撤回同意 | {stats['withdrawn']} |
| 三同意不全 | {stats['consent_invalid']} |
| 同影像被更新版取代 | {stats['superseded']} |
| 匯出時尺寸不符／空遮罩／碰撞 | {len(skipped)} |

## 已知限制
- 樣本量小，**不足以單獨訓練**；用途為微調／偽標籤驗證／回歸集，須混入既有資料防遺忘。
- GT 為單一醫師描邊，未做跨評分者一致性（ICC / 多讀者）；同影像只保留最新一筆，
  目前架構**無法**同時保存多位讀者的標註（要做 ICC 需改以 (image_id, actor) 為鍵）。
- `manifest.area_cm2` 由多邊形重新柵格化算得，與 App 顯示的塗抹像素面積會有輪廓往返
  （最大外輪廓＋RDP 簡化，孔洞與次要連通區會丟失）造成的小差異。
- App 全鏈含人工描邊誤差約 ±6%（見 EVIDENCE_LEDGER 2026-07-28）。
- 尚未切分 train/val/test；請以 image_id 分組切分，避免同一傷口跨集洩漏。
"""
    with open(os.path.join(a.out, "DATASET_CARD.md"), "w", encoding="utf-8") as f:
        f.write(card)

    print(f"\n✓ 匯出 {len(manifest)} 筆 → {a.out}")
    if skipped:
        print(f"⚠ 略過 {len(skipped)} 筆:")
        for s in skipped: print("   -", s["code"], s["reason"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
