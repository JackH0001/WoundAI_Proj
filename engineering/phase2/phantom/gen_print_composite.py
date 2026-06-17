# -*- coding: utf-8 -*-
"""面積驗證印刷張(每頁一傷口+複合校正貼紙)，圓形30mm與方形20mm兩版，1:1。
貼紙取 stickers/ 之 crisp 複合貼紙(ArUco+RGBY+凸點)。自含(seed 重建輪廓)。"""
import os, json, glob
import numpy as np, cv2
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.font_manager import FontProperties
WK="/sessions/nifty-sweet-edison/mnt/dev/WoundAI_work"; OUT=WK+"/phantom"; STK=OUT+"/stickers"
PXMM=8.0; FILL=(150,30,40)
def cjk():
    for p in glob.glob("/usr/share/fonts/**/NotoSansCJK*",recursive=True)+glob.glob("/usr/share/fonts/**/NotoSerifCJK*",recursive=True): return FontProperties(fname=p)
    return FontProperties()
FP=cjk()
def shoelace(x,y): return 0.5*abs(np.dot(x,np.roll(y,-1))-np.dot(y,np.roll(x,-1)))
def solid(tgt,seed,n=240):
    rng=np.random.default_rng(seed); ang=np.linspace(0,2*np.pi,n,endpoint=False); r=np.ones(n)
    for k in (2,3,5,7,11): r+=rng.uniform(0.04,0.16)*np.sin(k*ang+rng.uniform(0,2*np.pi))
    r=np.clip(r,0.5,None); x,y=r*np.cos(ang),r*np.sin(ang); s=np.sqrt(tgt*100/shoelace(x,y)); x*=s; y*=s
    xp=(x-x.min())*PXMM; yp=(y-y.min())*PXMM; W=int(xp.max())+1; H=int(yp.max())+1
    img=np.full((H,W,3),255,np.uint8); m=np.zeros((H,W),np.uint8)
    poly=np.column_stack([xp,yp]).astype(np.int32); cv2.fillPoly(img,[poly],FILL); cv2.fillPoly(m,[poly],1)
    return img,W/PXMM,H/PXMM,float(m.sum())/(PXMM**2)/100.0
ITEMS=[(1,11),(3,23),(5,35),(10,57),(16,79)]
def make(shape,foot_mm,stk_png,tag):
    stk=cv2.cvtColor(cv2.imread(stk_png),cv2.COLOR_BGR2RGB)
    pdf=PdfPages(os.path.join(OUT,f"WoundAI_面積驗證_{tag}_5頁_A4.pdf")); man={}
    for tgt,seed in ITEMS:
        crop,wmm,hmm,area=solid(tgt,seed); man[f"wound_{tgt}cm2"]=round(area,3)
        fig=plt.figure(figsize=(210/25.4,297/25.4)); ax=fig.add_axes([0,0,1,1]); ax.set_xlim(0,210); ax.set_ylim(0,297); ax.axis("off"); ax.set_aspect("equal")
        ax.text(105,285,f"WoundAI 面積驗證({tag}) {tgt} cm²（1:1・100% 列印）",ha="center",fontproperties=FP,fontsize=13)
        ax.text(105,278,f"傷口與右側 {tag} 複合貼紙同框(可斜拍)；先用 50mm 比例尺核對 100%",ha="center",fontproperties=FP,fontsize=8.5,color="#555")
        ax.imshow(crop,extent=[78-wmm/2,78+wmm/2,165-hmm/2,165+hmm/2],origin="upper",zorder=3)
        ax.text(78,165-hmm/2-4,f"{tgt} cm²（真實 {round(area,2)} cm²）SN:____",ha="center",va="top",fontproperties=FP,fontsize=10)
        ax.imshow(stk,extent=[165-foot_mm/2,165+foot_mm/2,165-foot_mm/2,165+foot_mm/2],origin="upper",interpolation="none",zorder=4)
        ax.text(165,165-foot_mm/2-2,f"{tag} 複合貼紙(ArUco+RGBY+凸點)",ha="center",va="top",fontproperties=FP,fontsize=7)
        bx,by=20,32; ax.plot([bx,bx+50],[by,by],color="k",lw=1.3)
        for i in range(6): ax.plot([bx+i*10,bx+i*10],[by,by+(3 if i%5==0 else 2)],color="k",lw=1.0)
        ax.text(bx,by-4,"比例尺 50 mm（每格10mm）",fontproperties=FP,fontsize=8)
        pdf.savefig(fig,dpi=200); plt.close(fig)
    pdf.close(); json.dump(man,open(os.path.join(OUT,f"{tag}_true_area.json"),"w"),ensure_ascii=False,indent=2)
    return man
m1=make("circle",30.0,STK+"/sticker_circle_30mm.png","圓形30mm")
m2=make("square",20.0,STK+"/sticker_square_20mm.png","方形20mm")
print("OK 兩版 5 頁；true area:",m1)
