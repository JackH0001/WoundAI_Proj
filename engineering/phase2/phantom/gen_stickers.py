# -*- coding: utf-8 -*-
"""ArUco+RGBY 複合校正貼紙（crisp cv2 合成，避免抗鋸齒破壞 ArUco）：
20mm 方形(制式) 與 30mm 圓形(精度優先)。中央 ArUco=幾何；四角 RGBY+灰=校色；灰點=凸點(0.15mm)供 LiDAR。"""
import os, sys, numpy as np, cv2
from PIL import Image
OUT="/sessions/nifty-sweet-edison/mnt/dev/WoundAI_work/phantom/stickers"; os.makedirs(OUT,exist_ok=True)
DICT=cv2.aruco.DICT_4X4_50; MID=7; SCALE=48  # px/mm
RGB={"R":(255,0,0),"B":(0,0,255),"G":(0,160,0),"Y":(255,210,0),"K":(189,189,189)}
def aruco(a_mm):
    n=int(round(a_mm*SCALE)); m=cv2.aruco.generateImageMarker(cv2.aruco.getPredefinedDictionary(DICT),MID,n)
    return cv2.cvtColor(m,cv2.COLOR_GRAY2RGB)
def dot(img,cx,cy,col,r_mm=1.4): cv2.circle(img,(int(cx*SCALE),int(cy*SCALE)),int(r_mm*SCALE),col,-1); cv2.circle(img,(int(cx*SCALE),int(cy*SCALE)),int(r_mm*SCALE),(110,110,110),max(1,SCALE//40))
def save(img,name,mm):
    d=int(SCALE*25.4)
    Image.fromarray(img).save(os.path.join(OUT,name+".png"),dpi=(d,d))  # 1:1 列印(嵌入 dpi)
def square20():
    mm=20; W=mm*SCALE; img=np.full((W,W,3),255,np.uint8)  # 不畫外框(避免被誤判為marker quad)；裁切線交印廠
    a=13.0; ap=aruco(a); o=int((mm-a)/2*SCALE); img[o:o+ap.shape[0],o:o+ap.shape[1]]=ap
    for col,(x,y) in zip("RBGY",[(2.0,2.0),(18.0,2.0),(2.0,18.0),(18.0,18.0)]): dot(img,x,y,RGB[col],1.0)
    dot(img,mm/2,18.4,RGB["K"],0.9)   # 灰=凸點(LiDAR)
    save(img,"sticker_square_20mm",mm)
def circle30():
    mm=30; W=mm*SCALE; img=np.full((W,W,3),255,np.uint8); cv2.circle(img,(W//2,W//2),W//2-2,(90,90,90),max(1,SCALE//30))
    a=20.0; ap=aruco(a); o=int((mm-a)/2*SCALE); img[o:o+ap.shape[0],o:o+ap.shape[1]]=ap
    for col,(x,y) in zip("RBGY",[(4.8,4.8),(25.2,4.8),(4.8,25.2),(25.2,25.2)]): dot(img,x,y,RGB[col],1.5)
    dot(img,mm/2,27.6,RGB["K"],1.5)   # 灰=凸點(LiDAR)
    save(img,"sticker_circle_30mm",mm)
square20(); circle30()
sys.path.insert(0,"/sessions/nifty-sweet-edison/mnt/dev/WoundAI_Proj/engineering/phase2"); import aruco_calibrate as ac
for nm in ("sticker_square_20mm","sticker_circle_30mm"):
    im=cv2.cvtColor(cv2.imread(os.path.join(OUT,nm+".png")),cv2.COLOR_BGR2RGB)
    d=ac.detect_marker(im); print(nm,im.shape[:2],"ArUco:",None if d is None else "id="+str(d[1]))
