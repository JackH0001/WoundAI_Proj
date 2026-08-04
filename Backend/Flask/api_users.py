# -*- coding: utf-8 -*-
"""帳號管理端點（僅 admin）。設計見 `docs/rbac_design.md` §5。

刻意做成**最小可用**：列出、新增/更新、停用。沒有密碼自助重設、沒有郵件流程——
那些都需要另一套身分驗證（信箱所有權），在 S3 接院內 SSO 時一併處理才對。
現在多做一個半殘的重設流程，只會多一條繞過認證的路。
"""
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt, get_jwt_identity

import auth_users
import api_flywheel as fw

users_bp = Blueprint("users", __name__)


def _guard():
    """回 (actor, role, org) 或 None 表示無權限。"""
    c = get_jwt() or {}
    role = c.get("role")
    if not auth_users.can(role, "user.manage"):
        return None
    return (get_jwt_identity() or "unknown", role, c.get("org"))


@users_bp.route("/api/v1/users", methods=["GET"])
@jwt_required()
def list_users():
    who = _guard()
    if who is None:
        return jsonify({"error": "權限不足", "issues": ["僅管理者可檢視帳號清單。"]}), 403
    return jsonify({"users": auth_users.list_users(),
                    "roles": auth_users.ROLES}), 200


@users_bp.route("/api/v1/users", methods=["POST"])
@jwt_required()
def upsert_user():
    who = _guard()
    if who is None:
        return jsonify({"error": "權限不足", "issues": ["僅管理者可新增或修改帳號。"]}), 403
    actor, role, org = who
    d = request.get_json(silent=True) or {}
    try:
        rec = auth_users.upsert_user(
            org=(d.get("org") or auth_users.DEFAULT_ORG).strip(),
            user=(d.get("user") or "").strip(),
            role=(d.get("role") or "").strip(),
            password=d.get("password"),
            display_name=d.get("display_name"),
            disabled=d.get("disabled"),
            actor=actor,
        )
    except ValueError as e:
        return jsonify({"error": "參數不合", "issues": [str(e)]}), 400
    except Exception as e:
        return jsonify({"error": "建立失敗", "issues": [str(e)]}), 500
    # 帳號異動一律進稽核。**誰開了帳號**與**誰用帳號做了什麼**同等重要——
    # 少了前者，一個被濫用的帳號查不出是誰核發的。
    fw.audit(actor, "user_upsert", "%s:%s" % (rec["org"], rec["user"]),
             "role=%s disabled=%s" % (rec["role"], rec["disabled"]), role, org)
    return jsonify({"status": "ok", "user": rec}), 200
