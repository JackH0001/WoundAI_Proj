"""校正測試：assisted（精確）+ auto color-corner（合成貼紙）+ 缺貼紙 graceful。"""
import sys, numpy as np, cv2
import calibration as cal
r=[]
def ck(n,c): r.append(bool(c)); print(("PASS " if c else "FAIL "),n)
# assisted bbox：100px 方形 / 20mm = 5.0 px/mm（精確）
d=cal.calibrate_from_bbox((10,10,110,110),sticker_mm=20.0)
ck("assisted bbox px/mm=5.0", abs(d["px_per_mm"]-5.0)<1e-9 and d["found"])
# assisted 2點：60px / 20mm = 3.0
d=cal.calibrate_from_two_points((0,0),(60,0),20.0)
ck("assisted 2pt px/mm=3.0", abs(d["px_per_mm"]-3.0)<1e-9)
ck("assisted 退化(同點) -> found False", cal.calibrate_from_two_points((5,5),(5,5),20.0)["found"] is False)
# 合成貼紙：四角彩色點，間距 100px / 20mm = 5.0
img=np.full((200,220,3),245,np.uint8)
for (x,y),col in [((50,50),(220,30,30)),((150,50),(30,30,220)),((50,150),(30,180,60)),((150,150),(230,200,40))]:
    cv2.circle(img,(x,y),5,col,-1)
d=cal.detect_color_corner_sticker(img,sticker_mm=20.0)
ck("color-corner 偵測到合成貼紙", d["found"])
ck("color-corner px/mm≈5.0(±5%)", d["px_per_mm"] is not None and abs(d["px_per_mm"]-5.0)/5.0<0.05)
ck("color-corner cv 很低(方正)", d["cv"]<0.05)
# 缺貼紙（全白）-> 不偽造
ck("無貼紙 -> found False", cal.calibrate(np.full((150,150,3),250,np.uint8))["found"] is False)
ck("auto 低信心回 assisted 提示", cal.calibrate(np.full((150,150,3),250,np.uint8))["reason"]=="auto_low_confidence_use_assisted")
# 決定性
ck("決定性", cal.detect_color_corner_sticker(img)==cal.detect_color_corner_sticker(img))
ok=sum(r); print(f"\n{ok}/{len(r)} PASS"); sys.exit(0 if ok==len(r) else 1)
