# -*- coding: utf-8 -*-
"""印刷模擬圖的決定性色彩分割回歸(classify `seg=color` 路徑的核心)。

背景:印刷模擬圖(面積驗證單)是分布外樣本,**student 與 A∪U 雙雙回空遮罩**
(EVIDENCE_LEDGER 2026-07-28)。解法不是把印刷色塊訓練進模型——那會污染臨床分布,
而且拿 AI 當量尺去驗量測鏈等於兩個未知數解一個方程式——而是走
`verify_area_sheet.seg_red()` 的決定性 HSV 分割,**完全不碰模型**。

本測試釘住:①色彩分割在合成驗證單上誤差 <2%;②它與模型完全解耦(不 import 任何模型)。

跑法:pytest engineering/phase2/test_phantom_color_seg.py -q
"""
import os, sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def _sheets():
    import gen_area_validation_v2 as gen
    out = []
    for i, target in enumerate([1.0, 3.0, 5.0, 10.0, 16.0]):
        r = gen.gen_sheet(target, seed=100 + i, sn=f"UT{i}")
        img = r[0] if isinstance(r, (tuple, list)) else r
        if isinstance(img, dict):
            img = img.get("image")
        img = np.asarray(img)
        if img.ndim == 3 and img.shape[2] == 3:
            out.append((target, img))
    return out


def test_色彩分割面積誤差小於2percent():
    """真值由 gen_area_validation_v2 的光柵計數保證(自驗 <0.7%)。"""
    try:
        import cv2  # noqa: F401
    except ImportError:
        import pytest; pytest.skip("需 cv2")
    from verify_area_sheet import seg_red
    import aruco_calibrate as ac

    sheets = _sheets()
    assert len(sheets) == 5, f"驗證單產生失敗({len(sheets)}/5)"
    errs = []
    for truth, rgb in sheets:
        m, _c = seg_red(rgb)
        det = ac.detect_marker(rgb)
        assert det is not None, f"真值 {truth}: ArUco 未偵測(貼紙產生器壞了?)"
        area = float(ac.measure_area_cm2_ratio(m.astype(np.uint8), det[0], marker_mm=12.0))
        err = abs(area - truth) / truth
        errs.append(err)
        assert err < 0.02, f"真值 {truth} → 色彩分割 {area:.2f} 誤差 {err:.1%}"
    assert float(np.mean(errs)) < 0.015, f"平均誤差 {np.mean(errs):.2%}"


def test_色彩分割不依賴任何模型():
    """seg=color 的價值就在於與模型解耦:模型換版、訓練集變動都不該影響驗證單結果。
    若哪天有人把模型推論塞進這條路徑,這個測試會擋下來。"""
    import inspect
    import verify_area_sheet as v
    src = inspect.getsource(v.seg_red)
    for banned in ("onnxruntime", "InferenceSession", "segment_wound_ai", "torch", "student"):
        assert banned not in src, f"seg_red 不該碰 {banned}"
    mod_src = inspect.getsource(v)
    assert "onnxruntime" not in mod_src and "torch" not in mod_src


def test_空影像與純白紙回空遮罩():
    """沒有紅色目標時必須回空,不可硬湊一塊出來(會讓驗證單誤差變成假數字)。"""
    try:
        import cv2  # noqa: F401
    except ImportError:
        import pytest; pytest.skip("需 cv2")
    from verify_area_sheet import seg_red
    white = np.full((300, 400, 3), 245, np.uint8)
    m, c = seg_red(white)
    assert int((m > 0).sum()) == 0 and c is None


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
