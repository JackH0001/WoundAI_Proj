"""ArUco 校正貼紙：穩健四角＋ID → homography(image→metric) 透視校正面積。
較棋盤角點穩健（detectMarkers 回傳一致角序 TL,TR,BR,BL）。marker_mm=標記實際邊長(mm)。"""
import numpy as np
import cv2
_DICT = cv2.aruco.DICT_4X4_50
def _detector():
    d = cv2.aruco.getPredefinedDictionary(_DICT)
    try:
        return ("new", cv2.aruco.ArucoDetector(d, cv2.aruco.DetectorParameters()))
    except AttributeError:
        return ("old", d)
def detect_marker(image_rgb):
    """回傳 (corners 4x2 影像座標 TL,TR,BR,BL, id) 或 None。"""
    gray = cv2.cvtColor(np.asarray(image_rgb), cv2.COLOR_RGB2GRAY)
    mode, det = _detector()
    if mode == "new":
        corners, ids, _ = det.detectMarkers(gray)
    else:
        corners, ids, _ = cv2.aruco.detectMarkers(gray, det, parameters=cv2.aruco.DetectorParameters_create())
    if ids is None or len(ids) == 0: return None
    return corners[0].reshape(4, 2).astype(np.float32), int(ids[0])
def homography_image_to_metric(corners, marker_mm=18.0, out_ppmm=10.0):
    s = marker_mm * out_ppmm
    dst = np.array([[0, 0], [s, 0], [s, s], [0, s]], np.float32)   # 對應 TL,TR,BR,BL
    return cv2.getPerspectiveTransform(corners.astype(np.float32), dst), out_ppmm
def measure_area_cm2(mask, corners, marker_mm=18.0, out_ppmm=10.0):
    """透視校正面積：傷口輪廓經 H 轉到 metric 平面 → 多邊形面積。"""
    H, p = homography_image_to_metric(corners, marker_mm, out_ppmm)
    cnts, _ = cv2.findContours(np.asarray(mask, np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts: return 0.0
    big = max(cnts, key=cv2.contourArea).reshape(1, -1, 2).astype(np.float32)
    metric = cv2.perspectiveTransform(big, H)[0]
    return float(abs(cv2.contourArea(metric.astype(np.float32)))) / (out_ppmm ** 2) / 100.0
