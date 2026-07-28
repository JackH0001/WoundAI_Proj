# -*- coding: utf-8 -*-
"""偽標籤品質把關的單元測試(合成機率圖,免模型免資料)。

偽標籤蒸餾唯一的護欄就是這組免-GT 指標——它壞了,老師的錯誤會被靜默蒸餾進學生,
而且因為沒有 GT，事後根本查不出是哪一步壞的。所以把每條規則都釘成回歸。

跑法:pytest engineering/phase2/test_pseudo_gate.py -q
"""
import os, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from distill_pseudo_gen import gate_metrics, GATE  # noqa: E402


def disc(cx=128, cy=128, r=40, p_in=0.95, size=256, blur=0.0):
    """畫一個圓形機率圖;blur>0 讓邊界帶一圈中間值(模擬老師猶豫)。"""
    yy, xx = np.mgrid[0:size, 0:size]
    d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    p = np.where(d <= r, p_in, 0.02).astype(np.float32)
    if blur > 0:
        ring = (d > r) & (d <= r + blur)
        p[ring] = 0.5
    return p


def test_乾淨遮罩通過():
    p = disc()
    m = gate_metrics(p, p)
    assert m["accept"], m["reasons"]
    assert m["tta_iou"] == 1.0 and m["mean_prob"] > 0.9


def test_翻轉不一致被擋():
    """最強的免-GT 不確定性訊號:老師換個方向就給不同答案 = 這張它其實不會。"""
    p = disc(cx=90); pf = disc(cx=180)
    m = gate_metrics(p, pf)
    assert not m["accept"] and any("翻轉不一致" in r for r in m["reasons"]), m


def test_空遮罩與失控全圖都被擋():
    empty = np.full((256, 256), 0.01, np.float32)
    assert not gate_metrics(empty, empty)["accept"]
    whole = np.full((256, 256), 0.95, np.float32)
    m = gate_metrics(whole, whole)
    assert not m["accept"] and any("過大" in r for r in m["reasons"]), m


def test_老師不確定被擋():
    p = disc(p_in=0.45)      # 剛過門檻但毫無把握
    m = gate_metrics(p, p)
    assert not m["accept"] and any("不確定" in r or "邊界糊" in r for r in m["reasons"]), m


def test_邊界糊被擋():
    p = disc(r=25, blur=30)  # 模糊帶比實心區還大
    m = gate_metrics(p, p)
    assert not m["accept"] and any("邊界糊" in r for r in m["reasons"]), m


def test_碎片被擋():
    try:
        import cv2  # noqa: F401
    except ImportError:
        import pytest; pytest.skip("連通元件需 cv2")
    p = np.full((256, 256), 0.02, np.float32)
    for cx, cy in [(40, 40), (80, 60), (120, 90), (160, 130), (200, 170)]:
        p = np.maximum(p, disc(cx, cy, r=18))
    m = gate_metrics(p, p)
    assert not m["accept"] and any("碎成" in r for r in m["reasons"]), m


def test_門檻與registry一致():
    """thr 必須跟 SSOT/registry 的 student 門檻同步,否則偽標籤與部署行為對不上。"""
    assert GATE["thr"] == 0.40


def test_產生器全流程_只留通過者(monkeypatch, tmp_path):
    """用假老師跑完 main():好樣本要落地 .npy,壞樣本只進 manifest 不落地。"""
    try:
        import cv2
    except ImportError:
        import pytest; pytest.skip("需 cv2")
    import json
    import distill_pseudo_gen as G

    src = tmp_path / "image"; src.mkdir()
    for n in ("good", "bad_empty", "bad_flip"):
        cv2.imwrite(str(src / f"{n}.png"), np.full((300, 400, 3), 100, np.uint8))
    onnx = tmp_path / "onnx"; onnx.mkdir()
    for f in ("a_unet.onnx", "unetpp.onnx"): (onnx / f).write_bytes(b"stub")

    # 假老師:依「這次餵進來的是不是翻轉圖」與檔名決定輸出
    state = {"stem": None, "call": 0}
    monkeypatch.setattr(G, "build_teacher", lambda d: (None, None))

    def fake(t_a, t_u, img256):
        state["call"] += 1
        stem = state["stem"]
        flipped = state["call"] % 2 == 0          # 第 2 次呼叫 = 翻轉圖
        if stem.endswith("good"): return disc()
        if stem.endswith("bad_empty"): return np.full((256, 256), 0.01, np.float32)
        return disc(cx=180) if flipped else disc(cx=90)   # bad_flip:兩次答案不同
    monkeypatch.setattr(G, "teacher_prob", fake)

    real_gate = G.gate_metrics
    def gate_wrap(p, pf=None, **kw):        # 記錄目前處理到哪一張
        return real_gate(p, pf, **kw)
    monkeypatch.setattr(G, "gate_metrics", gate_wrap)

    real_imread = G.imread_unicode
    def imread_wrap(path):
        state["stem"] = os.path.splitext(os.path.basename(path))[0]; state["call"] = 0
        return real_imread(path)
    monkeypatch.setattr(G, "imread_unicode", imread_wrap)

    out = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", ["x", "--src", str(src), "--out", str(out),
                                      "--onnx-dir", str(onnx), "--montage"])
    assert G.main() == 0
    npys = sorted(p.stem.split("__")[-1] for p in (out / "soft").glob("*.npy"))
    assert npys == ["good"], npys
    man = json.load(open(out / "pseudo_manifest.json", encoding="utf-8"))
    assert man["total"] == 3 and man["accepted"] == 1
    rej = {r["stem"].split("__")[-1]: r["reasons"] for r in man["records"] if not r["accept"]}
    assert any("過小" in x for x in rej["bad_empty"]), rej
    assert any("翻轉不一致" in x for x in rej["bad_flip"]), rej
    assert (out / "montage_accepted.png").exists()


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
