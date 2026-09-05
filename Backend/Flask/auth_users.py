# -*- coding: utf-8 -*-
"""帳號與角色（RBAC S1）。設計見 `docs/rbac_design.md`。

## 為什麼不再用環境變數

先前所有人共用一組 `admin`，於是：

- 稽核軌跡的 `actor` 一律是 `admin` —— **雜湊鏈能證明紀錄沒被竄改，
  卻證明不了任何一筆是誰做的**。飛輪的前提是 GT 來自有資格的人的判斷，
  這個欄位無法歸屬到人，整批訓練資料在方法學上就站不住。
- `doctor_verified` 任何人都能觸發。剛把「取消不算確認」修好，
  卻還留著「誰都算醫師」這個更大的洞。
- 一位測試者離開，只能換掉所有人的密碼。

## 儲存方式

用 `store.py` 的 `users.jsonl`，**append-only、同 id 取最新**——與佇列、稽核同一套機制：
本機檔案與 GCS 都能跑，而且帳號的變更歷史本身就是稽核軌跡（誰在什麼時候被停用）。

密碼以 **PBKDF2-HMAC-SHA256 加鹽** 儲存，不可還原。每筆各自的鹽——
共用鹽會讓「兩個人密碼相同」這件事從雜湊值看得出來。

## 識別碼格式：`<org>:<user>`

⚠ 這是**難以回頭**的決定，所以現在就定（見設計文件 §2.2）。單機構階段 org 固定 `default`，
但格式從第一天就帶著它。稽核是 append-only，若現在寫成 `admin`，
日後加機構時那段歷史永遠無法歸屬。
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import time

# 角色。id 用英文（進 JWT 與稽核），顯示名另存。
ROLES = {
    "physician": "醫師",
    "nurse": "護理師",
    "assistant": "助理",
    "engineer": "工程師",
    "admin": "管理者",
    # 民眾版（WoundLite）的過渡服務帳號。**刻意不出現在下面任何一個 PERMS 集合裡**——
    # 「這個角色有什麼權限」的答案要是「grep 不到」，而不是「要讀完整張表才知道」。
    #
    # 為什麼需要它：`/api/v1/classify` 只驗登入、不查角色，所以 Lite 只要有任何
    # 有效帳號就能辨識。但在這個角色存在之前，lite01 必須掛既有五個角色之一，
    # 而實際掛的是 **physician**——那是權限最大的一個：
    #
    #     gt.verify          doctor_verified 的唯一來源
    #     annotation.submit  送訓練標註
    #     patient.manage     對任意 WD 代碼撤回同意
    #
    # 民眾版 App 的服務帳號握著「醫師背書」，意味著民眾拍的照片**有辦法**帶著
    # 醫師身分進訓練集。擋住它的是 Lite 目前沒寫那段程式碼，不是後端的權限——
    # 而「還沒有人這樣做」不是控制措施。
    #
    # 另一件今天就已經成立的損害：稽核軌跡裡 lite01 的角色記載是 physician。
    # 那份紀錄說的不是真正發生的事，而稽核的價值正在於此。
    "lite": "民眾版服務帳號",
}

# 權限矩陣。**這是唯一真實來源**——端點一律查這裡，不要在各處各寫一份 if。
# 對照 docs/rbac_design.md §4。
PERMS = {
    "patient.manage":   {"physician", "nurse"},          # 建病患／簽同意／撤回
    "measure.clinical": {"physician", "nurse", "assistant"},
    "record.save":      {"physician", "nurse"},          # 存入個案時間軸
    "gt.verify":        {"physician"},                   # doctor_verified 的唯一來源
    "annotation.submit": {"physician"},
    "clinical.view":    {"physician", "nurse", "assistant"},
    # ⚠ 這一條原本是 `set(ROLES)`。那寫法有個安靜的副作用：**日後任何人新增角色，
    # 都會自動獲得這個權限**，而 code review 上看不出來——新增角色的那筆 diff 裡
    # 完全沒有提到 measure.sample。改成明列，讓「誰能測範例圖」是一個要動手寫的決定。
    #
    # （查證後補記：目前後端沒有任何端點在查 measure.sample，所以 `set(ROLES)`
    #   在今天是沒有實際效果的。這裡改的是形狀，不是修一個正在發生的漏洞——
    #   先前把它說成「洞」是誇大了。）
    "measure.sample":   {"physician", "nurse", "assistant", "engineer", "admin"},
    "backend.config":   {"engineer", "admin"},
    "flywheel.stats":   {"physician", "nurse", "engineer", "admin"},
    "audit.read":       {"engineer", "admin"},
    "user.manage":      {"admin"},
    "gcp.console":      {"engineer", "admin"},
}

USERS_KEY = "users.jsonl"
DEFAULT_ORG = "default"
PBKDF2_ITERS = 200_000

# 使用者名稱會進識別碼、稽核與 JWT，限制字元避免注入與難以辨識的同形字
USER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,30}$")
ORG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,20}$")


def _store():
    import store as _st
    import api_flywheel as _fw
    return _st.get_store(_fw.FLYWHEEL_DIR)


def identity(org: str, user: str) -> str:
    """稽核與 JWT 用的全域唯一識別碼。"""
    return "%s:%s" % (org, user)


def hash_password(password: str, salt: str = None, iters: int = PBKDF2_ITERS):
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                             bytes.fromhex(salt), iters)
    return {"salt": salt, "iters": iters, "hash": dk.hex()}


def verify_password(password: str, rec: dict) -> bool:
    try:
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 bytes.fromhex(rec["salt"]), int(rec["iters"]))
        # compare_digest：避免以比對耗時洩漏雜湊前綴
        return hmac.compare_digest(dk.hex(), rec["hash"])
    except Exception:
        return False


def _read_all():
    """回 {identity: 最新紀錄}。append-only，同 id 取最後一筆。"""
    out = {}
    for line in _store().read_lines(USERS_KEY):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if isinstance(r, dict) and r.get("org") and r.get("user"):
            out[identity(r["org"], r["user"])] = r
    return out


def list_users(include_disabled: bool = True):
    """不含密碼雜湊——這個結果會回給管理者的 UI。"""
    rows = []
    for ident, r in sorted(_read_all().items()):
        if not include_disabled and r.get("disabled"):
            continue
        rows.append({
            "identity": ident, "org": r["org"], "user": r["user"],
            "role": r.get("role"), "role_zh": ROLES.get(r.get("role"), "?"),
            "display_name": r.get("display_name"),
            "disabled": bool(r.get("disabled")),
            "created_at": r.get("created_at"), "updated_at": r.get("updated_at"),
        })
    return rows


def get_user(org: str, user: str):
    return _read_all().get(identity(org, user))


def validate_upsert_user(org: str, user: str, role: str, password: str = None):
    """驗證帳號異動而不寫入，並回傳目前紀錄。

    API 必須在寫入不可變 audit intent 之前先拒絕格式錯誤。驗證規則集中在這裡，
    避免路由與真正寫入路徑各自維護一份、日後悄悄漂移。
    """
    if not isinstance(org, str) or not ORG_RE.match(org):
        raise ValueError("org 格式不合（小寫英數與連字號，2-21 字）")
    if not isinstance(user, str) or not USER_RE.match(user):
        raise ValueError("user 格式不合（小寫英數起始，可含 . _ -，2-31 字）")
    if not isinstance(role, str) or role not in ROLES:
        raise ValueError("role 須為 %s" % "/".join(ROLES))

    cur = get_user(org, user) or {}
    if password is not None and not isinstance(password, str):
        raise ValueError("password 必須是字串")
    if password and len(password) < 10:
        raise ValueError("密碼至少 10 字元")
    if not password and not cur.get("pw"):
        raise ValueError("新帳號必須提供密碼")
    return cur


def upsert_user(org: str, user: str, role: str, password: str = None,
                display_name: str = None, disabled: bool = None, actor: str = "system"):
    """新增或更新。**append 一筆新紀錄**，不改寫舊的——帳號的變更歷史本身就是稽核軌跡。"""
    cur = validate_upsert_user(org, user, role, password)
    rec = {
        "org": org, "user": user, "role": role,
        "display_name": display_name if display_name is not None else cur.get("display_name"),
        "disabled": bool(cur.get("disabled")) if disabled is None else bool(disabled),
        "created_at": cur.get("created_at") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "updated_by": actor,
    }
    if password:
        rec["pw"] = hash_password(password)
    elif cur.get("pw"):
        rec["pw"] = cur["pw"]

    _store().append_line(USERS_KEY, json.dumps(rec, ensure_ascii=False))
    return {k: v for k, v in rec.items() if k != "pw"}


def authenticate(org: str, user: str, password: str):
    """回 (使用者紀錄或 None, 失敗原因)。原因只給日誌與稽核用，**不要原樣回給客戶端**——
    「帳號不存在」與「密碼錯誤」分開告訴外界等於送人一份帳號列舉工具。"""
    rec = get_user(org, user)
    if rec is None:
        return None, "no_such_user"
    if rec.get("disabled"):
        return None, "disabled"
    if not verify_password(password, rec.get("pw") or {}):
        return None, "bad_password"
    return rec, "ok"


def can(role: str, perm: str) -> bool:
    """角色是否具備某項權限。端點一律查這個函式。"""
    return role in PERMS.get(perm, set())


def bootstrap_from_env():
    """帳號檔為空時，用環境變數建立第一個管理者。

    為什麼需要：全新部署時沒有任何帳號，而帳號管理端點本身要 admin 才能用——
    沒有這個出口就是雞生蛋。只在**完全沒有帳號**時作用，已有帳號則什麼都不做
    （否則環境變數會變成一個永遠存在的後門）。
    """
    if _read_all():
        return None
    pw = (os.environ.get("ADMIN_PASSWORD") or "").strip()
    if not pw:
        return None
    try:
        return upsert_user(DEFAULT_ORG, "admin", "admin", pw,
                           display_name="系統管理者(bootstrap)", actor="bootstrap")
    except Exception:
        return None
