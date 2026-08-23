"""端點守門的結構性回歸測試。

2026-08-21 非實作者覆核發現兩件事，這個檔案是對兩者的回應：

1. `/api/v1/consent/restore` 只有 `@jwt_required()`，沒有 `patient.manage`——
   任何已登入角色都能撤銷病人的撤回、把隔離影像搬回訓練區。它與有守門的
   `/consent/withdraw` 相隔 30 行。

2. 這個檔案的第一版**自己有洞**：它只檢查 handler 有沒有「直接」呼叫三個
   save 函式，所以把 handler 改成 `return _legacy()` 就整條復活而測試照樣全綠。
   v2 改用**傳遞可達性**（呼叫圖 BFS）＋ handler 形狀斷言，並且對應的 patch
   已把舊實作完整刪除而非改名保留。

既有測試套件測的是「授權角色能不能正確完成操作」，沒測「未授權角色會不會被擋」。
這裡補的是後者，而且刻意寫成結構性的——釘那一類，不是釘那一個。

    python -m pytest engineering/phase2/test_endpoint_guards.py -v
"""
from __future__ import annotations

import ast
import io
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
FLASK_DIR = os.path.abspath(os.path.join(HERE, "..", "..", "Backend", "Flask"))
FLYWHEEL = os.path.join(FLASK_DIR, "api_flywheel.py")
APP = os.path.join(FLASK_DIR, "app.py")

TRAINING_SINKS = ("save_training_image", "save_training_mask", "save_training_data_record")


# ---------------------------------------------------------------- AST 工具

def _parse(path: str) -> ast.Module:
    return ast.parse(io.open(path, encoding="utf-8").read())


