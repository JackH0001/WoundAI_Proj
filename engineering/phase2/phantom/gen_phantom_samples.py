# -*- coding: utf-8 -*-
"""產生標準面積『仿傷口不規則輪廓』樣品（1/3/5/10/16 cm²）+ 印刷參照比例尺。
輸出：個別 SVG（雷射切割/印刷, 1:1 mm）、A4 列印 PDF（1:1）、預覽 PNG、true_area_manifest.csv。
面積以 shoelace 公式縮放到精確目標值並驗證。"""
import os, csv, glob
import numpy as np
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon as MplPoly, Rectangle
from matplotlib.font_manager import FontProperties
OUT = "/sessions/nifty-sweet-edison/mnt/WoundAI/phantom_samples"
os.makedirs(OUT, exist_ok=True)
def cjk():
    for p in glob.glob("/usr/share/fonts/**/NotoSansCJK*", recursive=True)+glob.glob("/usr/share/fonts/**/NotoSerifCJK*", recursive=True):
        return FontProperties(fname=sorted(p if isinstance(p,list) else [p])[0])
    return FontProperties()
FP = cjk()
TARGETS = [(1,1),(3,3),(5,5),(10,10),(16,16)]  # (label cm², seed)
def shoelace(x,y): return 0.5*abs(np.dot(x,np.roll(y,-1))-np.dot(y,np.roll(x,-1)))
def wound_contour(target_cm2, seed, n=200):
    """平滑不規則仿傷口輪廓；縮放到精確 target 面積(mm²)。回傳 mm 座標(置中於 0)。"""
    target_mm2 = target_cm2*100.0
    rng = np.random.default_rng(seed)
    ang = np.linspace(0, 2*np.pi, n, endpoint=False)
    r = np.ones(n)
    for k in (2,3,5,7,11):
        r += rng.uniform(0.04,0.16)*np.sin(k*ang+rng.uniform(0,2*np.pi))
    r = np.clip(r, 0.5, None)
    x, y = r*np.cos(ang), r*np.sin(ang)
    s = np.sqrt(target_mm2/shoelace(x,y))           # 縮放到精確面積
    return x*s, y*s
SHAPES = {t:(wound_contour(t,seed)) for t,seed in TARGETS}
# ---- 驗證面積 ----
print("=== 面積驗證(shoelace) ===")
for t,_ in TARGETS:
    x,y = SHAPES[t]; a = shoelace(x,y)/100.0
    print(f"  目標 {t} cm²  →  實際 {a:.4f} cm²  (誤差 {abs(a-t)/t*100:.3f}%)  外接 {x.max()-x.min():.1f}x{y.max()-y.min():.1f} mm")
# ---- 個別 SVG（雷射切割 cut=紅 hairline / engrave=label）----
def emit_svg(t):
    x,y = SHAPES[t]; w=x.max()-x.min(); h=y.max()-y.min()
    M=8; W=w+2*M; H=h+2*M+12
    px=x-x.min()+M; py=(y.max()-y)+M   # SVG y 向下
    d="M "+" L ".join(f"{a:.3f},{b:.3f}" for a,b in zip(px,py))+" Z"
    svg=f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W:.2f}mm" height="{H:.2f}mm" viewBox="0 0 {W:.2f} {H:.2f}">
<!-- WoundAI phantom 標準件 {t} cm² ; 1:1 mm ; CUT=紅 stroke 0.05mm, ENGRAVE=label -->
<path d="{d}" fill="#f2d9d4" stroke="#e00000" stroke-width="0.05"/>
<line x1="{M}" y1="{H-6}" x2="{M+10}" y2="{H-6}" stroke="#0050c0" stroke-width="0.3"/>
<text x="{M}" y="{H-7.5}" font-size="3" fill="#0050c0">10mm</text>
<text x="{M+14}" y="{H-5}" font-size="3.5" fill="#0050c0">WoundAI phantom {t}cm2  SN:______</text>
</svg>'''
    open(os.path.join(OUT,f"wound_phantom_{t}cm2.svg"),"w",encoding="utf-8").write(svg)
for t,_ in TARGETS: emit_svg(t)
# ---- A4 列印 PDF + 預覽 PNG（1:1）----
def draw_sheet(path, dpi=150):
    fig=plt.figure(figsize=(210/25.4,297/25.4)); ax=fig.add_axes([0,0,1,1]); ax.set_xlim(0,210); ax.set_ylim(0,297); ax.axis("off"); ax.set_aspect("equal")
    ax.text(105,288,"WoundAI 2D 面積標準件（仿傷口輪廓）— 1:1 列印請選『實際大小/100%』",ha="center",fontproperties=FP,fontsize=11)
    ax.text(105,282,"列印後用尺規核對下方 50mm 比例尺與 20mm 校正方塊，確認比例正確再裁切",ha="center",fontproperties=FP,fontsize=8,color="#555")
    pos=[(60,235),(150,235),(60,150),(150,150),(105,60)]  # 5 件位置(mm)
    for (t,_),(cx,cy) in zip(TARGETS,pos):
        x,y=SHAPES[t]
        ax.add_patch(MplPoly(np.column_stack([x+cx,y+cy]),closed=True,facecolor="#f2d9d4",edgecolor="#e00000",lw=1.2))
        ax.text(cx,cy-(np.ptp(y)/2)-6,f"{t} cm²",ha="center",fontproperties=FP,fontsize=10)
        ax.text(cx,cy-(np.ptp(y)/2)-11,"SN:______",ha="center",fontproperties=FP,fontsize=7,color="#555")
    # 50mm 比例尺(10mm 刻度)
    bx,by=20,25; ax.plot([bx,bx+50],[by,by],color="k",lw=1.5)
    for i in range(6): ax.plot([bx+i*10,bx+i*10],[by,by+(3 if i%5==0 else 2)],color="k",lw=1.2)
    ax.text(bx,by-4,"比例尺 50 mm（每格10mm）",fontproperties=FP,fontsize=8)
    # 20mm 校正方塊
    sx,sy=150,18; ax.add_patch(Rectangle((sx,sy),20,20,fill=False,edgecolor="k",lw=1.0))
    ax.text(sx+10,sy-3,"20mm 校正方塊",ha="center",fontproperties=FP,fontsize=8)
    fig.savefig(path,dpi=dpi); plt.close(fig)
draw_sheet(os.path.join(OUT,"wound_phantom_A4_sheet.pdf"))
draw_sheet(os.path.join(OUT,"wound_phantom_A4_preview.png"))
# ---- true area manifest ----
with open(os.path.join(OUT,"true_area_manifest.csv"),"w",newline="",encoding="utf-8") as f:
    w=csv.writer(f); w.writerow(["shape","target_cm2","actual_cm2_design","bbox_w_mm","bbox_h_mm","svg_file"])
    for t,_ in TARGETS:
        x,y=SHAPES[t]; w.writerow([f"wound_{t}cm2",t,round(shoelace(x,y)/100.0,4),round(np.ptp(x),1),round(np.ptp(y),1),f"wound_phantom_{t}cm2.svg"])
print("\n輸出檔案：")
for fn in sorted(os.listdir(OUT)): print("  ",fn)
