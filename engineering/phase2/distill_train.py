# -*- coding: utf-8 -*-
"""學生蒸餾訓練(turnkey,本機/GPU 執行)。
目標：把 A∪U 老師(a_unet∪unet++ 機率融合)的能力凝縮成單一「行動端輕量學生」。
策略：廣域泛用優先(ImageNet 預訓編碼器+強增廣)＋足部加強(資料多為足部、可加損失權重)。
學生：smp.Unet(輕量編碼器,預設 mobilenet_v2,256,單通道 sigmoid)。
損失：KD(學生 vs 老師軟標籤,BCE/MSE) + Dice(學生 vs 真值GT,有 GT 時) — 兼顧老師知識與真值。

前置(本機)：
  pip install torch torchvision segmentation_models_pytorch albumentations numpy pillow opencv-python onnx
  # 老師軟標籤：先跑 teacher_gen.py 產生 distill_teacher_AU/*.npy (256 soft float16)
資料佈局：
  IMAGES = retrain_merged/image/*.png
  TEACHER= 批次驗證工具/distill_teacher_AU/<stem>.npy   (256 soft)
  GT     = retrain_merged/labels/*.png                  (可選,用於 Dice 項與驗證)
輸出：student_distilled.onnx (+ 驗證報告)。之後轉 CoreML/TFLite 上行動端。
"""
import os, glob, numpy as np, cv2
from PIL import Image

# ===== 設定 =====
A = os.environ.get("WOUNDAI_ARCHIVE", "C:/dev/WoundAI_weights_archive")
IMG_DIR  = os.path.join(A, "批次驗證工具", "retrain_merged", "image")
GT_DIR   = os.path.join(A, "批次驗證工具", "retrain_merged", "labels")
TEACH_DIR= os.path.join(A, "批次驗證工具", "distill_teacher_AU")
SIZE = 256
ENCODER = os.environ.get("STUDENT_ENCODER", "mobilenet_v2")  # 輕量;可改 efficientnet-lite0/timm-mobilenetv3_small
EPOCHS = int(os.environ.get("EPOCHS", "60"))
BATCH  = int(os.environ.get("BATCH", "8"))
KD_W, DICE_W = 1.0, 1.0     # KD 與真值權重
EXCLUDE_EMPTY_GT = True     # 依 GT 稽核排除空GT(預設排除)

