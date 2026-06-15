"""Track A1：半自動分割推論轉接層。串接 FeatureFlags + ModelRegistry + 前處理 SSOT。
缺模型／旗標關閉 → 回 None（graceful degrade，禁止偽造遮罩）；有模型 → 產 AI 初稿供修邊 UI。"""
import os, sys, json
import numpy as np
P0 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "phase0")
sys.path.insert(0, P0)
from preprocess_consistency import preprocess  # 共用 SSOT 前處理

def _cfg(model_key):
    j = json.load(open(os.path.join(P0, "preprocessing.json"), encoding="utf-8"))
    return j["models"][model_key]

def _sigmoid(x): return 1.0 / (1.0 + np.exp(-x))

def segment(img_rgb_uint8, model_id, flags, registry, flag_name="semi_auto_segmentation"):
    """回 dict：status ∈ {disabled, model_unavailable, ok}；mask=bool HxW 或 None；confidence∈[0,1] 或 None。"""
    if not flags.is_enabled(flag_name):
        return {"status": "disabled", "mask": None, "confidence": None, "model_id": model_id}
    path = registry.require(model_id)
    if path is None:                       # 缺模型：絕不偽造
        return {"status": "model_unavailable", "mask": None, "confidence": None, "model_id": model_id}
    import onnxruntime as ort
    from PIL import Image
    meta = registry.m[model_id]
    cfg = _cfg(meta.get("preprocess", "wsm"))
    W, H = cfg["input_size"]
    img = np.asarray(Image.fromarray(img_rgb_uint8).resize((W, H), Image.BILINEAR))
    x = preprocess(img, cfg).astype(np.float32)
    sess = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
    out = sess.run(None, {sess.get_inputs()[0].name: x})[0]
    prob = np.squeeze(out).astype(np.float32)
    if prob.min() < 0.0 or prob.max() > 1.0:     # 若輸出非 [0,1] 視為 logits
        prob = _sigmoid(prob)
    thr = float(cfg.get("threshold", 0.5))
    mask = prob > thr
    region = prob[mask]
    conf = float(np.clip(region.mean() if region.size else float(prob.mean()), 0.0, 1.0))
    return {"status": "ok", "mask": mask.astype(bool), "confidence": conf,
            "model_id": model_id, "threshold": thr, "shape": list(mask.shape)}

def draft_to_ui_json(result, out_path):
    """把 AI 初稿轉成修邊 UI 可載入的 JSON（供 /segment → 前端橋接）。"""
    if result.get("mask") is None:
        payload = {"status": result["status"], "draft": None}
    else:
        m = result["mask"]
        payload = {"status": "ok", "h": int(m.shape[0]), "w": int(m.shape[1]),
                   "confidence": result["confidence"], "model_id": result["model_id"],
                   "data": m.astype(np.uint8).flatten().tolist()}
    json.dump(payload, open(out_path, "w"))
    return payload
