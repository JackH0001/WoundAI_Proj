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


def test_偏色照片_白平衡後備救得回來_且不動既有基準():
    """實機踩到:室內冷光＋深陰影會把印刷暗紅推向洋紅(H 掉出 168 的紅色窗)、明度也掉到門檻下。
    對策是**白平衡後重試**,不是放寬 seg_red 的門檻——放寬會順便收進紫色/膚色陰影,
    而且直接推翻 EVIDENCE_LEDGER 2026-07-20 的 n=15 實拍基準。"""
    try:
        import cv2  # noqa: F401
    except ImportError:
        import pytest; pytest.skip("需 cv2")
    from verify_area_sheet import seg_red, seg_red_robust

    # 物理模型:像素 = 反射率 × 照度(白平衡誤差與陰影都是乘法,不是加法)
    refl_wound = np.array([0.545, 0.118, 0.165])
    refl_paper = np.array([0.96, 0.96, 0.96])

    def shot(illum, gain):
        il = np.array(illum, float) * gain
        img = np.zeros((200, 200, 3), np.uint8)
        img[:, :] = np.clip(refl_paper * il * 255, 0, 255).astype(np.uint8)
        img[60:140, 60:140] = np.clip(refl_wound * il * 255, 0, 255).astype(np.uint8)
        return img

    # 一般情境:strict 就夠(不該無謂觸發白平衡)
    for illum, gain in [((1, 1, 1), 1.0), ((.88, 1, 1.28), 1.0), ((.85, 1, 1.35), .45), ((1.3, 1, .7), 1.0)]:
        img = shot(illum, gain)
        assert (seg_red(img)[0] > 0).sum() > 3000, (illum, gain)
        assert seg_red_robust(img)[2] == "strict", (illum, gain)

    # 極端藍偏 + 深陰影:strict 掛掉,白平衡後備救回
    bad = shot((0.75, 1.0, 1.55), 0.30)
    assert (seg_red(bad)[0] > 0).sum() == 0, "此情境本來就該讓 strict 失手,否則測試沒在測東西"
    m, c, which = seg_red_robust(bad)
    assert which == "gray_world_wb" and (m > 0).sum() > 3000, (which, int((m > 0).sum()))


def test_robust不得改變既有n5基準():
    """robust 在正常照片上必須與 strict 逐張同值,否則就是偷偷推翻了既有證據。"""
    try:
        import cv2  # noqa: F401
    except ImportError:
        import pytest; pytest.skip("需 cv2")
    from verify_area_sheet import seg_red, seg_red_robust
    import aruco_calibrate as ac
    for truth, rgb in _sheets():
        det = ac.detect_marker(rgb)
        assert det is not None
        a_s = float(ac.measure_area_cm2_ratio(seg_red(rgb)[0].astype(np.uint8), det[0], marker_mm=12.0))
        m, _c, which = seg_red_robust(rgb)
        a_r = float(ac.measure_area_cm2_ratio(m.astype(np.uint8), det[0], marker_mm=12.0))
        assert which == "strict" and abs(a_s - a_r) < 1e-6, (truth, a_s, a_r, which)


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
