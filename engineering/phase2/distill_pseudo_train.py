# -*- coding: utf-8 -*-
"""偽標籤蒸餾訓練:在既有「有 GT 的蒸餾集」之外，混入 `distill_pseudo_gen.py` 產出的
**無 GT 偽標籤**，把 student 的召回拉起來（目標 retrain_merged Dice 0.762 → 0.82–0.84）。

動機（EVIDENCE_LEDGER 2026-07-09/07-13）：student 對低對比／破碎傷口大幅低估
（FootUlcer 0.76 vs A∪U 6.07 cm²、Burn 3.59 vs 9.30），而**便宜推論調參已證實無效**
（降門檻 0.4→0.1 覆蓋幾乎不變 ⇒ 機率質量根本不在那裡，只能重訓）。

與 `distill_train.py` 的差別（那支不動，維持已驗證的路徑）：
  - 資料 = 有GT集（KD + Dice(GT)）∪ 偽標籤集（只有 KD，權重較低）
  - **每個 epoch 都混入有GT集**（防災難性遺忘），比例由 --pseudo-ratio 控制
  - 驗證只用有GT的 holdout（乾淨），偽標籤不參與評分——否則等於自己給自己打分

前置：
  pip install torch torchvision segmentation_models_pytorch albumentations opencv-python pillow numpy onnx
  python distill_teacher_gen.py                      # 有GT集的老師軟標籤
  python distill_pseudo_gen.py --src <無GT影像目錄>   # 偽標籤（含品質把關）

用法：
  python distill_pseudo_train.py --pseudo D:/pseudo_AU              # 完整訓練
  python distill_pseudo_train.py --pseudo D:/pseudo_AU --check      # 只檢查資料佈局(免 torch)
  EPOCHS=80 BATCH=8 STUDENT_ENCODER=mobilenet_v2 python distill_pseudo_train.py --pseudo ...

輸出：student_pseudo_best.pt / student_pseudo.onnx（256 NCHW imagenet RGB，thr 0.4）
之後接 `distill_export.py` 慣例量化 FP16 → 部署前必須用**未見過的 holdout** 驗證，
並走 EVIDENCE_LEDGER 流程＋更新 golden，才可換掉現役 student。
"""
import argparse, glob, os, sys
import numpy as np

A = os.environ.get("WOUNDAI_ARCHIVE", "C:/dev/WoundAI_weights_archive")
IMG_DIR = os.path.join(A, "批次驗證工具", "retrain_merged", "image")
GT_DIR = os.path.join(A, "批次驗證工具", "retrain_merged", "labels")
TEACH_DIR = os.path.join(A, "批次驗證工具", "distill_teacher_AU")
SIZE = 256
ENCODER = os.environ.get("STUDENT_ENCODER", "mobilenet_v2")
EPOCHS = int(os.environ.get("EPOCHS", "80"))
BATCH = int(os.environ.get("BATCH", "8"))
MEAN = np.array([0.485, 0.456, 0.406], np.float32)
STD = np.array([0.229, 0.224, 0.225], np.float32)


def scan(pseudo_dir):
    """回 (有GT樣本, 偽標籤樣本)。有GT需 image+labels+teacher soft;偽標籤需 image+soft。"""
    lab = []
    for p in sorted(glob.glob(os.path.join(IMG_DIR, "*.png"))):
        n = os.path.basename(p)
        if os.path.exists(os.path.join(TEACH_DIR, n.replace(".png", ".npy"))) \
                and os.path.exists(os.path.join(GT_DIR, n)):
            lab.append((p, os.path.join(GT_DIR, n), os.path.join(TEACH_DIR, n.replace(".png", ".npy"))))

    ps, soft_dir = [], os.path.join(pseudo_dir, "soft")
    man = os.path.join(pseudo_dir, "pseudo_manifest.json")
    paths = {}
    if os.path.exists(man):
        import json
        for r in json.load(open(man, encoding="utf-8"))["records"]:
            if r.get("accept"): paths[r["stem"]] = r["path"]
    for sp in sorted(glob.glob(os.path.join(soft_dir, "*.npy"))):
        stem = os.path.splitext(os.path.basename(sp))[0]
        ip = paths.get(stem)
        if ip and os.path.exists(ip): ps.append((ip, None, sp))
    return lab, ps


