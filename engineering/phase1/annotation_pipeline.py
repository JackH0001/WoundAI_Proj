"""半自動分割『修邊即標註』管線：把 AI 初稿 + 醫師修正後遮罩 → 標註紀錄（訓練佇列用）。
修正後遮罩即為 GT 標籤；correction_iou 越低代表修正越多（AI 越不可信、訓練價值越高）。"""
import numpy as np
SCHEMA_VERSION = "1.0"
def _iou(a, b):
    a = a.astype(bool); b = b.astype(bool); u = np.logical_or(a, b).sum()
    return float(np.logical_and(a, b).sum() / u) if u else 1.0
def make_annotation_record(image_id, ai_mask, edited_mask, editor_id,
                           model_id=None, px_per_mm=None, created_at=None):
    ai = ai_mask.astype(bool); ed = edited_mask.astype(bool)
    area_px = int(ed.sum())
    return {
        "schema_version": SCHEMA_VERSION, "image_id": image_id, "source": "semi_auto_edit",
        "model_id": model_id, "editor_id": editor_id, "area_px": area_px,
        "area_mm2": (round(area_px / (px_per_mm ** 2), 3) if px_per_mm else None),
        "correction_iou": round(_iou(ai, ed), 4),
        "pixels_changed": int(np.logical_xor(ai, ed).sum()),
        "status": "pending_qc", "created_at": created_at,
    }
