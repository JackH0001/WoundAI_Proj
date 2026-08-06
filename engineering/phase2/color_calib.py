# -*- coding: utf-8 -*-
"""用 ArUco 校正貼紙上的中性色塊做白平衡與曝光正規化。

目的是**跨裝置一致**：不同手機的自動白平衡與色彩科學差異很大，
而組織分類本質上是色彩判斷（肉芽紅／腐肉黃／壞死黑／上皮淡粉）。
同一個傷口在鎢絲燈與日光燈下拍，未校正時組織比例會不同——
那個差異不是傷口變了，是燈變了。

    from color_calib import calibrate
    res = calibrate(image_bgr, marker_quad, marker_mm=12.0)
    if res.ok:
        img = res.apply(image_bgr)

## ⚠ 為什麼不用 gray-world

gray-world 假設「場景平均為灰」。一張大面積紅色傷口的照片嚴重違反這個假設，
校正會把紅色「修掉」——而紅色正是肉芽的判準。用它做臨床影像的白平衡，
會系統性地把肉芽推向腐肉的色域，且**看起來一切正常**。

貼紙上有中性色塊，就是為了不必做這種假設。

## ⚠ 為什麼只用中性色，不用 RGBY 做 3×3 色彩矩陣

印出來的貼紙**不是 SVG 的標稱色**。`#FF0000` 經過噴墨／雷射、不同紙張之後，
實際色度與 sRGB 純紅差很遠，而且每台印表機不同。拿標稱值去擬合 CCM，
會引入一個系統性且無法察覺的偏差——比不校正更糟。

中性色沒有這個問題到同樣的程度：白底與灰點就算印偏了，它們在同一張貼紙上
是一致的，而白平衡問的正是「這個中性面在當前光源下呈現什麼顏色」。

RGBY 在這裡的角色是**驗證**：校正後它們的色相應該落在該落的地方。
落不到，代表校正失敗或貼紙有問題——那是要讓人知道的事。
等貼紙用色度計實測過，再升級成完整 CCM。

## 幾何：不做色彩 blob 偵測

貼紙的幾何是已知的（20mm 白底、marker 12mm 置中、灰點 ±9mm、RGBY ±8mm），
而 ArUco 偵測已經給了 marker 的四個角。所以用**單應變換**直接算出每個色塊
在影像上的位置，比「找紅色圓點」穩健得多——後者在傷口旁邊會找到傷口。
"""
import numpy as np

# 貼紙座標系：原點在 marker 中心，單位 mm，+x 右、+y 下（與影像一致）。
# 數值來自 svg_stickers.py 的 square_svg_20mm()。
STICKER_MM = 20.0
GREY_DOTS_MM = [(-9.0, -9.0), (9.0, -9.0), (-9.0, 9.0), (9.0, 9.0)]   # #BEBEBE r=1.0
COLOR_DOTS_MM = {                                                      # r=1.5
    "R": (0.0, -8.0), "G": (0.0, 8.0), "B": (-8.0, 0.0), "Y": (8.0, 0.0),
}
# 白底取樣點：避開 marker（±6mm）、灰點（±9mm 附近）與色點（±8mm 軸上）。
# 取四個對角中段，那裡只有白紙。
WHITE_MM = [(-7.0, -4.5), (7.0, -4.5), (-7.0, 4.5), (7.0, 4.5)]

# 校正後 RGBY 應有的**色相**（HSV 的 H，OpenCV 0–179）。用色相而非 RGB：
# 印刷的飽和度與明度會偏，色相相對穩定。容差放寬到 ±25，這是「有沒有大錯」
# 的檢查，不是色度計。
EXPECT_HUE = {"R": 0, "G": 60, "B": 120, "Y": 25}
HUE_TOL = 25


