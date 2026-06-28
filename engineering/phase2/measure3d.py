# -*- coding: utf-8 -*-
"""M3：LiDAR 深度 → 3D 表面積/體積（WoundAI3D 核心）。
輸入：對齊的 RGB、深度圖(公尺或公釐)、相機內參 K(fx,fy,cx,cy)、傷口遮罩(bool)。
原理：每像素反投影為 3D 點 → 對遮罩內的深度網格三角化 → 累加三角形面積=真實表面積。
對曲面/斜拍傷口優於 2D 投影(後者必低估)。深度單位請統一為 mm(depth_scale 轉換)。
資料來源：Record3D 匯出(RGB+depth+intrinsics) 或 ARKit sceneDepth+camera.intrinsics。"""
import numpy as np

def backproject(depth_mm, K):
    """depth_mm:(H,W) mm；回傳 (H,W,3) 相機座標 mm。深度<=0 視為無效(NaN)。"""
    H, W = depth_mm.shape
    fx, fy, cx, cy = K
    u, v = np.meshgrid(np.arange(W), np.arange(H))
    Z = depth_mm.astype(np.float64)
    X = (u - cx) * Z / fx
    Y = (v - cy) * Z / fy
    P = np.stack([X, Y, Z], -1)
    P[Z <= 0] = np.nan
    return P

def _tri_area(a, b, c):
    return 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=-1)

def surface_area_cm2(depth_mm, mask, K):
    """遮罩內 3D 表面積(cm²)。對每個 2x2 像素格切兩三角形，全頂點有效且在遮罩內才計入。"""
    P = backproject(depth_mm, K); m = np.asarray(mask, bool)
    p00 = P[:-1, :-1]; p10 = P[1:, :-1]; p01 = P[:-1, 1:]; p11 = P[1:, 1:]
    m00 = m[:-1, :-1] & m[1:, :-1] & m[:-1, 1:] & m[1:, 1:]
    valid = m00 & ~np.isnan(p00[..., 2]) & ~np.isnan(p10[..., 2]) & ~np.isnan(p01[..., 2]) & ~np.isnan(p11[..., 2])
    t1 = _tri_area(p00, p10, p11); t2 = _tri_area(p00, p11, p01)
    area_mm2 = np.nansum(np.where(valid, t1 + t2, 0.0))
    return float(area_mm2) / 100.0

def projected_area_cm2(mask, K, depth_mm=None, z_mm=None):
    """2D 投影面積(cm²)：用遮罩內平均深度的像素尺度換算(對照組，曲面會低估)。"""
    m = np.asarray(mask, bool); fx, fy, _, _ = K
    if z_mm is None:
        z_mm = float(np.nanmean(np.where(m, depth_mm, np.nan)))
    ppmm_x = fx / z_mm; ppmm_y = fy / z_mm
    return float(m.sum()) / (ppmm_x * ppmm_y) / 100.0

def volume_cm3(depth_mm, mask, K, baseline="plane_fit"):
    """傷口體積(cm³)：以傷口邊緣擬合基準面，積分(基準-表面)深度差×像素面積。粗估。"""
    P = backproject(depth_mm, K); m = np.asarray(mask, bool)
    # 邊緣環帶擬合平面 z=ax+by+c（相機座標）
    import numpy as _np
    from numpy.linalg import lstsq
    edge = m & ~_np.asarray(__import__("scipy.ndimage", fromlist=["binary_erosion"]).binary_erosion(m, iterations=8)) if False else None
    # 簡化：用遮罩外擴環帶
    return None  # 體積留待真實資料校準（曲率/基準面定義敏感）
