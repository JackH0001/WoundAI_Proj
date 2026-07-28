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


def test_來源展開_glob指到目錄也算數_且stem不碰撞(tmp_path):
    """實際踩到的坑:`--src ".../**/image"` 的 glob 命中的是**目錄**,
    舊版只把 glob 結果當檔案過濾 → 掃到 0 張。另外那種寫法下每個父目錄都叫 image,
    stem 用「父目錄_檔名」會大量碰撞而**靜默丟檔**(丟了永遠不知道少訓練了什麼)。"""
    try:
        import cv2
    except ImportError:
        import pytest; pytest.skip("需 cv2")
    from distill_pseudo_gen import collect

    root = tmp_path / "批次驗證工具"
    for d, n in [("retrain_merged/image", 3), ("retrain_merged/labels", 3),
                 ("AZH/image", 2), ("deep/sub/image", 1)]:
        (root / d).mkdir(parents=True)
        for i in range(n):
            cv2.imwrite(str(root / d / f"w{i}.png"), np.full((40, 50, 3), 100, np.uint8))

    got = collect([str(root / "**" / "image")])
    assert len(got) == 6, [p for _, p in got]              # glob 命中目錄要展開
    assert len(set(s for s, _ in got)) == 6, [s for s, _ in got]   # stem 不得碰撞
    assert not any("labels" in p for _, p in got)

    # 直接給目錄:預設不遞迴、--recursive 才往下
    assert len(collect([str(root)])) == 0
    assert len(collect([str(root)], recursive=True)) == 9

    # 同名檔在不同深度也要各自保留
    stems = {os.path.basename(p): s for s, p in got}
    assert len(set(s for s, _ in collect([str(root / "**" / "image")]))) == 6, stems


def test_已標註集不進偽標籤_內容重複只留一份(tmp_path, monkeypatch, capsys):
    """實際踩到的坑:archive 掃到的 375 張全部是已標註訓練集(旁邊就有 labels/)。
    對已標註集產偽標籤只是複述 student 早就從真值學過的東西,拉不動召回,
    還可能把評測子集(retrain_bottom)洗進訓練。工具必須自己擋下並說清楚。"""
    try:
        import cv2
    except ImportError:
        import pytest; pytest.skip("需 cv2")
    import distill_pseudo_gen as G

    for d in ("a/image", "a/labels", "b/image", "c/image"):
        (tmp_path / d).mkdir(parents=True)
    for i in range(3):
        cv2.imwrite(str(tmp_path / "a/image" / f"w{i}.png"), np.full((60, 80, 3), 50 + i * 30, np.uint8))
        cv2.imwrite(str(tmp_path / "a/labels" / f"w{i}.png"), np.zeros((60, 80), np.uint8))
    cv2.imwrite(str(tmp_path / "b/image/u0.png"), np.full((60, 80, 3), 200, np.uint8))
    import shutil; shutil.copy(tmp_path / "a/image/w0.png", tmp_path / "c/image/dup.png")

    assert G.sibling_gt(str(tmp_path / "a/image/w0.png")) is not None
    assert G.sibling_gt(str(tmp_path / "b/image/u0.png")) is None

    # 全部有 GT → 明確拒絕,不是靜默 0 張
    monkeypatch.setattr(sys, "argv", ["x", "--src", str(tmp_path / "a" / "image"), "--dry-run"])
    assert G.main() == 1
    out = capsys.readouterr().out
    assert "全部已經有人工 GT" in out and "--include-labeled" in out, out

    # 混合來源:排除有 GT(3) + 內容重複(dup 與 w0 同內容) → 只剩 b/u0
    monkeypatch.setattr(sys, "argv", ["x", "--src", str(tmp_path / "**" / "image"), "--dry-run"])
    assert G.main() == 0
    out = capsys.readouterr().out
    assert "內容重複 1" in out and "已有人工 GT 3(已排除)" in out and "候選 1 張" in out, out


def test_產生器全流程_只留通過者(monkeypatch, tmp_path):
    """用假老師跑完 main():好樣本要落地 .npy,壞樣本只進 manifest 不落地。"""
    try:
        import cv2
    except ImportError:
        import pytest; pytest.skip("需 cv2")
    import json
    import distill_pseudo_gen as G

    src = tmp_path / "image"; src.mkdir()
    # 三張內容必須不同,否則會被「內容重複」去重(那也是正確行為,見 test_內容重複只留一份)
    for k, n in enumerate(("good", "bad_empty", "bad_flip")):
        cv2.imwrite(str(src / f"{n}.png"), np.full((300, 400, 3), 60 + k * 40, np.uint8))
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
