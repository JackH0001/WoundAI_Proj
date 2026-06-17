# -*- coding: utf-8 -*-
"""每張 A4 一個標準傷口 + 旁邊校正貼紙（共 5 頁），利於逐張正確攝影量測。1:1。"""
import os, json, glob
import numpy as np, cv2
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle, Circle
from matplotlib.backends.backend_pdf import PdfPages
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
    crop=img[y0:y1+1,x0:x1+1].copy(); cm=m[y0:y1+1,x0:x1+1]; crop[~cm]=255
    return crop,(x1-x0+1)/PXMM,(y1-y0+1)/PXMM
def sticker(ax,cx,cy,mm=20.0):
    s=mm/5.0; x0,y0=cx-mm/2,cy-mm/2
    for r in range(5):
        for c in range(5):
            ax.add_patch(Rectangle((x0+c*s,y0+r*s),s,s,facecolor=("black" if (r+c)%2==0 else "white"),edgecolor="none",zorder=4))
    ax.add_patch(Rectangle((x0,y0),mm,mm,fill=False,edgecolor="#333",lw=0.5,zorder=5))
    for (dx,dy),cc in [((s/2,mm-s/2),"red"),((s/2,s/2),"blue"),((mm-s/2,mm-s/2),"yellow"),((mm-s/2,s/2),"green")]:
        ax.add_patch(Circle((x0+dx,y0+dy),s*0.3,color=cc,zorder=6))
    ax.text(cx,y0-3,"20mm 校正貼紙",ha="center",va="top",fontproperties=FP,fontsize=8)
items=["wound_1cm2","wound_3cm2","wound_5cm2","wound_10cm2","wound_16cm2"]
NAME={"wound_1cm2":"1 cm²","wound_3cm2":"3 cm²","wound_5cm2":"5 cm²","wound_10cm2":"10 cm²","wound_16cm2":"16 cm²"}
pdf=PdfPages(os.path.join(OUT,"WoundAI_印刷傷口圖樣張_單張5頁_A4.pdf"))
for nm in items:
    crop,wmm,hmm=wound_on_white(nm); meta=json.load(open(os.path.join(S,nm+"_meta.json"),encoding="utf-8")); g=meta["tissue_fraction_gt"]
    fig=plt.figure(figsize=(210/25.4,297/25.4)); ax=fig.add_axes([0,0,1,1]); ax.set_xlim(0,210); ax.set_ylim(0,297); ax.axis("off"); ax.set_aspect("equal")
    ax.text(105,285,f"WoundAI 標準傷口圖樣 {NAME[nm]}（1:1・列印選『實際大小/100%』）",ha="center",fontproperties=FP,fontsize=13)
    ax.text(105,278,"傷口與右側 20mm 校正貼紙同框拍攝；均勻光、與紙面正對；先用下方 50mm 比例尺核對 100%",ha="center",fontproperties=FP,fontsize=8.5,color="#555")
    wcx,wcy=78,165
    ax.imshow(crop,extent=[wcx-wmm/2,wcx+wmm/2,wcy-hmm/2,wcy+hmm/2],origin="upper",zorder=3)
    ax.text(wcx,wcy-hmm/2-4,f"{NAME[nm]}　SN:________",ha="center",va="top",fontproperties=FP,fontsize=10)
    sticker(ax,165,165,20.0)
    # GT 標註(右下角小字，供對照；拍攝時可裁掉不影響)
    ax.text(150,250,f"GT（對照用）\n真實面積：{meta['true_cm2']} cm²\n組織比例：\n 壞死 {g['necrosis']*100:.0f}%\n 腐肉 {g['slough']*100:.0f}%\n 肉芽 {g['granulation']*100:.0f}%\n 上皮 {g['epithelial']*100:.0f}%",
            ha="left",va="top",fontproperties=FP,fontsize=8,bbox=dict(boxstyle="round",fc="#F2F7F7",ec="#0B5E63"))
    bx,by=20,32; ax.plot([bx,bx+50],[by,by],color="k",lw=1.3)
    for i in range(6): ax.plot([bx+i*10,bx+i*10],[by,by+(3 if i%5==0 else 2)],color="k",lw=1.0)
    ax.text(bx,by-4,"比例尺 50 mm（每格 10mm）",fontproperties=FP,fontsize=8)
    pdf.savefig(fig); plt.close(fig)
pdf.close()
print("OK -> phantom_samples/WoundAI_印刷傷口圖樣張_單張5頁_A4.pdf")
