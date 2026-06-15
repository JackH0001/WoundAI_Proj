"""參考服務層：把 OpenAPI 契約（/segment、/annotations、/annotation-tasks）接到真實模組。
框架無關（回傳 dict + HTTP 碼），可被 Flask/FastAPI 包一層。維持 graceful degrade。"""
import io, base64, os, sys
import numpy as np
from PIL import Image
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "phase1")); sys.path.insert(0, HERE)
import inference_router as router
from annotation_pipeline import make_annotation_record
_DRAFTS = {}   # image_id -> AI 初稿遮罩（示意；生產改用 DB/cache）
def _mask_to_b64(mask):
    b = io.BytesIO(); Image.fromarray((np.asarray(mask, bool).astype(np.uint8) * 255)).save(b, format="PNG")
    return base64.b64encode(b.getvalue()).decode()
def _b64_to_mask(s):
    return np.asarray(Image.open(io.BytesIO(base64.b64decode(s))).convert("L")) > 127
def handle_segment(image_rgb, flags, registry, model_id=None, image_id=None, policy=None):
    res = router.route(np.asarray(image_rgb), flags, registry, policy)
    smap = {"ok": "ai_assistive", "disabled": "manual_fallback", "model_unavailable": "unavailable"}
    status = smap.get(res["status"], "unavailable")
    out = {"status": status}
    if res.get("model_id"): out["model_id"] = res["model_id"]
    if res["mask"] is not None:
        out["mask_png_b64"] = _mask_to_b64(res["mask"]); out["confidence"] = res["confidence"]
        out["needs_review"] = res.get("needs_review"); out["path"] = res.get("path")
        if image_id: _DRAFTS[image_id] = np.asarray(res["mask"], bool)
    http = 200 if status in ("ai_assistive", "manual_fallback") else 503
    return out, http
def handle_annotations(submit):
    ed = _b64_to_mask(submit["edited_mask_png_b64"])
    ai = _DRAFTS.get(submit["image_id"])
    if ai is None or ai.shape != ed.shape: ai = np.zeros_like(ed, bool)
    rec = make_annotation_record(submit["image_id"], ai, ed, submit["editor_id"],
                                 model_id=submit.get("model_id"), px_per_mm=submit.get("px_per_mm"))
    _DRAFTS.pop(submit["image_id"], None)
    return rec, 201
def handle_annotation_tasks():
    return {"tasks": [{"image_id": k, "status": "pending_segmentation_edit"} for k in _DRAFTS]}, 200
