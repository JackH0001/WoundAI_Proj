"""Track A 量測核心：透視校正(homography) → 面積(cm²)。
以校正貼紙四角(影像座標, 已知 sticker_mm 方形) 建 image→metric 單應，將傷口遮罩 warp 到正視 metric 平面再計面積，
消除相機傾斜/貼紙非正視造成的面積偏差。"""
import numpy as np
import cv2
def order_quad(pts):
    p = np.asarray(pts, np.float32); c = p.mean(0)
    return p[np.argsort(np.arctan2(p[:, 1] - c[1], p[:, 0] - c[0]))]
def homography_image_to_metric(sticker_quad_px, sticker_mm=20.0, out_ppmm=10.0):
    """回傳 (H, out_ppmm)：H 把影像像素映到 metric 平面（每 mm = out_ppmm 像素）。"""
    src = order_quad(sticker_quad_px); s = float(sticker_mm) * float(out_ppmm)
    dst = np.array([[0, 0], [s, 0], [s, s], [0, s]], np.float32)
    return cv2.getPerspectiveTransform(src, dst), float(out_ppmm)
def measure_area_cm2(mask, H, out_ppmm, pad=20):
    """把遮罩 warp 到 metric 平面後計面積（cm²），含透視校正。"""
    m = np.asarray(mask, np.uint8); h, w = m.shape
    corners = np.array([[[0, 0], [w, 0], [w, h], [0, h]]], np.float32)
    wc = cv2.perspectiveTransform(corners, H)[0]
    minx, miny = wc.min(0); maxx, maxy = wc.max(0)
    T = np.array([[1, 0, -minx + pad], [0, 1, -miny + pad], [0, 0, 1]], np.float64)
    Ht = T @ H
    ow = int(np.ceil(maxx - minx + 2 * pad)); oh = int(np.ceil(maxy - miny + 2 * pad))
    if ow <= 0 or oh <= 0 or ow * oh > 80_000_000: return None
    warped = cv2.warpPerspective(m, Ht, (ow, oh), flags=cv2.INTER_NEAREST)
    area_mm2 = float((warped > 0).sum()) / (float(out_ppmm) ** 2)
    return area_mm2 / 100.0
def measure_area_cm2_from_quad(mask, sticker_quad_px, sticker_mm=20.0, out_ppmm=10.0):
    H, p = homography_image_to_metric(sticker_quad_px, sticker_mm, out_ppmm)
    return measure_area_cm2(mask, H, p)
