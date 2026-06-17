# -*- coding: utf-8 -*-
"""ArUco 版面積驗證印刷張（每頁一傷口+18mm ArUco 校正貼紙），1:1。自含(以 seed 重建輪廓，不依賴 OneDrive)。"""
import os, json, glob
import numpy as np, cv2
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.font_manager import FontProperties
OUT="/sessions/nifty-sweet-edison/mnt/dev/WoundAI_work/phantom"; os.makedirs(OUT,exist_ok=True)
PXMM=8.0; MARK_MM=18.0; DICT=cv2.aruco.DICT_4X4_50; MID=7; FILL=(150,30,40)
def cjk():
    for p in glob.glob("/usr/share/fonts/**/NotoSansCJK*",recursive=True)+glob.glob("/usr/share/fonts/**/NotoSerifCJK*",recursive=True): return FontProperties(fname=p)
    return FontProperties()
FP=cjk()
def shoelace(x,y): return 0.5*abs(np.dot(x,np.roll(y,-1))-np.dot(y,np.roll(x,-1)))
def contour(target,seed,n=240):
    rng=np.random.default_rng(seed); ang=np.linspace(0,2*np.pi,n,endpoint=False); r=np.ones(n)
    for k in (2,3,5,7,11): r+=rng.uniform(0.04,0.16)*np.sin(k*ang+rng.uniform(0,2*np.pi))
    r=np.clip(r,0.5,None); x,y=r*np.cos(ang),r*np.sin(ang); s=np.sqrt(target*100/shoelace(x,y)); return x*s,y*s
def solid(target,seed):
    x,y=contour(target,seed); xp=(x-x.min())*PXMM; yp=(y-y.min())*PXMM
    W=int(xp.max())+1; H=int(yp.max())+1; img=np.full((H,W,3),255,np.uint8)
    cv2.fillPoly(img,[np.column_stack([xp,yp]).astype(np.int32)],FILL)
    m=np.zeros((H,W),np.uint8); cv2.fillPoly(m,[np.column_stack([xp,yp]).astype(np.int32)],1)
    return img, W/PXMM, H/PXMM, float(m.sum())/(PXMM**2)/100.0
ITEMS=[("1cm2",1,11),("3cm2",3,23),("5cm2",5,35),("10cm2",10,57),("16cm2",16,79)]
mk=cv2.aruco.generateImageMarker(cv2.aruco.getPredefinedDictionary(DICT),MID,600)
pdf=PdfPages(os.path.join(OUT,"WoundAI_面積驗證_ArUco_RGBY_5頁_A4.pdf")); manifest={}
for nm,tgt,seed in ITEMS:
    crop,wmm,hmm,area=solid(tgt,seed); manifest["wound_"+nm]=round(area,3)
    fig=plt.figure(figsize=(210/25.4,297/25.4)); ax=fig.add_axes([0,0,1,1]); ax.set_xlim(0,210); ax.set_ylim(0,297); ax.axis("off"); ax.set_aspect("equal")
    ax.text(105,285,f"WoundAI 面積驗證(ArUco) {tgt} cm²（1:1・100% 列印）",ha="center",fontproperties=FP,fontsize=13)
    ax.text(105,278,"傷口與右側 ArUco+RGBY 複合貼紙同框拍攝(可斜拍)；先用 50mm 比例尺核對 100%",ha="center",fontproperties=FP,fontsize=8.5,color="#555")
    ax.imshow(crop,extent=[78-wmm/2,78+wmm/2,165-hmm/2,165+hmm/2],origin="upper",zorder=3)
    ax.text(78,165-hmm/2-4,f"{tgt} cm²（真實 {round(area,2)} cm²）SN:____",ha="center",va="top",fontproperties=FP,fontsize=10)
    # 複合貼紙：白底 24mm + 中央 ArUco 18mm(幾何) + 四角 RGBY 校色點(色彩/白平衡) + 中下 18%灰
    cxs,cys=165.0,165.0; foot=24.0
    ax.add_patch(Rectangle((cxs-foot/2,cys-foot/2),foot,foot,facecolor="white",edgecolor="#999",lw=0.4,zorder=3))
    ax.imshow(cv2.cvtColor(mk,cv2.COLOR_GRAY2RGB),extent=[cxs-MARK_MM/2,cxs+MARK_MM/2,cys-MARK_MM/2,cys+MARK_MM/2],origin="upper",zorder=4)
    from matplotlib.patches import Circle as _C
    off=foot/2-2.0; dots=[(-off,off,(1,0,0)),(off,off,(0,0,1)),(-off,-off,(0,0.6,0)),(off,-off,(1,1,0))]  # R左上 B右上 G左下 Y右下
    for dx,dy,col in dots: ax.add_patch(_C((cxs+dx,cys+dy),1.6,facecolor=col,edgecolor="#666",lw=0.3,zorder=6))
    ax.add_patch(_C((cxs,cys-off),1.6,facecolor=(0.74,0.74,0.74),edgecolor="#666",lw=0.3,zorder=6))  # 18%灰(下緣)
    ax.text(cxs,cys-foot/2-2,f"ArUco {int(MARK_MM)}mm(幾何)+RGBY/灰(校色)  id{MID}",ha="center",va="top",fontproperties=FP,fontsize=6.5)
    bx,by=20,32; ax.plot([bx,bx+50],[by,by],color="k",lw=1.3)
    for i in range(6): ax.plot([bx+i*10,bx+i*10],[by,by+(3 if i%5==0 else 2)],color="k",lw=1.0)
    ax.text(bx,by-4,"比例尺 50 mm（每格10mm）",fontproperties=FP,fontsize=8)
    pdf.savefig(fig); plt.close(fig)
pdf.close(); json.dump(manifest,open(os.path.join(OUT,"ArUco_true_area.json"),"w"),ensure_ascii=False,indent=2)
print("OK 5頁 ArUco PDF + true area:",manifest)
