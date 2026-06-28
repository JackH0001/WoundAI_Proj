"""規則式（可解釋）嚴重度分數卡與治療方向建議；臨床可調參數，無需黑箱模型。輔助用途、需醫師確認。"""
import json
def _g(p, k, d): return (p or {}).get(k, d)
def severity_scorecard(area_cm2, tissue_frac, params=None):
    at = _g(params, "area_thresholds_cm2", [4, 16])
    nt = _g(params, "necrosis_frac", 0.2); st = _g(params, "slough_frac", 0.3)
    pts = (0 if area_cm2 < at[0] else (1 if area_cm2 < at[1] else 2))
    pts += (2 if tissue_frac.get("necrosis", 0) > nt else (1 if tissue_frac.get("slough", 0) > st else 0))
    return {"status": "rule_based", "grade": 1 + min(pts, 3), "points": pts,
            "explain": f"area_cm2={area_cm2}, necrosis={tissue_frac.get('necrosis',0)}, slough={tissue_frac.get('slough',0)}, pts={pts}"}
def treatment(grade, tissue_frac, params=None):
    if tissue_frac.get("necrosis", 0) > _g(params, "necrosis_frac", 0.2): rec = "疑壞死組織→建議清創評估／轉診"
    elif tissue_frac.get("slough", 0) > _g(params, "slough_frac", 0.3): rec = "腐肉偏多→清創與適當敷料"
    elif grade >= 3: rec = "情況偏重→轉介傷口照護專科評估"
    else: rec = "肉芽為主→維持濕潤敷料、定期追蹤"
    return {"status": "rule_based", "recommendation": rec, "note": "輔助建議，需醫師確認"}
def load_params(path): return json.load(open(path, encoding="utf-8"))

# ===== PUSH 量表(Pressure Ulcer Scale for Healing, NPUAP 公開、已驗證) =====
# 重建嚴重度方法(取代失敗的 DeepSkin/PWAT)。透明可解釋:面積子分+組織子分(+滲液子分需醫師)。
# 參考:NPUAP PUSH Tool 3.0。分數越低=越接近癒合。輔助用途、非診斷、需醫師確認。
_PUSH_AREA_BANDS = [  # (上限 cm2, 子分);依 PUSH 3.0
    (0.0, 0), (0.3, 1), (0.6, 2), (1.0, 3), (2.0, 4), (3.0, 5),
    (4.0, 6), (8.0, 7), (12.0, 8), (24.0, 9)]  # >24.0 → 10
def push_area_subscore(area_cm2):
    if area_cm2 is None: return None
    if area_cm2 <= 0: return 0
    for hi, sc in _PUSH_AREA_BANDS:
        if area_cm2 <= hi: return sc
    return 10
def push_tissue_subscore(tissue_frac, present_thresh=0.05):
    """PUSH 組織子分:取「最差」存在組織。4=壞死,3=腐肉,2=肉芽,1=上皮,0=已閉合/無。
    present_thresh:該組織占比門檻才算存在(濾雜訊)。"""
    f = tissue_frac or {}
    if f.get("necrosis", 0) >= present_thresh: return 4
    if f.get("slough", 0) >= present_thresh: return 3
    if f.get("granulation", 0) >= present_thresh: return 2
    if f.get("epithelial", 0) >= present_thresh: return 1
    return 0
def push_score(area_cm2, tissue_frac, exudate_level=None):
    """回傳 PUSH 子分與總分。exudate_level:0-3(none/light/moderate/heavy),單張照片量不到→None(需醫師)。
    total_partial=面積+組織(可由影像得);total_full 僅在有 exudate 時給出。"""
    a = push_area_subscore(area_cm2); t = push_tissue_subscore(tissue_frac)
    partial = (a + t) if (a is not None) else None
    full = (partial + exudate_level) if (partial is not None and exudate_level is not None) else None
    return {
        "tool": "PUSH (NPUAP 3.0)", "status": "validated_scale_rule_based",
        "area_subscore": a, "tissue_subscore": t, "exudate_subscore": exudate_level,
        "total_partial_img": partial, "total_full": full, "range_full": "0-17(低=癒合)",
        "exudate_note": "滲液量無法由單張影像判定,需醫師輸入(0-3)",
        "area_calibrated": area_cm2 is not None,
        "note": "PUSH 為已驗證壓瘡癒合量表;面積由 ArUco 校正、組織由色彩比例(粗估)推得;輔助用途、非診斷、需醫師確認"}
