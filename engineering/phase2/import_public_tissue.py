# -*- coding: utf-8 -*-
"""把公開的組織分割資料集轉成本專案的格式與類別。

    python import_public_tissue.py --src D:/WoundTissueSeg --preset woundtissueseg --out D:/public_ds
    python import_public_tissue.py --src D:/DFUTissue     --preset dfutissue      --out D:/public_ds --append
    python import_public_tissue.py --preset woundtissueseg --print-mapping   # 只印映射表

## ⚠ 資料集要自己下載

這支腳本**不會**幫你下載。WoundTissueSeg / DFUTissue 都需要到原始出處取得
（多半要填表或接受授權條款），而把「取得授權」自動化既做不到也不應該。
取得後的目錄結構請用 `--images-dir` / `--masks-dir` 指定。

出處見 docs/tissue_segmentation_plan.md 的參考章節。

## ⚠ 類別映射必須是明確的表，不能默默 argmax

公開資料集的類別與我們的五類**不是一對一**：

- WoundTissueSeg 有 **骨（bone）與肌腱（tendon）** 兩類，我們沒有
- 它有 **浸潤（maceration）**，那在我們的定義裡是傷口周圍皮膚，不是傷口內組織
- DFUTissue 的類別較少

把它們默默塞進最接近的一類，訓練資料就含有**我們自己都說不清楚的標籤**，
而模型學到的東西無法對應回臨床定義。所以每一條映射都要寫出來、寫上理由，
而且**不確定的一律歸「其他」**——「其他」的語意本來就是「不屬於前四類」，
把骨與肌腱放進去是誠實的；把它們算成壞死則是編造。

`--print-mapping` 可以把表印出來附在資料集說明裡。
"""
import argparse
import json
import os
import sys
from collections import Counter

# ⚠ 不可直接用 cv2.imread/imwrite：路徑含中文時它會靜默回 None
#（Windows 上走 ANSI 字碼頁開檔）。見 imio.py 的說明。
from imio import find_pairs, imread_any, imwrite_any

# 本專案的組織碼（＝修邊畫面碼，見 TissueSeg 的說明）
OURS = {0: "背景", 1: "肉芽", 2: "腐肉", 3: "壞死", 4: "上皮", 5: "其他"}

# ── 映射表 ──────────────────────────────────────────────────────────
#
# 格式：來源類別名 → (我們的碼, 理由)
# 理由欄不是註解，它會被寫進輸出的 mapping.json，日後有人問「骨為什麼算其他」
# 才有答案。沒有理由的映射等於沒有映射。
PRESETS = {
    "woundtissueseg": {
        "note": "WoundTissueSeg（147 張，6 類）。arXiv 2502.10652 / Sci Rep 2025。",
        "map": {
            "granulation": (1, "直接對應"),
            "slough":      (2, "直接對應"),
            "necrosis":    (3, "直接對應"),
            "eschar":      (3, "焦痂是壞死組織乾燥後的形態，臨床處置相同，併入壞死"),
            "epithelial":  (4, "直接對應"),
            "epithelium":  (4, "同上（不同資料集的拼法）"),
            # ⚠ 以下三類是這張表最需要被檢視的部分
            "bone":        (5, "我們沒有骨這一類。歸『其他』是誠實的（＝不屬於前四類）；"
                               "算成壞死則是編造——骨外露與壞死組織的臨床意義完全不同"),
            "tendon":      (5, "同骨。肌腱外露是傷口深度分級的指標，不是一種壞死"),
            "maceration":  (0, "浸潤發生在**傷口周圍皮膚**，不是傷口內組織。"
                               "併入背景而非任一組織類——把它算成組織會讓面積與比例都膨脹"),
            "background":  (0, "直接對應"),
        },
    },
    "dfutissue": {
        "note": ("DFUTissue（110 張：78/16/16，糖尿病足，AZH Wound Center）。"
                 "**8 類**：granulation, callus, fibrin, necrotic, eschar, neodermis, tendon, dressing。"
                 "github.com/uwm-bigdata/DFUTissueSegNet"),
        "map": {
            "granulation": (1, "直接對應"),
            "slough":      (2, "直接對應"),
            "fibrin":      (2, "纖維蛋白滲出物在臨床上與腐肉同一處置（清創／敷料）"),
            "necrosis":    (3, "直接對應"),
            "necrotic":    (3, "同上（DFUTissue 用形容詞形式）"),
            "eschar":      (3, "同 woundtissueseg"),
            "epithelial":  (4, "直接對應"),
            # ⚠ 以下三類**建議由醫師覆核**這個映射。它們在我們的五類裡沒有對應，
            #    而「歸哪一類」會直接影響模型學到什麼。
            "neodermis":   (5, "【待醫師覆核】新生真皮。歸 1 肉芽（成熟方向）或 4 上皮（覆蓋方向）"
                               "都說得通，也都會有一半是錯的。歸『其他』是唯一不編造的選擇——"
                               "但若院內認為它就是肉芽的一個階段，改成 1 是合理的"),
            "callus":      (5, "【待醫師覆核】胼胝是角化過度的皮膚，不是傷口床組織"),
            "tendon":      (5, "肌腱外露是傷口深度分級的指標，不是一種壞死"),
            "dressing":    (5, "【待醫師覆核】敷料**根本不是組織**，是遮蔽物。"
                               "我們的『其他』定義本來就含遮蔽物，所以歸 5；"
                               "但若要訓練『辨識並排除敷料』，它應該獨立成一類"),
            "background":  (0, "直接對應"),
        },
    },
}


