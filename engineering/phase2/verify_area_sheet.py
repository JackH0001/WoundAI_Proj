# -*- coding: utf-8 -*-
"""面積驗證單診斷:隔離「尺度(ArUco/透視)」vs「描邊(AI/人工)」誤差環節。
以 HSV 色彩直接分割紅色標準傷口(繞過 AI 與人工描邊),用 marker 比例法算 cm² 與真值比對:
  色彩分割 ≈ 真值 → 尺度正確,誤差來自描邊/舊 shoelace 環節(App 已改筆刷raster版);
  色彩分割 也偏低/偏高 → 尺度問題(斜拍透視:傷口與 marker 分處兩側 px/mm 不同;或 marker 偵測偏差)。
建議同時:正拍(垂直)一張、斜拍一張各跑一次,看誤差是否隨角度收斂。
用法: python engineering/phase2/verify_area_sheet.py --img <照片> --true 3.05
輸出:偵測結果 + <照片>_diag.png(目視疊圖)。"""
import sys, os, argparse
import numpy as np, cv2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aruco_calibrate as ac


def imread_u(path):
    """支援中文/Unicode 路徑的 imread(Windows cv2.imread 吃不了非 ASCII 路徑)。"""
    try:
        data = np.fromfile(path, dtype=np.uint8)
        if data.size == 0:
            return None
        return cv2.imdecode(data, cv2.IMREAD_COLOR)
    except Exception:
        return None


def imwrite_u(path, img):
    """支援中文/Unicode 路徑的 imwrite。"""
    ok, buf = cv2.imencode(os.path.splitext(path)[1] or ".png", img)
    if ok:
        buf.tofile(path)
    return ok


def seg_red(img_rgb):
    """分割印刷深紅/暗紅傷口(高S,紅色調)。回最大連通遮罩與其輪廓。"""
    hsv = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2HSV)
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    m = (((h < 12) | (h > 168)) & (s > 80) & (v > 40)).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return m, None
    big = max(cnts, key=cv2.contourArea)
    keep = np.zeros_like(m); cv2.drawContours(keep, [big], -1, 1, -1)
    return keep, big


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--img", required=True)
    ap.add_argument("--true", type=float, required=True, help="驗證單真實面積 cm²(印在單上)")
    ap.add_argument("--marker-mm", type=float, default=12.0)
    a = ap.parse_args()

    bgr = imread_u(a.img)
    if bgr is None:
        print("讀不到影像:", a.img); return 1
    img = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB); H, W = img.shape[:2]
    print(f"影像 {W}x{H}")

    det = ac.detect_marker(img)
    if det is None:
        print("✗ ArUco 未偵測(檢查貼紙入鏡/對焦)"); return 1
    c = np.array(det[0]).reshape(-1, 2)
    side = float(np.mean([np.linalg.norm(c[i] - c[(i + 1) % 4]) for i in range(4)]))
    ppm = side / a.marker_mm
    print(f"✓ ArUco  marker邊≈{side:.1f}px → {ppm:.2f} px/mm")

    mask, cnt = seg_red(img)
    if cnt is None:
        print("✗ 色彩分割失敗(紅色範圍不符?)"); return 1
    star_px = float(cv2.contourArea(cnt))
    area = float(ac.measure_area_cm2_ratio(mask, det[0], marker_mm=a.marker_mm))
    err = (area - a.true) / a.true * 100
    print(f"✓ 色彩分割 傷口輪廓 {star_px:.0f}px² → 比例法 {area:.2f} cm²  (真值 {a.true} cm², 誤差 {err:+.1f}%)")

    # 目視疊圖
    vis = bgr.copy()
    cv2.drawContours(vis, [cnt], -1, (0, 255, 255), 3)
    cv2.polylines(vis, [c.astype(np.int32)], True, (0, 255, 0), 3)
    cv2.putText(vis, f"color-seg {area:.2f} cm2 (true {a.true}) err {err:+.1f}%",
                (10, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 255), 2)
    out = os.path.splitext(a.img)[0] + "_diag.png"
    imwrite_u(out, vis)
    print("目視疊圖:", out)

    print("\n判讀:")
    print("  |誤差|≤5%  → 尺度正確;App 端誤差來自描邊(已改筆刷raster重算,舊 shoelace/自交/簡化問題已除)")
    print("  誤差顯著(如 -20%) → 尺度環節:斜拍透視(傷口與貼紙分處兩側)為主;請正拍重測比對,")
    print("     若正拍收斂→透視;仍偏→ ArUco 角點/鏡頭畸變,再深入")
    return 0


if __name__ == "__main__":
    sys.exit(main())
