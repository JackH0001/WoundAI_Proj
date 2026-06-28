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

# ===== 方案2:分類飛輪標註(組織/傷口類型) =====
# 約束:GT 須醫師驗證(doctor_verified)。未驗證者不得進訓練集。資料量達門檻才可訓(否則誠實延後)。
TISSUE_CLASSES = ["necrosis", "slough", "granulation", "epithelial", "other"]  # 對應 wound_classifier 組織碼 1-5
WOUNDTYPE_CLASSES = ["pressure", "diabetic_foot", "venous", "arterial", "burn", "surgical", "other"]
MIN_PER_CLASS = 50          # 每類最少醫師驗證樣本才建議訓練(務實下限,可調)
MIN_TYPE_TOTAL = 300        # 傷口類型分類總量下限

def make_tissue_annotation_record(image_id, tissue_classmap, editor_id,
                                  doctor_verified=False, wound_type=None,
                                  model_id=None, created_at=None):
    """組織分型標註(像素級 classmap=醫師確認的組織碼圖)+ 可選整張傷口類型標籤。
    tissue_classmap:HxW uint8,碼 0=遮罩外,1=壞死,2=腐肉,3=肉芽,4=上皮,5=其他。"""
    import numpy as _np
    cm = _np.asarray(tissue_classmap)
    tot = int((cm > 0).sum())
    frac = {c: (float((cm == i + 1).sum()) / tot if tot else 0.0) for i, c in enumerate(TISSUE_CLASSES)}
    assert wound_type is None or wound_type in WOUNDTYPE_CLASSES, f"未知 wound_type:{wound_type}"
    return {
        "schema_version": SCHEMA_VERSION, "image_id": image_id, "source": "tissue_label",
        "task": "tissue_segmentation", "model_id": model_id, "editor_id": editor_id,
        "doctor_verified": bool(doctor_verified),     # ★ GT 須醫師驗證
        "tissue_frac": {k: round(v, 4) for k, v in frac.items()},
        "wound_type": wound_type, "n_px": tot,
        "status": "verified" if doctor_verified else "pending_qc", "created_at": created_at,
    }

def aggregate_classifier_manifest(records):
    """聚合分類飛輪訓練清單。只納入 doctor_verified;回報每類樣本數與訓練就緒度(務實延後依據)。"""
    verified = [r for r in records if r.get("doctor_verified")]
    tissue_present = {c: 0 for c in TISSUE_CLASSES}
    for r in verified:
        for c in TISSUE_CLASSES:
            if r.get("tissue_frac", {}).get(c, 0) >= 0.05:  # 該組織存在(占比≥5%)
                tissue_present[c] += 1
    type_counts = {c: 0 for c in WOUNDTYPE_CLASSES}
    for r in verified:
        wt = r.get("wound_type")
        if wt in type_counts: type_counts[wt] += 1
    tissue_ready = all(tissue_present[c] >= MIN_PER_CLASS for c in ("necrosis", "slough", "granulation"))
    type_total = sum(type_counts.values())
    type_ready = type_total >= MIN_TYPE_TOTAL and sum(1 for v in type_counts.values() if v >= MIN_PER_CLASS) >= 2
    return {
        "n_total": len(records), "n_verified": len(verified),
        "tissue_class_present_counts": tissue_present, "woundtype_counts": type_counts,
        "tissue_train_ready": tissue_ready, "woundtype_train_ready": type_ready,
        "min_per_class": MIN_PER_CLASS, "min_type_total": MIN_TYPE_TOTAL,
        "recommendation": (
            "可啟動組織分類訓練" if tissue_ready else
            f"組織標註不足(需每類≥{MIN_PER_CLASS} 醫師驗證);續收標註,勿硬訓"),
        "note": "未達門檻即誠實延後訓練;對外勿稱 AI 診斷;GT 限醫師驗證",
    }
