#!/usr/bin/env python3
import argparse
import base64
import io
import json
import math
import os
import sys
from typing import Optional, Tuple

import cv2
import numpy as np
import requests


def detect_sticker_cm_per_pixel(image_path: str, expected_diameter_mm: float = 20.0) -> Tuple[float, float]:
    """
    以霍夫圓檢測估算貼紙直徑(像素)，計算 pixels/mm 與 cm/pixel。
    回傳: (pixels_per_mm, cm_per_pixel)
    """
    img = cv2.imread(image_path)
    if img is None:
        raise RuntimeError(f"讀取影像失敗: {image_path}")

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.medianBlur(gray, 5)

    h, w = gray.shape[:2]
    minR = max(10, min(h, w) // 40)
    maxR = max(20, min(h, w) // 4)

    circles = None
    # 多組參數嘗試
    for p1 in [100, 150, 200]:
        for p2 in [20, 30, 40, 60, 80, 100]:
            cs = cv2.HoughCircles(
                gray,
                cv2.HOUGH_GRADIENT,
                dp=1.2,
                minDist=min(h, w) // 8,
                param1=p1,
                param2=p2,
                minRadius=minR,
                maxRadius=maxR,
            )
            if cs is not None:
                circles = cs
                break
        if circles is not None:
            break

    if circles is None:
        raise RuntimeError("未偵測到校準貼紙圓形，請確認影像清晰且貼紙可見")

    circles = np.uint16(np.around(circles))
    # 取最大圓當作貼紙
    largest = max(circles[0, :], key=lambda c: c[2])
    radius_px = float(largest[2])
    diameter_px = radius_px * 2.0

    pixels_per_mm = diameter_px / expected_diameter_mm
    cm_per_pixel = 1.0 / (pixels_per_mm * 10.0)

    # 合理性檢查
    if not (1.0 <= pixels_per_mm <= 50.0):
        print(f"警告: pixels_per_mm 非常值: {pixels_per_mm:.3f}")
    if not (0.002 <= cm_per_pixel <= 0.1):
        print(f"警告: cm_per_pixel 可能不合理: {cm_per_pixel:.5f}")

    return pixels_per_mm, cm_per_pixel


def call_backend_analyze(image_path: str, base_url: str, cm_per_pixel: float) -> dict:
    url = base_url.rstrip('/') + '/api/analyze_wound'
    files = {
        'image': (os.path.basename(image_path), open(image_path, 'rb'), 'image/jpeg')
    }
    calibration = {'cm_per_pixel': cm_per_pixel}
    data = {
        'calibration_data': json.dumps(calibration)
    }
    resp = requests.post(url, files=files, data=data, timeout=60)
    resp.raise_for_status()
    return resp.json()


def main():
    parser = argparse.ArgumentParser(description='用貼紙標準比對後端量測與App數據')
    parser.add_argument('--image', required=True, help='測試影像路徑（含20mm校準貼紙）')
    parser.add_argument('--base-url', default='http://localhost:5000', help='後端伺服器位址')
    parser.add_argument('--app-area', type=float, default=None, help='App 端量測的面積 (cm^2)')
    parser.add_argument('--app-perimeter', type=float, default=None, help='App 端量測的周長 (cm)')
    args = parser.parse_args()

    pixels_per_mm, cm_per_pixel = detect_sticker_cm_per_pixel(args.image)
    print(f"偵測貼紙: pixels_per_mm={pixels_per_mm:.3f}, cm_per_pixel={cm_per_pixel:.5f}")

    result = call_backend_analyze(args.image, args.base_url, cm_per_pixel)
    ok = bool(result.get('success', False))
    if not ok:
        print('後端分析失敗:', result)
        sys.exit(2)

    measurements = result.get('analysis', {}).get('measurements', {})
    area_cm2 = float(measurements.get('area_cm2', 0.0))
    perimeter_cm = float(measurements.get('perimeter_cm', 0.0))
    print(f"後端量測: 面積={area_cm2:.3f} cm^2, 周長={perimeter_cm:.3f} cm")

    if args.app_area is not None:
        area_err = (area_cm2 - args.app_area)
        area_rel = (area_err / args.app_area) * 100.0 if args.app_area > 0 else float('nan')
        print(f"面積與App差異: {area_err:.3f} cm^2 ({area_rel:.2f}%)")

    if args.app_perimeter is not None:
        peri_err = (perimeter_cm - args.app_perimeter)
        peri_rel = (peri_err / args.app_perimeter) * 100.0 if args.app_perimeter > 0 else float('nan')
        print(f"周長與App差異: {peri_err:.3f} cm ({peri_rel:.2f}%)")

    print("建議: 若差異 > 8%，請檢查 cm_per_pixel 一致性與遮罩對齊、貼紙是否與傷口共平面。")


if __name__ == '__main__':
    main()

