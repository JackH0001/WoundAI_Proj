"""標註工作流端到端驗證：AI 初稿 → 醫師修邊(GT) → 標註紀錄 → 再訓練佇列。
量化飛輪價值：correction_iou 越低＝模型越錯＝該筆訓練價值越高；draft_dice＝初稿品質。"""
import os, sys, json
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "phase0"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "phase1"))
from annotation_pipeline import make_annotation_record
from eval_harness import seg_metrics
def validate_workflow(cases, queue_path=None, high_value_iou=0.5):
    """cases: list of dict(name, ai_mask, edited_mask, [editor_id, px_per_mm])。
    回傳逐筆紀錄(+draft_dice/iou) 與摘要(飛輪統計)。"""
    rows = []
    for i, c in enumerate(cases):
        ai = np.asarray(c["ai_mask"], bool); ed = np.asarray(c["edited_mask"], bool)
        rec = make_annotation_record(c.get("name", f"img_{i}"), ai, ed,
                                     c.get("editor_id", "dr_demo"), px_per_mm=c.get("px_per_mm"))
        dq = seg_metrics(ai, ed)                      # 初稿 vs 醫師 GT
        rec["draft_dice"] = round(dq["dice"], 4); rec["draft_iou"] = round(dq["iou"], 4)
        rec["high_training_value"] = bool(rec["correction_iou"] < high_value_iou)
        rows.append(rec)
        if queue_path:
            with open(queue_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    n = len(rows) or 1
    cis = [r["correction_iou"] for r in rows]; dds = [r["draft_dice"] for r in rows]
    summary = {"n": len(rows),
               "mean_draft_dice": round(float(np.mean(dds)), 4),
               "mean_correction_iou": round(float(np.mean(cis)), 4),
               "n_high_training_value": int(sum(r["high_training_value"] for r in rows)),
               "total_pixels_changed": int(sum(r["pixels_changed"] for r in rows)),
               "queue_len": (sum(1 for _ in open(queue_path, encoding="utf-8")) if queue_path and os.path.exists(queue_path) else len(rows))}
    return {"rows": rows, "summary": summary}
