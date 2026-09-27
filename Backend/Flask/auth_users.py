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


# ── demo 送審帳號的開機種子 ──────────────────────────────────────────
#
# 為什麼需要這個：候選版本跑 `WOUNDAI_STORE=local`，而 LocalStore 的根目錄在
# 容器內（`api_flywheel.FLYWHEEL_DIR` = `<Backend/Flask>/flywheel`）。
# Cloud Run 一回收執行個體，手動用 /console 建的帳號就消失了——App 審查員
# 幾天後登入會發現帳號不存在，那是直接被拒的理由。所以送審帳號不能
# 「建一次留著」，必須**每次冷啟動重建**。
#
# 為什麼不放寬 bootstrap_from_env()：那個出口只建 admin
# （`user.manage`／`audit.read`／`gcp.console`）。把它交給外部審查員是錯的。
#
# 這是一個**刻意做得很窄**的出口，五道機械關卡缺一不可：
#
#   1. 儲存層必須真的是 LocalStore。**問物件，不問環境變數**——
#      環境變數是「打算用什麼」，物件是「實際在用什麼」，兩者會漂移。
#   2. 帳號名必須是 `demoNN` 形狀。環境變數種不出 `admin2` 這種名字，
#      而且稽核軌跡裡一眼認得出哪些列是送審帳號。
#   3. 角色必須同時通過**名單**與**權限**兩層檢查。名單擋新角色偷渡；
#      權限檢查擋既有角色日後變胖——若哪天有人給 nurse 加上 gt.verify，
#      種子會**拒絕**，而不是安靜地把醫師背書交給外部審查員。
#   4. 密碼從環境變數（Cloud Run 掛 Secret Manager）來。程式碼裡沒有密碼，
#      也**不由後端隨機產生**——隨機的話每次冷啟動都換一組，審查員就登不進去。
#   5. 帳號已存在就不動。不覆蓋密碼，尤其不把一個被停用的帳號重新啟用。
#
# 送審結束後拿掉環境變數並重新部署，這個出口就跟著消失。
#
# **刻意不寫稽核**：種子只在 LocalStore 生效，而那個儲存層上的稽核鏈本身
# 也是每次冷啟動重建的——在那裡寫一筆「帳號被建立」證明不了任何事，
# 只會讓每次冷啟動都在鏈上多一筆一模一樣的紀錄。帳號的來源改為記在
# 紀錄本身的 `updated_by` 欄（`demo-seed`），那筆資料跟帳號同生共死。

DEMO_USER_RE = re.compile(r"^demo[0-9]{2}$")

# 允許被種的角色。**明列**，不是「除了 admin 以外都行」——後者會讓日後
# 新增的角色自動取得資格，而新增角色的那筆 diff 上完全看不出來。
DEMO_SEED_ROLES = frozenset({"nurse", "assistant"})

# 種子帳號絕不得持有的權限。這層跟上面的名單是**獨立**的兩道檢查：
# 名單認的是「這個名字現在可以」，這裡認的是「這個角色現在的權力還在界內」。
DEMO_SEED_FORBIDDEN_PERMS = frozenset({
    "user.manage",        # 開帳號
    "audit.read",         # 讀稽核軌跡
    "gcp.console",        # 雲端主控台
    "backend.config",     # 改後端設定
    "gt.verify",          # doctor_verified 的唯一來源
    "annotation.submit",  # 送訓練標註
})

# 種子密碼長度下限。對齊 api_users._gen_password 的 14——auth_users 本身
# 只要求 10，對一個**外部人士會拿到、而且存在於公開網路上**的帳號太短。
DEMO_SEED_MIN_PW = 14


def demo_role_refusal(role):
    """回拒絕原因字串；通過回 None。抽出來讓測試能直接問「為什麼拒絕」。"""
    if not isinstance(role, str) or role not in ROLES:
        return "role 不存在：%r" % (role,)
    if role not in DEMO_SEED_ROLES:
        return "role %s 不在種子白名單（允許：%s）" % (
            role, "/".join(sorted(DEMO_SEED_ROLES)))
    held = sorted(p for p in DEMO_SEED_FORBIDDEN_PERMS if can(role, p))
    if held:
        return ("role %s 持有禁止權限 %s——權限矩陣已變動，"
                "請重新檢視這個角色是否還適合送審帳號" % (role, ",".join(held)))
    return None


def seed_demo_from_env():
    """冷啟動時重建送審用 demo 帳號。條件見上方註解。

    回傳：
      None                                   功能未啟用（沒設 WOUNDAI_DEMO_SEED_USER）
      {"seeded": True,  "identity", "role"}  已建立
      {"seeded": False, "exists": True, "reason"}
                                             帳號已存在，不覆蓋（預期中的跳過）
      {"seeded": False, "reason"}            明確拒絕

    **刻意不用單純的 None 表示失敗**：一個拼錯的角色名若安靜地什麼都不做，
    你會在 Apple 審查員回報登不進去的那天才發現。拒絕必須看得見。
    """
    user = (os.environ.get("WOUNDAI_DEMO_SEED_USER") or "").strip()
    if not user:
        return None

    import store as _st
    st = _store()
    if not isinstance(st, _st.LocalStore):
        return {"seeded": False,
                "reason": "儲存層是 %s，種子只在 LocalStore 生效" % type(st).__name__}

    if not DEMO_USER_RE.match(user):
        return {"seeded": False, "reason": "帳號名 %r 不是 demoNN 形狀" % (user,)}

    role = (os.environ.get("WOUNDAI_DEMO_SEED_ROLE") or "").strip()
    refusal = demo_role_refusal(role)
    if refusal:
        return {"seeded": False, "reason": refusal}

    # 結尾的換行要去掉，這不是潔癖。PowerShell 把字串管線給原生程式時會補一個
    # 換行（Windows PowerShell 5.1 補 CRLF），所以用 `$pw | gcloud secrets versions
    # add ... --data-file=-` 建的密文，內容其實是「密碼＋\r\n」。Cloud Run 掛成環境
    # 變數時原樣照給，種子就會建出一個結尾帶 CRLF 的密碼——審查員照著輸入，
    # 永遠登不進去。只去掉結尾的 CR/LF；其餘任何空白或控制字元一律拒絕，
    # 因為那代表密文本身就不是一個人打得出來的密碼。
    pw = (os.environ.get("WOUNDAI_DEMO_SEED_PASSWORD") or "").rstrip("\r\n")
    if not pw:
        return {"seeded": False, "reason": "未掛 WOUNDAI_DEMO_SEED_PASSWORD"}
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in pw):
        return {"seeded": False,
                "reason": "種子密碼含空白或控制字元，審查員無法照著輸入"}
    if len(pw) < DEMO_SEED_MIN_PW:
        return {"seeded": False,
                "reason": "種子密碼至少 %d 字元（目前 %d）" % (DEMO_SEED_MIN_PW, len(pw))}

    if get_user(DEFAULT_ORG, user) is not None:
        # `exists` 與拒絕分開標示：同一個容器裡 worker 重啟會再跑一次種子，
        # 那時帳號已存在是預期中的事，不是設定錯誤。
        return {"seeded": False, "exists": True,
                "reason": "帳號 %s 已存在，不覆蓋" % user}

    rec = upsert_user(DEFAULT_ORG, user, role, pw,
                      display_name="送審測試帳號(demo-seed)", actor="demo-seed")
    return {"seeded": True, "identity": identity(DEFAULT_ORG, user),
            "role": rec["role"]}
