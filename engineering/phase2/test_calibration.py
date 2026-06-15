"""校正測試：棋盤(最穩健)+assisted(精確)+color-corner+缺貼紙 graceful。"""
import sys, numpy as np, cv2
import calibration as cal
r=[]
def ck(n,c): r.append(bool(c)); print(("PASS " if c else "FAIL "),n)
# assisted bbox：100px / 20mm = 5.0 px/mm（精確）
ck("assisted bbox px/mm=5.0", abs(cal.calibrate_from_bbox((10,10,110,110),20.0)["px_per_mm"]-5.0)<1e-9)
ck("assisted 2pt px/mm=3.0", abs(cal.calibrate_from_two_points((0,0),(60,0),20.0)["px_per_mm"]-3.0)<1e-9)
ck("assisted 退化 -> found False", cal.calibrate_from_two_points((5,5),(5,5),20.0)["found"] is False)
# color-corner：合成（四角彩色小點，邊長 100 / 20mm = 5）
img=np.full((200,220,3),245,np.uint8)
for (x,y),col in [((50,50),(220,30,30)),((150,50),(30,30,220)),((50,150),(30,180,60)),((150,150),(230,200,40))]:
    cv2.circle(img,(x,y),5,col,-1)
cc=cal.detect_color_corner_sticker(img,20.0)
ck("color-corner 偵測合成", cc["found"])
ck("color-corner px/mm≈5(±5%)", cc["px_per_mm"] is not None and abs(cc["px_per_mm"]-5.0)/5.0<0.05)
# 棋盤偵測（最穩健）：合成 8x8、每格 20px、sticker 20mm/8格=2.5mm → 8 px/mm
def _checker(squares=8, sq=20):
    im=np.zeros((squares*sq,squares*sq),np.uint8)
    for rr in range(squares):
        for c in range(squares):
            if (rr+c)%2==0: im[rr*sq:(rr+1)*sq,c*sq:(c+1)*sq]=255
    return np.stack([im]*3,-1)
cb=cal.detect_checkerboard_sticker(_checker(8,20),20.0,pattern=(7,7),n_squares=8)
ck("棋盤偵測合成 target", cb["found"])
ck("棋盤 square_px≈20", abs(cb["square_px"]-20)<2)
ck("棋盤 px/mm≈8.0", abs(cb["px_per_mm"]-8.0)/8.0<0.05)
ck("棋盤缺target -> found False", cal.detect_checkerboard_sticker(np.full((120,120,3),200,np.uint8),pattern=(7,7))["found"] is False)
# 缺貼紙 graceful
ck("無貼紙 -> found False", cal.calibrate(np.full((150,150,3),250,np.uint8))["found"] is False)
ck("auto 低信心回 assisted 提示", cal.calibrate(np.full((150,150,3),250,np.uint8))["reason"]=="auto_low_confidence_use_assisted")
ck("決定性", cal.detect_checkerboard_sticker(_checker(8,20),pattern=(7,7))==cal.detect_checkerboard_sticker(_checker(8,20),pattern=(7,7)))
ok=sum(r); print(f"\n{ok}/{len(r)} PASS"); sys.exit(0 if ok==len(r) else 1)
