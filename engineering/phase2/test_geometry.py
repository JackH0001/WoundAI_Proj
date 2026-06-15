"""透視校正面積測試：平面一致 + 傾斜後仍可回復真實面積。"""
import sys, numpy as np, cv2
from geometry import measure_area_cm2_from_quad, homography_image_to_metric, measure_area_cm2
r=[]
def ck(n,c): r.append(bool(c)); print(("PASS " if c else "FAIL "),n)
# 平面：貼紙 100px 方形(=20mm → 5px/mm)；傷口 50x50px 方塊 → 真實 (50/5)mm=10mm 邊 → 1.0 cm²
quad=np.array([[100,100],[200,100],[200,200],[100,200]],np.float32)
mask=np.zeros((400,400),bool); mask[250:300,250:300]=True   # 50x50 px
a=measure_area_cm2_from_quad(mask,quad,sticker_mm=20.0)
ck("平面面積≈1.0 cm²", abs(a-1.0)/1.0<0.03)
# 傾斜：以一個透視 Hd 同時變形 貼紙四角 與 傷口遮罩，校正後仍應≈1.0 cm²
Hd=cv2.getPerspectiveTransform(
    np.array([[0,0],[400,0],[400,400],[0,400]],np.float32),
    np.array([[20,40],[360,5],[395,380],[30,360]],np.float32))
quad_w=cv2.perspectiveTransform(quad.reshape(1,-1,2),Hd)[0]
mask_w=cv2.warpPerspective(mask.astype(np.uint8),Hd,(400,400),flags=cv2.INTER_NEAREST)>0
a2=measure_area_cm2_from_quad(mask_w,quad_w,sticker_mm=20.0)
ck("傾斜校正後面積≈1.0 cm²(±8%)", abs(a2-1.0)/1.0<0.08)
# 對照：不校正(naive 用傾斜後像素直接算)會明顯偏離 → 證明校正有效
naive_px=int(mask_w.sum())
# naive 用原 px/mm=5 假設：area_cm2 = px/(5^2)/100
naive=naive_px/25.0/100.0
ck("naive(未校正)與真值偏差較大", abs(naive-1.0) > abs(a2-1.0))
# 退化遮罩
ck("空遮罩 -> 0", measure_area_cm2_from_quad(np.zeros((50,50),bool),quad)==0.0)
ck("決定性", measure_area_cm2_from_quad(mask,quad)==measure_area_cm2_from_quad(mask,quad))
ok=sum(r); print(f"\n{ok}/{len(r)} PASS"); sys.exit(0 if ok==len(r) else 1)
