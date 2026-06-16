# -*- coding: utf-8 -*-
"""平面印刷傷口圖樣張：擬真組織色傷口(1:1 真實尺寸) + 20mm 校正貼紙 + 50mm 比例尺 + 已知面積/組織標註。
列印 100% 後拍照→跑管線→對照標註(GT)。資料源：phantom_samples/synthetic（含 GT 面積與組織比例）。"""
import os, json, glob
import numpy as np, cv2
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle, Circle
from matplotlib.font_manager import FontProperties
S="/sessions/nifty-sweet-edison/mnt/WoundAI/phantom_samples/synthetic"
OUT="/sessions/nifty-sweet-edison/mnt/WoundAI/phantom_samples"
PXMM=8.0
def cjk():
    for p in glob.glob("/usr/share/fonts/**/NotoSansCJK*",recursive=True)+glob.glob("/usr/share/fonts/**/NotoSerifCJK*",recursive=True): return FontProperties(fname=p)
    return FontProperties()
FP=cjk()
def wound_on_white(nm):
    img=cv2.cvtColor(cv2.imread(os.path.join(S,nm+"_image.png")),cv2.COLOR_BGR2RGB)
    m=cv2.imread(os.path.join(S,nm+"_woundmask.png"),cv2.IMREAD_GRAYSCALE)>127
    ys,xs=np.where(m); y0,y1,x0,x1=ys.min(),ys.max(),xs.min(),xs.max()
    crop=img[y0:y1+1,x0:x1+1].copy(); cm=m[y0:y1+1,x0:x1+1]
    crop[~cm]=255
    return crop, (x1-x0+1)/PXMM, (y1-y0+1)/PXMM   # mm
def sticker(ax,cx,cy,mm=20.0):
    s=mm/5.0; x0,y0=cx-mm/2,cy-mm/2
    for r in range(5):
        for c in range(5):
            col="black" if (r+c)%2==0 else "white"
            ax.add_patch(Rectangle((x0+c*s,y0+r*s),s,s,facecolor=col,edgecolor="none"))
    ax.add_patch(Rectangle((x0,y0),mm,mm,fill=False,edgecolor="#333",lw=0.5))
    for (dx,dy),cc in [((s/2,mm-s/2),"red"),((s/2,s/2),"blue"),((mm-s/2,mm-s/2),"yellow"),((mm-s/2,s/2),"green")]:
        ax.add_patch(Circle((x0+dx,y0+dy),s*0.3,color=cc))
    ax.text(cx,y0-2,"20mm 校正貼紙(列印用)",ha="center",va="top",fontproperties=FP,fontsize=7)
fig=plt.figure(figsize=(210/25.4,297/25.4)); ax=fig.add_axes([0,0,1,1]); ax.set_xlim(0,210); ax.set_ylim(0,297); ax.axis("off"); ax.set_aspect("equal")
ax.text(105,289,"WoundAI 平面印刷傷口圖樣張（擬真組織色・1:1）— 列印請選『實際大小/100%』",ha="center",fontproperties=FP,fontsize=11)
ax.text(105,283,"用下方 50mm 比例尺與 20mm 校正方塊核對 100% 後再使用；拍照於均勻光、與紙面正對",ha="center",fontproperties=FP,fontsize=8,color="#555")
items=[("wound_1cm2",38,250),("wound_3cm2",95,250),("wound_5cm2",160,248),("wound_10cm2",60,180),("wound_16cm2",150,178)]
for k,(nm,cx,cy) in enumerate(items):
    crop,wmm,hmm=wound_on_white(nm)
    ax.imshow(crop,extent=[cx-wmm/2,cx+wmm/2,cy-hmm/2,cy+hmm/2],origin="upper",zorder=3)
    meta=json.load(open(os.path.join(S,nm+"_meta.json"),encoding="utf-8")); g=meta["tissue_fraction_gt"]
    lab=(f"{nm.replace('wound_','').replace('cm2',' cm²')}  SN:____\n"
         f"GT 組織 壞死{g['necrosis']*100:.0f}/腐肉{g['slough']*100:.0f}/肉芽{g['granulation']*100:.0f}/上皮{g['epithelial']*100:.0f}%")
    ax.text(cx,cy-hmm/2-3,lab,ha="center",va="top",fontproperties=FP,fontsize=6.5)
# 50mm ruler
bx,by=20,28; ax.plot([bx,bx+50],[by,by],color="k",lw=1.3)
for i in range(6): ax.plot([bx+i*10,bx+i*10],[by,by+(3 if i%5==0 else 2)],color="k",lw=1.0)
ax.text(bx,by-4,"比例尺 50 mm（每格10mm）",fontproperties=FP,fontsize=7)
# sticker
sticker(ax,150,40,20.0)
fig.savefig(os.path.join(OUT,"WoundAI_印刷傷口圖樣張_A4.pdf"),dpi=150)
fig.savefig(os.path.join(OUT,"WoundAI_印刷傷口圖樣張_A4_preview.png"),dpi=130)
print("OK -> phantom_samples/WoundAI_印刷傷口圖樣張_A4.pdf")
