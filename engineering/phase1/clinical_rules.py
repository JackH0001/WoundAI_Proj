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
