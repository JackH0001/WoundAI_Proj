# -*- coding: utf-8 -*-
"""前處理一致性：依 preprocessing.json 之 SSOT 產生張量；附『兩實作必須位元級相同』之測試樣板。"""
import os, json, numpy as np
_IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], np.float32)
_IMAGENET_STD = np.array([0.229, 0.224, 0.225], np.float32)
def preprocess(img_rgb_uint8, cfg):
    x = img_rgb_uint8.astype(np.float32)            # 假設外部已 resize 至 cfg.input_size
    if cfg["channel_order"] == "BGR": x = x[..., ::-1]
    nrm = cfg["normalize"]
    if nrm == "[-1,1]": x = x / 127.5 - 1.0
    elif nrm == "[0,1]": x = x / 255.0
    elif nrm == "imagenet": x = (x / 255.0 - _IMAGENET_MEAN) / _IMAGENET_STD
    if cfg["layout"] == "NCHW": x = np.transpose(x, (2, 0, 1))
    return np.ascontiguousarray(x[None, ...])
if __name__ == "__main__":
    import sys
    P = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "preprocessing.json"), encoding="utf-8"))
    M = P["models"]; checks = []
    # wsm：[0,1] BGR（GT-Dice 0.786 實證；先前誤記 [-1,1] RGB 僅 0.189）。回歸防呆。
    cfg = M["wsm"]; sz = cfg["input_size"][0]
    img = np.random.default_rng(0).integers(0, 256, (sz, sz, 3), dtype=np.uint8)
    a = preprocess(img, cfg)
    ref = img.astype(np.float32)[..., ::-1] / 255.0          # BGR + [0,1]
    ref = np.ascontiguousarray(ref[None, ...])
    checks.append(("wsm [0,1] BGR", np.array_equal(a, ref) and cfg["channel_order"] == "BGR" and cfg["normalize"] == "[0,1]"))
    # fusegnet：imagenet NCHW
    fcfg = M["fusegnet"]; fimg = np.random.default_rng(1).integers(0, 256, (512, 512, 3), dtype=np.uint8)
    fa = preprocess(fimg, fcfg)
    fe = np.transpose((fimg.astype(np.float32) / 255.0 - _IMAGENET_MEAN) / _IMAGENET_STD, (2, 0, 1))[None]
    checks.append(("fusegnet imagenet NCHW", fa.shape == (1,3,512,512) and np.allclose(fa, fe, atol=1e-5) and fcfg["normalize"] == "imagenet"))
    ok = all(c for _, c in checks)
    for name, c in checks: print(("PASS " if c else "FAIL "), name)
    print("consistency:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
