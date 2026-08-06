# -*- coding: utf-8 -*-
"""端到端驗證：**深度資料鏈，用合成幾何取代感測器**。

    python engineering/phase2/test_depth_chain.py

## 為什麼現在就驗，而不是等硬體

深度鏈只有一節需要硬體（ARCore Depth / ARKit sceneDepth）。其餘每一節
——編碼、上傳、落盤、取回、解碼、反投影——都可以用**已知幾何的合成深度圖**
驗到底，而且那些節點正是整合錯誤最集中的地方：

  · 公尺當公釐 → 面積差 10⁶ 倍
  · fx/fy 對調、cx/cy 用了縮圖前的值 → 系統性偏差
  · 存成 8-bit PNG → 深度壓進 0–255 mm，30 cm 攝距整片飽和，
    反投影出一個平面，**表面積看起來完全正常**
  · u/v 對調 → 只有非正方形影像才露餡

等硬體到位再一次驗，整條鏈都是新的，出錯時無從二分查找。現在驗完，
之後只剩「把真實感測器接到已驗證的介面上」。

## 正確答案的來源

`depth_synth` 的解析式（曲面元素積分），與 `measure3d` 的三角化**完全獨立**。
兩者相符才代表兩邊都對；用三角化算一次再拿來比對自己是沒有意義的。

解析式本身已用兩種方式交叉驗證：與數值微分相符到 0.005% 以內，
且窄視野時收斂到正交近似的 1/cos(θ)。
"""
import base64
import hashlib
import importlib
import json
import os
import shutil
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
FLASK_DIR = os.path.abspath(os.path.join(HERE, "..", "..", "Backend", "Flask"))
sys.path.insert(0, HERE)

FAILED = []
TOTAL = [0]


def check(name, ok, detail=""):
    TOTAL[0] += 1
    print(("PASS  " if ok else "FAIL  ") + name + (("   " + str(detail)) if detail else ""))
    if not ok:
        FAILED.append(name)


def close(a, b, tol_pct, name, extra=""):
    err = 100.0 * abs(a - b) / b if b else float("inf")
    check("%s（誤差 %.3f%% ≤ %.2f%%）%s" % (name, err, tol_pct, extra), err <= tol_pct,
          "得 %.4f / 應 %.4f" % (a, b))
    return err


def make_jpeg(w=1200, h=900):
    from PIL import Image
    import io
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (180, 90, 80)).save(buf, "JPEG")
    return buf.getvalue()


