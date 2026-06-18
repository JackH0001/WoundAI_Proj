# -*- coding: utf-8 -*-
"""在『有 TensorFlow 的環境』評估微調 Keras .h5 分割模型（UNet256 retrain2 / A-UNet / UNet++ / UNet-DS）。
對範例臨床照計算 Dice/IoU vs 人工 GT，並輸出目視 montage + CSV。
本檔設計為 turnkey：改 ARCHIVE 路徑後 `python eval_tf_models.py`。
注意：沙箱 TF 安裝不全無法在此跑；請於本機 TF 環境執行。"""
import os, sys, csv, glob, numpy as np
ARCHIVE = os.environ.get("WOUNDAI_ARCHIVE", "C:/dev/WoundAI_weights_archive")
D = os.path.join(ARCHIVE, "test_images", "方形校正貼紙範例"); GTD = os.path.join(D, "labels_correct")
OUT = os.environ.get("OUT", "tf_eval_out"); os.makedirs(OUT, exist_ok=True)
MODELS = {  # 名稱: (相對 ARCHIVE 路徑, input_size, normalize)  ；normalize: '-11'(RGB[-1,1]) / '01' / 'imagenet'
 "UNet256_retrain2": ("批次驗證工具/Outputs/unet_tf_round3_retrain2/unet_tf_best.h5", 256, "-11"),
 "A-UNet":           ("批次驗證工具/Output_AUnet驗證及對照結果/att_unet_s256_full/att_unet_best.h5", 256, "-11"),
 "UNet++":           ("批次驗證工具/Output_AUnet驗證及對照結果/unetpp_s256_full/unetpp_best.h5", 256, "-11"),
 "UNet-DS":          ("批次驗證工具/Output_AUnet驗證及對照結果/unet_ds_s256_full/unet_ds_best.h5", 256, "-11"),
}
THRESH = 0.5
GT = {"Foot_chronic_ulcer_校正貼紙":".jpg","Bedsore_方形校正貼紙範例":".jpeg","Bedsore_02_方形校正貼紙範例":".jpeg"}
import cv2
from PIL import Image
def dice_iou(p,g):
    p=p.astype(bool); g=g.astype(bool); inter=(p&g).sum(); u=(p|g).sum()
    return (2*inter/(p.sum()+g.sum()) if (p.sum()+g.sum()) else 1.0), (inter/u if u else 1.0)
def prep(img,sz,norm):
    r=cv2.resize(img,(sz,sz)).astype(np.float32)
    if norm=="-11": x=r/127.5-1
    elif norm=="01": x=r/255.0
    else: x=(r/255.0-[0.485,0.456,0.406])/[0.229,0.224,0.225]
    return x[None].astype(np.float32)
def main():
    import tensorflow as tf
    rows=[]
    for mname,(rel,sz,norm) in MODELS.items():
        mp=os.path.join(ARCHIVE,rel)
        if not os.path.exists(mp): print("缺檔",mp); continue
        try: model=tf.keras.models.load_model(mp,compile=False)
        except Exception as e: print(mname,"load 失敗(試 custom_objects):",e); continue
        ds=[];ios=[]
        for nm,ext in GT.items():
            img=np.asarray(Image.open(os.path.join(D,nm+ext)).convert("RGB")); H,W=img.shape[:2]
            o=np.squeeze(model.predict(prep(img,sz,norm),verbose=0))
            if o.ndim==3: o=o[...,0]
            if o.min()<0 or o.max()>1: o=1/(1+np.exp(-o))
            pred=cv2.resize(o,(W,H))>THRESH
            gt=np.asarray(Image.open(os.path.join(GTD,nm+".png")).convert("L"))>127
            if gt.shape!=(H,W): gt=np.asarray(Image.fromarray(gt.astype(np.uint8)*255).resize((W,H)))>127
            d,i=dice_iou(pred,gt); ds.append(d); ios.append(i)
            np.save(os.path.join(OUT,f"{mname}_{nm}.npy"),pred)
        print(f"{mname}: mean Dice {np.mean(ds):.3f} IoU {np.mean(ios):.3f}  ({', '.join(f'{x:.2f}' for x in ds)})")
        rows.append([mname,round(float(np.mean(ds)),3),round(float(np.mean(ios)),3)])
    with open(os.path.join(OUT,"tf_models_dice.csv"),"w",newline="",encoding="utf-8") as f:
        w=csv.writer(f); w.writerow(["model","mean_dice","mean_iou"]); [w.writerow(r) for r in rows]
    print("→",os.path.join(OUT,"tf_models_dice.csv"))
if __name__=="__main__": main()
