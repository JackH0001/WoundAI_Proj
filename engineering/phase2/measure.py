"""Track A 統一量測管線：校正(貼紙) → 透視校正(homography) → 面積(cm²) → 可解釋分類。
assisted（框選四角/bbox，精確＋透視校正）為主；auto 棋盤偵測為備（px/mm，不含透視）；無校正 → 不偽造面積。"""
import numpy as np
import geometry, calibration, wound_classifier, mask_refine
REFINE_DEFAULTS = {"open_k": 3, "close_k": 9, "border_px": 4, "keep_largest": True}
def measure_wound(image_rgb, mask, sticker_mm=20.0, sticker_quad=None, assist_bbox=None, out_ppmm=10.0,
                  refine=True, roibox=None, refine_params=None):
    mask = np.asarray(mask, bool)
    if refine:                                   # 預設後處理：去 ROI 框邊偽影/碎塊/孔洞
        rp = dict(REFINE_DEFAULTS)
        if refine_params: rp.update(refine_params)
        mask = mask_refine.refine(mask, roibox=roibox, **rp)
    if sticker_quad is not None:
        quad = np.asarray(sticker_quad, float); method = "assisted_quad"
    elif assist_bbox is not None:
        x0, y0, x1, y1 = assist_bbox
        quad = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], float); method = "assisted_bbox"
    else:
        cb = calibration.detect_checkerboard_sticker(image_rgb, sticker_mm)
        if cb.get("found"):
            ppm = cb["px_per_mm"]; area = float(mask.sum()) / (ppm ** 2) / 100.0
            res = wound_classifier.classify(image_rgb, mask, px_per_mm=ppm); res["area_cm2"] = round(area, 2)
            return {"found": True, "method": "auto_checkerboard", "perspective_corrected": False,
                    "area_cm2": round(area, 2), "px_per_mm": ppm, "classification": res, "refined": bool(refine),
                    "note": "auto 無透視校正；如需更準請 assisted 框選四角"}
        return {"found": False, "method": "no_calibration", "area_cm2": None, "refined": bool(refine),
                "note": "未偵測到校正貼紙，請框選貼紙(assisted)；不偽造面積"}
    area = geometry.measure_area_cm2_from_quad(mask, quad, sticker_mm, out_ppmm)
    # 用透視校正後面積回推有效 px/mm，讓嚴重度分數採用校正後面積
    area_px = int(mask.sum()); eff_ppmm = (np.sqrt(area_px / (area * 100.0)) if area and area_px else None)
    res = wound_classifier.classify(image_rgb, mask, px_per_mm=eff_ppmm); res["area_cm2"] = round(area, 2)
    return {"found": True, "method": method, "perspective_corrected": True,
            "area_cm2": round(area, 2), "classification": res, "refined": bool(refine)}
