# -*- coding: utf-8 -*-
"""標註者間一致性（inter-rater Dice）。

    python analyze_interrater.py --data D:/woundai_interrater

## 這個數字是什麼

**模型表現的天花板。** 模型不可能比兩位醫師彼此同意的程度更準——
若兩位資深醫師對同一張傷口的肉芽範圍只有 0.65 的 Dice，那麼一個 0.70 的模型
已經在人類判斷的雜訊底線上了，再往上調參數只是在擬合某一位醫師的個人風格。

沒有這個數字，「Dice 0.70」到底是好是壞**無從判斷**——而那正是最容易被誤讀、
也最容易被拿去對外宣稱的一個數字。

## 為什麼不需要特地做「20 張研究」

任何被兩位以上標註者標過的影像都是免費的一致性資料。後端已經把平行標註保留下來
（`parallel_rater` 狀態），`pull_dataset.ps1 -Kind interrater` 會把它們抓下來。
只要在流程上偶爾讓兩位醫師標同一張，樣本自己會累積。

## 怎麼讀結果

- **逐類 Dice**：類內變異大的（「其他」、壞死）通常最低，那是定義本身的模糊，不是誰標錯
- **Krippendorff-like 逐像素同意率**：不受類別面積影響，適合看整體
- **混淆矩陣**：看的是「A 說肉芽時 B 說什麼」——最有用的一張表。
  若某兩類經常互相混淆，代表**標註規範需要補一條判準**，而不是模型要再訓練
"""
import argparse
import itertools
import json
import os
import sys

# ⚠ 路徑含中文時 cv2.imread/imwrite 會靜默回 None，見 imio.py
from imio import imread_any, imwrite_any
from collections import defaultdict

CLASSES = ["背景", "肉芽", "腐肉", "壞死", "上皮", "其他"]
N = len(CLASSES)


def load_masks(data):
    """回 {image_id: {actor: mask_ndarray}}。遮罩值在 R 通道（見 TissueMaskCodec）。"""
    import numpy as np
    import cv2
    man_path = os.path.join(data, "manifest.json")
    if not os.path.exists(man_path):
        print("找不到 %s。請先跑 pull_dataset.ps1 -Kind interrater" % man_path)
        return None
    with open(man_path, encoding="utf-8") as f:
        man = json.load(f)
    out = {}
    for it in man.get("items", []):
        iid = it["image_id"]
        for r in it.get("raters", []):
            fn = os.path.join(data, "masks", "%s__%s__%s.png"
                              % (r["code"], iid, r["actor"].replace(":", "_")))
            if not os.path.exists(fn):
                continue
            m = imread_any(fn, cv2.IMREAD_COLOR)
            if m is None:
                continue
            out.setdefault(iid, {})[r["actor"]] = m[:, :, 2]
    return out


