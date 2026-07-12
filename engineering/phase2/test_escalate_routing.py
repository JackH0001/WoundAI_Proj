# -*- coding: utf-8 -*-
"""escalate 路由決策回歸測試(釘住雙軌自動 escalate 規則)。

規則(與 Backend/Flask/app.py classify_wound 一致):
  難例判定 = A∪U 與 student 的「最大連通輪廓面積比 > 1.5」或「IoU < 0.5」→ 改用 A∪U。
  依據:單看 student 自身訊號(紅色 proxy/碎片數)不可靠(見 EVIDENCE_LEDGER 2026-07-09),
       故用「第二意見」(student vs A∪U)判難。

面積/IoU 數據來自 v2 貼紙測試圖 test_wounds_aruco_v2 之 n=5 真實推論(2026-07-09)。
純決策函式測試,不需模型,CI 可快速執行。"""

def decide_route(student_area, au_area, iou):
    """回 'student' 或 'cloud_escalated(AU)'。au 無效(<=0)→ 一律保留 student。"""
    if au_area is None or au_area <= 0:
        return "student"
    ratio = (au_area / student_area) if student_area and student_area > 0 else 999.0
    if ratio > 1.5 or (iou is not None and iou < 0.5):
        return "cloud_escalated(AU)"
    return "student"

# (名稱, student面積cm², A∪U面積cm², IoU(None=未測,以高值0.9代表一致), 期望路由)
CASES = [
    ("Bedsore_01", 4.28, 4.36, 0.87, "student"),              # 本來就準,兩者一致
    ("Bedsore_02", 4.85, 4.80, 0.90, "student"),
    ("image008",   9.01, 12.04, 0.90, "student"),             # 比1.34<1.5,保留
    ("Burn",       3.59, 9.30, 0.90, "cloud_escalated(AU)"),  # 比2.59>1.5,碎片→整條
    ("FootUlcer",  0.76, 6.07, 0.25, "cloud_escalated(AU)"),  # 比8.0 且 IoU0.25,大幅低估
]

def main():
    ok = True
    print("escalate 路由回歸:")
    for name, sa, aa, iou, exp in CASES:
        got = decide_route(sa, aa, iou)
        ratio = round(aa / sa, 2) if sa else None
        p = (got == exp)
        ok &= p
        print(f"  {name:<11} au/stu={ratio!s:<5} IoU={iou} → {got:<20} 期望 {exp:<20} {'PASS' if p else 'FAIL'}")
    # 邊界:au 不可用 → 保留 student(降級安全)
    edge = decide_route(1.0, 0.0, None) == "student"; ok &= edge
    print(f"  [邊界] A∪U 不可用→student: {'PASS' if edge else 'FAIL'}")
    print("\n總結:", "全部 PASS ✓" if ok else "有 FAIL ✗")
    return 0 if ok else 1

if __name__ == "__main__":
    import sys
    sys.exit(main())
