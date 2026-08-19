# -*- coding: utf-8 -*-
"""契約測試：**`lite` 角色不得擁有任何權限**。

    python engineering/phase2/test_lite_role.py

## 為什麼要一支測試守一個「空集合」

WoundLite 是民眾版 App，它的後端服務帳號 `lite01` 在 2026-08-19 之前掛的是
**physician**——因為 `ROLES` 裡沒有 lite，而帳號必須掛某個角色。

physician 是權限最大的那一個：

    gt.verify          doctor_verified 的唯一來源
    annotation.submit  送訓練標註
    patient.manage     對任意 WD 代碼撤回同意

一個民眾版服務帳號握著「醫師背書」，意味著民眾拍的照片**有辦法**帶著醫師身分
進訓練集。當時擋住它的是 Lite 沒寫那段程式碼——**「還沒有人這樣做」不是控制措施**。

空集合這種東西最容易在日後被「順手」加回去：有人為了讓 Lite 看個統計而給它
`flywheel.stats`，那一行 diff 看起來人畜無害。這支測試讓那一行必須連帶改測試，
而改測試的人就得面對「為什麼這個角色需要這個權限」。

## 這支測試不涵蓋的

`/api/v1/classify` **只驗登入、不查角色**（Lite 靠這個運作）。所以 lite 角色
權限全空，不代表 lite01 什麼都不能做——它仍然能辨識影像並讓影像落地到
`images/`。那是設計如此，真正的控制在 `lite/segment` 的 anon_id 與 consent 分流，
不在角色表。**不要把這支測試的綠燈讀成「Lite 完全無害」。**
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FLASK_DIR = os.path.abspath(os.path.join(HERE, "..", "..", "Backend", "Flask"))

FAILED = []
TOTAL = [0]


def check(name, ok, detail=""):
    TOTAL[0] += 1
    print(("PASS  " if ok else "FAIL  ") + name + (("   " + str(detail)) if (detail and not ok) else ""))
    if not ok:
        FAILED.append(name)


def main():
    sys.path.insert(0, FLASK_DIR)
    for m in list(sys.modules):
        if m.startswith("auth_users"):
            del sys.modules[m]
    import auth_users as au

    print("── 1 角色存在 ──")
    check("ROLES 有 lite", "lite" in au.ROLES, sorted(au.ROLES))
    check("lite 有中文說明（主控台使用者管理會顯示）",
          bool(au.ROLES.get("lite")), au.ROLES.get("lite"))

    print("\n── 2 lite 不在任何 PERMS 集合裡 ──")
    granted = sorted(p for p, s in au.PERMS.items() if "lite" in s)
    check("lite 的權限集合是空的", not granted,
          "它拿到了：%s" % "、".join(granted))
    for p in sorted(au.PERMS):
        check("  can('lite', %-18s) == False" % ("'%s'" % p), au.can("lite", p) is False)

    print("\n── 3 敏感權限的持有者沒有被動到 ──")
    # 加一個角色不該讓別人多拿或少拿東西。這幾條是後端**真的會查**的
    #（grep `_can(` 得到的清單），寫死在這裡當回歸基準。
    expect = {
        "gt.verify": {"physician"},
        "annotation.submit": {"physician"},
        "patient.manage": {"physician", "nurse"},
        "audit.read": {"engineer", "admin"},
        "user.manage": {"admin"},
        "flywheel.stats": {"physician", "nurse", "engineer", "admin"},
    }
    for p, want in expect.items():
        check("%-18s 持有者未變" % p, au.PERMS.get(p) == want,
              "實際 %s，期望 %s" % (sorted(au.PERMS.get(p, [])), sorted(want)))

    print("\n── 4 measure.sample 必須明列，不可用 set(ROLES) ──")
    # `set(ROLES)` 會讓**日後新增的任何角色**自動獲得這個權限，
    # 而新增角色那筆 diff 裡完全不會提到 measure.sample。
    check("measure.sample 不等於全體角色（＝沒有用 set(ROLES)）",
          au.PERMS.get("measure.sample") != set(au.ROLES),
          "它等於全體角色，新角色會靜默繼承")

    print("\n── 5 帳號建立時 lite 是合法角色 ──")
    # `create_user` 用 `role not in ROLES` 擋，所以管理者要建得起 lite01。
    src = open(os.path.join(FLASK_DIR, "auth_users.py"), encoding="utf-8").read()
    check("角色驗證仍以 ROLES 為準（管理者建得出 lite 帳號）",
          "role not in ROLES" in src)

    print("\n── 6 主控台跟得上角色表 ──")
    # 角色清單先前存在兩個地方：Python 的 ROLES 與主控台寫死的 <option>。
    # 沒有任何東西保證兩者一致——2026-08-19 加了 lite 之後，後端接受了、
    # 下拉卻選不到，**後端做對了、畫面沒跟上**。
    con = open(os.path.join(FLASK_DIR, "api_console.py"), encoding="utf-8").read()
    check("角色下拉由 ROLES 產生（不是寫死的 <option>）",
          "<!--ROLE_OPTIONS-->" in con and "_au.ROLES.items()" in con)
    check("沒有殘留寫死的角色選項",
          '<option value="physician">' not in con,
          "還有寫死的 option，會與 ROLES 分岔")

    # 沒有「改角色」入口的話，lite01 這種要降權的帳號只能靠重建——
    # 而重建會換掉密碼，也會讓稽核上看起來像換了一個人。
    check("帳號管理有「改角色」入口", "changeRole" in con and "改角色" in con)
    check("改自己的管理者角色前有警告（改完就進不了帳號管理）",
          "改掉**自己**的管理者角色" in con or "自己" in con and "救援" in con)
    check("改角色不連帶重設密碼（不傳 password＝沿用既有雜湊）",
          "改角色不該連帶換密碼" in con)

    # 頁面產生器要能在測試環境跑得起來（import auth_users 不可失敗）
    sys.path.insert(0, FLASK_DIR)
    try:
        import importlib
        for m in list(sys.modules):
            if m.startswith("api_console"):
                del sys.modules[m]
        ac = importlib.import_module("api_console")
        html = ac._page_html()
        ok = all(('value="%s"' % r) in html for r in au.ROLES)
        check("_page_html() 產出的下拉含每一個角色", ok,
              [r for r in au.ROLES if ('value="%s"' % r) not in html])
        check("  其中包含 lite", 'value="lite"' in html)
    except Exception as e:
        check("_page_html() 可執行", False, e)

    print("\n%d 項檢查，%d 項失敗" % (TOTAL[0], len(FAILED)))
    if FAILED:
        print("失敗：")
        for x in FAILED:
            print("  · " + x)
        return 1
    print("全部通過：lite 角色存在且權限為空，既有角色的敏感權限未被動到。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