def main():
    import torch, torch.nn as nn
    from torch.utils.data import Dataset, DataLoader
    import segmentation_models_pytorch as smp
    try:
        import albumentations as Aug
        HAS_AUG = True
    except Exception:
        HAS_AUG = False
    dev = "cuda" if torch.cuda.is_available() else "cpu"

    names = [os.path.basename(p) for p in sorted(glob.glob(os.path.join(IMG_DIR, "*.png")))]
    # 過濾:需有老師軟標籤;可選排除空GT
    samples = []
    for n in names:
        tp = os.path.join(TEACH_DIR, n.replace(".png", ".npy"))
        if not os.path.exists(tp): continue
        if EXCLUDE_EMPTY_GT:
            g = np.asarray(Image.open(os.path.join(GT_DIR, n)).convert("L")) > 127
            if g.sum() == 0: continue
        samples.append(n)
    print(f"訓練樣本 {len(samples)} (有老師標籤{'、排除空GT' if EXCLUDE_EMPTY_GT else ''})")

    mean = np.array([0.485,0.456,0.406],np.float32); std=np.array([0.229,0.224,0.225],np.float32)
    class DS(Dataset):
        def __init__(s, names, train=True): s.n=names; s.train=train
        def __len__(s): return len(s.n)
        def __getitem__(s, i):
            n=s.n[i]
            img=np.asarray(Image.open(os.path.join(IMG_DIR,n)).convert("RGB"))
            soft=np.load(os.path.join(TEACH_DIR,n.replace(".png",".npy"))).astype(np.float32)  # 256
            gtp=os.path.join(GT_DIR,n); gt=(np.asarray(Image.open(gtp).convert("L"))>127).astype(np.float32) if os.path.exists(gtp) else None
            img=cv2.resize(img,(SIZE,SIZE)); 
            soft=cv2.resize(soft,(SIZE,SIZE))
            gt=cv2.resize(gt,(SIZE,SIZE),interpolation=cv2.INTER_NEAREST) if gt is not None else np.zeros((SIZE,SIZE),np.float32)
            if s.train and HAS_AUG:
                au=Aug.Compose([Aug.HorizontalFlip(p=.5),Aug.RandomRotate90(p=.5),
                                Aug.RandomBrightnessContrast(p=.3),Aug.ShiftScaleRotate(p=.3,border_mode=cv2.BORDER_REFLECT)],
                               additional_targets={"soft":"mask","gt":"mask"})
                r=au(image=img,soft=soft,gt=gt); img,soft,gt=r["image"],r["soft"],r["gt"]
            x=((img/255.0-mean)/std).transpose(2,0,1).astype(np.float32)
            return torch.from_numpy(x), torch.from_numpy(soft[None]), torch.from_numpy(gt[None])

    n_val=max(1,len(samples)//6); tr,va=samples[n_val:],samples[:n_val]
    dl=DataLoader(DS(tr,True),batch_size=BATCH,shuffle=True,num_workers=0)
    dlv=DataLoader(DS(va,False),batch_size=BATCH,num_workers=0)
    model=smp.Unet(encoder_name=ENCODER,encoder_weights="imagenet",in_channels=3,classes=1,activation=None).to(dev)
    opt=torch.optim.AdamW(model.parameters(),lr=3e-4,weight_decay=1e-4)
    bce=nn.BCEWithLogitsLoss()
    def dice_loss(logit,tgt):
        p=torch.sigmoid(logit); inter=(p*tgt).sum((2,3)); s=p.sum((2,3))+tgt.sum((2,3))
        return (1-(2*inter+1)/(s+1)).mean()
    def dice_score(logit,tgt,thr=0.4):
        p=(torch.sigmoid(logit)>thr).float(); inter=(p*tgt).sum((2,3)); s=p.sum((2,3))+tgt.sum((2,3))
        return ((2*inter+1)/(s+1)).mean().item()
    best=0
    for ep in range(EPOCHS):
        model.train()
        for x,soft,gt in dl:
            x,soft,gt=x.to(dev),soft.to(dev),gt.to(dev)
            out=model(x)
            loss=KD_W*(bce(out,soft)+dice_loss(out,soft)) + DICE_W*dice_loss(out,gt)
            opt.zero_grad(); loss.backward(); opt.step()
        model.eval(); sc=[]
        with torch.no_grad():
            for x,soft,gt in dlv:
                sc.append(dice_score(model(x.to(dev)),gt.to(dev)))
        v=float(np.mean(sc)); 
        if v>best:
            best=v; torch.save(model.state_dict(),"student_best.pt")
        print(f"ep{ep+1}/{EPOCHS} val_dice {v:.3f} (best {best:.3f})")
    # 匯出 ONNX
    model.load_state_dict(torch.load("student_best.pt",map_location=dev)); model.eval()
    dummy=torch.randn(1,3,SIZE,SIZE,device=dev)
    torch.onnx.export(model,dummy,"student_distilled.onnx",input_names=["input"],output_names=["mask"],
                      opset_version=13,dynamic_axes={"input":{0:"N"},"mask":{0:"N"}})
    print(f"完成。best val_dice={best:.3f} → student_distilled.onnx (encoder={ENCODER}, 256, imagenet RGB NCHW)")
    print("下一步:量化 FP16/INT8 + 轉 CoreML/TFLite;用 hold-out(未見過足部照)驗證 vs 老師/現有模型。")

if __name__=="__main__": main()
