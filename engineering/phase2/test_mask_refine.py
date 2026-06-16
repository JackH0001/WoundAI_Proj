"""遮罩精修測試：去框邊偽影、去細刺、保留最大連通區。"""
import sys, numpy as np
from mask_refine import refine
r=[]
def ck(n,c): r.append(bool(c)); print(("PASS " if c else "FAIL "),n)
H=W=120; m=np.zeros((H,W),bool)
m[40:80,40:80]=True                       # 主傷口
m[10:14,10:110]=True                      # 貼上框邊的直線偽影(頂邊)
m[100:103,100:103]=True                   # 離散小塊
box=(8,8,112,112)
out=refine(m, roibox=box, open_k=5, close_k=9, border_px=6, keep_largest=True)
ck("主傷口保留", out[55:65,55:65].all())
ck("框頂邊直線偽影被移除", not out[10:14,10:110].any())
ck("離散小塊被移除(保留最大)", not out[100:103,100:103].any())
ck("空遮罩 graceful", refine(np.zeros((20,20),bool)).sum()==0)
ck("決定性", np.array_equal(refine(m,roibox=box),refine(m,roibox=box)))
# 面積應下降(去掉偽影)
ck("精修後面積 < 原始(去偽影)", int(out.sum()) < int(m.sum()))
ok=sum(r); print(f"\n{ok}/{len(r)} PASS"); sys.exit(0 if ok==len(r) else 1)
