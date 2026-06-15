"""Track A（量測核心）：校正貼紙偵測 → px/mm，供精確面積換算。
支援 20mm×20mm 方形 與 20mm 直徑 圓形 兩種貼紙（sticker_mm 可調）。
缺貼紙/低信心 → found=False、px_per_mm=None（不偽造）。"""
import numpy as np
import cv2
def _binaries(gray):
    blur = cv2.GaussianBlur(gray, (5, 5), 0); out = []
    e = cv2.Canny(blur, 40, 140); out.append(cv2.dilate(e, np.ones((3, 3), np.uint8), 2))
    for bs, C in ((31, 5), (51, 7)):
        out.append(cv2.adaptiveThreshold(blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, bs, C))
    return out
def _corner_count(roi):
    if roi.size == 0: return 0
    pts = cv2.goodFeaturesToTrack(roi, 300, 0.01, 3)
    return 0 if pts is None else len(pts)
def detect_square_sticker(image_rgb, sticker_mm=20.0, min_frac=0.0004, max_frac=0.25):
    gray = cv2.cvtColor(np.asarray(image_rgb), cv2.COLOR_RGB2GRAY)
    H, W = gray.shape; imgarea = float(H * W); best = None
    for b in _binaries(gray):
        cnts, _ = cv2.findContours(b, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for c in cnts:
            area = cv2.contourArea(c)
            if area < imgarea * min_frac or area > imgarea * max_frac: continue
            peri = cv2.arcLength(c, True)
            for eps in (0.02, 0.04, 0.06):
                approx = cv2.approxPolyDP(c, eps * peri, True)
                if len(approx) != 4 or not cv2.isContourConvex(approx): continue
                (cx, cy), (rw, rh), _ = cv2.minAreaRect(c)
                if min(rw, rh) < 6: continue
                ar = max(rw, rh) / min(rw, rh)
                if ar > 1.45: continue
                x, y, ww, hh = cv2.boundingRect(c); nc = _corner_count(gray[y:y+hh, x:x+ww])
                score = nc / ar
                if best is None or score > best["score"]:
                    best = {"score": score, "side_px": (rw+rh)/2.0, "ar": ar, "corners": nc,
                            "quad": approx.reshape(-1, 2).tolist(), "center": [float(cx), float(cy)]}
                break
    if best is None: return {"found": False, "px_per_mm": None, "method": "square", "reason": "no_square_candidate"}
    ppm = best["side_px"] / float(sticker_mm); ok = (1.0 <= ppm <= 80.0) and best["corners"] >= 8
    return {"found": bool(ok), "px_per_mm": (float(ppm) if ok else None), "method": "square",
            "side_px": float(best["side_px"]), "aspect": float(best["ar"]), "corners": int(best["corners"]),
            "sticker_mm": float(sticker_mm), "quad": best["quad"], "score": float(best["corners"]),
            "reason": ("ok" if ok else "low_confidence")}
def detect_circle_sticker(image_rgb, sticker_mm=20.0):
    gray = cv2.cvtColor(np.asarray(image_rgb), cv2.COLOR_RGB2GRAY)
    H, W = gray.shape; g = cv2.medianBlur(gray, 5)
    circles = cv2.HoughCircles(g, cv2.HOUGH_GRADIENT, dp=1, minDist=H/4.0,
                               param1=120, param2=40, minRadius=8, maxRadius=int(min(H, W)/4))
    if circles is None: return {"found": False, "px_per_mm": None, "method": "circle", "reason": "no_circle"}
    x, y, r = circles[0][0]
    ppm = (2.0 * float(r)) / float(sticker_mm); ok = 1.0 <= ppm <= 80.0
    return {"found": bool(ok), "px_per_mm": (float(ppm) if ok else None), "method": "circle",
            "diameter_px": float(2*r), "center": [float(x), float(y)], "radius_px": float(r),
            "sticker_mm": float(sticker_mm), "score": float(r), "reason": ("ok" if ok else "low_confidence")}

def _color_blobs(hsv, ranges, imgarea, kmax=10):
    mask = None
    for lo, hi in ranges:
        mm = cv2.inRange(hsv, np.array(lo), np.array(hi))
        mask = mm if mask is None else (mask | mm)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3,3), np.uint8))
    nlab, lab, stats, cent = cv2.connectedComponentsWithStats(mask)
    blobs = []
    for i in range(1, nlab):
        a = stats[i, cv2.CC_STAT_AREA]
        if a < imgarea*8e-6 or a > imgarea*3e-3: continue   # 角點為「小色點」
        blobs.append((a, cent[i]))
    blobs.sort(key=lambda x: -x[0])
    return [c for _, c in blobs[:kmax]]
