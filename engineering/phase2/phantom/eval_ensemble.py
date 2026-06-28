# -*- coding: utf-8 -*-
"""機率融合集成評估：A-UNet 與 UNet++ 各取機率圖 → 平均融合 → 門檻。
比二值聯集更穩、更可控。對 retrain_merged(image/labels 117 對) 與臨床 n=3 算 Dice/IoU。
GT 多為小/空遮罩 → 分『僅非空 GT』與『全部(含空)』兩種統計(誠實)。
本機 TF 執行：設 WOUNDAI_ARCHIVE 後 python eval_ensemble.py。
誠實提醒：retrain_merged 是微調用的訓練集，在其上驗證偏樂觀，僅供相對比較與健全性檢查。"""
import os, csv, glob, numpy as np, cv2
from PIL import Image
import eval_tf_models as E   # 重用 _get_keras/_load/_ascii_stage/prep

ARCHIVE = E.ARCHIVE
OUT = os.environ.get("OUT", "ensemble_out"); os.makedirs(OUT, exist_ok=True)
ENS = {"A-UNet": E.MODELS["A-UNet"], "UNet++": E.MODELS["UNet++"]}  # 互補雙模型
W_A = float(os.environ.get("W_A", "0.5"))   # A-UNet 權重；UNet++ = 1-W_A
THRS = [0.3, 0.4, 0.5, 0.6]

def dice(p,g):
    p=p.astype(bool); g=g.astype(bool); s=p.sum()+g.sum(); return 2*(p&g).sum()/s if s else 1.0
def iou(p,g):
    p=p.astype(bool); g=g.astype(bool); u=(p|g).sum(); return (p&g).sum()/u if u else 1.0

def prob(model, img, sz, norm):
    H,W=img.shape[:2]
    o=np.squeeze(model.predict(E.prep(img,sz,norm),verbose=0))
    if o.ndim==3: o=o[...,0]
    if o.min()<0 or o.max()>1: o=1.0/(1.0+np.exp(-np.clip(o,-30,30)))
    return cv2.resize(o,(W,H))

def load_pairs_dir(d):
    ims=sorted(os.listdir(os.path.join(d,"image")))
    return [(os.path.join(d,"image",n), os.path.join(d,"labels",n), os.path.splitext(n)[0]) for n in ims]

def load_pairs_clinical():
    D=os.path.join(ARCHIVE,"test_images","方形校正貼紙範例"); GTD=os.path.join(D,"labels_correct")
    g={"Foot_chronic_ulcer_校正貼紙":".jpg","Bedsore_方形校正貼紙範例":".jpeg","Bedsore_02_方形校正貼紙範例":".jpeg"}
    return [(os.path.join(D,k+v), os.path.join(GTD,k+".png"), k) for k,v in g.items()]

def gt_mask(p,H,W):
    g=np.asarray(Image.open(p).convert("L"))>127
    return g if g.shape==(H,W) else np.asarray(Image.fromarray(g.astype(np.uint8)*255).resize((W,H)))>127

def agg(vals, nonempty):
    """回傳 (全部均值, 僅非空均值, n非空)。"""
    arr=np.array(vals); ne=arr[nonempty]
    return float(arr.mean()), (float(ne.mean()) if len(ne) else float("nan")), int(nonempty.sum())

def run(name, pairs, models, sizes, norms):
    print("\n=== 資料集: %s (n=%d) ==="%(name,len(pairs)))
    pA=[]; pU=[]; gts=[]; sizes_hw=[]
    dA=[]; dU=[]; nonempty=[]
    for ip,gp,stem in pairs:
        img=np.asarray(Image.open(ip).convert("RGB")); H,W=img.shape[:2]
        a=prob(models["A-UNet"],img,256,"-11"); u=prob(models["UNet++"],img,256,"-11")
        g=gt_mask(gp,H,W)
        pA.append(a); pU.append(u); gts.append(g); sizes_hw.append((H,W))
        nonempty.append(g.sum()>0)
    nonempty=np.array(nonempty)
    # 各門檻掃描(集成)
    best=None
    for t in THRS:
        ds=[dice((W_A*pA[i]+(1-W_A)*pU[i])>t, gts[i]) for i in range(len(pairs))]
        ios=[iou((W_A*pA[i]+(1-W_A)*pU[i])>t, gts[i]) for i in range(len(pairs))]
        allm, nem, nn = agg(ds, nonempty); _, nei, _ = agg(ios, nonempty)
        print("  集成 thr=%.1f  Dice(非空) %.3f  IoU(非空) %.3f  Dice(全部) %.3f  [非空 n=%d]"%(t,nem,nei,allm,nn))
        if best is None or nem>best[1]: best=(t,nem,nei,ds,ios)
    # 單模型(thr0.5)對照
    for mn,pm in (("A-UNet",pA),("UNet++",pU)):
        ds=[dice(pm[i]>0.5, gts[i]) for i in range(len(pairs))]
        allm,nem,nn=agg(ds,nonempty); print("  %-7s thr0.5 Dice(非空) %.3f  Dice(全部) %.3f"%(mn,nem,allm))
    # 二值聯集對照
    dsu=[dice((pA[i]>0.5)|(pU[i]>0.5), gts[i]) for i in range(len(pairs))]
    allu,neu,_=agg(dsu,nonempty); print("  A∪U(二值) Dice(非空) %.3f  Dice(全部) %.3f"%(neu,allu))
    t,nem,nei,ds,ios=best
    print("  >> 集成最佳 thr=%.1f  Dice(非空)=%.3f  IoU(非空)=%.3f  (W_A=%.2f)"%(t,nem,nei,W_A))
    # 存 per-image CSV
    with open(os.path.join(OUT,"ensemble_%s.csv"%name),"w",newline="",encoding="utf-8") as f:
        w=csv.writer(f); w.writerow(["stem","gt_nonempty","ens_dice@best","ens_iou@best"])
        for i,(ip,gp,stem) in enumerate(pairs): w.writerow([stem,int(nonempty[i]),round(ds[i],3),round(ios[i],3)])
    return dict(name=name,pairs=pairs,pA=pA,pU=pU,gts=gts,best_t=t,ds=ds,nonempty=nonempty)

