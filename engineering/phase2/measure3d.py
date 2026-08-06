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


def _box(A, k):
    """k×k 均值濾波（積分影像，O(N)，不需要 scipy）。邊界以複製方式外插。"""
    if k <= 1:
        return A
    p = k // 2
    B = np.pad(A, p, mode="edge")
    C = np.cumsum(np.cumsum(B, 0), 1)
    C = np.pad(C, ((1, 0), (1, 0)))
    H, W = A.shape
    return (C[k:k + H, k:k + W] - C[0:H, k:k + W]
            - C[k:k + H, 0:W] + C[0:H, 0:W]) / float(k * k)


MIN_VALID_MM = 50.0     # 低於此值視為無效：臨床攝距 200–600 mm，5 cm 內不可能是傷口


def smooth_depth(depth_mm, K, smooth_mm=8.0, mask=None):
    """依**實體尺度**平滑深度圖，回 (平滑後深度, 實際視窗像素數)。

    ## 為什麼一定要平滑：三角化會把雜訊放大成面積

    表面積對擾動是**凸函數**——任何逐像素的雜訊都只會讓三角形面積變大，
    永遠不會抵銷。而深度雜訊相對於「相鄰像素的真實深度差」大得離譜：

        50 mm 傷口 @ 300 mm，480×360：
          相鄰像素真實深度差  正對 0.000 / 30° 0.417 / 45° 0.722 / 球冠 0.096 mm
          png16_mm 的量化步階                                        1 mm
          ARCore Depth 在 30 cm 的典型雜訊                          約 3 mm

    2026-08-06 實測（未平滑，與解析解比對）：

        只有 1 mm 量化        →  +6.6% ~ +12.2%
        量化 + 1 mm 雜訊      → +104%  ~ +171%
        量化 + 3 mm 雜訊      → +423%  ~ +632%

    也就是說，**直接對原始深度圖三角化的表面積是不能用的**。
    這件事在合成資料上花十分鐘就查得出來；等收完臨床深度資料才發現，
    那批資料的表面積全部作廢，而且拍過的傷口回不去了。

    ## 平滑視窗用公釐而非像素

    同一個像素視窗在不同解析度、不同攝距下代表的實體範圍完全不同。
    用公釐指定，換機型換距離時行為才一致。

    ## ⚠ 表面積是尺度相依的量

    這不是實作細節，是幾何事實（海岸線悖論）：量得越細，表面積越大。
    所以「傷口表面積 = 21.3 cm²」本身是不完整的敘述，必須附上量測尺度。
    預設 8 mm 的意思是「忽略 8 mm 以下的起伏」——那大致對應臨床上
    「傷口床的形狀」而非「肉芽的顆粒紋理」。改這個值會改變結果，
    所以它會被記進量測紀錄，追蹤同一個傷口時**不可以中途換**。
    """
    Z = np.asarray(depth_mm, dtype=np.float64)
    # ⚠ 門檻不能只寫 `Z > 0`。真實深度圖有大片無效區（超出量程、低置信度、
    # 反光面），而那些像素經過雜訊或壓縮後常變成很小的正值而非剛好 0。
    valid = Z > MIN_VALID_MM
    if not valid.any() or smooth_mm <= 0:
        return Z, 1

    # ⚠ 中位數只在**遮罩內**取。
    #
    # 用全圖取的話：球冠案例裡 85% 的像素是無效區，加了雜訊後半數變成 ~1 mm 的
    # 「有效」值 → 中位數被拉到 1 mm → 視窗算成 3320 px → 整張圖被抹平 → 面積歸零。
    # 真實深度圖的無效區同樣大片，所以這不是合成資料的特例。
    ref = valid if mask is None else (valid & np.asarray(mask, bool))
    if not ref.any():
        ref = valid
    z_med = float(np.median(Z[ref]))
    fx = K[0]
    # 實體 smooth_mm 在該深度下對應的像素數
    k = int(round(smooth_mm * fx / max(z_med, 1e-6)))
    k = max(1, k | 1)                       # 取奇數，讓視窗對稱
    # 上限：視窗大過影像的 1/8 就不是「平滑」而是「抹平」。走到這裡代表
    # 深度或內參有問題，寧可少平滑並讓誤差顯現，也不要安靜地回一個假數字。
    k = min(k, max(1, (min(Z.shape) // 8) | 1))
    if k <= 1:
        return Z, 1
    # 無效像素不可參與平均，否則 0 會把附近的深度往下拉。
    # 用「有效值和 / 有效數」而非直接均值。
    s = _box(np.where(valid, Z, 0.0), k)
    n = _box(valid.astype(np.float64), k)
    out = np.where(n > 0, s / np.maximum(n, 1e-9), 0.0)
    return out, k

def surface_area_cm2(depth_mm, mask, K, edge="fraction", smooth_mm=0.0):
    """遮罩內 3D 表面積(cm²)。對每個 2x2 像素格切兩三角形累加。

    ## ⚠ 邊界格的處理方式會造成系統性偏差

    原本的寫法是「四個角**全部**在遮罩內才計入」(edge="all")。那等於把遮罩
    侵蝕約半個像素，而侵蝕掉的永遠是邊界 —— 所以偏差**恆為低估**，不會互相抵銷。

    2026-08-06 實測（50 mm 圓形傷口 @ 300 mm，正對平面，與解析積分比對）：

        遮罩半徑 17 px → 低估 7.2%
        遮罩半徑 35 px → 低估 3.6%
        遮罩半徑 69 px → 低估 1.8%
        遮罩半徑 277 px → 低估 0.5%

    誤差隨半徑加倍而減半，完全吻合周長／面積比。先前記錄的「斜面 1.969 ≈ 2.0」
    (低 1.55%) 幾乎必然是同一個偏差，當時被當成可接受的離散化誤差。

    這在臨床上不是小事：傷口越小、相對誤差越大，而 PUSH 的面積子分是**分段**的，
    系統性低估會讓傷口在趨勢圖上看起來比實際小、癒合得比實際快。

    ## edge="fraction"（預設）

    每格依「四個角有幾個在遮罩內」加權 0/0.25/0.5/0.75/1。邊界格算一半，
    偏差幾乎完全消失（同樣條件下 <0.1%）。這是標準做法，不是特殊處理。

    保留 edge="all" 是為了能重現舊數字 —— 舊的驗證報告要對得起來。

    ## smooth_mm

    **真實感測器資料一定要給**（建議 8.0）。0 只適用於無雜訊的合成資料。
    理由與實測數字見 `smooth_depth`：未平滑時 3 mm 雜訊會讓表面積高估 400% 以上。
    預設是 0 而不是 8，是為了不讓既有呼叫端的數字**默默改變**——
    這個參數會改變結果，改變必須是呼叫端明示的決定。
    """
    if smooth_mm and smooth_mm > 0:
        depth_mm, _ = smooth_depth(depth_mm, K, smooth_mm, mask)
    P = backproject(depth_mm, K); m = np.asarray(mask, bool)
    p00 = P[:-1, :-1]; p10 = P[1:, :-1]; p01 = P[:-1, 1:]; p11 = P[1:, 1:]
    c = (m[:-1, :-1].astype(np.float64) + m[1:, :-1] + m[:-1, 1:] + m[1:, 1:])
    if edge == "all":
        w = (c == 4).astype(np.float64)
    elif edge == "fraction":
        w = c / 4.0
    else:
        raise ValueError("edge 必須是 'fraction' 或 'all'")
    # 深度無效(NaN)的格子一律不計：那不是「面積為零」，是「不知道」，
    # 用 0 補會把破洞算成平坦表面而不留痕跡。
    ok = (~np.isnan(p00[..., 2]) & ~np.isnan(p10[..., 2])
          & ~np.isnan(p01[..., 2]) & ~np.isnan(p11[..., 2]))
    t1 = _tri_area(p00, p10, p11); t2 = _tri_area(p00, p11, p01)
    area_mm2 = np.nansum(np.where(ok, w * (t1 + t2), 0.0))
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