def _funcs(tree: ast.Module):
    return [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _route_of(fn):
    for d in getattr(fn, "decorator_list", []):
        if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr == "route":
            if d.args and isinstance(d.args[0], ast.Constant):
                methods = []
                for kw in d.keywords:
                    if kw.arg == "methods" and isinstance(kw.value, ast.List):
                        methods = [e.value for e in kw.value.elts if isinstance(e, ast.Constant)]
                return d.args[0].value, methods or ["GET"]
    return None, None


def _called_names(fn) -> set:
    """該函式**體內**所有被呼叫的名字（`f()` 與 `x.f()` 都算）。

    只走 fn.body，不走整個節點——否則 decorator（`@app.route(...)`、
    `@jwt_required()`）也會被算成「這個函式呼叫了 route/jwt_required」，
    而那不是函式執行時做的事。第一次寫成 ast.walk(fn) 就吃了這個虧。
    """
    out = set()
    for stmt in fn.body:
        for n in ast.walk(stmt):
            if isinstance(n, ast.Call):
                fx = n.func
                if isinstance(fx, ast.Name):
                    out.add(fx.id)
                elif isinstance(fx, ast.Attribute):
                    out.add(fx.attr)
    return out


def _perm_names(fn) -> set:
    out = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_can":
            out.add(n.args[1].value if len(n.args) >= 2 and isinstance(n.args[1], ast.Constant) else "<dynamic>")
    return out


def _routes(path: str):
    rows = []
    for n in _funcs(_parse(path)):
        rule, methods = _route_of(n)
        if rule:
            rows.append((rule, tuple(methods), n.name, _perm_names(n), n.lineno))
    return sorted(rows)


def _reachable(path: str, start_fn_name: str) -> set:
    """從某個函式出發，在同一模組內傳遞可達的所有函式名。

    這是 v1 缺的那一段：v1 只看直接呼叫，所以 `return _legacy()` 這種
    一層轉手就整個看不見。
    """
    tree = _parse(path)
    graph = {}
    for fn in _funcs(tree):
        graph.setdefault(fn.name, set()).update(_called_names(fn))
    seen, stack = set(), [start_fn_name]
    while stack:
        cur = stack.pop()
        for callee in graph.get(cur, ()):
            if callee not in seen:
                seen.add(callee)
                stack.append(callee)
    return seen


# ------------------------------------------------------- 1. 零容忍守門覆蓋

# 2026-08-21 實測：api_flywheel.py 的 12 個路由全部都有 _can 檢查，所以沒有例外。
# 要新增例外必須改這個常數並寫下理由——讓「不守門」變成需要有人簽名的決定。
#
# ⚠ 這個測試只證明「有寫守門」，不證明「守門正確」。它是哨兵，不是授權證明；
#    角色矩陣的正確性要靠下面的功能測試與既有的 RBAC 測試。
FLYWHEEL_ROUTES_WITHOUT_PERMISSION: dict = {}


def test_every_flywheel_route_is_permission_gated():
    rows = _routes(FLYWHEEL)
    assert rows, "沒有從 api_flywheel.py 解析到任何路由——測試本身壞了"

    ungated = [(r, m, n, ln) for r, m, n, p, ln in rows if not p or p == {"<dynamic>"}]
    unexpected = [u for u in ungated if u[0] not in FLYWHEEL_ROUTES_WITHOUT_PERMISSION]
    assert not unexpected, (
        "以下路由只有身分驗證、沒有權限驗證：\n"
        + "\n".join(f"  {r}  ({','.join(m)})  {n}()  api_flywheel.py:{ln}" for r, m, n, ln in unexpected)
        + "\n\n@jwt_required() 只證明『是某個已登入的人』，不證明『這個人可以做這件事』。"
    )


# ------------------------------------- 2. 反向操作不得比正向操作更容易觸及

INVERSE_PAIRS = [
    ("/api/v1/consent/withdraw", "/api/v1/consent/restore"),
    ("/api/v1/retract", "/api/v1/unretract"),
]


def _roles_for(perms: set) -> set:
    sys.path.insert(0, FLASK_DIR)
    try:
        import auth_users
    finally:
        sys.path.pop(0)
    roles = set()
    for p in perms:
        roles |= set(auth_users.PERMS.get(p, set()))
    return roles


@pytest.mark.parametrize("forward,inverse", INVERSE_PAIRS)
def test_inverse_operation_is_not_more_reachable(forward, inverse):
    """守住破壞性那一邊、漏掉還原那一邊，是最容易犯的守門錯誤。

    注意 retract/unretract 的不對稱是**刻意且正確**的（醫師可撤回自己送的標註，
    只有管理員能撤銷撤回），所以條件是子集而不是相等——寫成相等會誤報。
    """
    by_rule = {r: p for r, _m, _n, p, _ln in _routes(FLYWHEEL)}
    assert forward in by_rule and inverse in by_rule, f"找不到 {forward} 或 {inverse}"
    fwd, inv = _roles_for(by_rule[forward]), _roles_for(by_rule[inverse])
    assert inv, (f"{inverse} 沒有任何權限守門，等於所有已登入角色都能執行；"
                 f"而正向操作 {forward} 限定 {sorted(fwd)}")
    assert inv <= fwd, (f"反向操作比正向操作更容易觸及：\n"
                        f"  {forward} -> {sorted(fwd)}\n  {inverse} -> {sorted(inv)}\n"
                        f"  多出來的角色: {sorted(inv - fwd)}")


# --------------------------------------- 3. 不得存在第二條訓練資料入口

def test_api_train_handler_is_only_a_410():
    """handler 的函式體必須只有 docstring ＋ 一個回傳。

    這條看起來很嚴格，但它正是 v1 缺的守門：`return _legacy()` 會被這裡擋下，
    因為 return 的內容不是一個 dict/tuple 常量結構而是一次呼叫。
    """
    rows = [r for r in _routes(APP) if r[0] == "/api/train"]
    assert rows, "/api/train 路由不見了——若是刻意刪除，請一併刪掉本測試並在 commit 說明"
    name = rows[0][2]
    fn = next(f for f in _funcs(_parse(APP)) if f.name == name)

    body = [s for s in fn.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant)
                                       and isinstance(s.value.value, str))]
    assert len(body) == 1 and isinstance(body[0], ast.Return), (
        f"/api/train 的 handler 應該只剩一個 return（目前 {len(body)} 個語句）。"
        f"任何額外邏輯都代表它又在做事了。"
    )
    calls = _called_names(fn) - {"jsonify"}
    assert not calls, (
        f"/api/train 的 handler 呼叫了 {sorted(calls)}。除了 jsonify 之外不該呼叫任何東西——"
        f"包括把舊實作改名之後再轉呼叫。"
    )

    # 狀態碼本身必須被釘住。v2 只檢查語句數與呼叫名稱，所以把 410 改成 200
    # 六項結構測試照樣全綠——覆核用一行變異證明了這點。
    ret = body[0].value
    assert isinstance(ret, ast.Tuple) and len(ret.elts) == 2, (
        "/api/train 必須 `return jsonify(...), 410`（一個兩元素的 tuple）。"
        f"目前回傳的是 {type(ret).__name__}。"
    )
    status = ret.elts[1]
    assert isinstance(status, ast.Constant) and status.value == 410, (
        f"/api/train 的狀態碼必須是常數 410，目前是 "
        f"{getattr(status, 'value', ast.dump(status))}。"
        f"退役端點回 2xx 等於沒退役——呼叫端會以為成功。"
    )
    assert isinstance(ret.elts[0], ast.Call) and getattr(ret.elts[0].func, "id", None) == "jsonify", (
        "/api/train 的回傳主體必須是 jsonify(...)。"
    )


def test_no_route_can_reach_the_legacy_training_sinks():
    """整個 app.py 裡，沒有任何路由 handler 能（傳遞地）到達訓練資料寫入函式。

    這是 v1 真正缺的那條。覆核把 handler 改成 `return _contribute_training_data_legacy()`
    之後 v1 四項測試仍全綠，因為 v1 只看直接呼叫。這裡改用呼叫圖可達性。
    """
    offenders = []
    for rule, methods, name, _perms, lineno in _routes(APP):
        hit = _reachable(APP, name) & set(TRAINING_SINKS)
        if hit:
            offenders.append((rule, ",".join(methods), name, sorted(hit), lineno))
    assert not offenders, (
        "以下路由可以（直接或間接）寫入訓練資料，但訓練資料只能從 "
        "/api/v1/annotation 進來（需醫師角色、image_id 綁定、去識別化、consent_train）：\n"
        + "\n".join(f"  {r}  ({m})  {n}()  -> {h}  app.py:{ln}" for r, m, n, h, ln in offenders)
    )


