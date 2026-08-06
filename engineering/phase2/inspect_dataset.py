# -*- coding: utf-8 -*-
"""在匯入之前先看清楚一份公開資料集到底是什麼。

    python inspect_dataset.py --src "D:/DFUTissue/Labeled/Original" \
        --images-dir Images --masks-dir Annotations

## 為什麼要有這一步

匯入腳本會要求 `classes.json`（值→類別名），而那個對照**不能用猜的**：
猜錯的話訓練照樣跑得完、Dice 照樣有數字，但模型學到的是別的東西。
這支腳本把「遮罩裡實際出現哪些值、各佔多少」列出來，
讓你拿它去跟資料集的說明文件對照，而不是憑索引順序推測。

## 順便回答兩個會改變訓練設定的問題

1. **影像是整張照片還是已裁好的 ROI？**
   看非背景像素佔比。若中位數 > 60%，多半已經裁到傷口——
   那麼「短邊 134 px」全是有效像素，跟整張照片短邊 134 是完全不同的事。

2. **解析度夠不夠？**
   組織分類靠紋理，而紋理在降採樣時最先消失。
   短邊遠低於 `--size` 就是在餵內插出來的像素給模型。
"""
import argparse
import os
import sys
from collections import Counter

from imio import find_pairs, imread_any


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--images-dir", default="images")
    ap.add_argument("--masks-dir", default="masks")
    ap.add_argument("--limit", type=int, default=0, help="只看前 N 組（0＝全部）")
    a = ap.parse_args()

    try:
        import numpy as np
        import cv2
    except ImportError as e:
        print("缺少相依：%s\n  pip install numpy opencv-python" % e)
        return 1

    im_dir = os.path.join(a.src, a.images_dir)
    mk_dir = os.path.join(a.src, a.masks_dir)
    if not os.path.isdir(im_dir) or not os.path.isdir(mk_dir):
        print("找不到目錄：\n  %s\n  %s" % (im_dir, mk_dir))
        return 1

    pairs, n_im, n_mk = find_pairs(im_dir, mk_dir)
    print("影像 %d、遮罩 %d、可配對 %d\n" % (n_im, n_mk, len(pairs)))
    if not pairs:
        print("❌ 一張都配不起來——檢查 --images-dir / --masks-dir 與檔名主檔名是否一致。")
        return 1
    if a.limit:
        pairs = pairs[:a.limit]

    sides, fg_frac, vals = [], [], Counter()
    shapes_mismatch = 0
    channels = Counter()
    unreadable = 0

    for stem, ip, mp in pairs:
        img = imread_any(ip)
        m = imread_any(mp, cv2.IMREAD_UNCHANGED)
        if img is None or m is None:
            unreadable += 1
            continue
        sides.append(min(img.shape[:2]))
        if m.ndim == 3:
            channels["%dch" % m.shape[2]] += 1
            # 三通道且三通道相同 → 其實是灰階存成彩色；取一個通道即可
            m2 = m[:, :, 0] if (m.shape[2] >= 3 and np.array_equal(m[:, :, 0], m[:, :, 2])) else m[:, :, 2]
        else:
            channels["1ch"] += 1
            m2 = m
        if m2.shape[:2] != img.shape[:2]:
            shapes_mismatch += 1
        u, c = np.unique(m2, return_counts=True)
        for uu, cc in zip(u.tolist(), c.tolist()):
            vals[int(uu)] += int(cc)
        fg_frac.append(float((m2 > 0).mean()))

    if unreadable:
        print("⚠ 讀不到 %d 組（若路徑含中文而這裡是 0，代表 imio 生效了）\n" % unreadable)
    if not sides:
        print("❌ 全部讀不到。")
        return 1

    med = int(np.median(sides))
    print("── 解析度 ──")
    print("  短邊：最小 %d / 中位 %d / 最大 %d" % (min(sides), med, max(sides)))
    if med < 256:
        print("  ⚠ 中位 %d 低於本專案品質門檻 min_roi_px=256。" % med)
        print("    訓練時 --size 不要超過 %d 太多，否則是在學內插出來的細節。"
              % (max(128, med // 32 * 32)))

    mf = float(np.median(fg_frac))
    print("\n── 影像是整張照片還是已裁好的 ROI ──")
    print("  非背景像素佔比中位數：%.1f%%" % (mf * 100))
    if mf > 0.6:
        print("  → 多半**已裁到傷口**。那麼短邊 %d px 全是有效像素，" % med)
        print("    與『整張照片短邊 %d』是完全不同的事——可用性比表面數字高。" % med)
    elif mf < 0.25:
        print("  → 多半是**整張照片**，傷口只佔一小塊。")
        print("    實際的傷口 ROI 短邊會遠小於 %d px，可用性比表面數字更差。" % med)
    else:
        print("  → 介於兩者之間，建議肉眼看幾張再決定。")

    print("\n── 遮罩通道 ──")
    for k, v in channels.most_common():
        print("  %s：%d 張" % (k, v))
    if shapes_mismatch:
        print("  ⚠ %d 組的遮罩尺寸與影像不同——匯入時會出問題。" % shapes_mismatch)

    total = sum(vals.values())
    print("\n── 遮罩裡實際出現的值（拿去對照資料集說明，建 classes.json）──")
    print("  %5s  %10s  %7s" % ("值", "像素數", "佔比"))
    for v in sorted(vals):
        print("  %5d  %10d  %6.2f%%" % (v, vals[v], 100.0 * vals[v] / total))
    print("\n  共 %d 個相異值。" % len(vals))
    print("  ⚠ 值的順序**不保證**與類別名的順序一致——")
    print("    請拿資料集自己的說明文件（README／論文）對照，不要用佔比推測。")
    print("    佔比只能當佐證：背景通常最大，罕見類別通常最小。")

    print("\n  建好之後存成 %s：" % os.path.join(a.src, "classes.json"))
    print("    {" + ", ".join('"%d": "?"' % v for v in sorted(vals)[:6])
          + (", ..." if len(vals) > 6 else "") + "}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
