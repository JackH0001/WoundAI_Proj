# -*- coding: utf-8 -*-
"""組織分割訓練（P1 基線 / P2 微調）。**本機跑，不需要雲端 GPU。**

    pip install torch torchvision segmentation_models_pytorch albumentations numpy pillow opencv-python

用法：
    # P1：先用公開資料建立基線（WoundTissueSeg / DFUTissue 轉成同一格式後）
    python train_tissue_seg.py --data D:/public_ds --epochs 60 --out runs/base

    # P2：以基線為起點，加入自有資料微調
    python train_tissue_seg.py --data D:/woundai_ds --init runs/base/best.pt --epochs 40

    python train_tissue_seg.py --data D:/ds --dry-run     # 只檢查資料，不訓練

資料格式（`pull_dataset.ps1` 的輸出）：
    <data>/images/<code>__<image_id>.jpg
    <data>/masks/<code>__<image_id>.png     值＝組織碼 0..5（R 通道）
    <data>/manifest.json                    仿射參數、品質指標、標註者

## 為什麼這支腳本很短

因為**計算不是瓶頸**。512×512、5 類、250 張，ResNet34-UNet 在中階 GPU 約 20–40 分鐘，
CPU 過夜也跑得完。真正的成本是醫師標註的時間（一張 5–15 分鐘）。
所以這裡不做任何為了省算力的複雜設計，把力氣放在**不要把資料用錯**。

## 這支腳本刻意攔下的三件事

1. **未經醫師修正的遮罩**（`tissue_edited=false`）。那是色彩啟發式的原樣輸出，
   拿來訓練＝用模型自己的輸出訓練自己：驗證指標漂亮，臨床表現不動。
2. **依病患切分，不是依張數切分**。同一個病患的不同回診照片極為相似，
   落在訓練與驗證兩邊會讓 Dice 虛高好幾個百分點，而那個提升是假的。
3. **類別不平衡**。壞死通常只出現在少數影像上（文獻中 56/147），
   不加權的話模型會學會「永遠不預測壞死」，而整體 Dice 看起來還行。
"""
import argparse
import json
import os
import sys

# ⚠ 路徑含中文時 cv2.imread/imwrite 會靜默回 None，見 imio.py
from imio import imread_any, imwrite_any
from collections import Counter

# 修邊畫面碼：1 肉芽 / 2 腐肉 / 3 壞死 / 4 上皮 / 5 其他（0＝傷口外，不計入損失）
CLASSES = ["背景", "肉芽", "腐肉", "壞死", "上皮", "其他"]
N_CLASS = len(CLASSES)


def load_manifest(root):
    p = os.path.join(root, "manifest.json")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def collect(root, require_edited=True):
    """回 [(img_path, mask_path, meta)]。**過濾規則寫在這裡而不是資料夾裡**——
    資料夾只是位元組，判準要看得見、可調、可被測試。"""
    man = load_manifest(root)
    idx = {}
    if man:
        for it in man.get("items", []):
            idx["%s__%s" % (it.get("code"), it.get("image_id"))] = it
    im_dir = os.path.join(root, "images")
    mk_dir = os.path.join(root, "masks")
    out, skipped = [], Counter()
    for fn in sorted(os.listdir(im_dir)):
        stem = os.path.splitext(fn)[0]
        mk = os.path.join(mk_dir, stem + ".png")
        if not os.path.exists(mk):
            skipped["無遮罩"] += 1
            continue
        meta = idx.get(stem, {})
        if require_edited and man and not meta.get("tissue_edited", False):
            skipped["未經醫師修正"] += 1
            continue
        out.append((os.path.join(im_dir, fn), mk, meta))
    return out, skipped