def imread_rgb(path):
    import cv2
    arr = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)  # 中文路徑安全
    if arr is None: raise IOError(path)
    return cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pseudo", required=True, help="distill_pseudo_gen.py 的 --out 目錄")
    ap.add_argument("--pseudo-ratio", type=float, default=0.5,
                    help="每個 epoch 偽標籤佔比(0.5=一半)。太高會被老師的錯誤帶走")
    ap.add_argument("--pseudo-weight", type=float, default=0.5,
                    help="偽標籤 KD 損失權重(相對有GT樣本)。偽標籤沒有真值背書,壓低")
    ap.add_argument("--check", action="store_true", help="只檢查資料佈局,不載 torch")
    a = ap.parse_args()

    lab, ps = scan(a.pseudo)
    print(f"有GT樣本 {len(lab)} 張(image+labels+teacher soft)")
    print(f"偽標籤樣本 {len(ps)} 張(通過品質把關者)")
    if not lab:
        print(f"✗ 找不到有GT樣本。檢查 WOUNDAI_ARCHIVE={A} 與 distill_teacher_gen.py 是否跑過"); return 1
    if not ps:
        print(f"✗ 找不到偽標籤。先跑 distill_pseudo_gen.py --out {a.pseudo}"); return 1
    if len(ps) < len(lab) * 0.5:
        print(f"⚠ 偽標籤只有 {len(ps)} 張,相對有GT集 {len(lab)} 張偏少,拉升幅度有限")
    if a.check:
        print("\n[check] 佈局 OK。拿掉 --check 即開始訓練。"); return 0

    import torch, torch.nn as nn
    from torch.utils.data import Dataset, DataLoader
    import segmentation_models_pytorch as smp
    import cv2
    try:
        import albumentations as Aug
        HAS_AUG = True
    except Exception:
        HAS_AUG = False
        print("⚠ 無 albumentations,關閉增廣(泛化會變差,建議安裝)")
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"裝置 {dev} / encoder {ENCODER} / epochs {EPOCHS} / batch {BATCH}")

    class DS(Dataset):
        """回 (x, soft, gt, has_gt)。偽標籤樣本 has_gt=0,不計 Dice(GT) 項。"""
        def __init__(s, items, train=True): s.it = items; s.train = train
        def __len__(s): return len(s.it)
        def __getitem__(s, i):
            ip, gp, sp = s.it[i]
            img = cv2.resize(imread_rgb(ip), (SIZE, SIZE))
            soft = cv2.resize(np.load(sp).astype(np.float32), (SIZE, SIZE))
            if gp:
                g = cv2.imdecode(np.fromfile(gp, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
                gt = cv2.resize((g > 127).astype(np.float32), (SIZE, SIZE), interpolation=cv2.INTER_NEAREST)
                has = 1.0
            else:
                gt = np.zeros((SIZE, SIZE), np.float32); has = 0.0
            if s.train and HAS_AUG:
                au = Aug.Compose([Aug.HorizontalFlip(p=.5), Aug.RandomRotate90(p=.5),
                                  Aug.RandomBrightnessContrast(p=.3),
                                  Aug.ShiftScaleRotate(p=.3, border_mode=cv2.BORDER_REFLECT)],
                                 additional_targets={"soft": "mask", "gt": "mask"})
                r = au(image=img, soft=soft, gt=gt); img, soft, gt = r["image"], r["soft"], r["gt"]
            x = ((img / 255.0 - MEAN) / STD).transpose(2, 0, 1).astype(np.float32)
            return (torch.from_numpy(x), torch.from_numpy(soft[None]),
                    torch.from_numpy(gt[None]), torch.tensor(has))

    # 驗證集只取有GT樣本(乾淨);偽標籤全部進訓練
    n_val = max(1, len(lab) // 6)
    lab_va, lab_tr = lab[:n_val], lab[n_val:]
    # 依 --pseudo-ratio 決定每 epoch 取多少偽標籤(過多會蓋掉真值訊號)
    n_ps = min(len(ps), int(len(lab_tr) * a.pseudo_ratio / max(1e-6, 1 - a.pseudo_ratio)))
    print(f"訓練 {len(lab_tr)} 有GT + {n_ps} 偽標籤 / 驗證 {len(lab_va)}(僅有GT)")

    dlv = DataLoader(DS(lab_va, False), batch_size=BATCH, num_workers=0)
    model = smp.Unet(encoder_name=ENCODER, encoder_weights="imagenet",
                     in_channels=3, classes=1, activation=None).to(dev)
    opt = torch.optim.AdamW(model.parameters(), lr=3e-4, weight_decay=1e-4)
    bce = nn.BCEWithLogitsLoss(reduction="none")

    def dice_loss(logit, tgt, w=None):
        p = torch.sigmoid(logit)
        inter = (p * tgt).sum((2, 3)); s = p.sum((2, 3)) + tgt.sum((2, 3))
        d = 1 - (2 * inter + 1) / (s + 1)
        return (d * w).sum() / w.sum().clamp(min=1) if w is not None else d.mean()

    def dice_score(logit, tgt, thr=0.4):
        p = (torch.sigmoid(logit) > thr).float()
        inter = (p * tgt).sum((2, 3)); s = p.sum((2, 3)) + tgt.sum((2, 3))
        return ((2 * inter + 1) / (s + 1)).mean().item()

    rng = np.random.default_rng(0)
    best, best_ep, patience = 0.0, 0, 20
    for ep in range(EPOCHS):
        # 每個 epoch 重抽偽標籤子集 → 全體偽標籤都看得到,又不會壓過真值(防遺忘)
        idx = rng.choice(len(ps), size=n_ps, replace=False) if n_ps else []
        epoch_items = lab_tr + [ps[i] for i in idx]
        dl = DataLoader(DS(epoch_items, True), batch_size=BATCH, shuffle=True, num_workers=0)
        model.train()
        for x, soft, gt, has in dl:
            x, soft, gt, has = x.to(dev), soft.to(dev), gt.to(dev), has.to(dev).view(-1, 1)
            out = model(x)
            # KD:有GT 權重 1、偽標籤 pseudo_weight
            w = has + (1 - has) * a.pseudo_weight
            kd = (bce(out, soft).mean((1, 2, 3)) * w.view(-1)).sum() / w.sum().clamp(min=1)
            kd = kd + dice_loss(out, soft, w.view(-1, 1))
            sup = dice_loss(out, gt, has)          # 只有 has_gt=1 的樣本貢獻
            loss = kd + sup
            opt.zero_grad(); loss.backward(); opt.step()

        model.eval(); sc = []
        with torch.no_grad():
            for x, soft, gt, has in dlv:
                sc.append(dice_score(model(x.to(dev)), gt.to(dev)))
        v = float(np.mean(sc))
        if v > best:
            best, best_ep = v, ep
            torch.save(model.state_dict(), "student_pseudo_best.pt")
        print(f"ep{ep+1}/{EPOCHS} val_dice {v:.3f} (best {best:.3f} @ep{best_ep+1})")
        if ep - best_ep >= patience:
            print(f"早停:{patience} epoch 無進步"); break

    model.load_state_dict(torch.load("student_pseudo_best.pt", map_location=dev)); model.eval()
    dummy = torch.randn(1, 3, SIZE, SIZE, device=dev)
    try:
        torch.onnx.export(model, dummy, "student_pseudo.onnx", input_names=["input"],
                          output_names=["mask"], opset_version=13, dynamo=False,
                          dynamic_axes={"input": {0: "N"}, "mask": {0: "N"}})
    except TypeError:
        torch.onnx.export(model, dummy, "student_pseudo.onnx", input_names=["input"],
                          output_names=["mask"], opset_version=13,
                          dynamic_axes={"input": {0: "N"}, "mask": {0: "N"}})
    print(f"\n完成。best val_dice={best:.3f} → student_pseudo.onnx")
    print("⚠ 這個 val_dice 是**分布內**(retrain_merged holdout),偏樂觀。上線前必做：")
    print("   1) STUDENT_ONNX=student_pseudo.onnx python distill_eval.py   # 對比老師/現役 student")
    print("   2) 用未見過的臨床照(≥20)算 GT-Dice,並目視 5 張難例遮罩是否補全")
    print("   3) 沒有超過現役 student(0.762) 就不要換;要換須走 EVIDENCE_LEDGER + 更新 golden")
    return 0


if __name__ == "__main__":
    sys.exit(main())