def test_no_renamed_copy_of_the_legacy_handler_survives():
    """舊實作必須是**刪除**，不是改名保留。

    production source 裡留一個可呼叫的繞過路徑，等於把復活成本降到一行。
    稽核需求由 git history 負責。
    """
    # ⚠ v2 用 startswith("save_training") 當例外，而覆核加了一個叫
    # save_training_legacy_handler 的函式就整個溜過去了。例外必須是**精確集合**，
    # 不能是前綴——前綴例外永遠可以被構造出來的名字繞過。
    bad = [f.name for f in _funcs(_parse(APP))
           if _called_names(f) & set(TRAINING_SINKS) and f.name not in set(TRAINING_SINKS)]
    assert not bad, (
        f"app.py 裡仍有函式會寫入訓練資料：{sorted(bad)}。"
        f"舊的 /api/train 實作應完整刪除，不是改名成 helper 留著。"
    )


# ------------------------------------------------ 4. 功能面（環境允許才跑）

# 結構測試是**哨兵**，不是 410 或授權矩陣的 runtime 證明。後端／部署 CI 應設
# WOUNDAI_REQUIRE_FUNCTIONAL_TESTS=1，讓下面兩個功能測試變成不可跳過的守門；
# 本機開發環境沒有 Flask 時仍然 skip，不擋快速回饋。
REQUIRE_FUNCTIONAL = os.environ.get("WOUNDAI_REQUIRE_FUNCTIONAL_TESTS") == "1"


def _flask_or_skip():
    sys.path.insert(0, FLASK_DIR)
    try:
        import auth_users
        from app import app as flask_app
        from flask_jwt_extended import create_access_token
        return auth_users, flask_app, create_access_token
    except Exception as exc:  # noqa: BLE001
        if FLASK_DIR in sys.path:
            sys.path.remove(FLASK_DIR)
        if REQUIRE_FUNCTIONAL:
            pytest.fail(
                f"WOUNDAI_REQUIRE_FUNCTIONAL_TESTS=1 但 Flask app 匯不進來：{exc}\n"
                f"結構測試只證明『有寫守門』；410 與角色矩陣要靠這兩個功能測試在 runtime 證明。"
            )
        pytest.skip(f"Flask app 無法匯入，略過功能測試: {exc}")


def test_restore_rejects_role_without_patient_manage():
    """靜態測試證明「有寫守門」，這個證明「守門真的會擋」。"""
    auth_users, flask_app, mint = _flask_or_skip()
    try:
        role = next((r for r in getattr(auth_users, "ROLES", ["assistant"])
                     if not auth_users.can(r, "patient.manage")), None)
        if role is None:
            pytest.skip("找不到缺少 patient.manage 的角色")
        flask_app.config["TESTING"] = True
        with flask_app.app_context():
            tok = mint(identity="guardtest", additional_claims={"role": role})
        with flask_app.test_client() as c:
            r = c.post("/api/v1/consent/restore", json={"code": "WD-guardtest"},
                       headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 403, (
            f"角色 {role} 沒有 patient.manage 卻拿到 {r.status_code}（預期 403）。"
            f"回應: {r.get_data(as_text=True)[:300]}")
    finally:
        if FLASK_DIR in sys.path:
            sys.path.remove(FLASK_DIR)


def test_api_train_returns_410_and_writes_nothing(monkeypatch):
    """以**有效** JWT 呼叫 /api/train，必須 410，且不得產生任何落地物。

    覆核要求的：不只斷言狀態碼，也要斷言沒有新的 blob / SQLite record / 檔案。
    做法是把三個寫入函式換成會讓測試失敗的哨兵——若還有任何路徑會呼叫它們，
    這裡會直接炸。
    """
    auth_users, flask_app, mint = _flask_or_skip()
    try:
        import app as app_mod
        touched = []
        for sink in TRAINING_SINKS:
            if hasattr(app_mod, sink):
                monkeypatch.setattr(app_mod, sink,
                                    lambda *a, _s=sink, **k: touched.append(_s), raising=False)
        role = next((r for r in getattr(auth_users, "ROLES", ["physician"])), "physician")
        flask_app.config["TESTING"] = True
        with flask_app.app_context():
            tok = mint(identity="retiretest", additional_claims={"role": role})
        with flask_app.test_client() as c:
            r = c.post("/api/train", data={}, headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 410, (
            f"/api/train 回 {r.status_code}（預期 410）。回應: {r.get_data(as_text=True)[:300]}")
        assert not touched, f"/api/train 仍然呼叫了訓練資料寫入函式: {touched}"
    finally:
        if FLASK_DIR in sys.path:
            sys.path.remove(FLASK_DIR)