class CalibResult(object):
    def __init__(self, ok, reason="", gains=None, exposure=1.0, patches=None,
                 illuminant=None, cast=0.0, hue_err=None, clipped=0.0):
        self.ok = ok
        self.reason = reason
        # 對角增益（von Kries）。BGR 順序，與 OpenCV 一致。
        self.gains = gains if gains is not None else np.ones(3)
        self.exposure = float(exposure)
        self.patches = patches or {}
        self.illuminant = illuminant          # 中性面在影像中的 BGR 原值
        self.cast = float(cast)               # 偏色強度：max/min 通道比 - 1
        self.hue_err = hue_err or {}
        self.clipped = float(clipped)         # 中性面過曝比例
        self.channel_order = "bgr"            # gains/illuminant 的通道順序

    def grey_reference(self):
        """回灰點的平均值（依 channel_order），供 wound_classifier.patch_wb 使用。

        那支函式的介面是「量到的灰塊值」而不是增益，所以直接給它原始量測值——
        中間再轉一手只會多一個對不上的機會。
        """
        ks = [v for k, v in self.patches.items() if k.startswith("K")]
        if not ks:
            return None
        m = np.mean(np.asarray(ks, dtype=np.float64), axis=0)
        return (m[::-1] if self.channel_order == "bgr" else m).tolist()   # 回 RGB

    def as_dict(self):
        """寫進 provenance 的形式。**參數要存下來**——影像會依保存政策清除，
        而事後想知道「那批資料當時的光源是什麼」只剩這幾個數字。"""
        return {
            "ok": bool(self.ok), "reason": self.reason,
            "gain_b": round(float(self.gains[0]), 5),
            "gain_g": round(float(self.gains[1]), 5),
            "gain_r": round(float(self.gains[2]), 5),
            "exposure": round(self.exposure, 5),
            "illuminant_bgr": [round(float(x), 2) for x in (self.illuminant
                                                            if self.illuminant is not None
                                                            else [0, 0, 0])],
            "cast": round(self.cast, 4),
            "clipped_frac": round(self.clipped, 4),
            "hue_err": {k: round(float(v), 1) for k, v in self.hue_err.items()},
            "method": "vonkries_neutral_v1",
        }

    def apply(self, img_bgr):
        """套用增益與曝光。回 uint8 BGR。

        ⚠ 這是**有損**的：增益 >1 的通道在原本就接近飽和的地方會被裁掉。
        所以原圖一定要另外保留，校正參數也要存——不能只留校正後的結果。
        """
        if not self.ok:
            return img_bgr
        a = np.asarray(img_bgr, dtype=np.float32)
        a *= (self.gains * self.exposure).astype(np.float32)[None, None, :]
        return np.clip(a, 0, 255).astype(np.uint8)


def _homography(marker_quad, marker_mm):
    """marker 四角（影像像素）→ 貼紙 mm 座標的反向單應。

    ArUco 回的角點順序是左上→右上→右下→左下（cv2.aruco 的慣例）。
    marker 邊長 marker_mm，中心為原點。
    """
    import cv2
    h = marker_mm / 2.0
    src = np.array([[-h, -h], [h, -h], [h, h], [-h, h]], dtype=np.float32)
    dst = np.asarray(marker_quad, dtype=np.float32).reshape(4, 2)
    return cv2.getPerspectiveTransform(src, dst)


