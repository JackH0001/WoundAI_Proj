"""Track B3 測試：可解釋粗分類（色彩組織比例 + 規則式嚴重度）。"""
import sys, numpy as np
from wound_classifier import tissue_proxy, classify
r=[]
def ck(n,c): r.append(bool(c)); print(("PASS " if c else "FAIL "),n)
H=W=64; mask=np.zeros((H,W),bool); mask[16:48,16:48]=True
red=np.zeros((H,W,3),np.uint8); red[...,0]=200; red[...,1]=60; red[...,2]=60      # 肉芽(紅)
blk=np.zeros((H,W,3),np.uint8)                                                     # 壞死(黑)
yel=np.zeros((H,W,3),np.uint8); yel[...,0]=200; yel[...,1]=170; yel[...,2]=40     # 腐肉(黃)
ck("空遮罩 -> 全 0、graceful", tissue_proxy(red, np.zeros((H,W),bool))["n_px"]==0)
ck("比例落在 [0,1] 且總和≈1", abs(sum(tissue_proxy(red,mask)[k] for k in("necrosis","slough","granulation","epithelial","other"))-1)<1e-6)
ck("紅 -> 肉芽為主", classify(red,mask)["tissue_dominant"]=="肉芽為主")
ck("黑 -> 壞死為主", classify(blk,mask)["tissue_dominant"]=="壞死為主")
ck("黃 -> 腐肉為主", classify(yel,mask)["tissue_dominant"]=="腐肉為主")
pink=np.zeros((H,W,3),np.uint8); pink[...,0]=228; pink[...,1]=172; pink[...,2]=168  # 淡粉上皮
ck("淡粉 -> 上皮為主", classify(pink,mask)["tissue_dominant"]=="上皮為主")
ck("純紅非上皮(仍肉芽)", classify(red,mask)["tissue_dominant"]=="肉芽為主")
ck("壞死 -> 治療含清創/轉診", "清創" in classify(blk,mask)["treatment"]["recommendation"])
ck("肉芽 -> 治療含敷料", "敷料" in classify(red,mask)["treatment"]["recommendation"])
ck("未校正 -> area_cm2 None + 標記", classify(red,mask)["area_cm2"] is None and classify(red,mask)["area_uncalibrated"])
ck("有校正 -> area_cm2 數值", classify(red,mask,px_per_mm=2.0)["area_cm2"] is not None)
ck("severity 為規則式可解釋", classify(red,mask)["severity"]["status"]=="rule_based" and "explain" in classify(red,mask)["severity"])
ck("note 含需醫師確認", "醫師" in classify(red,mask)["note"])
ck("決定性", classify(red,mask)==classify(red,mask))
ok=sum(r); print(f"\n{ok}/{len(r)} PASS"); sys.exit(0 if ok==len(r) else 1)