def pair_stats(ma, mb):
    """一組配對的 (交集, 聯集, 同意像素, 有效像素, 混淆矩陣)。

    抽成函式是為了可被測試——Dice 算錯不會有任何徵兆，
    而這個數字日後會被拿去當「模型表現的天花板」引用。
    """
    import numpy as np
    inter = np.zeros(N); union = np.zeros(N)
    conf = np.zeros((N, N), dtype=np.int64)
    # 兩人都認為是傷口外（0）的像素不計入：那不是判斷，是留白。
    valid = (ma > 0) | (mb > 0)
    for c in range(1, N):
        pa, pb = (ma == c), (mb == c)
        inter[c] = int((pa & pb).sum()); union[c] = int(pa.sum() + pb.sum())
    for ca in range(N):
        for cb in range(N):
            conf[ca, cb] = int(((ma == ca) & (mb == cb) & valid).sum())
    return inter, union, int((ma[valid] == mb[valid]).sum()), int(valid.sum()), conf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--min-pairs", type=int, default=5,
                    help="低於這個配對數就只列出，不下任何結論")
    a = ap.parse_args()

    try:
        import numpy as np
        import cv2  # noqa: F401  （load_masks 用得到）
    except ImportError as e:
        print("缺少相依：%s\n  pip install numpy opencv-python" % e)
        return 1

    data = load_masks(a.data)
    if not data:
        return 1

    inter = np.zeros(N); union = np.zeros(N)
    agree = 0; total = 0
    conf = np.zeros((N, N), dtype=np.int64)
    pair_rows = []

    for iid, byactor in sorted(data.items()):
        actors = sorted(byactor)
        if len(actors) < 2:
            continue
        for x, y in itertools.combinations(actors, 2):
            ma, mb = byactor[x], byactor[y]
            if ma.shape != mb.shape:
                # 兩人的柵格尺寸不同（各自的 ROI 起點與擴張範圍不一樣）。
                # 硬 resize 會在類別邊界造出不存在的類別，比較的就不是他們的判斷了。
                print("⚠ %s：%s 與 %s 的遮罩尺寸不同 %s vs %s，略過"
                      % (iid[:8], x, y, ma.shape, mb.shape))
                continue
            i_, u_, ag, tt, cf = pair_stats(ma, mb)
            if tt == 0:
                continue
            inter += i_; union += u_; agree += ag; total += tt; conf += cf
            per = {CLASSES[c]: round(2 * i_[c] / u_[c], 3) for c in range(1, N) if u_[c] > 0}
            pair_rows.append((iid[:8], x, y, per))

    n_pairs = len(pair_rows)
    print("影像 %d 張・可比較配對 %d 組\n" % (len(data), n_pairs))
    if n_pairs == 0:
        print("沒有可比較的配對。讓兩位醫師標同一張影像即可累積——"
              "不需要特地做研究，日常流程中偶爾重複標註就會產生。")
        return 0

    print("逐類 inter-rater Dice（所有配對合併）")
    dice = np.where(union > 0, 2 * inter / np.maximum(union, 1), np.nan)
    for c in range(1, N):
        if not np.isnan(dice[c]):
            print("  %-4s %.3f" % (CLASSES[c], dice[c]))
    mean = float(np.nanmean(dice[1:]))
    print("  %-4s %.3f" % ("平均", mean))
    print("\n逐像素同意率：%.3f（%d / %d）" % (agree / max(1, total), agree, total))

    print("\n混淆矩陣（列＝標註者 A，欄＝標註者 B，僅前四類與其他）")
    hdr = "        " + "".join("%8s" % CLASSES[c] for c in range(1, N))
    print(hdr)
    for ca in range(1, N):
        row = "".join("%8d" % conf[ca, cb] for cb in range(1, N))
        print("%-8s%s" % (CLASSES[ca], row))
    off = [(conf[i, j] + conf[j, i], CLASSES[i], CLASSES[j])
           for i in range(1, N) for j in range(i + 1, N)]
    off.sort(reverse=True)
    if off and off[0][0] > 0:
        print("\n最常互相混淆：%s ↔ %s（%d 像素）" % (off[0][1], off[0][2], off[0][0]))
        print("  ⚠ 這通常代表**標註規範少了一條判準**，不是模型要再訓練。")
        print("  先把兩類的分界寫成文字（例如「表面覆蓋一層黃膜但底下可見紅色顆粒 → 算肉芽」），")
        print("  再讓標註者重看那幾張——修規範比修模型便宜太多。")

    if n_pairs < a.min_pairs:
        print("\n⚠ 只有 %d 組配對，這些數字的變異很大，**不足以下任何結論**。" % n_pairs)
        print("  建議累積到 %d 組以上再解讀。" % a.min_pairs)
    else:
        print("\n判讀：模型的多類平均 Dice 若已接近 %.3f，" % mean)
        print("  代表它已經在人類判斷的雜訊底線上——再調參數只是在擬合某一位標註者的個人風格。")
        print("  那時該做的是提升標註一致性（補判準、訓練標註者），不是換更大的模型。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
