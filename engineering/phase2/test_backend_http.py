# -*- coding: utf-8 -*-
"""後端真實 HTTP 連線測試(本機啟動 Flask 後執行)。
用法:1) cd Backend/Flask && python app.py   2) python engineering/phase2/test_backend_http.py [--url http://127.0.0.1:5000] --img <測試圖>
需 requests。驗證:登入→classify(schema+影像綁定)→annotation(守門/孤兒擋下/去重)→stats→consent/withdraw。

【2026-07-28 資料鏈修正】annotation 現在強制 image_id/image_w/image_h。
本測試因此**必須先跑 classify 取得 image_id**(故 --img 由選配改為必要),
並新增三個守門斷言:孤兒 GT→400、座標超界→400、同影像同遮罩→duplicate_skipped。"""
import os, sys, uuid, argparse
try:
    import requests
except ImportError:
    print("需 pip install requests"); sys.exit(1)
sys.path.insert(0, ".")
from test_api_contract import validate   # 重用契約 schema 驗證


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:5000")
    ap.add_argument("--img", default=None, help="測試影像(取得 image_id 用;不給則跳過飛輪資料鏈測試)")
    ap.add_argument("--user", default="admin"); ap.add_argument("--pw", default="woundai-admin")  # 後端預設 ADMIN_PASSWORD
    a = ap.parse_args(); U = a.url; ok = True

    # 1 登入
    r = requests.post(f"{U}/api/auth/login", json={"username": a.user, "password": a.pw}, timeout=10)
    if r.status_code != 200:
        print("登入失敗", r.status_code, r.text[:120]); return
    tok = r.json().get("access_token"); H = {"Authorization": f"Bearer {tok}"}
    print("登入 OK")

    if not a.img:
        print("(未提供 --img:跳過 classify 與飛輪資料鏈測試——新契約需 image_id,無影像測不了)")
        print("\n總結: 略過 ⚠")
        return

    # 2 classify:schema + 影像綁定欄位
    # ⚠ 每輪必須用「內容不同」的影像:image_id 是內容雜湊,而本測試結尾會撤回同意;
    #   沿用同一張圖 → 第二次跑就會被「該影像已撤回」擋下,被誤判成產品迴歸。
    #   JPEG 解碼器會忽略 EOI 之後的位元組,故附加隨機尾碼可安全地讓雜湊唯一。
    with open(a.img, "rb") as f:
        payload = f.read() + os.urandom(8)
    code = "WD-T" + uuid.uuid4().hex[:8].upper()
    print(f"本輪測試代碼 {code}(每輪唯一,避免污染產線佇列的既有樣本)")
    r = requests.post(f"{U}/api/v1/classify", headers=H,
                      files={"image": ("wound.jpg", payload, "image/jpeg")}, timeout=120)
    if r.status_code != 200:
        print("classify HTTP", r.status_code, r.text[:160]); print("\n總結: 有 FAIL ✗"); return
    j = r.json()
    good, iss = validate(j); print("classify schema:", "PASS" if good else f"FAIL {iss}"); ok &= good
    iid, iw, ih = j.get("image_id"), j.get("image_w"), j.get("image_h")
    bind = bool(iid) and (iw or 0) > 0 and (ih or 0) > 0
    print(f"classify 影像綁定: {'PASS' if bind else 'FAIL'} (image_id={iid}, {iw}x{ih})"); ok &= bind
    if not bind:
        print("\n總結: 有 FAIL ✗(後端未重啟?image_id/image_w/image_h 是本輪新增)"); return

    poly = (j.get("stage2_segment") or {}).get("wound_polygon") or []
    if len(poly) < 3:   # AI 空遮罩(OOD)時用一個安全的假多邊形,仍可測資料鏈守門
        poly = [[10, 10], [min(200, iw - 1), 10], [min(200, iw - 1), min(200, ih - 1)], [10, min(200, ih - 1)]]
    base = {"code": code, "gt_polygon": poly, "exudate": 2,
            "doctor_verified": True, "deidentified": True, "consent_train": True,
            "image_id": iid, "image_w": iw, "image_h": ih,
            "mm_per_px": (j.get("stage3_calibrate") or {}).get("mm_per_px"),
            "route": (j.get("stage2_segment") or {}).get("route"),
            "care_note": "test_backend_http"}

    # 3 annotation 守門矩陣
    def post_anno(payload, label, expect, expect_status=None):
        rr = requests.post(f"{U}/api/v1/annotation", headers=H, json=payload, timeout=15)
        st = rr.json().get("status") if rr.status_code == 200 else None
        good_ = (rr.status_code == expect) and (expect_status is None or st == expect_status)
        print(f"annotation {label}→ {rr.status_code}{'/' + str(st) if st else ''} (期望 {expect}"
              f"{'/' + expect_status if expect_status else ''}) {'PASS' if good_ else 'FAIL ' + rr.text[:140]}")
        return good_

    ok &= post_anno(base, "合格", 200, "enqueued")
    ok &= post_anno(base, "同影像同遮罩(去重)", 200, "duplicate_skipped")
    ok &= post_anno({**base, "consent_train": False}, "未同意", 400)
    ok &= post_anno({k: v for k, v in base.items() if k != "image_id"}, "孤兒GT(無image_id)", 400)
    ok &= post_anno({**base, "image_id": "deadbeefdeadbeef"}, "影像不存在", 400)
    ok &= post_anno({**base, "gt_polygon": [[0, 0], [iw + 500, 0], [0, 10]]}, "座標超界", 400)
    ok &= post_anno({**base, "exudate": 9}, "滲液超出0-3", 400)

    # 4 佇列健康度
    r = requests.get(f"{U}/api/v1/flywheel/stats", headers=H, timeout=10)
    if r.status_code == 200:
        s = r.json(); print("stats:", s)
        ok &= (s.get("trainable", 0) >= 1)
        print(f"可訓練樣本 ≥1: {'PASS' if s.get('trainable', 0) >= 1 else 'FAIL'}")
    else:
        print("stats HTTP", r.status_code); ok = False

    # 5 撤回 → 應從可訓練樣本中消失
    r = requests.post(f"{U}/api/v1/consent/withdraw", headers=H, json={"code": code}, timeout=10)
    print("withdraw→", r.status_code, "(期望200)"); ok &= (r.status_code == 200)
    s2 = requests.get(f"{U}/api/v1/flywheel/stats", headers=H, timeout=10).json()
    gone = s2.get("withdrawn", 0) >= 1
    print(f"撤回後排除生效: {'PASS' if gone else 'FAIL'} (withdrawn={s2.get('withdrawn')}, trainable={s2.get('trainable')})")
    ok &= gone
    ok &= post_anno(base, "撤回後再送(應擋)", 400)

    # 6 重新取得同意 → 應可再次入列(沒有這條,撤回就是死局)
    r = requests.post(f"{U}/api/v1/consent/restore", headers=H, json={"code": code}, timeout=10)
    print("restore→", r.status_code, "(期望200)"); ok &= (r.status_code == 200)
    ok &= post_anno({**base, "gt_polygon": poly[::-1] if len(poly) > 3 else poly,
                     "care_note": "re-consent"}, "重新同意後再送", 200)

    print("\n總結:", "全部 PASS ✓" if ok else "有 FAIL ✗")
    print("提示:本腳本會寫入產線 flywheel/。要完全隔離,啟動後端時設 WOUNDAI_FLYWHEEL_DIR=<暫存目錄>。")


if __name__ == "__main__": main()