def split_by_patient(items, val_frac=0.2, seed=0):
    """依 **WD-code**（＝病患的個案傷口）切分，不是依張數。

    同一個傷口的不同回診照片極為相似：同一位病患、同一個部位、同一台相機、
    同一種光線。落在訓練與驗證兩邊，模型只要記住那個傷口長什麼樣就能拿高分，
    而那個 Dice **不代表它在新病患身上的表現**。這是小資料集最常見的假成績來源。
    """
    import random
    codes = sorted({os.path.basename(i[0]).split("__")[0] for i in items})
    rnd = random.Random(seed)
    rnd.shuffle(codes)
    n_val = max(1, int(len(codes) * val_frac))
    val_codes = set(codes[:n_val])
    tr = [i for i in items if os.path.basename(i[0]).split("__")[0] not in val_codes]
    va = [i for i in items if os.path.basename(i[0]).split("__")[0] in val_codes]
    return tr, va, sorted(val_codes)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", default="runs/tissue")
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--size", type=int, default=512)
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--init", default=None, help="以既有權重起始（P2 微調用）")
    ap.add_argument("--allow-unedited", action="store_true",
                    help="⚠ 納入未經醫師修正的遮罩。只該用於清點，不該用於訓練。")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--seed", type=int, default=0, help="切分用亂數種子")
    ap.add_argument("--val-frac", type=float, default=0.2)
    ap.add_argument("--val-codes", default=None,
                    help="逗號分隔，指定驗證集的 WD-code。"
                         "傷口數很少時單次切分沒有意義——用它跑 leave-one-wound-out："
                         "每個代碼各當一次驗證集，看 7 個結果的**分散程度**，"
                         "而不是相信其中任何一個數字。")
    a = ap.parse_args()

    items, skipped = collect(a.data, require_edited=not a.allow_unedited)
    if a.val_codes:
        want = {c.strip() for c in a.val_codes.split(",") if c.strip()}
        tr = [i for i in items if os.path.basename(i[0]).split("__")[0] not in want]
        va = [i for i in items if os.path.basename(i[0]).split("__")[0] in want]
        val_codes = sorted(want)
    else:
        tr, va, val_codes = split_by_patient(items, a.val_frac, a.seed)

    all_codes = sorted({os.path.basename(i[0]).split("__")[0] for i in items})
    print("資料集：%s" % a.data)
    print("  可用 %d 筆，來自 %d 個傷口（訓練 %d / 驗證 %d，依 WD-code 切分）"
          % (len(items), len(all_codes), len(tr), len(va)))
    for k, v in skipped.items():
        print("  略過 %s：%d" % (k, v))
    print("  驗證集傷口：%s" % (", ".join(val_codes[:8]) + ("…" if len(val_codes) > 8 else "")))

    # ⚠ 驗證集只有一個傷口時，所有指標都是**那一個傷口的運氣**。
    #
    # 7 個傷口配 val_frac=0.2 就會落到這裡（int(7*0.2)=1）。此時單次切分的
    # 變異極大：換一個種子，Dice 可能從 0.3 跳到 0.7，而模型完全沒變。
    # 正確做法是 leave-one-wound-out——每個代碼各當一次驗證集，
    # 看那 N 個結果的**分散程度**，而不是相信其中任何一個。
    if len(val_codes) < 2:
        print("\n  ⚠ 驗證集只有 %d 個傷口。單一數字在此**不構成證據**。" % len(val_codes))
        print("     建議跑 leave-one-wound-out（每個代碼各當一次驗證集）：")
        for c in all_codes[:3]:
            print("       python train_tissue_seg.py --data %s --val-codes %s" % (a.data, c))
        if len(all_codes) > 3:
            print("       …（共 %d 個代碼）" % len(all_codes))
    if len(all_codes) < 15:
        print("\n  ⚠ 只有 %d 個傷口。這個規模足以驗證**流程**（匯出→訓練→評估跑得通），"
              % len(all_codes))
        print("     但任何準確度數字都不該對外引用——它衡量的是這幾個傷口，不是這個任務。")

    if a.allow_unedited:
        print("\n⚠ --allow-unedited：納入了未經醫師修正的遮罩。")
        print("  那些是色彩啟發式的原樣輸出，訓練它等於讓模型複製自己已經會做的事——")
        print("  驗證指標會上升，臨床表現不會。這個結果不可作為任何結論的依據。")

    if len(items) < 50:
        print("\n⚠ 只有 %d 筆。組織分割在 50 筆以下訓不出有意義的結果" % len(items))
        print("  （見 docs/tissue_segmentation_plan.md §3：公開資料集也才 110–147 張，")
        print("   而已發表的最佳多類 Dice 約 0.78）。")
        print("  建議先用公開資料建立基線，自有資料作微調（--init）。")
    if len(va) == 0:
        print("\n❌ 驗證集是空的——所有樣本都屬於同一個 WD-code。")
        print("  再收幾個不同的傷口再訓練；現在訓出來的任何數字都無法解讀。")
        return 1

    if a.dry_run:
        print("\n--dry-run：只檢查資料，不訓練。")
        return 0

    # ── 以下需要 torch。刻意延後 import：沒裝 torch 也要能跑 --dry-run 檢查資料。 ──
    try:
        import numpy as np
        import torch
        import torch.nn as nn
        from torch.utils.data import Dataset, DataLoader
        import segmentation_models_pytorch as smp
        import cv2
    except ImportError as e:
        print("\n缺少訓練相依：%s" % e)
        print("  pip install torch torchvision segmentation_models_pytorch opencv-python numpy")
        return 1

    # ── 解析度守門 ────────────────────────────────────────────────
    #
    # 在遠小於原生解析度的資料上用大 --size 訓練，是把**內插出來的像素**餵給模型：
    # 它會去學那些其實不存在的細節，而驗證集也被同樣放大，所以 Dice 看起來完全正常。
    # 真正的代價出現在換到高解析度的臨床照片時——模型沒見過真實紋理。
    #
    # 組織分類主要靠**紋理**（肉芽的顆粒感 vs 腐肉的平滑膜狀），而紋理正是
    # 降採樣時第一個消失的東西。DFUTissue 短邊中位數僅 134 px（實測 2026-08-06），
    # 用 --size 512 等於 4× 上採樣。
    sides = []
    for ip, _, _ in (tr + va)[:80]:
        im = imread_any(ip)
        if im is not None:
            sides.append(min(im.shape[:2]))
    if sides:
        med = int(np.median(sides))
        print("  原生短邊中位數：%d px" % med)
        if a.size > med * 1.5:
            print("  ⚠ --size %d 是原生解析度的 %.1f× —— 模型會學到內插出來的細節。" % (a.size, a.size / med))
            print("    組織分類靠紋理，而紋理在降採樣時最先消失；放大回來的不是紋理，是猜測。")
            print("    建議 --size %d（≈原生），或明確接受這是**預訓練**而非最終模型。" % (max(128, med // 32 * 32)))
        if med < 256:
            print("  ⚠ 原生短邊 %d px 低於本專案的品質門檻（min_roi_px=256）——" % med)
            print("    我們會拒收自己拍出這種解析度的照片。這份資料適合當暖啟動，")
            print("    **不適合**用它的 Dice 當作臨床表現的預期值。")

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    print("\n裝置：%s" % dev)
    if dev == "cpu":
        print("  CPU 也跑得完（250 張約數小時）。這個任務的瓶頸是標註時間，不是算力。")

    class DS(Dataset):
        def __init__(self, rows, size, train):
            self.rows, self.size, self.train = rows, size, train

        def __len__(self):
            return len(self.rows)

        def __getitem__(self, i):
            ip, mp, _ = self.rows[i]
            img = cv2.cvtColor(imread_any(ip, cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
            # 遮罩存在 R 通道（見 TissueMaskCodec）。用 INTER_NEAREST——
            # 類別值不可內插，"3.7 類" 沒有意義，雙線性會在邊界造出不存在的類別。
            m = imread_any(mp, cv2.IMREAD_COLOR)[:, :, 2]
            m = cv2.resize(m, (self.size, self.size), interpolation=cv2.INTER_NEAREST)
            img = cv2.resize(img, (self.size, self.size), interpolation=cv2.INTER_LINEAR)
            if self.train:
                if np.random.rand() < 0.5:
                    img, m = img[:, ::-1], m[:, ::-1]
                if np.random.rand() < 0.5:
                    img, m = img[::-1], m[::-1]
            x = torch.from_numpy(np.ascontiguousarray(img).transpose(2, 0, 1)).float() / 255.0
            x = (x - torch.tensor([0.485, 0.456, 0.406])[:, None, None]) / \
                torch.tensor([0.229, 0.224, 0.225])[:, None, None]
            return x, torch.from_numpy(np.ascontiguousarray(m)).long()

    net = smp.Unet("resnet34", encoder_weights="imagenet", classes=N_CLASS).to(dev)
    if a.init and os.path.exists(a.init):
        net.load_state_dict(torch.load(a.init, map_location=dev))
        print("  以 %s 為起點微調" % a.init)

    # 類別權重：以訓練集的實際像素頻率取倒數平方根。
    # 壞死通常只出現在少數影像（文獻中 56/147）；不加權的話模型會學會
    # 「永遠不預測壞死」，而整體 Dice 看起來還過得去——一個看不出問題的失敗。
    cnt = np.zeros(N_CLASS, dtype=np.int64)
    for _, mp, _ in tr:
        m = imread_any(mp, cv2.IMREAD_COLOR)[:, :, 2]
        cnt += np.bincount(m.ravel(), minlength=N_CLASS)[:N_CLASS]
    w = 1.0 / np.sqrt(np.maximum(cnt, 1))
    w = w / w.sum() * N_CLASS
    w[0] = 0.0          # 背景（傷口外）不計入損失：那不是要學的東西
    print("  類別像素分布：%s" % {CLASSES[i]: int(cnt[i]) for i in range(N_CLASS)})
    absent = [CLASSES[i] for i in range(1, N_CLASS) if cnt[i] == 0]
    if absent:
        print("  ⚠ 訓練集完全沒有這些類別：%s —— 模型不可能學會它們，"
              "而驗證集若也沒有，指標會看不出這個缺口。" % "、".join(absent))
    ce = nn.CrossEntropyLoss(weight=torch.tensor(w, dtype=torch.float32).to(dev), ignore_index=0)
    # ⚠ 只有加權 CE 是不夠的。
    #
    # 倒數平方根權重把「肉芽:其他 = 27×」的像素比壓到 5.2×——仍然偏向大類，
    # 而合成資料的煙霧測試正好照出這件事：肉芽 0.846、上皮 0.830，
    # 但腐肉 0.169、壞死 0.074、其他 0.026（幾乎等於「永遠不預測」）。
    #
    # Dice loss 是**逐類正規化**的：一個只佔 2% 像素的類別，它的 Dice 項與
    # 佔 70% 的那類同等重要。CE 管像素層級的分類信心，Dice 管區域層級的重疊，
    # 兩者相加是不平衡分割的標準組合。
    dice_loss = smp.losses.DiceLoss(mode="multiclass", from_logits=True, ignore_index=0)

    def criterion(logits, y):
        return ce(logits, y) + dice_loss(logits, y)

    dl_tr = DataLoader(DS(tr, a.size, True), batch_size=a.batch, shuffle=True, num_workers=0)
    dl_va = DataLoader(DS(va, a.size, False), batch_size=1, shuffle=False, num_workers=0)
    opt = torch.optim.AdamW(net.parameters(), lr=a.lr)
    os.makedirs(a.out, exist_ok=True)

    # 驗證集裡每一類出現在幾張影像。
    # 只出現在 1 張的類別，它的 Dice 是那一張的運氣，不是模型能力——
    # 而平均值會把它跟其他類等權相加，讓整體指標帶著一個純雜訊項。
    val_imgs = np.zeros(N_CLASS, dtype=np.int64)
    for _, mp, _ in va:
        mm = imread_any(mp, cv2.IMREAD_COLOR)
        if mm is None:
            continue
        for c in np.unique(mm[:, :, 2]):
            if 1 <= int(c) < N_CLASS:
                val_imgs[int(c)] += 1
    print("  驗證集類別覆蓋：%s（共 %d 張）"
          % ({CLASSES[i]: int(val_imgs[i]) for i in range(1, N_CLASS)}, len(va)))
    thin = [CLASSES[i] for i in range(1, N_CLASS) if 0 < val_imgs[i] <= 1]
    if thin:
        print("  ⚠ 這些類別只出現在 1 張驗證影像：%s" % "、".join(thin))
        print("    它們的 Dice 是那一張的運氣，不要拿去做任何比較。")

    def evaluate():
        net.eval()
        inter = np.zeros(N_CLASS); union = np.zeros(N_CLASS)
        with torch.no_grad():
            for x, y in dl_va:
                pr = net(x.to(dev)).argmax(1).cpu().numpy()
                gt = y.numpy()
                for c in range(1, N_CLASS):
                    p, g = (pr == c), (gt == c)
                    inter[c] += (p & g).sum(); union[c] += p.sum() + g.sum()
        dice = np.where(union > 0, 2 * inter / np.maximum(union, 1), np.nan)
        return dice

    best = -1.0
    for ep in range(1, a.epochs + 1):
        net.train(); tot = 0.0
        for x, y in dl_tr:
            opt.zero_grad()
            loss = criterion(net(x.to(dev)), y.to(dev))
            loss.backward(); opt.step(); tot += float(loss)
        d = evaluate()
        mean = float(np.nanmean(d[1:]))
        line = "  ".join("%s %.3f" % (CLASSES[c], d[c]) for c in range(1, N_CLASS)
                         if not np.isnan(d[c]))
        print("ep %3d  loss %.4f  Dice(平均 %.3f)  %s" % (ep, tot / max(1, len(dl_tr)), mean, line))
        if mean > best:
            best = mean
            torch.save(net.state_dict(), os.path.join(a.out, "best.pt"))

    print("\n最佳驗證 Dice（多類平均）：%.3f  → %s/best.pt" % (best, a.out))
    print("對照：已發表最佳約 0.78（DFUTissue），肉芽 0.70、壞死 0.50。")
    print("⚠ 這個數字的天花板是**兩位醫師彼此同意的程度**（inter-rater Dice）。")
    print("  沒有量過 inter-rater 之前，0.70 到底是好是壞無從判斷"
          "（見 docs/tissue_segmentation_plan.md §6）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
