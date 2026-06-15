"""Track A3：邊緣／雲端推論路徑分離 + 信心值門檻。
邊緣優先（on-device wsm）→ 缺則雲端（fusegnet）→ 再缺則 stub；confidence < min_confidence 標記 needs_review。"""
import os, json
import seg_infer
def load_policy(path=None):
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "routing_policy.json")
    return {k: v for k, v in json.load(open(path, encoding="utf-8")).items() if not k.startswith("_")}
def _order(policy):
    edge = ("edge", policy.get("edge_model")); cloud = ("cloud", policy.get("cloud_model"))
    seq = [edge, cloud] if policy.get("prefer", "edge") == "edge" else [cloud, edge]
    fb = policy.get("fallback_model")
    if fb: seq.append(("fallback", fb))
    return [(lbl, m) for lbl, m in seq if m]
def route(img_rgb_uint8, flags, registry, policy=None, flag_name="semi_auto_segmentation"):
    policy = policy or load_policy()
    if not flags.is_enabled(flag_name):
        return {"status": "disabled", "mask": None, "confidence": None, "path": None, "needs_review": None, "model_id": None}
    for label, mid in _order(policy):
        if not registry.is_available(mid):
            continue
        res = seg_infer.segment(img_rgb_uint8, mid, flags, registry, flag_name)
        if res["status"] == "ok":
            minc = float(policy.get("min_confidence", 0.5))
            res["path"] = label
            res["needs_review"] = bool(res["confidence"] < minc)
            res["min_confidence"] = minc
            return res
    return {"status": "model_unavailable", "mask": None, "confidence": None, "path": None, "needs_review": None, "model_id": None}
