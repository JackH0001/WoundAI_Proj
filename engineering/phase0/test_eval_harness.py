"""Phase 1 #3：分割評測 harness 單元測試（合成資料、無真實影像，確保 CI 乾淨）。"""
import sys, numpy as np
from eval_harness import seg_metrics
r=[]
def ck(n,c): r.append(bool(c)); print(("PASS " if c else "FAIL "),n)
Z=lambda:np.zeros((10,10),bool)
# 完全吻合
g=Z(); g[2:8,2:8]=True; m=seg_metrics(g.copy(),g)
ck("perfect: iou=dice=1", abs(m["iou"]-1)<1e-9 and abs(m["dice"]-1)<1e-9)
ck("perfect: area_err=0", m["area_err_pct"]==0.0)
# 完全不重疊
p=Z(); p[0:3,0:3]=True; g2=Z(); g2[6:9,6:9]=True; m=seg_metrics(p,g2)
ck("disjoint: iou=0 dice=0", m["iou"]==0 and m["dice"]==0)
# 已知重疊：gt 6x6=36, pred 右移3 列重疊 18
gt=Z(); gt[0:6,0:6]=True; pr=Z(); pr[0:6,3:9]=True; m=seg_metrics(pr,gt)
ck("known iou=18/54=0.3333", abs(m["iou"]-18/54)<1e-9)
ck("known dice=0.5", abs(m["dice"]-0.5)<1e-9)
ck("known area_err=0 (同面積)", m["area_err_pct"]==0.0)
# dice=2*iou/(1+iou) 恆等式（隨機案例）
rng=np.random.default_rng(0); a=rng.random((20,20))>0.5; b=rng.random((20,20))>0.5; m=seg_metrics(a,b)
ck("dice=2iou/(1+iou) 恆等", abs(m["dice"]-2*m["iou"]/(1+m["iou"]))<1e-9)
# area_err 公式
pr=Z(); pr[0:4,0:5]=True; gt=Z(); gt[0:4,0:4]=True; m=seg_metrics(pr,gt)  # ap=20 ag=16
ck("area_err=|20-16|/16*100=25", abs(m["area_err_pct"]-25.0)<1e-9)
# 邊界：皆空 -> iou/dice=1, area_err=0
m=seg_metrics(Z(),Z()); ck("both empty -> iou=dice=1, area_err=0", m["iou"]==1 and m["dice"]==1 and m["area_err_pct"]==0.0)
# 邊界：pred 空 gt 非空 -> recall0 area_err100
g=Z(); g[2:5,2:5]=True; m=seg_metrics(Z(),g)
ck("pred empty -> recall=0 area_err=100", m["recall"]==0.0 and m["area_err_pct"]==100.0)
# 範圍
ck("所有指標落在 [0,1] / area_err>=0", 0<=m["iou"]<=1 and 0<=m["dice"]<=1 and m["area_err_pct"]>=0)
# 決定性
ck("決定性", seg_metrics(a,b)==seg_metrics(a,b))
ok=sum(r); print(f"\n{ok}/{len(r)} PASS"); sys.exit(0 if ok==len(r) else 1)
