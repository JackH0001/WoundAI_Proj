# -*- coding: utf-8 -*-
"""面積驗證用『實心填色』傷口圖樣張（每頁一個+校正貼紙）。整個輪廓填單一飽和色→白紙上高對比→分割零誤差→面積準。"""
import os, json, glob
import numpy as np, cv2
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle, Circle
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.font_manager import FontProperties
S="/sessions/nifty-sweet-edison/mnt/WoundAI/phantom_samples/synthetic"; OUT="/sessions/nifty-sweet-edison/mnt/WoundAI/phantom_samples"; PXMM=8.0
FILL=(150,30,40)  # 飽和暗紅，高對比於白紙
def cjk():
    for p in glob.glob("/usr/share/fonts/**/NotoSansCJK*",recursive=True)+glob.glob("/usr/share/fonts/**/NotoSerifCJK*",recursive=True): return FontProperties(fname=p)
    return FontProperties()
FP=cjk()
def solid_crop(nm):
    m=cv2.imread(os.path.join(S,nm+"_woundmask.png"),cv2.IMREAD_GRAYSCALE)>127
    ys,xs=np.where(m); y0,y1,x0,x1=ys.min(),ys.max(),xs.min(),xs.max(); cm=m[y0:y1+1,x0:x1+1]
    crop=np.full((cm.shape[0],cm.shape[1],3),255,np.uint8); crop[cm]=FILL
    return crop,(x1-x0+1)/PXMM,(y1-y0+1)/PXMM,float(cm.sum())/(PXMM**2)/100.0  # 實際填色面積 cm²
def sticker(ax,cx,cy,mm=20.0):
    s=mm/5.0; x0,y0=cx-mm/2,cy-mm/2
    for r in range(5):
        for c in range(5): ax.add_patch(Rectangle((x0+c*s,y0+r*s),s,s,facecolor=("black" if (r+c)%2==0 else "white"),edgecolor="none",zorder=4))
    ax.add_patch(Rectangle((x0,y0),mm,mm,fill=False,edgecolor="#333",lw=0.5,zorder=5))
    for (dx,dy),cc in [((s/2,mm-s/2),"red"),((s/2,s/2),"blue"),((mm-s/2,mm-s/2),"yellow"),((mm-s/2,s/2),"green")]:
        ax.add_patch(Circle((x0+dx,y0+dy),s*0.3,color=cc,zorder=6))
    ax.text(cx,y0-3,"20mm 校正貼紙",ha="center",va="top",fontproperties=FP,fontsize=8)
NAME={"wound_1cm2":"1 cm²","wound_3cm2":"3 cm²","wound_5cm2":"5 cm²","wound_10cm2":"10 cm²","wound_16cm2":"16 cm²"}
pdf=PdfPages(os.path.join(OUT,"WoundAI_面積驗證_實心填色_5頁_A4.pdf")); manifest=[]
for nm in ["wound_1cm2","wound_3cm2","wound_5cm2","wound_10cm2","wound_16cm2"]:
    crop,wmm,hmm,area=solid_crop(nm); manifest.append((nm,round(area,3)))
    fig=plt.figure(figsize=(210/25.4,297/25.4)); ax=fig.add_axes([0,0,1,1]); ax.set_xlim(0,210); ax.set_ylim(0,297); ax.axis("off"); ax.set_aspect("equal")
    ax.text(105,285,f"WoundAI 面積驗證(實心) {NAME[nm]}（1:1・100% 列印）",ha="center",fontproperties=FP,fontsize=13)
    ax.text(105,278,"實心高對比；傷口與右側 20mm 貼紙同框拍攝；先用下方 50mm 比例尺核對 100%",ha="center",fontproperties=FP,fontsize=8.5,color="#555")
    ax.imshow(crop,extent=[78-wmm/2,78+wmm/2,165-hmm/2,165+hmm/2],origin="upper",zorder=3)
    ax.text(78,165-hmm/2-4,f"{NAME[nm]}（真實 {round(area,2)} cm²）SN:____",ha="center",va="top",fontproperties=FP,fontsize=10)
    sticker(ax,165,165,20.0)
    bx,by=20,32; ax.plot([bx,bx+50],[by,by],color="k",lw=1.3)
    for i in range(6): ax.plot([bx+i*10,bx+i*10],[by,by+(3 if i%5==0 else 2)],color="k",lw=1.0)
    ax.text(bx,by-4,"比例尺 50 mm（每格10mm）",fontproperties=FP,fontsize=8)
    pdf.savefig(fig); plt.close(fig)
pdf.close()
json.dump(dict(manifest),open(os.path.join(OUT,"面積驗證_實心_true_area.json"),"w"),ensure_ascii=False,indent=2)
print("OK 5頁實心面積驗證 PDF; true area:",manifest)
