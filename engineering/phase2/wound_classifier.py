"""Track B3：可解釋粗分類。分割遮罩內的幾何 + 色彩啟發式組織比例 → 規則式組織分型與嚴重度。
標準組織：necrosis 壞死(暗) / slough 腐肉(黃) / granulation 肉芽(深紅) / epithelial 上皮(淡粉) / other。
無黑箱；色彩為粗估，輔助用途、需醫師確認。複用 phase1 clinical_rules。"""
import os, sys
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "phase1"))
from clinical_rules import severity_scorecard, treatment
# 組織碼：0=遮罩外 1=壞死 2=腐肉 3=肉芽 4=上皮 5=其他
def tissue_classmap(img_rgb, mask):
    m = np.asarray(mask, bool); cm = np.zeros(m.shape, np.uint8)
    if m.sum() == 0: return cm
    img = np.asarray(img_rgb).astype(np.int32); R, G, B = img[..., 0], img[..., 1], img[..., 2]
    lum = 0.299 * R + 0.587 * G + 0.114 * B
    mn = np.minimum(np.minimum(R, G), B)
    nec = m & (lum < 55)
    slo = m & (~nec) & (R > 120) & (G > 90) & (B < 110) & ((R - B) > 35) & ((G - B) > 15)
    epi = m & (~nec) & (~slo) & (R > 165) & (G > 120) & (B > 115) & ((R - G) < 70) & ((G - B) < 45) & (mn >= 100)  # 淡粉上皮
    gra = m & (~nec) & (~slo) & (~epi) & (R > G + 15) & (R > B + 15)                                                # 深紅肉芽
    cm[nec] = 1; cm[slo] = 2; cm[gra] = 3; cm[epi] = 4; cm[m & (cm == 0)] = 5
    return cm
def tissue_proxy(img_rgb, mask):
    cm = tissue_classmap(img_rgb, mask); tot = int(np.asarray(mask, bool).sum())
    if tot == 0: return {"necrosis": 0.0, "slough": 0.0, "granulation": 0.0, "epithelial": 0.0, "other": 0.0, "n_px": 0}
    f = lambda code: float((cm == code).sum()) / tot
    return {"necrosis": f(1), "slough": f(2), "granulation": f(3), "epithelial": f(4), "other": f(5), "n_px": tot}
def classify(img_rgb, mask, px_per_mm=None, params=None):
    t = tissue_proxy(img_rgb, mask)
    area_px = int(np.asarray(mask, bool).sum())
    area_cm2 = (area_px / (px_per_mm ** 2)) / 100.0 if px_per_mm else None
    sev = severity_scorecard(area_cm2 if area_cm2 is not None else 0.0, t, params)
    tx = treatment(sev["grade"], t, params)
    cand = {k: t[k] for k in ("necrosis", "slough", "granulation", "epithelial")}
    dom = max(cand, key=cand.get)
    LAB = {"necrosis": "壞死為主", "slough": "腐肉為主", "granulation": "肉芽為主", "epithelial": "上皮為主"}
    label = LAB[dom] if cand[dom] >= 0.15 else "未定（組織比例不明顯）"
    return {"status": "rule_based", "area_px": area_px, "area_cm2": area_cm2,
            "area_uncalibrated": px_per_mm is None, "tissue_proxy": t,
            "tissue_dominant": label, "severity": sev, "treatment": tx,
            "confidence": "heuristic-low",
            "note": "色彩啟發式組織估計為粗估、規則式嚴重度；輔助用途、需醫師確認"}
