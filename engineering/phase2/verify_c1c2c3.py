# -*- coding: utf-8 -*-
"""C1+C2 後端實測驗證(對執行中的 Flask)。一行執行,自動驗:
  C1 雙軌自動 escalate:5 張測試圖 → 難例(Burn/FootUlcer)route=cloud_escalated(AU)、面積放大;
                        易例(Bedsore_01/02/image008)route=student。
  C2 飛輪:classify 回傳 wound_polygon → 以之送 /api/v1/annotation(醫師驗證+同意)→ 200 進佇列;
           未同意 payload → 400 擋下。
用法:先啟動後端(python app.py),再:
  python engineering/phase2/verify_c1c2c3.py [--url http://127.0.0.1:5000] [--dir <測試圖資料夾>]
預設圖資料夾 = C:\\dev\\WoundAI_work\\out\\test_wounds_aruco_v2(v2 貼紙合成圖)。需 requests。"""
import sys, os, json, argparse
try:
    import requests
except ImportError:
    print("需 pip install requests"); sys.exit(1)

EXPECT = {  # 期望 route(依 EVIDENCE_LEDGER 2026-07-09 決策)
    "Bedsore_01_arucoV2.png": "student",
    "Bedsore_02_arucoV2.png": "student",
    "image008_arucoV2.png": "student",
    "Burn_ChronicWound_01_arucoV2.png": "cloud_escalated(AU)",
    "足部潰瘍_arucoV2.png": "cloud_escalated(AU)",
}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:5000")
    ap.add_argument("--dir", default=r"C:\dev\WoundAI_work\out\test_wounds_aruco_v2")
    ap.add_argument("--user", default="admin"); ap.add_argument("--pw", default="woundai-admin")
    a = ap.parse_args(); U = a.url; ok = True

    r = requests.post(f"{U}/api/auth/login", json={"username": a.user, "password": a.pw}, timeout=10)
    if r.status_code != 200:
        print("登入失敗", r.status_code, r.text[:120]); return 1
    H = {"Authorization": f"Bearer {r.json()['access_token']}"}
    print("登入 OK\n=== C1 雙軌自動 escalate ===")

    poly_for_flywheel = None
    print(f"{'圖':<34}{'route':<20}{'escal':<6}{'面積cm2':>8}{'poly點':>7}  判定")
    for fn, exp in EXPECT.items():
        p = os.path.join(a.dir, fn)
        if not os.path.exists(p):
            print(f"{fn:<34} (檔案不存在,跳過)"); continue
        with open(p, "rb") as f:
            rr = requests.post(f"{U}/api/v1/classify", headers=H, files={"image": f}, timeout=120)
        if rr.status_code != 200:
            print(f"{fn:<34} classify HTTP {rr.status_code} {rr.text[:80]}"); ok = False; continue
        j = rr.json(); s2 = j["stage2_segment"]; s3 = j["stage3_calibrate"]
        route = s2.get("route", "?"); esc = s2.get("escalated"); area = s3.get("area_cm2")
        npoly = len(s2.get("wound_polygon", []))
        good = (route == exp)
        ok &= good
        if poly_for_flywheel is None and npoly >= 3:
            poly_for_flywheel = s2["wound_polygon"]
        print(f"{fn:<34}{route:<20}{str(esc):<6}{str(area):>8}{npoly:>7}  {'PASS' if good else 'FAIL 期望'+exp}")

    print("\n=== C2 飛輪(醫師驗證標註 → 佇列) ===")
    gp = poly_for_flywheel or [[10, 20], [30, 20], [30, 50], [10, 50]]
    good_anno = {"code": "WD-VERIFY01", "gt_polygon": gp, "exudate": 2,
                 "doctor_verified": True, "deidentified": True, "consent_train": True,
                 "correction_iou": 0.9, "care_note": "verify_c1c2c3"}
    rr = requests.post(f"{U}/api/v1/annotation", headers=H, json=good_anno, timeout=10)
    print("合格標註 →", rr.status_code, "(期望200)"); ok &= (rr.status_code == 200)
    rr = requests.post(f"{U}/api/v1/annotation", headers=H, json={**good_anno, "consent_train": False}, timeout=10)
    print("未同意 →", rr.status_code, "(期望400)"); ok &= (rr.status_code == 400)
    rr = requests.post(f"{U}/api/v1/consent/withdraw", headers=H, json={"code": "WD-VERIFY01"}, timeout=10)
    print("撤回 →", rr.status_code, "(期望200)"); ok &= (rr.status_code == 200)

    print("\n總結:", "全部 PASS ✓ (C1 escalate + C2 飛輪 後端閉環)" if ok else "有 FAIL ✗")
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
