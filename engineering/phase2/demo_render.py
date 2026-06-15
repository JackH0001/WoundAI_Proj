"""目視驗證渲染：原圖(+校正貼紙偵測框) | 分割疊圖 | 組織分型疊圖 | 規則式結論(含校正後 cm²)。
用法：python demo_render.py OUT.png "img|mask|標題" ...  [--mm 20]"""
import sys, os, glob
import numpy as np
import cv2
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from PIL import Image
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wound_classifier import classify, tissue_classmap
import calibration as cal
def cjk():
    for pat in ("NotoSansCJK*", "NotoSerifCJK*"):
        g = glob.glob(f"/usr/share/fonts/**/{pat}", recursive=True)
        if g: return FontProperties(fname=sorted(g)[0])
    return FontProperties()
FP = cjk()
TC = {1:(30,30,30), 2:(235,200,40), 3:(210,55,55), 4:(150,150,150)}
def load(p): return np.asarray(Image.open(p).convert("RGB"))
def loadmask(p): return np.asarray(Image.open(p).convert("L")) > 127
def ov_mask(img, m, a=0.45):
    o = img.copy(); g = np.zeros_like(img); g[...,1]=255
    o[m] = (img[m]*(1-a)+g[m]*a).astype(np.uint8); return o
def ov_tissue(img, cm, a=0.55):
    o = img.copy()
    for code,col in TC.items():
        sel = cm==code
        if sel.any(): o[sel] = (img[sel]*(1-a)+np.array(col)*a).astype(np.uint8)
    return o
def ov_sticker(img, c):
    o = img.copy()
    if not c.get("found"): return o
    if c["method"]=="square":
        cv2.polylines(o, [np.array(c["quad"],np.int32)], True, (0,255,255), max(2,o.shape[1]//300))
    else:
        cv2.circle(o, (int(c["center"][0]),int(c["center"][1])), int(c["radius_px"]), (0,255,255), max(2,o.shape[1]//300))
    return o
def main():
    args=[a for a in sys.argv[1:]]; mm=20.0
    if "--mm" in args: i=args.index("--mm"); mm=float(args[i+1]); del args[i:i+2]
    out=args[0]; items=[a.split("|") for a in args[1:]]
    n=len(items); fig,ax=plt.subplots(n,4,figsize=(16,4.1*n))
    if n==1: ax=ax.reshape(1,-1)
    for i,it in enumerate(items):
        ip,mp,title=it[0],it[1],it[2]; abox=[int(v) for v in it[3].split(",")] if len(it)>3 and it[3] else None
        img=load(ip); m=loadmask(mp)
        if m.shape!=img.shape[:2]:
            m=np.asarray(Image.fromarray(m.astype(np.uint8)*255).resize((img.shape[1],img.shape[0])))>127
        c=cal.calibrate(img, sticker_mm=mm, assist_bbox=abox); ppm=c["px_per_mm"]
        cm=tissue_classmap(img,m); res=classify(img,m,px_per_mm=ppm)
        base0=img.copy()
        if abox is not None:
            cv2.rectangle(base0,(abox[0],abox[1]),(abox[2],abox[3]),(0,255,255),max(2,img.shape[1]//300))
        else:
            base0=ov_sticker(img,c)
        ax[i,0].imshow(base0); ax[i,0].set_title(f"① 原圖＋校正貼紙偵測：{title}", fontproperties=FP, fontsize=12)
        ax[i,1].imshow(ov_mask(img,m)); ax[i,1].set_title("② 分割遮罩（此例＝人工標註GT）", fontproperties=FP, fontsize=12)
        ax[i,2].imshow(ov_tissue(img,cm)); ax[i,2].set_title("③ 組織分型（黑壞死/黃腐肉/紅肉芽/灰其他）", fontproperties=FP, fontsize=11)
        t=res["tissue_proxy"]; sev=res["severity"]
        mname={"assisted_bbox":"框選(assisted)","assisted_2pt":"兩點(assisted)","color_corner":"自動四角點","circle":"自動圓形","square":"自動方形"}.get(c.get("method"),"未偵測")
        cal_line = (f"校正貼紙 20mm（{mname}），px/mm={ppm:.2f}" if c.get("found") else "校正貼紙：未偵測到，建議框選校正")
        area_line = (f"面積：{res['area_px']:,} px ＝ {res['area_cm2']:.2f} cm²"
                     if res["area_cm2"] is not None else f"面積：{res['area_px']:,} px（未校正）")
        txt=(f"規則式分類（輔助・需醫師確認）\n\n{cal_line}\n{area_line}\n\n"
             f"組織比例（色彩粗估）：\n  壞死 {t['necrosis']*100:4.1f}%  腐肉 {t['slough']*100:4.1f}%\n"
             f"  肉芽 {t['granulation']*100:4.1f}%  其他 {t['other']*100:4.1f}%\n\n"
             f"組織分型：{res['tissue_dominant']}\n嚴重度（規則式）：grade {sev['grade']}/4\n\n"
             f"治療建議：\n  {res['treatment']['recommendation']}")
        ax[i,3].axis("off"); ax[i,3].text(0.0,0.98,txt,fontproperties=FP,fontsize=10.5,va="top",ha="left",
            bbox=dict(boxstyle="round",fc="#F2F7F7",ec="#0B5E63"))
        for j in range(3): ax[i,j].axis("off")
    fig.suptitle("WoundAI：校正貼紙→精確面積(cm²) + 可解釋分類（規則式・需醫師確認・未臨床校正驗證）",
                 fontproperties=FP, fontsize=14, y=0.997)
    plt.tight_layout(rect=[0,0,1,0.985]); plt.savefig(out,dpi=110,bbox_inches="tight"); print("OK ->",out)
if __name__=="__main__": main()