def _squareness(P):
    c = P.mean(0); P = P[np.argsort(np.arctan2(P[:,1]-c[1], P[:,0]-c[0]))]
    e = [np.linalg.norm(P[i]-P[(i+1)%4]) for i in range(4)]
    d = [np.linalg.norm(P[0]-P[2]), np.linalg.norm(P[1]-P[3])]
    me = float(np.mean(e))
    if me < 8: return None
    if max(e)/max(min(e),1e-6) > 1.5: return None
    if max(d)/max(min(d),1e-6) > 1.4: return None
    cv = float(np.std(e)/me)
    return {"P": P, "mean_edge": me, "cv": cv}
def detect_color_corner_sticker(image_rgb, sticker_mm=20.0):
    """以 R/B/G/Y 四彩色角點定位方形貼紙；蒐集小色點候選後搜尋最方正的四點組合。對膚色/傷口最穩。"""
    rgb = np.asarray(image_rgb); hsv = cv2.cvtColor(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), cv2.COLOR_BGR2HSV)
    imgarea = float(rgb.shape[0]*rgb.shape[1]); maxedge = 0.4*min(rgb.shape[:2])
    COL = {"red":[((0,110,70),(10,255,255)),((170,110,70),(179,255,255))],
           "blue":[((100,90,60),(132,255,255))], "green":[((38,70,50),(88,255,255))],
           "yellow":[((18,110,110),(34,255,255))]}
    cand = {k: _color_blobs(hsv, v, imgarea) for k, v in COL.items()}
    if any(len(v)==0 for v in cand.values()):
        return {"found": False, "px_per_mm": None, "method": "color_corner", "reason": "missing_color"}
    best = None
    import itertools
    for r in cand["red"]:
        for b in cand["blue"]:
            for g in cand["green"]:
                for y in cand["yellow"]:
                    P = np.array([r,b,g,y], np.float32)
                    if np.ptp(P[:,0]) > maxedge*1.6 or np.ptp(P[:,1]) > maxedge*1.6: continue
                    sq = _squareness(P)
                    if sq is None: continue
                    if best is None or sq["cv"] < best["cv"]: best = sq
    if best is None:
        return {"found": False, "px_per_mm": None, "method": "color_corner", "reason": "no_square_combo"}
    ppm = best["mean_edge"] / float(sticker_mm); ok = (1.0 <= ppm <= 80.0) and best["cv"] < 0.18
    return {"found": bool(ok), "px_per_mm": (float(ppm) if ok else None), "method": "color_corner",
            "side_px": float(best["mean_edge"]), "cv": float(best["cv"]),
            "quad": best["P"].astype(int).tolist(), "center": [float(best["P"][:,0].mean()), float(best["P"][:,1].mean())],
            "sticker_mm": float(sticker_mm), "score": float(2000.0 - best["cv"]*1000),
            "reason": ("ok" if ok else "low_confidence")}
def calibrate_from_bbox(bbox, sticker_mm=20.0):
    """assisted：使用者框選貼紙 bbox=(x0,y0,x1,y1) → px/mm（邊長取寬高平均）。精確、可靠。"""
    x0,y0,x1,y1 = bbox; side = (abs(x1-x0)+abs(y1-y0))/2.0
    return {"found": side>1, "px_per_mm": (side/float(sticker_mm) if side>1 else None),
            "method": "assisted_bbox", "side_px": float(side), "sticker_mm": float(sticker_mm)}
