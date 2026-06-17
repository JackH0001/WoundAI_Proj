"""ArUco 校正測試：偵測四角序、透視傾斜下仍能準確回復面積（優於 naive px/mm）。"""
import sys, numpy as np, cv2
import aruco_calibrate as ac
r=[]
def ck(n,c): r.append(bool(c)); print(("PASS " if c else "FAIL "),n)
PXMM=10.0; MARK_MM=18.0
def scene(area_cm2):
    W=1400; img=np.full((W,W,3),255,np.uint8); m=np.zeros((W,W),np.uint8)
    rad=int(round((area_cm2*100/np.pi)**0.5*PXMM)); cv2.circle(img,(450,500),rad,(60,40,160),-1); cv2.circle(m,(450,500),rad,1,-1)
    mk=cv2.aruco.generateImageMarker(cv2.aruco.getPredefinedDictionary(ac._DICT),7,int(MARK_MM*PXMM)); s=int(MARK_MM*PXMM)
    img[850:850+s,820:820+s]=cv2.cvtColor(mk,cv2.COLOR_GRAY2BGR)
    return img,m>0,round(np.pi*(rad/PXMM)**2/100.0,3)
def warp(img,m,t):
    H,W=img.shape[:2]; M=cv2.getPerspectiveTransform(np.float32([[0,0],[W,0],[W,H],[0,H]]),np.float32([[W*t,0],[W*(1-t),0],[W,H],[0,H]]))
    return cv2.warpPerspective(img,M,(W,H),borderValue=(255,255,255)), cv2.warpPerspective(m.astype(np.uint8),M,(W,H),flags=cv2.INTER_NEAREST)>0
def naive(m,c): 
    e=[np.linalg.norm(c[i]-c[(i+1)%4]) for i in range(4)]; return float(m.sum())/((np.mean(e)/MARK_MM)**2)/100.0
img,m,true=scene(5.0)
d=ac.detect_marker(cv2.cvtColor(img,cv2.COLOR_BGR2RGB))
ck("正視偵測到 ArUco", d is not None)
ck("四角為 4x2", d[0].shape==(4,2))
ck("正視 ArUco 面積誤差<3%", abs(ac.measure_area_cm2(m,d[0],MARK_MM)-true)/true<0.03)
# 傾斜 ~60°
wi,wm=warp(img,m,0.22); d2=ac.detect_marker(cv2.cvtColor(wi,cv2.COLOR_BGR2RGB))
ck("傾斜仍偵測到 ArUco", d2 is not None)
e_homo=abs(ac.measure_area_cm2(wm,d2[0],MARK_MM)-true)/true
e_naive=abs(naive(wm,d2[0])-true)/true
ck("傾斜 ArUco 誤差<12%", e_homo<0.12)
ck("傾斜 ArUco 明顯優於 naive px/mm", e_homo < e_naive*0.5)
ck("缺 ArUco -> None", ac.detect_marker(np.full((200,200,3),255,np.uint8)) is None)
ok=sum(r); print(f"\n{ok}/{len(r)} PASS"); sys.exit(0 if ok==len(r) else 1)
