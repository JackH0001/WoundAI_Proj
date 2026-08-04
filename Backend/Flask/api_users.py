# -*- coding: utf-8 -*-
"""帳號管理端點（僅 admin）。設計見 `docs/rbac_design.md` §5。

刻意做成**最小可用**：列出、新增/更新、停用。沒有密碼自助重設、沒有郵件流程——
那些都需要另一套身分驗證（信箱所有權），在 S3 接院內 SSO 時一併處理才對。
現在多做一個半殘的重設流程，只會多一條繞過認證的路。
"""
import secrets

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt, get_jwt_identity

import auth_users
import api_flywheel as fw

# 密碼字元集刻意排除 l/1/I、O/0 —— 密碼要靠人抄寫或口述傳遞，
# 同形字造成的「明明打對卻登不進去」會消耗掉大量支援時間。
_PW_CHARS = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _gen_password(n: int = 14) -> str:
    return "".join(secrets.choice(_PW_CHARS) for _ in range(n))

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
    # generate_password：由伺服器產生並**只回傳這一次**。
    # 讓瀏覽器自己產生也可以，但那樣密碼會經過前端程式碼與可能的擴充功能；
    # 伺服器產生後直接回應，路徑最短。
    generated = None
    if d.get("generate_password"):
        generated = _gen_password()
        d["password"] = generated
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
             "role=%s disabled=%s pw_changed=%s" % (rec["role"], rec["disabled"],
                                                    bool(d.get("password"))), role, org)
    # 回應形狀與 GET /users 的每一列對齊（補 identity / role_zh）——
    # 兩個端點回不同形狀的同一種物件，前端就得寫兩套解析，那是 bug 的溫床。
    rec = dict(rec, identity="%s:%s" % (rec["org"], rec["user"]),
               role_zh=auth_users.ROLES.get(rec.get("role"), "?"))
    out = {"status": "ok", "user": rec}
    if generated:
        out["generated_password"] = generated
        out["note"] = "此密碼只顯示這一次；後端只存 PBKDF2 雜湊，之後任何人都取不回。"
    return jsonify(out), 200


@users_bp.route("/api/v1/audit", methods=["GET"])
@jwt_required()
def read_audit():
    """稽核軌跡查詢 + 鏈完整性。`audit.read` 權限（工程師／管理者）。

    ⚠ 回傳的是**已去識別**的操作紀錄：actor 是 `<org>:<user>` 編號、code 是 WD-代碼，
    沒有任何病患姓名或影像。工程師看得到這裡但看不到臨床資料，兩者是分開的權限。
    """
    c = get_jwt() or {}
    if not auth_users.can(c.get("role"), "audit.read"):
        return jsonify({"error": "權限不足", "issues": ["僅工程師／管理者可查詢稽核軌跡。"]}), 403
    try:
        limit = max(1, min(int(request.args.get("limit", 50)), 500))
    except (TypeError, ValueError):
        limit = 50
    action = request.args.get("action")

    recs = fw.read_jsonl(fw.AUDIT)
    # 先驗鏈再過濾：過濾後的子集鏈結必然不連續，拿去驗會得到一堆假的 broken_link。
    ok, issues, stats = fw.verify_audit_chain(recs=recs)
    if action:
        recs = [r for r in recs if r.get("action") == action]
    return jsonify({
        "entries": recs[-limit:][::-1],      # 新到舊
        "total": stats.get("total"),
        "chain_ok": ok,
        "chain_head": stats.get("head"),
        "chain_issues": issues[:20],
        "actions": sorted({r.get("action") for r in recs if r.get("action")}),
    }), 200
