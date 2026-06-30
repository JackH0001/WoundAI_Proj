# -*- coding: utf-8 -*-
"""後端真實 HTTP 連線測試(本機啟動 Flask 後執行)。
用法:1) cd Backend/Flask && python app.py   2) python engineering/phase2/test_backend_http.py [--url http://127.0.0.1:5000] [--img path]
需 requests。驗證:登入→classify(schema)→annotation(守門 200/400)→consent/withdraw(200)。"""
import sys, json, argparse
try:
    import requests
except ImportError:
    print("需 pip install requests"); sys.exit(1)
sys.path.insert(0, ".")
from test_api_contract import validate   # 重用契約 schema 驗證

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:5000")
    ap.add_argument("--img", default=None)
    ap.add_argument("--user", default="admin"); ap.add_argument("--pw", default="admin123")
    a = ap.parse_args(); U = a.url; ok = True
    # 1 登入
    r = requests.post(f"{U}/api/auth/login", json={"username": a.user, "password": a.pw}, timeout=10)
    if r.status_code != 200: print("登入失敗", r.status_code, r.text[:120]); return
    tok = r.json().get("access_token"); H = {"Authorization": f"Bearer {tok}"}
    print("登入 OK")
    # 2 classify(需提供影像)
    if a.img:
        with open(a.img, "rb") as f:
            r = requests.post(f"{U}/api/v1/classify", headers=H, files={"image": f}, timeout=60)
        if r.status_code == 200:
            good, iss = validate(r.json()); print("classify schema:", "PASS" if good else f"FAIL {iss}"); ok &= good
        else: print("classify HTTP", r.status_code, r.text[:160])
    else: print("(略過 classify:未提供 --img)")
    # 3 annotation:合格→200、不合格→400
    good_anno = {"code":"WD-TEST01","gt_polygon":[[1,2]],"exudate":2,"doctor_verified":True,"deidentified":True,"consent_train":True}
    r = requests.post(f"{U}/api/v1/annotation", headers=H, json=good_anno, timeout=10)
    print("annotation 合格→", r.status_code, "(期望200)"); ok &= (r.status_code == 200)
    r = requests.post(f"{U}/api/v1/annotation", headers=H, json={**good_anno, "consent_train": False}, timeout=10)
    print("annotation 未同意→", r.status_code, "(期望400)"); ok &= (r.status_code == 400)
    # 4 撤回
    r = requests.post(f"{U}/api/v1/consent/withdraw", headers=H, json={"code":"WD-TEST01"}, timeout=10)
    print("withdraw→", r.status_code, "(期望200)"); ok &= (r.status_code == 200)
    print("\n總結:", "全部 PASS ✓" if ok else "有 FAIL ✗")

if __name__ == "__main__": main()
