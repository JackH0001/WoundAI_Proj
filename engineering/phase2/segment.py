# -*- coding: utf-8 -*-
"""Track A 分割推論（已驗證最佳設定）。前處理常數一律讀 SSOT(preprocessing.json)。
經實證(臨床照 n=3, 人工 GT)：
  smp 原始 0.737 -> smp(thr0.3+水平翻轉TTA+閉運算) 0.800 -> smp x FUSegNet 機率集成 0.855。
平台主力=輕型 smp，餵全幅、不做 ROI；難例(足部)可開 ensemble=True 取 FUSegNet 補召回。"""
import os, json, numpy as np, cv2

_HERE = os.path.dirname(os.path.abspath(__file__))
_SSOT = os.path.join(_HERE, "..", "phase0", "preprocessing.json")


def _cfg():
    with open(_SSOT, encoding="utf-8") as f:
        return json.load(f)


def _sig(x):
    return 1.0 / (1.0 + np.exp(-np.clip(x, -30, 30)))


def _norm(r, kind, P):
    r = r.astype(np.float32)
    if kind == "[-1,1]":
        return r / 127.5 - 1.0
    if kind == "[0,1]":
        return r / 255.0
    m = np.array(P["imagenet_mean"], np.float32)
    s = np.array(P["imagenet_std"], np.float32)
    return (r / 255.0 - m) / s


def _infer(sess, inp, img_rgb, sz, kind, layout, P, tta=False):
    """回傳 [0,1] 機率圖(原圖尺寸)。tta=水平翻轉平均。"""
    H, W = img_rgb.shape[:2]
    acc = np.zeros((H, W), np.float32)
    n = 0
    for flip in ((False, True) if tta else (False,)):
        im = img_rgb[:, ::-1] if flip else img_rgb
        r = cv2.resize(im, (sz, sz))
        x = _norm(r, kind, P)
        x = np.transpose(x, (2, 0, 1))[None] if layout == "NCHW" else x[None]
        o = np.squeeze(sess.run(None, {inp: np.ascontiguousarray(x.astype(np.float32))})[0]).astype(np.float32)
        if o.ndim == 3:
            o = o[0] if layout == "NCHW" else o[..., 0]
        if o.min() < 0 or o.max() > 1:
            o = _sig(o)
        o = cv2.resize(o, (W, H))
        acc += o[:, ::-1] if flip else o
        n += 1
    return acc / n


def _close(mask, W):
    k = max(7, W // 120)
    return cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((k, k), np.uint8)) > 0


def segment(img_rgb, smp_onnx, fusegnet_onnx=None, ensemble=None,
            smp_thr=0.3, ens_w=0.6, ens_thr=0.45, tta=True):
    """回傳 bool 遮罩(原圖尺寸)。預設「廣域軌」：有給 fusegnet_onnx 就走 smp×FUSegNet 集成。
    經 retrain_bottom(最難子集) 驗證：smp×FUSeg(0.873) > 微調 A∪U(0.810)，通用模型泛化更廣。
    ensemble=None（預設）：給 fusegnet → 集成(廣域軌)；未給 → 單 smp。
    ensemble=True ：0.6*smp + 0.4*FUSegNet -> thr0.45 +閉運算（廣域穩健，建議預設）。
    ensemble=False：smp(thr0.3)+水平翻轉TTA+閉運算（最快、無 fusegnet 時）。"""
    import onnxruntime as ort
    if ensemble is None:                     # 預設廣域軌：有 fusegnet 就集成
        ensemble = fusegnet_onnx is not None
    P = _cfg()
    M = P["models"]
    ms = M["smp"]  # 輕型 UNet：NCHW/imagenet/256
    ss = ort.InferenceSession(smp_onnx, providers=["CPUExecutionProvider"])
    sp = _infer(ss, ss.get_inputs()[0].name, img_rgb, ms["input_size"][0],
                ms["normalize"], ms["layout"], P, tta=tta)
    if not ensemble:
        return _close(sp > smp_thr, img_rgb.shape[1])
    if not fusegnet_onnx:
        raise ValueError("ensemble=True 需提供 fusegnet_onnx")
    fs = M["fusegnet"]
    fsess = ort.InferenceSession(fusegnet_onnx, providers=["CPUExecutionProvider"])
    fp = _infer(fsess, fsess.get_inputs()[0].name, img_rgb, fs["input_size"][0],
                fs["normalize"], fs["layout"], P, tta=False)
    return _close((ens_w * sp + (1 - ens_w) * fp) > ens_thr, img_rgb.shape[1])
