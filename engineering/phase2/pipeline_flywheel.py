"""Track A4：端到端資料飛輪。影像→AI 初稿(路由)→醫師修邊→標註紀錄→再訓練佇列；
以 eval_harness 量初稿 vs 醫師 GT 的 Dice/IoU（=修邊前品質），並記錄 correction_iou（修邊幅度）。"""
import os, sys, json
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "phase0"))
sys.path.insert(0, os.path.join(HERE, "..", "phase1"))
import inference_router as router
from eval_harness import seg_metrics
from annotation_pipeline import make_annotation_record
def run_flywheel(img_rgb_uint8, edited_mask, flags, registry, editor_id, image_id,
                 px_per_mm=None, policy=None, queue_path=None):
    edited_mask = np.asarray(edited_mask, bool)
    res = router.route(img_rgb_uint8, flags, registry, policy)
    if res["mask"] is None:                       # graceful：無 AI 初稿→只記可靠幾何
        ai = np.zeros_like(edited_mask, bool); ai_available = False; draft_vs_gt = None
    else:
        ai = res["mask"]; ai_available = True
        draft_vs_gt = seg_metrics(ai, edited_mask)   # 初稿 vs 醫師 GT（修邊前品質）
    rec = make_annotation_record(image_id, ai, edited_mask, editor_id,
                                 model_id=res.get("model_id"), px_per_mm=px_per_mm)
    rec["ai_available"] = ai_available
    rec["routing_path"] = res.get("path")
    rec["needs_review"] = res.get("needs_review")
    rec["draft_vs_gt"] = draft_vs_gt              # Dice/IoU；None 表無 AI 初稿
    if queue_path:
        with open(queue_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return {"record": rec, "routing": res, "metrics": draft_vs_gt, "queued_to": queue_path}
