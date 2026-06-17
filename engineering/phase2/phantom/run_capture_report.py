# -*- coding: utf-8 -*-
"""一鍵：印刷傷口圖樣張拍照 → 校正＋量測＋對 GT 出報告。
校正優先序：ArUco(透視校正, 斜拍可用) → 棋盤 px/mm(無透視)。分割：實心紅(紙內填洞最大連通)。
用法：python run_capture_report.py --manifest caps.csv --images DIR --gt gt.json --out OUTDIR
manifest 欄：image,gt_name。gt.json：{"wound_5cm2":5.065,...}。"""
import os, sys, csv, json, argparse, glob
import numpy as np, cv2
HERE=os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(HERE,".."))
import aruco_calibrate as ac
from measure import measure_wound
ARUCO_MM = 18.0
def checker_bbox(img):
    g=cv2.cvtColor(img,cv2.COLOR_RGB2GRAY)
    for sc in (1,2):
        gg=cv2.resize(g,None,fx=sc,fy=sc) if sc>1 else g
        ok,c=cv2.findChessboardCornersSB(gg,(3,3))
        if ok:
            P=c.reshape(-1,2)/sc; x0,y0=P.min(0); x1,y1=P.max(0); pad=(x1-x0)/3*1.4
            return (int(x0-pad),int(y0-pad),int(x1+pad),int(y1+pad))
    return None
def solid_red_mask(img, exclude_box=None):
    H,W=img.shape[:2]; hsv=cv2.cvtColor(cv2.cvtColor(img,cv2.COLOR_RGB2BGR),cv2.COLOR_BGR2HSV)
    S,V=hsv[...,1].astype(int),hsv[...,2].astype(int); R,G,B=img[...,0].astype(int),img[...,1].astype(int),img[...,2].astype(int)
    paper=cv2.morphologyEx(((V>150)&(S<60)).astype(np.uint8),cv2.MORPH_CLOSE,np.ones((25,25),np.uint8))
    n,lab,st,_=cv2.connectedComponentsWithStats(paper); pm=(lab==(1+int(np.argmax(st[1:,cv2.CC_STAT_AREA])))).astype(np.uint8)
    cnts,_=cv2.findContours(pm,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)        # 填補傷口洞→整張紙
    pm=(cv2.fillPoly(np.zeros_like(pm),[max(cnts,key=cv2.contourArea)],1).astype(bool)) if cnts else pm.astype(bool)
    red=(pm&(R>G+22)&(R>B+18)&(S>50)&(V>40)).astype(np.uint8)
    if exclude_box is not None:
        x0,y0,x1,y1=[int(v) for v in exclude_box]; p=int(0.035*max(H,W)); red[max(0,y0-p):y1+p,max(0,x0-p):x1+p]=0
    k=max(7,W//180); red=cv2.morphologyEx(red,cv2.MORPH_CLOSE,np.ones((k,k),np.uint8)); red=cv2.morphologyEx(red,cv2.MORPH_OPEN,np.ones((5,5),np.uint8))
    n,lab,st,_=cv2.connectedComponentsWithStats(red)
    return (lab==max(range(1,n),key=lambda i:st[i,cv2.CC_STAT_AREA])) if n>1 else red.astype(bool)
def load_rgb(path, max_dim=2400):
    if path.lower().endswith(".heic"):
        import pillow_heif; pillow_heif.register_heif_opener()
        from PIL import Image; img=np.asarray(Image.open(path).convert("RGB"))
    else:
        img=cv2.cvtColor(cv2.imread(path),cv2.COLOR_BGR2RGB)
    sc=max_dim/max(img.shape[:2])
    return cv2.resize(img,(int(img.shape[1]*sc),int(img.shape[0]*sc))) if sc<1 else img
def measure_one(img):
    """回 (area_cm2, method, exclude_box_for_mask)。ArUco 優先(透視校正)。"""
    d=ac.detect_marker(img)
    if d is not None:
        corners,_=d; box=(corners[:,0].min(),corners[:,1].min(),corners[:,0].max(),corners[:,1].max())
        return ("aruco", corners, box)
    return ("checker", None, checker_bbox(img))
def run(manifest, images_dir, gt_json, outdir):
    os.makedirs(outdir,exist_ok=True); GT=json.load(open(gt_json,encoding="utf-8")); rows=[]; viz=[]
    for r in csv.DictReader(open(manifest,encoding="utf-8")):
        img=load_rgb(os.path.join(images_dir,r["image"])); true=GT[r["gt_name"]]
        method,corners,box=measure_one(img); mask=solid_red_mask(img,box)
        if method=="aruco":
            area=round(ac.measure_area_cm2(mask,corners,ARUCO_MM),2)
        else:
            area=measure_wound(img,mask,sticker_mm=20.0).get("area_cm2")
        ae=None if not area else round(abs(area-true)/true*100,2)
        rows.append({"image":r["image"],"gt":r["gt_name"],"true_cm2":true,"measured_cm2":area,"area_err_pct":ae,"calib":method})
        viz.append((img,mask,box,r["image"],area,true,ae,method)); print(r["image"],method,"meas",area,"true",true,"err",ae,"%",flush=True)
    with open(os.path.join(outdir,"capture_report.csv"),"w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0].keys())); w.writeheader(); [w.writerow(x) for x in rows]
    aes=[x["area_err_pct"] for x in rows if x["area_err_pct"] is not None]
    summary={"n":len(rows),"mean_area_err_pct":round(float(np.mean(aes)),2) if aes else None,"calib_used":list({x["calib"] for x in rows})}
    json.dump(summary,open(os.path.join(outdir,"capture_summary.json"),"w"),ensure_ascii=False,indent=2)
    try:
        import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
        from matplotlib.font_manager import FontProperties
        fp=FontProperties(fname=(glob.glob("/usr/share/fonts/**/NotoSansCJK*",recursive=True)+glob.glob("/usr/share/fonts/**/NotoSerifCJK*",recursive=True)+[None])[0])
        nn=len(viz); fig,ax=plt.subplots((nn+1)//2,2,figsize=(11,3.0*((nn+1)//2))); ax=np.atleast_1d(ax).ravel()
        for i,(img,m,box,base,meas,true,ae,method) in enumerate(viz):
            o=img.copy(); o[m]=(o[m]*0.45+np.array([0,255,0])*0.55).astype(np.uint8)
            if box is not None: cv2.rectangle(o,(int(box[0]),int(box[1])),(int(box[2]),int(box[3])),(0,200,255),6)
            sc=560/o.shape[0]; o=cv2.resize(o,(int(o.shape[1]*sc),560))
            ax[i].imshow(o); ax[i].axis("off"); ax[i].set_title(f"{base} [{method}]\n量測 {meas} cm² (真實 {true}, 誤差 {ae}%)",fontproperties=fp,fontsize=9)
        for j in range(nn,len(ax)): ax[j].axis("off")
        plt.tight_layout(); plt.savefig(os.path.join(outdir,"capture_montage.png"),dpi=95,bbox_inches="tight"); plt.close()
    except Exception as e: print("montage skip:",e)
    return {"rows":rows,"summary":summary}
if __name__=="__main__":
    ap=argparse.ArgumentParser(); ap.add_argument("--manifest",required=True); ap.add_argument("--images",required=True)
    ap.add_argument("--gt",required=True); ap.add_argument("--out",required=True); a=ap.parse_args()
    res=run(a.manifest,a.images,a.gt,a.out); print("summary:",json.dumps(res["summary"],ensure_ascii=False))
