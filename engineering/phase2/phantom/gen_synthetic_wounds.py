# -*- coding: utf-8 -*-
"""擬真合成傷口影像產生器（含標準化組織分類 GT + 內嵌校正貼紙）。
標準組織：granulation 肉芽(紅) / slough 腐肉(黃) / necrosis 壞死(黑) / epithelial 上皮(粉，邊緣)。
每件輸出：擬真 RGB 影像、傷口二值遮罩、組織分類圖、metadata(真實面積cm² + 組織比例 + 貼紙 bbox/px_mm)。"""
import os, json, csv
import numpy as np, cv2
OUT = "/sessions/nifty-sweet-edison/mnt/WoundAI/phantom_samples/synthetic"
os.makedirs(OUT, exist_ok=True)
PXMM = 8.0
SKINS=[(222,184,160),(205,165,140),(238,200,178),(190,150,128),(215,178,150)]  # 多樣膚色                       # 解析度 px/mm
NEC, SLO, GRA, EPI = 1, 2, 3, 4
COL = {NEC:(45,35,32), SLO:(200,175,95), GRA:(165,52,55), EPI:(225,170,168)}  # RGB 基色
# 各件設計組織傾向（核心區 necrosis/slough 比例；其餘為 granulation；epithelial 為邊緣環）
MIX = {1:(0.00,0.10), 3:(0.00,0.30), 5:(0.10,0.30), 10:(0.20,0.30), 16:(0.45,0.25)}
def shoelace(x,y): return 0.5*abs(np.dot(x,np.roll(y,-1))-np.dot(y,np.roll(x,-1)))
def wound_contour(target_cm2, seed, n=240):
    rng=np.random.default_rng(seed); ang=np.linspace(0,2*np.pi,n,endpoint=False); r=np.ones(n)
    for k in (2,3,5,7,11): r+=rng.uniform(0.04,0.16)*np.sin(k*ang+rng.uniform(0,2*np.pi))
    r=np.clip(r,0.5,None); x,y=r*np.cos(ang),r*np.sin(ang)
    s=np.sqrt(target_cm2*100.0/shoelace(x,y)); return x*s,y*s
def lowfreq(shape, rng, sigma=14):
    n=rng.random(shape).astype(np.float32); n=cv2.GaussianBlur(n,(0,0),sigma)
    n-=n.min(); n/=(n.max()+1e-6); return n
def jitter(img, mask, base, rng, amp=18):
    noise=(rng.standard_normal(img.shape[:2]).astype(np.float32))
    noise=cv2.GaussianBlur(noise,(0,0),2)*amp
    for c in range(3): img[...,c]=np.where(mask, np.clip(base[c]+noise,0,255), img[...,c])
