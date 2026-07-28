# -*- coding: utf-8 -*-
"""C1+C2 後端實測驗證(對執行中的 Flask)。一行執行,自動驗:
  C1 雙軌自動 escalate:5 張測試圖 → 難例(Burn/FootUlcer)route=cloud_escalated(AU)、面積放大;
                        易例(Bedsore_01/02/image008)route=student。
  C2 飛輪:classify 回傳 wound_polygon → 以之送 /api/v1/annotation(醫師驗證+同意)→ 200 進佇列;
           未同意 payload → 400 擋下。
用法:先啟動後端(python app.py),再:
  python engineering/phase2/verify_c1c2c3.py [--url http://127.0.0.1:5000] [--dir <測試圖資料夾>]
預設圖資料夾 = C:\\dev\\WoundAI_work\\out\\test_wounds_aruco_v2(v2 貼紙合成圖)。需 requests。"""
import sys, os, json, uuid, argparse
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

    poly_for_flywheel = None; img_bind = {}
    # 每輪唯一代碼:結尾會撤回同意,固定代碼會讓第二次執行被「已撤回」擋下(誤判為迴歸)
    vcode = "WD-V" + uuid.uuid4().hex[:8].upper()
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
            # 飛輪標註須綁定影像:一併帶走 image_id 與座標空間尺寸(2026-07-28 資料鏈修正)
            poly_for_flywheel = s2["wound_polygon"]
            img_bind = {"image_id": j.get("image_id"), "image_w": j.get("image_w"),
                        "image_h": j.get("image_h"), "mm_per_px": s3.get("mm_per_px"), "route": route}
        print(f"{fn:<34}{route:<20}{str(esc):<6}{str(area):>8}{npoly:>7}  {'PASS' if good else 'FAIL 期望'+exp}")

    print("\n=== C2 飛輪(醫師驗證標註 → 佇列) ===")
    if not img_bind.get("image_id"):
        print("✗ 沒有任何一張取得 image_id(後端未重啟?)→ 跳過飛輪測試"); ok = False
    else:
        gp = poly_for_flywheel
        good_anno = {"code": vcode, "gt_polygon": gp, "exudate": 2,
                     "doctor_verified": True, "deidentified": True, "consent_train": True,
                     "correction_iou": 0.9, "care_note": "verify_c1c2c3", **img_bind}
        rr = requests.post(f"{U}/api/v1/annotation", headers=H, json=good_anno, timeout=10)
        print("合格標註 →", rr.status_code, "(期望200)"); ok &= (rr.status_code == 200)
        rr = requests.post(f"{U}/api/v1/annotation", headers=H, json={**good_anno, "consent_train": False}, timeout=10)
        print("未同意 →", rr.status_code, "(期望400)"); ok &= (rr.status_code == 400)
        rr = requests.post(f"{U}/api/v1/annotation", headers=H,
                           json={k: v for k, v in good_anno.items() if k != "image_id"}, timeout=10)
        print("孤兒GT(無image_id) →", rr.status_code, "(期望400)"); ok &= (rr.status_code == 400)
        rr = requests.get(f"{U}/api/v1/flywheel/stats", headers=H, timeout=10)
        print("佇列健康度 →", rr.status_code, rr.text[:160]); ok &= (rr.status_code == 200)
        rr = requests.post(f"{U}/api/v1/consent/withdraw", headers=H, json={"code": vcode}, timeout=10)
        print("撤回(含影像隔離) →", rr.status_code, "(期望200)"); ok &= (rr.status_code == 200)
        rr = requests.post(f"{U}/api/v1/annotation", headers=H, json=good_anno, timeout=10)
        print("撤回後再送 →", rr.status_code, "(期望400)"); ok &= (rr.status_code == 400)
        # 本腳本用固定測試圖(image_id 恆定),撤回會把它隔離 → 不還原的話下一輪就跑不動。
        # 順便驗證 re-consent 路徑存在(否則撤回是死局)。
        rr = requests.post(f"{U}/api/v1/consent/restore", headers=H, json={"code": vcode}, timeout=10)
        print("重新同意(還原影像) →", rr.status_code, "(期望200)"); ok &= (rr.status_code == 200)

    print("\n總結:", "全部 PASS ✓ (C1 escalate + C2 飛輪 後端閉環)" if ok else "有 FAIL ✗")
    print("⚠ 本腳本會在產線 flywheel/ 留下一筆測試標註與測試影像。真正收案前請確認 "
          "GET /api/v1/flywheel/stats 的 total 歸零;要完全隔離,啟動後端時設 "
          "WOUNDAI_FLYWHEEL_DIR=<暫存目錄>。")
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
