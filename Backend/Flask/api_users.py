# -*- coding: utf-8 -*-
"""帳號管理端點（僅 admin）。設計見 `docs/rbac_design.md` §5。

刻意做成**最小可用**：列出、新增/更新、停用。沒有密碼自助重設、沒有郵件流程——
那些都需要另一套身分驗證（信箱所有權），在 S3 接院內 SSO 時一併處理才對。
現在多做一個半殘的重設流程，只會多一條繞過認證的路。
"""
import csv
import io
import secrets
import time

from flask import Blueprint, request, jsonify, Response
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
    """稽核軌跡查詢：篩選 + 分頁 + （選用）雜湊鏈驗證 + CSV 匯出。

    `audit.read` 權限（工程師／管理者）。

    ⚠ 回傳的是**已去識別**的操作紀錄：actor 是 `<org>:<user>` 編號、code 是 WD-代碼，
    沒有任何病患姓名或影像。工程師看得到這裡但看不到臨床資料，兩者是分開的權限。

    ## 為什麼鏈驗證是「選用」而不是每次都跑

    驗證雜湊鏈**在定義上**就必須讀完整份紀錄並逐筆重算 SHA-256——它是 O(n)，
    而且沒有任何取巧空間（只驗最後一段等於沒驗）。若讓它跟著每次開頁跑，
    稽核累積到幾萬筆之後主控台就會慢到沒人想開，而**不開的主控台等於沒有稽核**。
    所以改成明確的動作：`?verify=1`，由人在需要時按下去。

    平時回傳的 `head` 只是最後一筆的 hash（O(1)，不代表驗證過）——
    這兩者刻意用不同欄位名，避免有人把「有 head」誤讀成「鏈是好的」。

    ## 分頁的參數方向

    `offset` 從**最新**往回數（entries 一律新到舊）。稽核的閱讀習慣是從最近看起，
    用「從頭數第幾筆」分頁會讓第 1 頁的內容隨著新紀錄寫入而不斷改變。
    """
    c = get_jwt() or {}
    role, org = c.get("role"), c.get("org")
    actor = get_jwt_identity() or "unknown"
    if not auth_users.can(role, "audit.read"):
        return jsonify({"error": "權限不足", "issues": ["僅工程師／管理者可查詢稽核軌跡。"]}), 403

    def _int(name, default, lo, hi):
        try:
            return max(lo, min(int(request.args.get(name, default)), hi))
        except (TypeError, ValueError):
            return default

    limit = _int("limit", 50, 1, 500)
    offset = _int("offset", 0, 0, 10_000_000)
    f_action = (request.args.get("action") or "").strip()
    f_actor = (request.args.get("actor") or "").strip()
    f_role = (request.args.get("role") or "").strip()
    f_since = (request.args.get("since") or "").strip()      # YYYY-MM-DD
    f_until = (request.args.get("until") or "").strip()      # YYYY-MM-DD（含當日）
    want_csv = (request.args.get("format") or "").lower() == "csv"
    want_verify = request.args.get("verify") in ("1", "true", "yes")

    recs = fw.read_jsonl(fw.AUDIT)

    # 選單用的可選值：從**全量**算，不受目前篩選影響——
    # 若跟著篩選變動，使用者一旦選了某個動作就再也看不到其他動作可選，等於把自己鎖住。
    facets = {
        "actions": sorted({r.get("action") for r in recs if r.get("action")}),
        "actors": sorted({r.get("actor") for r in recs if r.get("actor")}),
        "roles": sorted({r.get("role") for r in recs if r.get("role")}),
    }

    def keep(r):
        if f_action and r.get("action") != f_action:
            return False
        if f_actor and r.get("actor") != f_actor:
            return False
        if f_role and r.get("role") != f_role:
            return False
        # ts 是 "2026-08-04T07:31:14Z"，前 10 碼就是日期，字串比較即為時間比較。
        d = (r.get("ts") or "")[:10]
        if f_since and d < f_since:
            return False
        if f_until and d > f_until:
            return False
        return True

    matched = [r for r in recs if keep(r)] if (f_action or f_actor or f_role
                                               or f_since or f_until) else recs

    if want_csv:
        # 匯出動作本身也進稽核。「誰把稽核軌跡帶走了」與「誰做了什麼」同等重要——
        # 少了這筆，一份外流的稽核 CSV 追不到是誰匯出的。
        fw.audit(actor, "audit_export", "-",
                 "匯出 %d 筆（action=%s actor=%s role=%s %s~%s）"
                 % (len(matched), f_action or "*", f_actor or "*", f_role or "*",
                    f_since or "起始", f_until or "至今"), role, org)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["seq", "ts", "actor", "role", "org", "action", "code", "result", "hash"])
        for r in matched:
            w.writerow([r.get(k, "") for k in
                        ("seq", "ts", "actor", "role", "org", "action", "code", "result", "hash")])
        # utf-8-sig：Excel 開 UTF-8 CSV 沒有 BOM 就會用系統字碼頁解讀，中文全變亂碼。
        # 這份檔案是要交給法遵窗口的，開不開得起來比檔案乾不乾淨重要。
        data = buf.getvalue().encode("utf-8-sig")
        fname = "woundai_audit_%s.csv" % time.strftime("%Y%m%d_%H%M%S", time.gmtime())
        return Response(data, mimetype="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="%s"' % fname})

    # entries 一律新到舊；offset 從最新往回數。
    page = list(reversed(matched))[offset:offset + limit]

    out = {
        "entries": page,
        "limit": limit, "offset": offset,
        "matched": len(matched), "total": len(recs),
        "head": (recs[-1].get("hash") if recs else None),   # O(1)，**不代表驗證過**
        "verified": None,
    }
    out.update(facets)
    if want_verify:
        ok, issues, stats = fw.verify_audit_chain(recs=recs)
        out["verified"] = {
            "ok": ok,
            "head": stats.get("head"),
            "total": stats.get("total"),
            "issues": issues[:50],
            "kinds": stats.get("kinds"),
            # 錨定用：把這兩個值抄進會議紀錄，日後就能證明「此時間點之前未被竄改」。
            "verified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "verified_by": actor,
        }
        fw.audit(actor, "audit_verify", "-",
                 "鏈驗證 %s（%d 筆，%d 處異常）" % ("通過" if ok else "失敗",
                                                stats.get("total", 0), len(issues)),
                 role, org)
    return jsonify(out), 200