def render_sticker(canvas, x0, y0, mm=20.0):
    """畫 20mm 棋盤校正貼紙(5x5)+四角彩色點，回傳 bbox(px)。"""
    side=int(mm*PXMM); s=side//5
    cv2.rectangle(canvas,(x0-4,y0-4),(x0+side+4,y0+side+4),(245,245,245),-1)
    for r in range(5):
        for c in range(5):
            col=(20,20,20) if (r+c)%2==0 else (250,250,250)
            cv2.rectangle(canvas,(x0+c*s,y0+r*s),(x0+(c+1)*s,y0+(r+1)*s),col,-1)
    for (cx,cy),cc in [((x0+side//2,y0+s//2),(230,30,30)),((x0+s//2,y0+side//2),(30,30,230)),
                       ((x0+side-s//2,y0+side//2),(230,210,40)),((x0+side//2,y0+side-s//2),(40,190,70))]:
        cv2.circle(canvas,(cx,cy),max(3,s//3),cc,-1)
    return (x0,y0,x0+side,y0+side)  # 20mm=棋盤外緣(不含白邊)
def make_case(target_cm2, seed):
    rng=np.random.default_rng(seed)
    x,y=wound_contour(target_cm2,seed)
    wpx=(x*PXMM); hpx=(y*PXMM)
    W=int(np.ptp(wpx)); H=int(np.ptp(hpx))
    margin=int(18*PXMM); gap=int(10*PXMM); stick=int(20*PXMM)+8
    cw=margin*2+W+gap+stick; ch=margin*2+max(H,stick)
    # 皮膚底
    skin=np.zeros((ch,cw,3),np.uint8); base=np.array(SKINS[seed%len(SKINS)],np.float32)
    tex=cv2.GaussianBlur(rng.standard_normal((ch,cw)).astype(np.float32),(0,0),6)*10
    for c in range(3): skin[...,c]=np.clip(base[c]+tex+rng.uniform(-6,6),0,255)
    # 傷口遮罩
    cx0=margin-int(wpx.min()); cy0=margin-int(hpx.min())
    poly=np.column_stack([wpx+cx0,hpx+cy0]).astype(np.int32)
    mask=np.zeros((ch,cw),np.uint8); cv2.fillPoly(mask,[poly],1); mb=mask.astype(bool)
    # 柔和投影：位移+模糊的暗遮罩壓低皮膚
    sh=np.zeros((ch,cw),np.float32); cv2.fillPoly(sh,[poly+np.array([int(3*PXMM*0.3),int(3*PXMM*0.3)])],1.0)
    sh=cv2.GaussianBlur(sh,(0,0),6)*0.35
    skin=(skin.astype(np.float32)*(1-sh[...,None])).clip(0,255).astype(np.uint8)
    # 組織分類
    dist=cv2.distanceTransform(mask,cv2.DIST_L2,5)
    rim=mb&(dist< max(2.0,2.0*PXMM*0.4))   # 上皮邊緣環
    core=mb&~rim
    f=lowfreq((ch,cw),rng,sigma=int(6+target_cm2)); cls=np.zeros((ch,cw),np.uint8)
    mn,ms=MIX[target_cm2]
    if core.sum()>0:
        vals=f[core]; qn=np.quantile(vals,mn) if mn>0 else -1; qs=np.quantile(vals,min(mn+ms,0.99))
        cls[core&(f<=qn)]=NEC; cls[core&(f>qn)&(f<=qs)]=SLO; cls[core&(f>qs)]=GRA
    cls[rim]=EPI
    # 上色 + 紋理 + 濕潤反光
    img=skin.copy()
    for code in (GRA,SLO,NEC,EPI):
        sel=cls==code
        if sel.any(): jitter(img,sel,COL[code],rng,amp=14 if code!=NEC else 8)
    sheen=cv2.GaussianBlur((rng.random((ch,cw))>0.985).astype(np.float32),(0,0),1.5)
    for c in range(3): img[...,c]=np.clip(img[...,c].astype(np.float32)+sheen*120*mb,0,255)
    # 傷口周圍紅暈
    ring=(cv2.dilate(mask,np.ones((9,9),np.uint8),iterations=3).astype(bool))&~mb
    img[ring]=(img[ring].astype(np.float32)*[1.05,0.92,0.9]).clip(0,255).astype(np.uint8)
    # 校正貼紙
    bbox=render_sticker(img, margin+W+gap, margin, mm=20.0)
    # 不均光照：方向光 + 暗角(vignette)
    yy,xx=np.mgrid[0:ch,0:cw]
    lx,ly=rng.uniform(0.2,0.8)*cw, rng.uniform(0.2,0.8)*ch
    d=np.sqrt((xx-lx)**2+(yy-ly)**2); d/=d.max()
    light=(1.18-0.5*d).astype(np.float32)
    img=np.clip(img.astype(np.float32)*light[...,None],0,255).astype(np.uint8)
    # 鏡面反光亮點(傷口濕潤)
    for _ in range(rng.integers(2,5)):
        ys,xs=np.where(mb)
        if len(xs)==0: break
        i=rng.integers(len(xs)); cv2.circle(img,(xs[i],ys[i]),rng.integers(2,5),(255,255,255),-1)
    img=cv2.GaussianBlur(img,(0,0),0.7)
    # 攝影感：輕微模糊+雜訊
    img=cv2.GaussianBlur(img,(0,0),0.6)
    img=np.clip(img.astype(np.float32)+rng.standard_normal(img.shape)*3,0,255).astype(np.uint8)
    # GT 組織比例（核心+rim 全傷口）
    tot=int(mb.sum()); frac={k:round(float((cls==v).sum())/tot,4) for k,v in [("necrosis",NEC),("slough",SLO),("granulation",GRA),("epithelial",EPI)]}
    return img, mask*255, cls, bbox, frac
def cls_color(cls):
    out=np.zeros((*cls.shape,3),np.uint8)
    for code,c in COL.items(): out[cls==code]=c
    return out
rows=[]
for t,seed in [(1,11),(3,23),(5,35),(10,57),(16,79)]:
    img,mask,cls,bbox,frac=make_case(t,seed)
    nm=f"wound_{t}cm2"
    cv2.imwrite(os.path.join(OUT,nm+"_image.png"),cv2.cvtColor(img,cv2.COLOR_RGB2BGR))
    cv2.imwrite(os.path.join(OUT,nm+"_woundmask.png"),mask)
    cv2.imwrite(os.path.join(OUT,nm+"_tissue.png"),cv2.cvtColor(cls_color(cls),cv2.COLOR_RGB2BGR))
    meta={"name":nm,"true_cm2":t,"px_per_mm":PXMM,"sticker_mm":20.0,"sticker_bbox_px":list(map(int,bbox)),"tissue_fraction_gt":frac}
    json.dump(meta,open(os.path.join(OUT,nm+"_meta.json"),"w"),ensure_ascii=False,indent=2)
    rows.append((nm,t,bbox,frac))
    print(f"{nm}: bbox={bbox} 組織GT={frac}")
# 寫 validation manifest（直接可餵 phantom_validation.run_from_manifest）
with open(os.path.join(OUT,"manifest.csv"),"w",newline="",encoding="utf-8") as f:
    w=csv.writer(f); w.writerow(["name","image_path","mask_path","true_cm2","sticker_x0","sticker_y0","sticker_x1","sticker_y1","sticker_mm","notes"])
    for nm,t,bb,_ in rows: w.writerow([nm,nm+"_image.png",nm+"_woundmask.png",t,bb[0],bb[1],bb[2],bb[3],20,"synthetic"])
print("\n輸出於 phantom_samples/synthetic/")
