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
def classify(img_rgb, mask, px_per_mm=None, params=None, area_cm2=None,
             use_v2=True, gray_patch_rgb=None, exudate_level=None):
    """area_cm2 可直接傳入(建議用 ArUco 面積比例法);否則由 px_per_mm 推算。
    use_v2=True:白平衡+飽和度感知組織分型(方案3);severity 用 PUSH 量表(方案1重建)。"""
    from clinical_rules import push_score
    t = tissue_proxy_v2(img_rgb, mask, gray_patch_rgb=gray_patch_rgb) if use_v2 else tissue_proxy(img_rgb, mask)
    area_px = int(np.asarray(mask, bool).sum())
    if area_cm2 is None and px_per_mm:
        area_cm2 = (area_px / (px_per_mm ** 2)) / 100.0
    sev_legacy = severity_scorecard(area_cm2 if area_cm2 is not None else 0.0, t, params)
    push = push_score(area_cm2, t, exudate_level=exudate_level)
    tx = treatment(sev_legacy["grade"], t, params)
    cand = {k: t[k] for k in ("necrosis", "slough", "granulation", "epithelial")}
    dom = max(cand, key=cand.get)
    LAB = {"necrosis": "壞死為主", "slough": "腐肉為主", "granulation": "肉芽為主", "epithelial": "上皮為主"}
    label = LAB[dom] if cand[dom] >= 0.15 else "未定（組織比例不明顯）"
    return {"status": "rule_based", "area_px": area_px, "area_cm2": area_cm2,
            "area_uncalibrated": area_cm2 is None, "tissue_proxy": t,
            "tissue_dominant": label, "severity_push": push, "severity_legacy": sev_legacy, "severity": sev_legacy,
            "treatment": tx, "tissue_method": "v2(WB+HSV)" if use_v2 else "v1",
            "confidence": "heuristic-low",
            "note": "組織為色彩啟發式(已白平衡+飽和度感知,仍粗估);嚴重度用 PUSH 已驗證量表(滲液需醫師);輔助用途、非診斷、需醫師確認"}

# ===== 方案3:白平衡 + 飽和度感知組織分型(v2) =====
import cv2 as _cv2
def gray_world_wb(img_rgb):
    """灰世界自動白平衡(無需色塊):各通道增益使平均趨近灰。輸出 uint8 RGB。"""
    im = np.asarray(img_rgb).astype(np.float32); mu = im.reshape(-1,3).mean(0) + 1e-6
    g = mu.mean() / mu
    return np.clip(im * g, 0, 255).astype(np.uint8)
def patch_wb(img_rgb, gray_patch_rgb, target=189.0):
    """有校正貼紙灰塊時:用已知灰塊(sRGB gray18≈189)校正增益。gray_patch_rgb=量到的灰塊RGB。"""
    gp = np.asarray(gray_patch_rgb, np.float32) + 1e-6
    g = target / gp
    return np.clip(np.asarray(img_rgb).astype(np.float32) * g, 0, 255).astype(np.uint8)
def tissue_classmap_v2(img_rgb, mask, wb=True, gray_patch_rgb=None):
    """飽和度感知:用 HSV 區分暗紅肉芽 vs 真壞死。
    壞死=暗 且 低飽和(黑/棕焦痂);腐肉=黃;肉芽=紅/高飽和(即使偏暗);上皮=淡粉高明度。"""
    m = np.asarray(mask, bool); cm = np.zeros(m.shape, np.uint8)
    if m.sum() == 0: return cm
    img = patch_wb(img_rgb, gray_patch_rgb) if gray_patch_rgb is not None else (gray_world_wb(img_rgb) if wb else np.asarray(img_rgb))
    img = img.astype(np.int32); R,G,B = img[...,0],img[...,1],img[...,2]
    hsv = _cv2.cvtColor(np.clip(img,0,255).astype(np.uint8), _cv2.COLOR_RGB2HSV)
    Hh,Ss,Vv = hsv[...,0].astype(np.int32), hsv[...,1].astype(np.int32), hsv[...,2].astype(np.int32)
    lum = 0.299*R + 0.587*G + 0.114*B
    # 真壞死:暗 且 低飽和(排除飽和紅)
    nec = m & (Vv < 75) & (Ss < 90)
    # 腐肉:黃(H≈20-45)、中高飽和
    slo = m & (~nec) & (Hh>=18) & (Hh<=45) & (Ss>=60) & (Vv>=60)
    # 上皮:淡粉、高明度低飽和
    epi = m & (~nec) & (~slo) & (Vv>=170) & (Ss<70) & (R>150)
    # 肉芽:紅(H<15 或 >160)、有飽和(即使偏暗)
    gra = m & (~nec) & (~slo) & (~epi) & ((Hh<15)|(Hh>160)) & (Ss>=60)
    cm[nec]=1; cm[slo]=2; cm[gra]=3; cm[epi]=4; cm[m & (cm==0)]=5
    return cm
def tissue_proxy_v2(img_rgb, mask, wb=True, gray_patch_rgb=None):
    cm = tissue_classmap_v2(img_rgb, mask, wb, gray_patch_rgb); tot=int(np.asarray(mask,bool).sum())
    if tot==0: return {"necrosis":0.0,"slough":0.0,"granulation":0.0,"epithelial":0.0,"other":0.0,"n_px":0,"wb":wb}
    f=lambda c: float((cm==c).sum())/tot
    return {"necrosis":f(1),"slough":f(2),"granulation":f(3),"epithelial":f(4),"other":f(5),"n_px":tot,"wb":wb}
