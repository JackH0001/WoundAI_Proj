"""目視驗證用：對 (影像, 遮罩) 例子渲染 原圖 | 分割疊圖 | 組織分型疊圖 + 規則式分類/嚴重度/治療。
用法：python demo_render.py OUT.png "img1|mask1|標題1" "img2|mask2|標題2" ..."""
import sys, os, glob
import numpy as np
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from PIL import Image
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wound_classifier import classify, tissue_classmap
def cjk():
    for pat in ("NotoSansCJK*","NotoSerifCJK*"):
        g = glob.glob(f"/usr/share/fonts/**/{pat}", recursive=True)
        if g: return FontProperties(fname=sorted(g)[0])
    return FontProperties()
FP = cjk()
TC = {1:(30,30,30), 2:(235,200,40), 3:(210,55,55), 4:(150,150,150)}  # 壞死/腐肉/肉芽/其他
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
def main():
    out = sys.argv[1]; items = [a.split("|") for a in sys.argv[2:]]
    n = len(items); fig, ax = plt.subplots(n, 4, figsize=(16, 4.1*n))
    if n==1: ax = ax.reshape(1,-1)
    for i,(ip,mp,title) in enumerate(items):
        img = load(ip); m = loadmask(mp)
        if m.shape != img.shape[:2]:
            m = np.asarray(Image.fromarray(m.astype(np.uint8)*255).resize((img.shape[1],img.shape[0]))) > 127
        cm = tissue_classmap(img, m); res = classify(img, m)
        ax[i,0].imshow(img); ax[i,0].set_title(f"① 原圖：{title}", fontproperties=FP, fontsize=12)
        ax[i,1].imshow(ov_mask(img,m)); ax[i,1].set_title("② 分割遮罩（此例＝人工標註GT）", fontproperties=FP, fontsize=12)
        ax[i,2].imshow(ov_tissue(img,cm)); ax[i,2].set_title("③ 組織分型疊圖（黑壞死/黃腐肉/紅肉芽/灰其他）", fontproperties=FP, fontsize=11)
        t = res["tissue_proxy"]; sev = res["severity"]
        txt = (f"規則式分類結果（輔助・需醫師確認）\n\n"
               f"面積：{res['area_px']:,} px（未校正，cm² 需校正件）\n\n"
               f"組織比例（色彩粗估）：\n  壞死 {t['necrosis']*100:4.1f}%\n  腐肉 {t['slough']*100:4.1f}%\n"
               f"  肉芽 {t['granulation']*100:4.1f}%\n  其他 {t['other']*100:4.1f}%\n\n"
               f"組織分型：{res['tissue_dominant']}\n\n"
               f"嚴重度（規則式）：grade {sev['grade']}/4\n\n"
               f"治療建議：\n  {res['treatment']['recommendation']}")
        ax[i,3].axis("off"); ax[i,3].text(0.0,0.98,txt,fontproperties=FP,fontsize=11,va="top",ha="left",
                                          bbox=dict(boxstyle="round",fc="#F2F7F7",ec="#0B5E63"))
        for j in range(3): ax[i,j].axis("off")
    fig.suptitle("WoundAI Track B：可解釋粗分類實例（規則式・色彩啟發式組織估計・需醫師確認・未臨床校正）",
                 fontproperties=FP, fontsize=14, y=0.997)
    plt.tight_layout(rect=[0,0,1,0.985]); plt.savefig(out, dpi=110, bbox_inches="tight"); print("OK ->", out)
if __name__ == "__main__": main()
