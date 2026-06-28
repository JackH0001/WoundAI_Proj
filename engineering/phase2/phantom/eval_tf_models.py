# -*- coding: utf-8 -*-
"""在『有 TensorFlow 的環境』評估微調 Keras .h5 分割模型（UNet256 retrain2 / A-UNet / UNet++ / UNet-DS）。
對範例臨床照計算 Dice/IoU vs 人工 GT，並輸出遮罩 .npy + CSV。turnkey：設 WOUNDAI_ARCHIVE 後 python eval_tf_models.py。
Keras 3 相容：TF2.16+ 內建 Keras 3 常無法直接讀 Keras 2 的 .h5；本檔優先用 tf-keras(pip install tf-keras)。"""
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
    else: x=(r/255.0-np.array([0.485,0.456,0.406]))/np.array([0.229,0.224,0.225])
    return x[None].astype(np.float32)

def _get_keras():
    """優先用 tf-keras(Keras 2 相容)載入舊 .h5；TF2.16+ 內建 Keras 3 常無法直接讀 Keras 2 的 .h5。"""
    os.environ.setdefault("TF_USE_LEGACY_KERAS", "1")
    try:
        import tf_keras as keras  # pip install tf-keras
        print("[keras] 使用 tf-keras (Keras 2 相容)")
        return keras
    except Exception:
        import tensorflow as tf
        try: ver = __import__("keras").__version__
        except Exception: ver = "?"
        print("[keras] 用 tf.keras (Keras %s)；若舊 .h5 載入失敗請: pip install tf-keras" % ver)
        return tf.keras

def _load(keras, mp):
    """多策略載入：compile=False -> safe_mode=False -> 常見自訂層 custom_objects。"""
    last=None
    for kw in ({"compile":False}, {"compile":False,"safe_mode":False}):
        try: return keras.models.load_model(mp, **kw)
        except TypeError:
            try: return keras.models.load_model(mp, compile=False)
            except Exception as e: last=e
        except Exception as e: last=e
    co={}
    for n in ("dice_loss","bce_dice_loss","iou","dice_coef","DiceLoss","AttentionGate"):
        co[n]=(lambda *a,**k:0.0)
    try: return keras.models.load_model(mp, compile=False, custom_objects=co, safe_mode=False)
    except Exception as e: last=e
    raise last

def _ascii_stage(mp, tag):
    """HDF5/h5py 在 Windows 無法處理非 ASCII 路徑(中文會亂碼成 not found)。
    Python 能正確讀中文路徑，故先複製到純英文暫存路徑再交給 h5py。"""
    import tempfile, shutil, hashlib
    if mp.isascii():
        return mp
    d = os.path.join(tempfile.gettempdir(), "woundai_eval_models")
    os.makedirs(d, exist_ok=True)
    h = hashlib.md5(mp.encode("utf-8")).hexdigest()[:8]
    dst = os.path.join(d, "%s_%s.h5" % (tag, h))
    if not os.path.exists(dst) or os.path.getsize(dst) != os.path.getsize(mp):
        shutil.copy2(mp, dst)
    return dst
def main():
    keras=_get_keras()
    rows=[]
    for i,(mname,(rel,sz,norm)) in enumerate(MODELS.items()):
        mp=os.path.join(ARCHIVE,rel)
        if not os.path.exists(mp): print("缺檔",mp); continue
        try: lp=_ascii_stage(mp, "m%d"%i)
        except Exception as e: print(mname,"stage 失敗:",repr(e)[:160]); continue
        try: model=_load(keras,lp)
        except Exception as e: print(mname,"load 失敗:",repr(e)[:200]); continue
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
            np.save(os.path.join(OUT,"%s_%s.npy"%(mname,nm)),pred)
        print("%s: mean Dice %.3f IoU %.3f  (%s)"%(mname,np.mean(ds),np.mean(ios),", ".join("%.2f"%x for x in ds)))
        rows.append([mname,round(float(np.mean(ds)),3),round(float(np.mean(ios)),3)])
    with open(os.path.join(OUT,"tf_models_dice.csv"),"w",newline="",encoding="utf-8") as f:
        w=csv.writer(f); w.writerow(["model","mean_dice","mean_iou"]); [w.writerow(r) for r in rows]
    print("->",os.path.join(OUT,"tf_models_dice.csv"))

if __name__=="__main__": main()