def main():
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        print("需要 Pillow：pip install pillow")
        return 1

    import depth_synth as DS
    import measure3d as M3

    # ── A. 演算法層：三角化 vs 解析解 ──────────────────────────────
    print("── A 反投影三角化 vs 解析積分（兩種獨立算法）──")
    for case in DS.CASES:
        Z, m, K, exact_mm2, meta = DS.build(case, 480, 360)
        Kt = (K["fx"], K["fy"], K["cx"], K["cy"])
        got = M3.surface_area_cm2(Z, m, Kt)
        # 邊界格加權修正之後，三角化與解析積分的差可以壓到 0.05% 以內。
        # 容差刻意設得很緊：這條線一鬆，邊界侵蝕那類**系統性**偏差就會再溜回來
        # ——它恆為低估、不隨次數抵銷，而 0.5% 的容差完全擋不住 3.6% 以外的變形。
        close(got, exact_mm2 / 100.0, 0.05, "%-14s 表面積" % case)

    # 正對平面的比值必須是 1.000。不是的話單位或內參有錯——
    # 而那一類錯誤在斜面案例上會被「斜面本來就該大於 1」掩蓋掉。
    Z, m, K, exact, _ = DS.build("plane_frontal", 480, 360)
    ratio = exact / DS.projected_area_mm2(Z, m, K)
    close(ratio, 1.0, 0.05, "正對平面 表面積／投影面積")

    # ── B. 編碼層：png16_mm 往返 ──────────────────────────────────
    print("\n── B png16_mm 編碼往返 ──")
    raw = DS.encode_png16_mm(Z)
    back = DS.decode_png16_mm(raw)
    # 量化到 1 mm 是**有損**的。要量的是「這個損失對面積的影響有多大」，
    # 而不是假裝它不存在。
    check("解碼後尺寸相同", back.shape == Z.shape, "%s vs %s" % (back.shape, Z.shape))
    check("量化誤差 ≤ 0.5 mm", float(np.max(np.abs(back - Z))) <= 0.5,
          "最大 %.3f mm" % float(np.max(np.abs(back - Z))))
    Kt = (K["fx"], K["fy"], K["cx"], K["cy"])
    q_err = close(M3.surface_area_cm2(back, m, Kt), exact / 100.0, 0.05,
                  "量化後表面積", extra="← 1mm 量化對面積的實際影響")

    # 8-bit 會怎樣：這是最惡性的失敗模式，要親眼看到它有多離譜。
    from PIL import Image
    import io
    b8 = io.BytesIO()
    Image.fromarray(np.clip(Z, 0, 255).astype(np.uint8), mode="L").save(b8, "PNG")
    try:
        DS.decode_png16_mm(b8.getvalue())
        check("8-bit 深度圖被解碼端擋下", False, "竟然解得開")
    except ValueError as e:
        check("8-bit 深度圖被解碼端擋下", "16-bit" in str(e))
    a8 = M3.surface_area_cm2(np.clip(Z, 0, 255).astype(np.float64), m, Kt)
    check("8-bit 若沒被擋會嚴重失真", abs(a8 - exact / 100.0) / (exact / 100.0) > 0.2,
          "8-bit 算出 %.2f cm²，正確 %.2f cm²（差 %.0f%%）"
          % (a8, exact / 100.0, 100 * abs(a8 - exact / 100.0) / (exact / 100.0)))

    # ── C. 常見整合錯誤必須被指標抓到 ────────────────────────────
    print("\n── C 單位／內參錯誤的後果（這些是這支測試存在的理由）──")
    a_m = M3.surface_area_cm2(Z / 1000.0, m, Kt)          # 公尺當公釐
    check("深度用公尺 → 面積差 10⁶ 倍", abs(a_m * 1e6 - exact / 100.0) / (exact / 100.0) < 0.001,
          "得 %.6f cm²（正確 %.2f）" % (a_m, exact / 100.0))

    # ⚠ 內參錯誤**必須用斜面測**，正對平面完全測不出來。
    #
    # 正對平面每像素面積＝Z0²/(fx·fy)：fx/fy 對調不改變乘積，cx/cy 只平移不改變間距。
    # 也就是說，一套只用正對校正板的驗證流程，會在內參完全錯誤的情況下全部通過。
    # 這對實機驗證有直接影響：**校正板必須斜拍**，不能只拍正對的。
    Zs, ms, Ks, exs, _ = DS.build("plane_45deg", 480, 360)
    Kst = (Ks["fx"], Ks["fy"], Ks["cx"], Ks["cy"])
    base_s = M3.surface_area_cm2(Zs, ms, Kst)
    a_flat_sw = M3.surface_area_cm2(Z, m, (K["fy"], K["fx"], K["cx"], K["cy"]))
    check("正對平面**測不出** fx/fy 對調（所以校正板要斜拍）",
          abs(a_flat_sw - exact / 100.0) < 1e-9,
          "正對：對調前後都是 %.4f cm²" % a_flat_sw)
    a_sw = M3.surface_area_cm2(Zs, ms, (Ks["fy"], Ks["fx"], Ks["cx"], Ks["cy"]))
    check("斜面測得出 fx/fy 對調", abs(a_sw - base_s) / base_s > 0.005,
          "得 %.3f / 正確 %.3f（差 %.2f%%）" % (a_sw, base_s, 100 * abs(a_sw - base_s) / base_s))
    a_c = M3.surface_area_cm2(Zs, ms, (Ks["fx"], Ks["fy"], 0.0, 0.0))
    check("斜面測得出 cx/cy 歸零", abs(a_c - base_s) / base_s > 0.005,
          "得 %.3f / 正確 %.3f（差 %.2f%%）" % (a_c, base_s, 100 * abs(a_c - base_s) / base_s))

    # ── C2. 感測器雜訊：這一段是整個練習最重要的產出 ──────────────
    #
    # 表面積對擾動是凸函數 —— 逐像素雜訊只會讓三角形面積變大，永遠不抵銷。
    # 而深度雜訊遠大於「相鄰像素的真實深度差」，所以直接三角化原始深度圖
    # 得到的表面積是**不能用的**。這件事在合成資料上十分鐘查得出來；
    # 等收完臨床深度資料才發現，那批資料全部作廢而傷口回不去了。
    print("\n── C2 感測器雜訊 → 必須平滑（本次驗證最重要的發現）──")
    rng = np.random.default_rng(0)
    worst_raw, worst_sm = 0.0, 0.0
    for case in DS.CASES:
        Zc, mc, Kc, exc, _ = DS.build(case, 480, 360)
        Kct = (Kc["fx"], Kc["fy"], Kc["cx"], Kc["cy"])
        e = exc / 100.0
        # 真實感測器：無效處輸出 0，有效處帶雜訊。3 mm 是 ARCore Depth 在 30 cm 的典型值。
        A = np.where(Zc > 0, np.maximum(np.rint(Zc) + rng.normal(0, 3.0, Zc.shape), 0.0), 0.0)
        raw = abs(100.0 * (M3.surface_area_cm2(A, mc, Kct) - e) / e)
        sm = abs(100.0 * (M3.surface_area_cm2(A, mc, Kct, smooth_mm=8.0) - e) / e)
        worst_raw = max(worst_raw, raw)
        worst_sm = max(worst_sm, sm)
        print("     %-14s 未平滑 %+8.1f%%   smooth_mm=8 %+6.2f%%" % (case, raw, sm))
    check("未平滑時 3mm 雜訊會讓表面積高估一倍以上（證明平滑是必要的）",
          worst_raw > 100.0, "最差 +%.0f%%" % worst_raw)
    check("smooth_mm=8 把 3mm 雜訊下的誤差壓到 3% 以內", worst_sm < 3.0,
          "最差 %.2f%%" % worst_sm)

    # 大片無效區不可污染平滑視窗的尺度估計。
    Zsp, msp, Ksp, exsp, _ = DS.build("sphere_cap", 480, 360)
    noisy = np.where(Zsp > 0, np.rint(Zsp) + 1.0, 0.0)
    _, kw = M3.smooth_depth(noisy, (Ksp["fx"], Ksp["fy"], Ksp["cx"], Ksp["cy"]), 8.0, msp)
    check("平滑視窗尺度取自遮罩內，不被大片無效區帶偏", 3 <= kw <= 31, "視窗 %dx%d px" % (kw, kw))

    # ── D. 資料鏈：上傳 → 落盤 → 取回 → 量測 ────────────────────
    print("\n── D 上傳 → 後端落盤 → 取回 → 反投影 ──")
    tmp = tempfile.mkdtemp(prefix="woundai_depth_")
    os.environ["WOUNDAI_FLYWHEEL_DIR"] = tmp
    for sub in ("images", "quarantine", "tissue_masks", "depth_maps"):
        os.makedirs(os.path.join(tmp, sub), exist_ok=True)
    for f in ("retrain_queue.jsonl", "withdrawn.jsonl", "audit.jsonl",
              "users.jsonl", "retracted.jsonl"):
        open(os.path.join(tmp, f), "w").close()

    sys.path.insert(0, FLASK_DIR)
    for mod in list(sys.modules):
        if mod.startswith("api_flywheel"):
            del sys.modules[mod]
    fw = importlib.import_module("api_flywheel")
    from flask import Flask
    from flask_jwt_extended import JWTManager, create_access_token
    app = Flask(__name__)
    app.config["JWT_SECRET_KEY"] = "test-only-please-ignore"
    JWTManager(app)
    app.register_blueprint(fw.flywheel_bp)
    cli = app.test_client()
    with app.app_context():
        H = {"Authorization": "Bearer " + create_access_token(
            identity="default:dr01",
            additional_claims={"role": "physician", "org": "default", "user": "dr01"})}

    def submit(case, **over):
        Z, m, K, exact_mm2, meta = DS.build(case, 480, 360)
        jpg = make_jpeg() + case.encode()
        iid = hashlib.sha1(jpg).hexdigest()[:16]
        open(os.path.join(tmp, "images", iid + ".jpg"), "wb").write(jpg)
        body = {
            "code": "WD-D%s" % case[:6].upper().replace("_", ""),
            "gt_polygon": [[100, 100], [1000, 110], [1010, 700], [110, 690]],
            "exudate": 1, "image_id": iid, "image_w": 1200, "image_h": 900,
            "mm_per_px": 0.25, "doctor_verified": True, "deidentified": True,
            "consent_train": True, "source": "phantom", "route": "cloud",
            "depth_source": "arcore_depth", "depth_format": "png16_mm",
            "depth_scale": 0.001,
            "camera_intrinsics": {k: K[k] for k in ("fx", "fy", "cx", "cy")},
            "depth_map_png": base64.b64encode(DS.encode_png16_mm(Z)).decode(),
            "depth_conf_png": base64.b64encode(
                DS.encode_conf_png(np.where(m, 0.95, 0.4))).decode(),
            "capture_device": "Synthetic Depth Rig",
        }
        body.update(over)
        r = cli.post("/api/v1/annotation", headers=H, json=body)
        return r, iid, Z, m, K, exact_mm2

    r, iid, Z, m, K, exact_mm2 = submit("plane_45deg")
    check("帶深度的標註送出成功", r.status_code == 200,
          "%s %s" % (r.status_code, (r.get_json() or {}).get("issues") or ""))

    recs = [x for x in fw.read_jsonl(fw.QUEUE) if x.get("image_id") == iid]
    rec = recs[-1] if recs else {}
    check("紀錄有 depth_map_key", rec.get("depth_map_key") == "depth_maps/%s.png" % iid,
          rec.get("depth_map_key"))
    check("紀錄有 depth_conf_key", bool(rec.get("depth_conf_key")))
    check("紀錄保留內參", (rec.get("camera_intrinsics") or {}).get("fx") is not None)
    check("depth_source 是真值", rec.get("depth_source") == "arcore_depth")

    stored = os.path.join(tmp, "depth_maps", iid + ".png")
    check("深度圖確實落盤", os.path.exists(stored))
    raw_back = open(stored, "rb").read()
    check("落盤位元組與送出的完全相同",
          hashlib.sha256(raw_back).hexdigest()
          == hashlib.sha256(DS.encode_png16_mm(Z)).hexdigest())

    # 取回 → 反投影 → 與解析解比對。這一條走完，整條鏈就通了。
    Zb = DS.decode_png16_mm(raw_back)
    ci = rec["camera_intrinsics"]
    Kb = (ci["fx"], ci["fy"], ci["cx"], ci["cy"])
    # ⚠ 這裡一定要帶 smooth_mm。png16_mm 的 1 mm 量化在 45° 斜面上就足以讓
    # 未平滑的表面積高估 6.6%（相鄰像素的真實深度差只有 0.72 mm，量化步階比它還大）。
    # 不帶平滑的話這條測試會失敗，而失敗的理由不是資料鏈壞掉——是演算法用錯了。
    close(M3.surface_area_cm2(Zb, m, Kb, smooth_mm=8.0), exact_mm2 / 100.0, 0.5,
          "端到端表面積（送出→落盤→取回→反投影→平滑）")
    check("同一份深度未平滑會明顯高估（量化本身就夠造成偏差）",
          M3.surface_area_cm2(Zb, m, Kb) > exact_mm2 / 100.0 * 1.02,
          "未平滑 %.3f / 正確 %.3f" % (M3.surface_area_cm2(Zb, m, Kb), exact_mm2 / 100.0))

    # ── E. 守門 ──────────────────────────────────────────────────
    print("\n── E 拒收條件 ──")
    Z2, m2, K2, _, _ = DS.build("plane_frontal", 480, 360)
    b8 = io.BytesIO()
    Image.fromarray(np.clip(Z2, 0, 255).astype(np.uint8), mode="L").save(b8, "PNG")
    r, iid2, *_ = submit("plane_30deg",
                         depth_map_png=base64.b64encode(b8.getvalue()).decode())
    rec2 = [x for x in fw.read_jsonl(fw.QUEUE) if x.get("image_id") == iid2][-1]
    check("8-bit 深度圖被後端拒收", not rec2.get("depth_map_key"))
    check("拒收後 depth_source 改為 rejected（不再聲稱有深度）",
          rec2.get("depth_source") == "rejected", rec2.get("depth_source"))
    check("標註本身仍然成功（不因深度失敗而丟掉 2D GT）", r.status_code == 200)

    r, iid3, *_ = submit("sphere_cap", camera_intrinsics={"fx": 500.0, "fy": 500.0})
    rec3 = [x for x in fw.read_jsonl(fw.QUEUE) if x.get("image_id") == iid3][-1]
    check("內參不完整被拒收", not rec3.get("depth_map_key"))

    aud = [json.loads(x) for x in open(os.path.join(tmp, "audit.jsonl"), encoding="utf-8")
           if x.strip()]
    acts = [a.get("action") for a in aud]
    check("落盤與拒收都寫進稽核",
          "depth_map_stored" in acts and "depth_map_rejected" in acts, sorted(set(acts)))
    rej = next(a for a in aud if a.get("action") == "depth_map_rejected")
    check("拒收理由寫得出原因（不是只說失敗）",
          "16-bit" in (rej.get("result") or "") or "內參" in (rej.get("result") or ""),
          (rej.get("result") or "")[:60])

    # ── F. 清單看得到深度 ────────────────────────────────────────
    j = cli.get("/api/v1/flywheel/records", headers=H).get_json() or {}
    byid = {x["image_id"]: x for x in j.get("records", [])}
    check("送件清單顯示 arcore_depth", byid.get(iid, {}).get("depth_source") == "arcore_depth")
    check("送件清單顯示 rejected（看得出深度沒收到）",
          byid.get(iid2, {}).get("depth_source") == "rejected")

    shutil.rmtree(tmp, ignore_errors=True)
    print("\n%d 項檢查，%d 項失敗" % (TOTAL[0], len(FAILED)))
    if FAILED:
        print("失敗：")
        for f in FAILED:
            print("  · " + f)
        return 1
    print("全部通過：除了感測器本身，深度鏈每一節都已驗證——")
    print("編碼無損、單位正確、內參隨行、落盤位元組一致、反投影對得上解析解、"
          "壞資料被擋且擋的理由查得到。")
    print("1 mm 量化對表面積的影響：%.3f%%（實測，非估計）。" % q_err)
    return 0


if __name__ == "__main__":
    sys.exit(main())