def print_mapping(preset):
    p = PRESETS[preset]
    print("# %s\n" % p["note"])
    print("| 來源類別 | → 本專案 | 理由 |")
    print("|---|---|---|")
    for k, (v, why) in sorted(p["map"].items(), key=lambda x: (x[1][0], x[0])):
        print("| `%s` | %d %s | %s |" % (k, v, OURS[v], why))
    unmapped = [c for c in OURS.values() if c not in ("背景",)
                and c not in {OURS[v] for v, _ in p["map"].values()}]
    if unmapped:
        print("\n⚠ 本專案有、但這個資料集沒有的類別：%s" % "、".join(unmapped))
        print("  模型在這些類別上得不到任何訓練訊號——驗證集若也沒有，")
        print("  Dice 會看起來正常而那個缺口完全看不出來。")


def build_lut(preset, src_values):
    """來源像素值 → 我們的碼。

    公開資料集通常用整數值編類別，且**索引順序不保證與類別名一致**。
    所以要求提供 `classes.json`（值 → 名稱），而不是猜。猜錯的話整批標籤是錯的，
    而訓練跑得起來、Dice 也會有數字——只是那個模型學到的是別的東西。
    """
    m = PRESETS[preset]["map"]
    lut, missing = {}, []
    for val, name in src_values.items():
        key = str(name).strip().lower()
        if key in m:
            lut[int(val)] = m[key][0]
        else:
            missing.append((val, name))
    return lut, missing


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preset", required=True, choices=sorted(PRESETS))
    ap.add_argument("--src", help="資料集根目錄")
    ap.add_argument("--images-dir", default="images")
    ap.add_argument("--masks-dir", default="masks")
    ap.add_argument("--classes", default="classes.json",
                    help="值→類別名的對照（相對 --src）。沒有它就只能猜，而猜錯不會報錯。")
    ap.add_argument("--out", help="輸出目錄（與 pull_dataset.ps1 同格式）")
    ap.add_argument("--append", action="store_true", help="併入既有輸出（混多個來源時用）")
    ap.add_argument("--print-mapping", action="store_true")
    a = ap.parse_args()

    if a.print_mapping:
        print_mapping(a.preset)
        return 0
    if not a.src or not a.out:
        print("需要 --src 與 --out（或用 --print-mapping 只看映射表）")
        return 1

    try:
        import numpy as np
        import cv2
    except ImportError as e:
        print("缺少相依：%s\n  pip install numpy opencv-python" % e)
        return 1

    cls_path = os.path.join(a.src, a.classes)
    if not os.path.exists(cls_path):
        print("找不到 %s。" % cls_path)
        print("請建一個 JSON：{\"0\": \"background\", \"1\": \"granulation\", ...}")
        print("⚠ 不要用猜的。索引順序猜錯的話訓練照樣跑得完、Dice 照樣有數字，")
        print("  但模型學到的是完全不同的東西，而且從指標上看不出來。")
        return 1
    with open(cls_path, encoding="utf-8") as f:
        src_values = json.load(f)

    lut, missing = build_lut(a.preset, src_values)
    if missing:
        print("⚠ 這些來源類別不在映射表裡：%s" % missing)
        print("  請在 PRESETS['%s']['map'] 補上，並寫下理由。" % a.preset)
        print("  不補的話它們會被當成背景——一個安靜的資料損失。")
        return 1

    im_dir = os.path.join(a.src, a.images_dir)
    mk_dir = os.path.join(a.src, a.masks_dir)
    if not os.path.isdir(im_dir) or not os.path.isdir(mk_dir):
        print("找不到影像或遮罩目錄：\n  %s\n  %s" % (im_dir, mk_dir))
        print("公開資料集的目錄結構各不相同，請用 --images-dir / --masks-dir 指定")
        print("（相對 --src；子目錄會遞迴搜尋，train/val/test 分層不影響）。")
        return 1
    out_im = os.path.join(a.out, "images"); out_mk = os.path.join(a.out, "masks")
    os.makedirs(out_im, exist_ok=True); os.makedirs(out_mk, exist_ok=True)

    man_path = os.path.join(a.out, "manifest.json")
    items = []
    if a.append and os.path.exists(man_path):
        with open(man_path, encoding="utf-8") as f:
            items = json.load(f).get("items", [])

    pairs, n_im, n_mk = find_pairs(im_dir, mk_dir)
    print("找到影像 %d、遮罩 %d、可配對 %d" % (n_im, n_mk, len(pairs)))
    if not pairs:
        # 大聲失敗。回報「匯入 0 張」而不說原因，使用者會以為腳本壞了，
        # 而真正的原因通常是目錄層級或檔名不一致——那是查得出來的。
        print("\n❌ 一張都配不起來。可能原因：")
        print("  · --images-dir / --masks-dir 指錯（目前：%s / %s）" % (a.images_dir, a.masks_dir))
        print("  · 影像與遮罩的檔名主檔名不同（本腳本以主檔名配對）")
        return 1

    hist = Counter(); n = 0; unreadable = 0
    for stem, ip, mp in pairs:
        img = imread_any(ip)
        m = imread_any(mp, __import__("cv2").IMREAD_UNCHANGED)
        if img is None or m is None:
            unreadable += 1
            continue
        if m.ndim == 3:
            m = m[:, :, 0]
        out = np.zeros_like(m, dtype=np.uint8)
        for sv, ov in lut.items():
            out[m == sv] = ov
        b = np.bincount(out.ravel(), minlength=6)[:6]
        for c in range(6):
            hist[c] += int(b[c])

        # 來源前綴當成「WD-code」：訓練切分依它分組，公開資料集的每張圖各自獨立，
        # 所以用檔名當群組是對的——但**不可與我們自己的 WD- 代碼混淆**，故加前綴。
        code = "PUB-%s-%s" % (a.preset[:3].upper(), stem)
        imwrite_any(os.path.join(out_im, "%s__%s.jpg" % (code, stem)), img)
        # 值放 R 通道，與 TissueMaskCodec 的編碼一致，讓 train_tissue_seg 一套讀法通吃
        z = np.zeros_like(out)
        imwrite_any(os.path.join(out_mk, "%s__%s.png" % (code, stem)), np.dstack([z, z, out]))
        items.append({
            "code": code, "image_id": stem, "source": "external",
            "dataset": a.preset,
            # ⚠ 公開資料集的標註是**別人的醫師**做的，我們的 tissue_edited 語意
            # （「我們的醫師改過」）不適用。標 true 會讓它混進「經我方確認」的統計，
            # 標 false 又會被訓練腳本排除。用獨立的 external_verified 說清楚。
            "tissue_edited": True, "external_verified": True,
            "actor": "external:%s" % a.preset,
        })
        n += 1
    if unreadable:
        print("⚠ 有 %d 對讀不進來（檔案損毀或格式不支援）。" % unreadable)

    with open(man_path, "w", encoding="utf-8") as f:
        json.dump({"items": items, "mapping": {k: list(v) for k, v in PRESETS[a.preset]["map"].items()},
                   "note": PRESETS[a.preset]["note"]}, f, ensure_ascii=False, indent=1)

    print("匯入 %d 張 → %s（累計 %d 張）" % (n, a.out, len(items)))
    tot = sum(hist[c] for c in range(1, 6)) or 1
    print("\n類別像素分布（不含背景）")
    for c in range(1, 6):
        print("  %-4s %10d  %5.1f%%" % (OURS[c], hist[c], 100.0 * hist[c] / tot))
    absent = [OURS[c] for c in range(1, 6) if hist[c] == 0]
    if absent:
        print("\n⚠ 完全沒有這些類別：%s" % "、".join(absent))
        print("  模型在它們身上得不到任何訓練訊號。若驗證集也沒有，")
        print("  多類平均 Dice 會看起來正常，而那個缺口完全看不出來——")
        print("  train_tissue_seg.py 會在訓練前把這件事印出來。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