def montage(res, k=6):
    name=res["name"]; pairs=res["pairs"]; t=res["best_t"]
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    from matplotlib.font_manager import FontProperties
    cand=glob.glob("/usr/share/fonts/**/NotoSansCJK*",recursive=True)+glob.glob("/usr/share/fonts/**/NotoSerifCJK*",recursive=True)
    for w in ("C:/Windows/Fonts/msjh.ttc","C:/Windows/Fonts/msyh.ttc","C:/Windows/Fonts/simhei.ttf","C:/Windows/Fonts/mingliu.ttc"):
        if os.path.exists(w): cand.append(w)
    fp=FontProperties(fname=cand[0]) if cand else None
    idx=[i for i in range(len(pairs)) if res["nonempty"][i]]
    idx=sorted(idx, key=lambda i:res["ds"][i])[:k]  # 取最差 k 張(看弱點)
    if not idx: idx=list(range(min(k,len(pairs))))
    cols=[("原圖+GT",(255,70,70)),("A-UNet",(0,150,255)),("UNet++",(255,140,0)),("集成",(180,0,200))]
    fig,ax=plt.subplots(len(idx),len(cols),figsize=(4*len(cols),3.4*len(idx)))
    if len(idx)==1: ax=ax[None,:]
    def ov(img,m,c,a=0.45):
        o=img.copy(); cc=np.zeros_like(img); cc[:]=c; o[m]=(o[m]*(1-a)+cc[m]*a).astype(np.uint8); return o
    for r,i in enumerate(idx):
        ip,gp,stem=pairs[i]; img=np.asarray(Image.open(ip).convert("RGB")); g=res["gts"][i]
        a=res["pA"][i]>0.5; u=res["pU"][i]>0.5; e=(W_A*res["pA"][i]+(1-W_A)*res["pU"][i])>t
        ms=[g,a,u,e]
        for j,(cn,col) in enumerate(cols):
            lab=cn if j==0 else "%s D%.2f"%(cn,dice(ms[j],g))
            ax[r,j].imshow(ov(img,ms[j],col)); ax[r,j].axis("off")
            ax[r,j].set_title("%s %s"%(stem,lab),fontproperties=fp,fontsize=9)
    fig.suptitle("集成(機率融合) 目視 - %s (最差%d張) thr=%.1f"%(name,len(idx),t),fontproperties=fp,fontsize=12,y=0.999)
    plt.tight_layout(rect=[0,0,1,0.99]); fpth=os.path.join(OUT,"ensemble_%s.png"%name); plt.savefig(fpth,dpi=90,bbox_inches="tight"); plt.close()
    print("  目視 →",fpth)

def main():
    keras=E._get_keras()
    models={}
    for i,(mn,(rel,sz,norm)) in enumerate(ENS.items()):
        mp=os.path.join(ARCHIVE,rel); lp=E._ascii_stage(mp,"e%d"%i)
        models[mn]=E._load(keras,lp); print("已載入",mn)
    # ① 臨床 n=3
    r1=run("clinical_n3", load_pairs_clinical(), models, None, None); montage(r1,k=3)
    # ③ retrain_merged
    rm=os.path.join(ARCHIVE,"批次驗證工具","retrain_merged")
    if os.path.isdir(rm):
        r2=run("retrain_merged", load_pairs_dir(rm), models, None, None); montage(r2,k=6)
    else:
        print("找不到 retrain_merged:",rm)
    print("\n完成。CSV/目視 →", OUT)

if __name__=="__main__": main()
