# -*- coding: utf-8 -*-
"""飛輪 HTTP 端點(Blueprint):/api/v1/annotation(上傳醫師驗證標註→再訓練佇列)、
/api/v1/consent/withdraw(撤回同意→下架排除訓練)。需 JWT、去識別、醫師驗證。輔助、非診斷。
app.py 註冊:from api_flywheel import flywheel_bp; app.register_blueprint(flywheel_bp)
驗證邏輯抽出為純函式(validate_annotation/append_jsonl)供契約/單元測試。"""
import os, json, time, hashlib

HERE = os.path.dirname(os.path.abspath(__file__))
QUEUE = os.path.join(HERE, "flywheel", "retrain_queue.jsonl")
WITHDRAWN = os.path.join(HERE, "flywheel", "withdrawn.jsonl")
AUDIT = os.path.join(HERE, "flywheel", "audit.jsonl")

# 標註必要欄位(去識別 + 醫師驗證守門)
REQUIRED = ["code", "gt_polygon", "exudate", "doctor_verified", "deidentified", "consent_train"]

def validate_annotation(d: dict):
    """回 (ok, 問題清單)。守門:必填齊、doctor_verified、deidentified、consent_train 皆 True。"""
    issues = []
    for k in REQUIRED:
        if k not in d: issues.append(f"缺 {k}")
    if not d.get("doctor_verified"): issues.append("未經醫師驗證(doctor_verified=false)")
    if not d.get("deidentified"): issues.append("未去識別化(deidentified=false)")
    if not d.get("consent_train"): issues.append("未取得訓練同意(consent_train=false)")
    if "code" in d and not str(d.get("code", "")).startswith("WD-"): issues.append("code 非去識別代碼(應 WD-*)")
    return (len(issues) == 0, issues)

def append_jsonl(path: str, rec: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

def audit(actor: str, action: str, code: str, result: str):
    append_jsonl(AUDIT, {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "actor": actor, "action": action, "code": code, "result": result})

def poly_sig(poly):
    """遮罩輪廓內容雜湊(座標四捨五入後序列化)→ 供去重。同一傷口遮罩→同 sig。"""
    try:
        norm = json.dumps([[round(float(p[0])), round(float(p[1]))] for p in (poly or [])], sort_keys=True)
    except Exception:
        norm = json.dumps(poly or [])
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]

def is_duplicate(path, poly):
    """佇列中是否已有相同遮罩(依 poly_sig)。撤回過的不算(下架另處理)。"""
    sig = poly_sig(poly)
    if not sig or sig == poly_sig([]) or not os.path.exists(path):
        return False
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
                if poly_sig(rec.get("gt_polygon")) == sig:
                    return True
            except Exception:
                pass
    return False

# ---- Flask Blueprint(import flask 失敗時不影響純函式測試) ----
try:
    from flask import Blueprint, request, jsonify
    from flask_jwt_extended import jwt_required, get_jwt_identity
    flywheel_bp = Blueprint("flywheel", __name__)

    @flywheel_bp.route("/api/v1/annotation", methods=["POST"])
    @jwt_required()
    def post_annotation():
        d = request.get_json(silent=True) or {}
        ok, issues = validate_annotation(d)
        actor = get_jwt_identity() or "unknown"
        if not ok:
            audit(actor, "annotation_rejected", d.get("code", "?"), ";".join(issues))
            return jsonify({"error": "標註不符上傳規範", "issues": issues}), 400
        # 去重:相同傷口遮罩已在佇列 → 自動略過(避免多次上傳灌爆同一樣本)
        if is_duplicate(QUEUE, d.get("gt_polygon")):
            audit(actor, "annotation_duplicate", d.get("code", "?"), "同遮罩已在佇列,自動略過")
            return jsonify({"status": "duplicate_skipped", "code": d.get("code"),
                            "note": "相同傷口遮罩已存在再訓練佇列,已自動略過(避免重複樣本)"}), 200
        rec = {k: d.get(k) for k in REQUIRED}
        rec["correction_iou"] = d.get("correction_iou")
        rec["care_note"] = d.get("care_note")
        rec["received_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        append_jsonl(QUEUE, rec)
        audit(actor, "annotation_enqueued", rec["code"], "ok")
        return jsonify({"status": "enqueued", "code": rec["code"], "queue": "retrain"}), 200

    @flywheel_bp.route("/api/v1/consent/withdraw", methods=["POST"])
    @jwt_required()
    def post_withdraw():
        d = request.get_json(silent=True) or {}
        code = d.get("code")
        actor = get_jwt_identity() or "unknown"
        if not code:
            return jsonify({"error": "缺 code"}), 400
        append_jsonl(WITHDRAWN, {"code": code, "withdrawn_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
        audit(actor, "consent_withdraw", code, "下架+排除訓練")
        return jsonify({"status": "withdrawn", "code": code,
                        "effect": "已下架、移出再訓練佇列、排除後續訓練;已發布模型於下輪重訓不再納入"}), 200
except ImportError:
    flywheel_bp = None  # 無 flask 環境(僅跑純函式測試)
