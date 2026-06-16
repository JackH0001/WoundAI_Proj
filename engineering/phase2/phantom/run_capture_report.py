# -*- coding: utf-8 -*-
"""一鍵：印刷傷口圖樣張拍照 → 自動校正＋量測＋對 GT 出報告。
用法：python run_capture_report.py --manifest caps.csv --images DIR --synth SYNTH_DIR --out OUTDIR
manifest 欄：image,gt_name[,sticker_x0,sticker_y0,sticker_x1,sticker_y1]。gt_name 對應 synth/*_meta.json。"""
import os, sys, csv, json, argparse, glob
import numpy as np, cv2
HERE=os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(HERE,".."))
from measure import measure_wound
def checker_bbox(img_rgb):
    g=cv2.cvtColor(img_rgb,cv2.COLOR_RGB2GRAY)
    for sc in (1,2):
        gg=cv2.resize(g,None,fx=sc,fy=sc) if sc>1 else g
        ok,c=cv2.findChessboardCornersSB(gg,(3,3))
        if ok:
            P=c.reshape(-1,2)/sc; x0,y0=P.min(0); x1,y1=P.max(0); pad=(x1-x0)/3*1.4
            return (int(x0-pad),int(y0-pad),int(x1+pad),int(y1+pad))
    return None
def paper_wound_mask(img_rgb, sticker_bbox=None):
    """印刷張為白底：傷口＝非白且有彩度/暗的最大中央連通區（排除貼紙、淡邊）。"""
    hsv=cv2.cvtColor(cv2.cvtColor(img_rgb,cv2.COLOR_RGB2BGR),cv2.COLOR_BGR2HSV)
    S,V=hsv[...,1].astype(int),hsv[...,2].astype(int)
    colored=((S>55)|(V<90)).astype(np.uint8)           # 非白紙
    H,W=colored.shape
    if sticker_bbox:
        x0,y0,x1,y1=[int(v) for v in sticker_bbox]; pad=int(0.02*max(H,W))
        colored[max(0,y0-pad):y1+pad,max(0,x0-pad):x1+pad]=0
    colored=cv2.morphologyEx(colored,cv2.MORPH_OPEN,np.ones((5,5),np.uint8))
    colored=cv2.morphologyEx(colored,cv2.MORPH_CLOSE,np.ones((15,15),np.uint8))
    n,lab,stats,cents=cv2.connectedComponentsWithStats(colored)
    if n<=1: return colored.astype(bool)
    cx,cy=W/2,H/2; best=None
    for i in range(1,n):
        a=stats[i,cv2.CC_STAT_AREA]
        if a<H*W*0.002: continue
        d=np.hypot(cents[i][0]-cx,cents[i][1]-cy)/np.hypot(cx,cy)
        sc=a*(1.3-d)
        if best is None or sc>best[0]: best=(sc,i)
    return (lab==best[1]) if best else colored.astype(bool)
def tissue_err(det,gt):
    return {k:round(abs(det.get(k,0)-gt.get(k,0)),3) for k in ("necrosis","slough","granulation","epithelial")}
def _montage(viz, out):
    import glob as _g
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    from matplotlib.font_manager import FontProperties
    fp=None
    for p in _g.glob("/usr/share/fonts/**/NotoSansCJK*",recursive=True)+_g.glob("/usr/share/fonts/**/NotoSerifCJK*",recursive=True): fp=FontProperties(fname=p); break
    if not fp: fp=FontProperties()
    n=len(viz); 
    if n==0: return
    fig,ax=plt.subplots(n,2,figsize=(9,3.2*n))
    if n==1: ax=ax.reshape(1,-1)
    for i,(img,mask,bbox,nm,meas,gt,ae) in enumerate(viz):
        b=img.copy()
        if bbox: cv2.rectangle(b,(int(bbox[0]),int(bbox[1])),(int(bbox[2]),int(bbox[3])),(0,200,255),max(2,img.shape[1]//300))
        ax[i,0].imshow(b); ax[i,0].set_title(f"{nm} 原圖+貼紙偵測",fontproperties=fp,fontsize=10); ax[i,0].axis("off")
        o=img.copy(); o[mask]=(o[mask]*0.5+np.array([0,210,90])*0.5).astype(np.uint8)
        ax[i,1].imshow(o); ax[i,1].set_title(f"量測 {meas} cm²  (真實 {gt}, 誤差 {ae}%)",fontproperties=fp,fontsize=10); ax[i,1].axis("off")
    plt.tight_layout(); plt.savefig(out,dpi=105,bbox_inches="tight"); plt.close(fig)
def run(manifest, images_dir, synth_dir, outdir):
    os.makedirs(outdir,exist_ok=True); rows=[]; viz=[]
    for r in csv.DictReader(open(manifest,encoding="utf-8")):
        img=cv2.cvtColor(cv2.imread(os.path.join(images_dir,r["image"])),cv2.COLOR_BGR2RGB)
        meta=json.load(open(os.path.join(synth_dir,r["gt_name"]+"_meta.json"),encoding="utf-8"))
        gt_area=meta["true_cm2"]; gt_t=meta["tissue_fraction_gt"]
        bbox=None
        if all(r.get(k) not in (None,"") for k in ("sticker_x0","sticker_y0","sticker_x1","sticker_y1")):
            bbox=tuple(int(float(r[k])) for k in ("sticker_x0","sticker_y0","sticker_x1","sticker_y1"))
        if bbox is None: bbox=checker_bbox(img)        # 自動偵測棋盤貼紙
        mask=paper_wound_mask(img, bbox)
        res=measure_wound(img, mask, sticker_mm=20.0)   # auto 棋盤校正(角點間距×4mm/格)；bbox 僅供排除貼紙
        meas=res.get("area_cm2"); c=res.get("classification",{}); det=c.get("tissue_proxy",{})
        ae=None if meas is None else round(abs(meas-gt_area)/gt_area*100,2)
        te=tissue_err(det,gt_t)
        viz.append((img,mask,bbox,r["gt_name"],meas,gt_area,ae))
        rows.append({"image":r["image"],"gt_name":r["gt_name"],"true_cm2":gt_area,"measured_cm2":meas,
                     "area_err_pct":ae,"method":res.get("method"),"dom":c.get("tissue_dominant","-"),
                     **{f"tissue_abserr_{k}":v for k,v in te.items()}})
    cols=list(rows[0].keys()) if rows else []
    with open(os.path.join(outdir,"capture_report.csv"),"w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=cols); w.writeheader(); [w.writerow(x) for x in rows]
    _montage(viz, os.path.join(outdir,"capture_montage.png"))
    aes=[x["area_err_pct"] for x in rows if x["area_err_pct"] is not None]
    summary={"n":len(rows),"n_measured":len(aes),
             "mean_area_err_pct":round(float(np.mean(aes)),2) if aes else None,
             "max_area_err_pct":round(float(np.max(aes)),2) if aes else None}
    json.dump(summary,open(os.path.join(outdir,"capture_summary.json"),"w"),ensure_ascii=False,indent=2)
    return {"rows":rows,"summary":summary}
if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--manifest",required=True); ap.add_argument("--images",required=True)
    ap.add_argument("--synth",required=True); ap.add_argument("--out",required=True)
    a=ap.parse_args(); res=run(a.manifest,a.images,a.synth,a.out)
    print("summary:",json.dumps(res["summary"],ensure_ascii=False))
    for x in res["rows"]: print(f"  {x['gt_name']:12} 真實{x['true_cm2']} 量測{x['measured_cm2']} 誤差{x['area_err_pct']}% 分型{x['dom']}")