def _sample(img, H, xy_mm, radius_mm, min_px=2):
    """在貼紙座標 xy_mm 處取一個半徑 radius_mm 的圓形區域中位數 BGR。

    用中位數而非平均：色點邊緣有抗鋸齒與印刷暈開，平均會被邊緣拉偏。
    """
    import cv2
    pts = np.array([[[xy_mm[0], xy_mm[1]]]], dtype=np.float32)
    c = cv2.perspectiveTransform(pts, H)[0][0]
    # 半徑也要經過同一個變換才對——貼紙斜拍時 mm→px 的比例是各向異性的。
    e = cv2.perspectiveTransform(
        np.array([[[xy_mm[0] + radius_mm, xy_mm[1]]]], dtype=np.float32), H)[0][0]
    r = float(np.hypot(*(e - c)))
    r = max(1.0, r * 0.6)                     # 內縮，避開邊緣暈開
    h, w = img.shape[:2]
    x0, x1 = int(max(0, c[0] - r)), int(min(w, c[0] + r + 1))
    y0, y1 = int(max(0, c[1] - r)), int(min(h, c[1] + r + 1))
    if x1 - x0 < min_px or y1 - y0 < min_px:
        return None, 0.0
    win = img[y0:y1, x0:x1].reshape(-1, 3).astype(np.float32)
    yy, xx = np.mgrid[y0:y1, x0:x1]
    inside = ((xx - c[0]) ** 2 + (yy - c[1]) ** 2 <= r * r).reshape(-1)
    win = win[inside] if inside.any() else win
    if len(win) < min_px:
        return None, 0.0
    clipped = float((win >= 254).any(axis=1).mean())
    return np.median(win, axis=0), clipped


