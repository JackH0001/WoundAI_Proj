"""前處理一致性：依 preprocessing.json 之 SSOT 產生張量；附『兩實作必須位元級相同』之測試樣板。"""
import json, numpy as np
def preprocess(img_rgb_uint8, cfg):
    x = img_rgb_uint8.astype(np.float32)            # 假設外部已 resize 至 cfg.input_size
    if cfg["channel_order"] == "BGR": x = x[..., ::-1]
    if cfg["normalize"] == "[-1,1]": x = x / 127.5 - 1.0
    elif cfg["normalize"] == "[0,1]": x = x / 255.0
    if cfg["layout"] == "NCHW": x = np.transpose(x, (2, 0, 1))
    return np.ascontiguousarray(x[None, ...])
if __name__ == "__main__":
    cfg = json.load(open("preprocessing.json", encoding="utf-8"))["models"]["wsm"]
    img = np.random.default_rng(0).integers(0, 256, (256, 256, 3), dtype=np.uint8)
    a = preprocess(img, cfg)
    # 模擬另一端的等價（但不同寫法）實作：
    b = np.ascontiguousarray((img.astype(np.float32)[..., ::-1] / 127.5 - 1.0)[None, ...])
    ok = np.array_equal(a, b) and a.shape == (1,256,256,3) and abs(float(a.min()) + 1) < 1e-6
    print("consistency:", "PASS" if ok else "FAIL", "| shape", a.shape, "| range", [round(float(a.min()),3), round(float(a.max()),3)])
