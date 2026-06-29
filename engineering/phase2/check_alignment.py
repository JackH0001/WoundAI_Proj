# -*- coding: utf-8 -*-
"""App↔後端 對齊比對器:比較 App 輸出與 backend_reference.json(面積±tol%、PUSH 精確、組織±abs)。
Android SampleValidationTest 以相同門檻斷言;此檔供 CI/本機批次驗證與檢錯。"""
import json, sys, os
REF = os.path.join(os.path.dirname(__file__), "..", "generated", "backend_reference.json")
def check(app_out: dict, ref: dict):
    """app_out[key]={area_cm2,push_partial,tissue:{...}}。回 (ok, 問題清單)。"""
    issues = []
    for key, r in ref.items():
        a = app_out.get(key)
        if a is None: issues.append(f"{key}: App 無輸出"); continue
        tol = r["tolerance"]
        # 面積 ±%
        if r["area_cm2"] is not None and a.get("area_cm2") is not None:
            err = abs(a["area_cm2"] - r["area_cm2"]) / max(r["area_cm2"], 1e-6) * 100
            if err > tol["area_pct"]: issues.append(f"{key}: 面積誤差 {err:.1f}%>{tol['area_pct']}%")
        # PUSH 精確
        if tol["push_exact"] and a.get("push_partial") != r["push"]["partial"]:
            issues.append(f"{key}: PUSH {a.get('push_partial')}≠{r['push']['partial']}")
        # 組織 ±abs
        for c, v in r["tissue"].items():
            av = (a.get("tissue") or {}).get(c)
            if av is not None and abs(av - v) > tol["tissue_abs"]:
                issues.append(f"{key}: 組織{c} 差 {abs(av-v):.2f}>{tol['tissue_abs']}")
    return (len(issues) == 0, issues)
if __name__ == "__main__":
    ref = json.load(open(REF, encoding="utf-8"))
    # 自測1:以後端值當 App 輸出 → 應 PASS
    app_ok = {k: {"area_cm2": v["area_cm2"], "push_partial": v["push"]["partial"], "tissue": v["tissue"]} for k, v in ref.items()}
    ok, iss = check(app_ok, ref); print("自測(後端=App):", "PASS" if ok else f"FAIL {iss}")
    # 自測2:刻意把第一個面積放大20% + PUSH 改錯 → 應 FAIL(檢錯有效)
    k0 = next(iter(ref)); bad = {k: dict(v["push"]["partial"] and {"area_cm2": v["area_cm2"], "push_partial": v["push"]["partial"], "tissue": v["tissue"]}) for k, v in ref.items()}
    if bad[k0]["area_cm2"]: bad[k0]["area_cm2"] *= 1.2
    bad[k0]["push_partial"] = (bad[k0]["push_partial"] or 0) + 3
    ok2, iss2 = check(bad, ref); print("自測(擾動):", "正確攔截 ✓" if not ok2 else "未攔截 ✗", "→", iss2[:2])
