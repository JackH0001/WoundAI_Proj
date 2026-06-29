# -*- coding: utf-8 -*-
"""/api/v1/classify 回應契約測試:確保後端回傳 schema 與 App 解析(BackendClient)一致。
與 Backend/Flask/app.py classify_wound 回傳鍵對齊;App 需 area_cm2 / push / tissue。"""
SCHEMA = {
    "stage2_segment": ["model", "wound_ratio", "confidence"],
    "stage3_calibrate": ["method", "area_cm2"],
    "stage4_tissue": ["method", "tissue_frac"],
    "stage5_severity": ["tool", "area_subscore", "tissue_subscore", "exudate_subscore", "total_partial_img", "total_full"],
    "_top": ["disclaimer"],
}
TISSUE_KEYS = ["necrosis", "slough", "granulation", "epithelial", "other"]
def validate(resp: dict):
    issues = []
    for sect, keys in SCHEMA.items():
        if sect == "_top":
            for k in keys:
                if k not in resp: issues.append(f"缺頂層 {k}")
            continue
        if sect not in resp: issues.append(f"缺區段 {sect}"); continue
        for k in keys:
            if k not in resp[sect]: issues.append(f"{sect} 缺 {k}")
    tf = resp.get("stage4_tissue", {}).get("tissue_frac", {})
    for k in TISSUE_KEYS:
        if k not in tf: issues.append(f"tissue_frac 缺 {k}")
    return (len(issues) == 0, issues)

if __name__ == "__main__":
    # 代表性回應(同 app.py classify_wound 鍵)
    good = {
        "stage2_segment": {"model": "student", "wound_ratio": 0.077, "confidence": 0.83},
        "stage3_calibrate": {"method": "aruco(marker 12.0mm)", "area_cm2": 8.07, "note": None},
        "stage4_tissue": {"method": "v2(WB+HSV)", "tissue_frac": {k: 0.0 for k in TISSUE_KEYS}},
        "stage5_severity": {"tool": "PUSH (NPUAP 3.0)", "area_subscore": 7, "tissue_subscore": 2,
                            "exudate_subscore": None, "total_partial_img": 9, "total_full": None, "range_full": "0-17"},
        "disclaimer": "輔助、非診斷、需醫師確認",
    }
    ok, iss = validate(good); print("契約(完整回應):", "PASS" if ok else f"FAIL {iss}")
    # App 取用欄位可映射
    area = good["stage3_calibrate"]["area_cm2"]; push = good["stage5_severity"]["total_partial_img"]
    g = good["stage4_tissue"]["tissue_frac"]["granulation"]
    print(f"App 映射: area_cm2={area} push={push} 肉芽={g} → 可解析 ✓")
    # 缺鍵 → 攔截
    bad = {k: v for k, v in good.items() if k != "stage5_severity"}
    ok2, iss2 = validate(bad); print("契約(缺 stage5):", "正確攔截 ✓" if not ok2 else "未攔截 ✗", "→", iss2[:1])