def calibrate_from_two_points(p1, p2, known_mm):
    """assisted：兩點（已知實際距離 known_mm）→ px/mm。"""
    d = float(np.linalg.norm(np.array(p1,float)-np.array(p2,float)))
    return {"found": d>1, "px_per_mm": (d/float(known_mm) if d>1 else None),
            "method": "assisted_2pt", "dist_px": d, "known_mm": float(known_mm)}

def detect_checkerboard_sticker(image_rgb, sticker_mm=20.0, pattern=(3,3), n_squares=5, square_mm=None):
    """最穩健：以棋盤角點偵測方形校正貼紙（黑白格 calibration target）。
    對雜亂背景/光照/透視遠比輪廓或色塊穩定（findChessboardCornersSB）。
    比例＝相鄰角點中位間距 / 每格 mm；square_mm 預設 sticker_mm/n_squares（須對印製設計一次性校準）。"""
    gray = cv2.cvtColor(np.asarray(image_rgb), cv2.COLOR_RGB2GRAY)
    found, corners = cv2.findChessboardCornersSB(gray, pattern)
    if not found:
        g2 = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        found, corners = cv2.findChessboardCornersSB(g2, pattern)
        if found: corners = corners / 2.0
    if not found:
        return {"found": False, "px_per_mm": None, "method": "checkerboard", "reason": "no_checkerboard"}
    P = corners.reshape(-1, 2)
    nn = []
    for i in range(len(P)):
        d = np.linalg.norm(P - P[i], axis=1); d[i] = np.inf; nn.append(d.min())
    sp = float(np.median(nn))                       # 一格邊長(px)
    sq_mm = float(square_mm if square_mm else sticker_mm / float(n_squares))
    ppm = sp / sq_mm; ok = 1.0 <= ppm <= 80.0
    cx, cy = float(P[:, 0].mean()), float(P[:, 1].mean())
    return {"found": bool(ok), "px_per_mm": (float(ppm) if ok else None), "method": "checkerboard",
            "square_px": sp, "square_mm": sq_mm, "n_corners": int(len(P)), "center": [cx, cy],
            "score": 3000.0, "sticker_mm": float(sticker_mm), "reason": ("ok" if ok else "low_confidence")}

def calibrate(image_rgb, sticker_mm=20.0, shape="auto", assist_bbox=None, assist_points=None):
    """回傳校正結果。assisted 優先（最可靠）：assist_bbox=(x0,y0,x1,y1) 或 assist_points=(p1,p2,known_mm)。
    否則 auto best-effort：僅接受高信心 color_corner(cv<0.05) 或 circle；信心不足 → found=False，建議改 assisted。"""
    if assist_bbox is not None:
        return calibrate_from_bbox(assist_bbox, sticker_mm)
    if assist_points is not None:
        return calibrate_from_two_points(assist_points[0], assist_points[1], assist_points[2])
    cb = detect_checkerboard_sticker(image_rgb, sticker_mm) if shape in ("square", "auto") else {"found": False, "px_per_mm": None, "score": -1}
    cc = detect_color_corner_sticker(image_rgb, sticker_mm) if shape in ("square", "auto") else {"found": False}
    if not (cc.get("found") and cc.get("cv", 1) < 0.05): cc = {"found": False, "px_per_mm": None, "score": -1}
    ci = detect_circle_sticker(image_rgb, sticker_mm) if shape in ("circle", "auto") else {"found": False, "px_per_mm": None, "score": -1}
    cands = [d for d in (cb, cc, ci) if d.get("found")]
    if not cands:
        return {"found": False, "px_per_mm": None, "method": None, "reason": "auto_low_confidence_use_assisted"}
    return max(cands, key=lambda d: d.get("score", 0))