def calibrate(img_bgr, marker_quad, marker_mm=12.0, order="bgr"):
    """回 CalibResult。marker_quad 為 ArUco 的四角像素座標。

    order: 影像的通道順序。app.py 的工作影像是 RGB，本模組內部以 BGR 為準
    （與 OpenCV 一致），所以 RGB 進來時先翻轉、結果再翻回去。
    ⚠ 弄錯的話增益會反向套用——紅色偏差會被當成藍色偏差修正，
    而輸出仍然是一張看起來「有被處理過」的影像。
    """
    try:
        import cv2  # noqa: F401
    except ImportError:
        return CalibResult(False, "沒有 OpenCV")
    if marker_quad is None or len(marker_quad) != 4:
        return CalibResult(False, "沒有 ArUco 四角，無法定位色卡")

    if order.lower() == "rgb":
        r = calibrate(np.asarray(img_bgr)[..., ::-1], marker_quad, marker_mm, order="bgr")
        r.gains = r.gains[::-1].copy()
        if r.illuminant is not None:
            r.illuminant = np.asarray(r.illuminant)[::-1].copy()
        # ⚠ patches 也要翻。漏掉這一行的後果實測過：grey_reference() 回的是 BGR
        # 數值卻標成 RGB，下游 patch_wb 把紅藍增益對調套用——組織分類在偏藍光源下
        # 從「肉芽 73%」變成「肉芽 0%、其他 98%」，而**沒有任何錯誤**。
        # 這正是本檔開頭警告的那個坑，寫警告的人自己踩了一次。
        r.patches = {k: list(v)[::-1] for k, v in (r.patches or {}).items()}
        r.channel_order = "rgb"
        return r

    img = np.asarray(img_bgr)
    if img.ndim != 3 or img.shape[2] != 3:
        return CalibResult(False, "不是三通道影像")

    H = _homography(marker_quad, marker_mm)
    patches, clip = {}, []

    # ⚠ 白底與灰點要**分開保存**，不能混成一堆「中性色」。
    #
    # 兩者用途不同：
    #   · 色度（增益）：白＋灰一起用，樣本越多估得越穩
    #   · 曝光：**只能用白底**。混進灰點的話基準會落在白與灰之間（本例約 203），
    #     於是連 D65 這種完全不需要校正的影像都會被提亮 15%，
    #     把暗部推過壞死的明度門檻——壞死佔比從 9.0% 掉到 3.2%，而沒有任何徵兆。
    whites, greys = [], []
    for i, p in enumerate(WHITE_MM):
        v, c = _sample(img, H, p, 1.6)
        if v is not None:
            whites.append(v); clip.append(c); patches["W%d" % i] = v.tolist()
    for i, p in enumerate(GREY_DOTS_MM):
        v, c = _sample(img, H, p, 1.0)
        if v is not None:
            greys.append(v); clip.append(c); patches["K%d" % i] = v.tolist()
    neutrals = whites + greys

    if len(neutrals) < 3 or not whites:
        return CalibResult(False, "取不到足夠的中性色塊（貼紙被遮住或超出畫面？）")

    clipped = float(np.mean(clip)) if clip else 0.0
    N = np.array(neutrals, dtype=np.float64)
    # ⚠ 過曝的中性面不能拿來估光源：三個通道都被裁到 255，看起來完全中性，
    # 於是算出「不需要校正」——而實際上那張照片曝光爆掉、色彩資訊已經沒了。
    # 這是會安靜給出錯誤結論的情況，必須擋。
    if clipped > 0.25:
        return CalibResult(False, "白色參考過曝（%.0f%%），無法估計光源；請降低曝光重拍"
                           % (100 * clipped), clipped=clipped)

    illum = N.mean(axis=0)                      # BGR
    if float(illum.min()) < 12.0:
        return CalibResult(False, "白色參考過暗（最暗通道 %.0f），訊噪比不足以估計光源"
                           % illum.min(), clipped=clipped)

    # von Kries 對角校正：把中性面拉回中性（三通道相等）。
    # 以幾何平均為目標而非取最大值——後者會把整張圖提亮，把原本接近飽和的
    # 區域推過 255，而那多半就是傷口的高光處。
    target = float(np.exp(np.mean(np.log(illum))))
    gains = target / illum
    cast = float(illum.max() / max(illum.min(), 1e-6) - 1.0)

    # 曝光正規化：讓校正後的**白底**落在固定亮度，跨裝置才有共同基準。
    # 基準只取白底（已知反射率最高的那一面），不含灰點——見上方說明。
    # 不設 255：那會讓白底剛好飽和，而它是後續判斷是否過曝的參考。
    WHITE_TARGET = 235.0
    w_after = float(np.exp(np.mean(np.log(
        np.maximum(np.array(whites, dtype=np.float64) * gains, 1e-6)))))
    exposure = float(np.clip(WHITE_TARGET / max(w_after, 1e-6), 0.4, 2.5))

    res = CalibResult(True, "", gains=gains, exposure=exposure, patches=patches,
                      illuminant=illum, cast=cast, clipped=clipped)

    # ── RGBY：校正後的色相驗證（不是 CCM 的錨點，是校正對不對的檢查）──
    import cv2
    hue_err = {}
    for k, p in COLOR_DOTS_MM.items():
        v, _ = _sample(img, H, p, 1.5)
        if v is None:
            continue
        patches[k] = v.tolist()
        cv_ = np.clip(v * gains * exposure, 0, 255).astype(np.uint8).reshape(1, 1, 3)
        hsv = cv2.cvtColor(cv_, cv2.COLOR_BGR2HSV)[0][0]
        if int(hsv[1]) < 40:
            continue                            # 飽和度太低，色相沒有意義
        d = abs(int(hsv[0]) - EXPECT_HUE[k])
        hue_err[k] = min(d, 180 - d)            # 色相是環狀的
    res.hue_err = hue_err
    res.patches = patches

    bad = [k for k, v in hue_err.items() if v > HUE_TOL]
    if len(bad) >= 2:
        # 兩個以上色點偏離 → 多半不是印刷差異，是校正本身有問題
        #（貼紙認錯、色卡被反光蓋住、影像本身色彩已被破壞）。
        # 仍回 ok=True 但把理由帶出去，讓品質旗標與稽核看得到。
        res.reason = "校正後 %s 色相偏離 >%d°，此張色準可信度低" % ("、".join(bad), HUE_TOL)
    return res


def gray_world_gains(img_bgr):
    """gray-world 的增益。**只供對照實驗用，不要拿來校正臨床影像。**

    留在這裡是為了讓測試能證明它在大面積紅色傷口上會失敗——
    「我們知道它為什麼不行」比「我們沒用它」有價值。
    """
    a = np.asarray(img_bgr, dtype=np.float64).reshape(-1, 3)
    m = a.mean(axis=0)
    return float(np.exp(np.mean(np.log(np.maximum(m, 1e-6))))) / np.maximum(m, 1e-6)
