# -*- coding: utf-8 -*-
"""產生合成的組織分割資料集，**用來驗證工具鏈，不是用來訓練模型**。

    python make_synthetic_tissue.py --out D:/synth_ds --n 40

## 為什麼先跑這個再去下載真資料

下載公開資料集要填表、等授權、解壓、對照類別編碼——那是一個下午。
而 torch 裝不起來、CUDA 版本不對、訓練迴圈有 bug 這些事，**十分鐘就能發現**。

先用合成資料把整條鏈跑通一次：匯入 → dry-run → 訓練 → 存權重。
確定管線是好的之後再去弄真資料，就不會在「到底是資料有問題還是程式有問題」之間耗掉時間。

## ⚠ 這些影像不是傷口

它們是有已知類別區域的色塊，只有一個用途：**證明程式跑得動、指標算得出來**。
在合成資料上得到的 Dice **完全沒有臨床意義**——那個任務簡單到連隨機初始化的模型
都能學會。看到 0.9 不要高興，看到 0.5 要擔心（代表管線有問題）。

輸出格式與 `pull_dataset.ps1` 相同，所以 `train_tissue_seg.py` 可以直接吃。
"""
import argparse
import json
import os
import sys

# ⚠ 路徑含中文時 cv2.imread/imwrite 會靜默回 None，見 imio.py
from imio import imread_any, imwrite_any


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--n", type=int, default=40, help="影像張數")
    ap.add_argument("--per-wound", type=int, default=2,
                    help="每個「傷口」幾張（驗證切分依傷口分組時用得到）")
    ap.add_argument("--size", type=int, default=256)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()

    try:
        import numpy as np
        import cv2
    except ImportError as e:
        print("缺少相依：%s\n  pip install numpy opencv-python" % e)
        return 1

    rng = np.random.default_rng(a.seed)
    os.makedirs(os.path.join(a.out, "images"), exist_ok=True)
    os.makedirs(os.path.join(a.out, "masks"), exist_ok=True)

    # 每一類給一個代表色，讓「顏色 → 類別」有跡可循（模型才學得到東西）。
    # 這些色值刻意接近真實組織的色相，但**不是**真實組織——見檔頭說明。
    COLOR = {1: (60, 140, 90), 2: (60, 170, 230), 3: (70, 55, 60),
             4: (190, 190, 235), 5: (170, 170, 165)}   # BGR
    items = []
    S = a.size
    n_wounds = max(1, a.n // max(1, a.per_wound))

    for w in range(n_wounds):
        code = "SYN-%03d" % w
        # 同一個「傷口」的多張影像刻意高度相似——這正是切分依傷口分組要防的情況：
        # 若它們橫跨訓練與驗證，模型只要記住這個形狀就能拿高分，而那個 Dice 是假的。
        cx, cy = rng.integers(S // 3, S * 2 // 3, 2)
        rad = int(rng.integers(S // 6, S // 3))
        # 每張圖出現哪幾類：刻意讓「壞死」與「其他」較罕見，
        # 模擬真實資料的類別不平衡（文獻中壞死只出現在 56/147 張）。
        for k in range(a.per_wound):
            img = np.full((S, S, 3), (150, 170, 205), np.uint8)   # 皮膚底色
            img += rng.integers(-12, 12, img.shape).astype(np.int16).clip(-255, 255).astype(np.uint8)
            lab = np.zeros((S, S), np.uint8)
            yy, xx = np.mgrid[0:S, 0:S]
            jx, jy = int(rng.integers(-6, 6)), int(rng.integers(-6, 6))
            wound = ((xx - cx - jx) ** 2 + (yy - cy - jy) ** 2) < rad ** 2
            lab[wound] = 1                                        # 底層先全是肉芽
            # 疊上其他組織的區塊
            present = [2] + ([3] if rng.random() < 0.4 else []) + \
                      ([4] if rng.random() < 0.5 else []) + ([5] if rng.random() < 0.25 else [])
            for c in present:
                ox = int(rng.integers(-rad // 2, rad // 2)); oy = int(rng.integers(-rad // 2, rad // 2))
                rr = int(rad * rng.uniform(0.25, 0.55))
                blob = ((xx - cx - jx - ox) ** 2 + (yy - cy - jy - oy) ** 2) < rr ** 2
                lab[wound & blob] = c
            for c, col in COLOR.items():
                img[lab == c] = col
            img = cv2.GaussianBlur(img, (5, 5), 0)
            stem = "%s__%02d" % (code, k)
            imwrite_any(os.path.join(a.out, "images", stem + ".jpg"), img)
            # 值放 R 通道，與 TissueMaskCodec 一致
            z = np.zeros_like(lab)
            imwrite_any(os.path.join(a.out, "masks", stem + ".png"), np.dstack([z, z, lab]))
            items.append({"code": code, "image_id": "%02d" % k, "source": "synthetic",
                          # 合成資料沒有醫師，但要讓 train_tissue_seg 收得進來。
                          # 標 synthetic 讓它在 manifest 裡一眼可辨，不會被誤當成臨床樣本。
                          "tissue_edited": True, "synthetic": True,
                          "actor": "synthetic:generator"})

    json.dump({"items": items, "note": "合成資料，僅供驗證工具鏈，無臨床意義"},
              open(os.path.join(a.out, "manifest.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("產生 %d 張（%d 個合成傷口 × %d）→ %s" % (len(items), n_wounds, a.per_wound, a.out))
    print("\n⚠ 這些不是傷口影像。它們只證明程式跑得動——")
    print("  合成資料上的 Dice 沒有臨床意義。看到 0.9 不要高興，看到 0.5 要擔心。")
    print("\n下一步：")
    print("  python train_tissue_seg.py --data %s --dry-run" % a.out)
    print("  python train_tissue_seg.py --data %s --epochs 8 --size 256" % a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
