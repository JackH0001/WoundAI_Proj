# -*- coding: utf-8 -*-
"""契約測試：**使用說明書寫的東西，程式裡真的要有**。

    python engineering/phase2/test_manual_consistency.py

## 為什麼手冊需要測試

手冊會過時，而過時的手冊比沒有手冊糟：使用者照著找一顆已經改名的按鈕，
找不到之後他的結論是「這個 App 有問題」，不是「手冊舊了」。

臨床手冊更嚴重——它寫的是**閘門條件**（誰能送訓練標註、什麼情況下面積不可信）。
那些條件寫錯會讓人做出錯誤的臨床判斷，或讓不該進訓練集的資料進去。

所以這支測試把手冊裡出現的按鈕名稱、角色權限、組織顏色拿去跟原始碼比對。
比對不到就是有一邊改了而另一邊沒跟上，**兩邊都要查**——
有可能是手冊過時，也有可能是程式改壞了按鈕文字。

## 守不到的部分

文案的**語意**是否正確、圖是否畫對、流程順序是否合理——這些只能靠人看。
這支測試守的是「字面上對不上」的那一類，那一類佔了實際發生的大多數。
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
APP = os.path.join(ROOT, "Android", "app", "src", "main", "java",
                   "com", "woundmeasurement", "app")
MANUAL = os.path.join(ROOT, "Android", "app", "src", "main", "assets", "manual.html")
PERMS_PY = os.path.join(ROOT, "Backend", "Flask", "auth_users.py")

FAILED = []
TOTAL = [0]


def check(name, ok, detail=""):
    TOTAL[0] += 1
    print(("PASS  " if ok else "FAIL  ") + name + (("   " + str(detail)) if detail else ""))
    if not ok:
        FAILED.append(name)


def kt_sources():
    out = {}
    for root, _, files in os.walk(APP):
        for f in files:
            if f.endswith(".kt"):
                p = os.path.join(root, f)
                out[f] = open(p, encoding="utf-8").read()
    return out


def main():
    if not os.path.exists(MANUAL):
        print("找不到手冊：%s" % MANUAL)
        return 1
    man = open(MANUAL, encoding="utf-8").read()
    kt = kt_sources()
    allkt = "\n".join(kt.values())

    # ── 1. 手冊提到的按鈕，程式裡要有同名字串 ─────────────────────
    #
    # 這些字串是使用者實際會在畫面上看到的。任一顆改名而手冊沒改，
    # 使用者就會在畫面上找不到手冊說的那顆按鈕。
    BUTTONS = [
        "個案（病患・同意書・個案傷口・量測）",
        "最近就診",
        "快速量測（範例／模擬圖・不綁個案）",
        "設定（後端連線・帳號・佇列健康度）",
        "＋新增病患", "＋新增個案傷口", "簽署同意書", "開始量測",
        "邊界＋", "邊界－", "移動", "組織🖌", "按住看原圖",
        "肉芽", "腐肉", "壞死", "上皮", "其他",
        "ROI", "全圖", "取消", "完成修邊",
        "存入個案時間軸", "醫師確認・修邊",
        "醫師確認・送出標註 → 再訓練佇列",
        "重新修邊（載回原影像與輪廓）",
        "補送訓練標註 → 再訓練佇列",
        "連線測試", "撤回訓練同意",
    ]
    for b in BUTTONS:
        in_man = b in man
        in_kt = b in allkt
        check("按鈕「%s」手冊與程式都有" % b, in_man and in_kt,
              "手冊%s／程式%s" % ("有" if in_man else "無", "有" if in_kt else "無"))

    # ── 2. 手冊引用的訊息文字，程式裡要有 ────────────────────────
    #
    # 疑難排解那張表的價值全在「使用者看到的字」與「表格第一欄」能對得起來。
    # 對不起來的話，他會以為自己遇到的是手冊沒寫的狀況。
    MSGS = [
        "⚠ 請先選擇個案傷口",
        "請先輸入滲液量",
        "尚未完成醫師修邊確認",
        "AI 未偵測到傷口",
        "後端可能處於休眠",
        "不得存入個案時間軸",
        # 撤回訓練同意：本機生效 + 雲端同步 + 失敗要誠實。
        # 這三句任一改掉而手冊沒跟上，護理師就會以為撤回已經完成了。
        "雲端尚未完成",
        "仍在雲端訓練佇列中",
    ]
    for m in MSGS:
        check("訊息「%s…」手冊與程式都有" % m[:14], m in man and m in allkt,
              "手冊%s／程式%s" % ("有" if m in man else "無", "有" if m in allkt else "無"))

    # ── 3. 組織顏色必須與 T_COLORS 完全一致 ──────────────────────
    #
    # 手冊印的色塊若與畫面不同，醫師會照著手冊的顏色去認組織——
    # 而那是一個沒有任何提示的誤讀。
    wes = kt.get("WoundEditScreen.kt", "")
    rgb = re.findall(r"argb\(T_ALPHA,\s*(\d+),\s*(\d+),\s*(\d+)\)", wes)
    check("讀得到 T_COLORS 的五個顏色", len(rgb) == 5, "找到 %d 組" % len(rgb))
    names = ["肉芽", "腐肉", "壞死", "上皮", "其他"]
    for i, (r, g, b) in enumerate(rgb[:5]):
        hexv = "#%02x%02x%02x" % (int(r), int(g), int(b))
        check("%s 的色碼 %s 出現在手冊" % (names[i], hexv), hexv in man.lower(), hexv)
    edge = re.search(r"EDGE_COLOR\s*=\s*android\.graphics\.Color\.argb\(255,\s*(\d+),\s*(\d+),\s*(\d+)\)", wes)
    if edge:
        hexv = "#%02x%02x%02x" % tuple(int(x) for x in edge.groups())
        check("邊界線色碼 %s 出現在手冊" % hexv, hexv in man.lower(), hexv)

    # ── 4. 角色權限表必須與 PERMS 一致 ──────────────────────────
    #
    # 這是手冊裡**最危險**的一張表。寫錯的話，助理會以為自己能送訓練標註、
    # 或醫師以為自己不能——前者浪費時間，後者讓資料收不進來。
    src = open(PERMS_PY, encoding="utf-8").read()
    block = re.search(r"PERMS\s*=\s*\{(.*?)\n\}", src, re.S)
    check("讀得到 auth_users.PERMS", block is not None)
    if block:
        perms = {}
        for m in re.finditer(r'"([a-z._]+)":\s*(\{[^}]*\}|set\(ROLES\))', block.group(1)):
            k, v = m.group(1), m.group(2)
            if v == "set(ROLES)":
                perms[k] = {"physician", "nurse", "assistant", "engineer", "admin"}
            else:
                perms[k] = set(re.findall(r'"(\w+)"', v))
        # 手冊表格的每一列：(手冊列名, 權限鍵)
        ROWS = [
            ("建病患・簽同意・撤回", "patient.manage"),
            ("臨床量測", "measure.clinical"),
            ("存入個案時間軸", "record.save"),
            ("送出訓練標註", "annotation.submit"),
            ("稽核軌跡・系統狀態", "audit.read"),
            ("帳號管理", "user.manage"),
        ]
        ZH = [("醫師", "physician"), ("護理師", "nurse"), ("助理", "assistant"),
              ("工程師", "engineer"), ("管理者", "admin")]
        for label, key in ROWS:
            # 容忍列名裡的行內標籤（<b> 之類）——那是排版，不是內容。
            row = re.search(r"<td>(?:[^<]|<(?!/td>))*?%s(?:[^<]|<(?!/td>))*?</td>"
                            r"((?:\s*<td>[^<]*</td>){5})" % re.escape(label), man)
            check("手冊有「%s」這一列" % label, row is not None)
            if not row:
                continue
            cells = re.findall(r"<td>([^<]*)</td>", row.group(1))
            for (zh, role), cell in zip(ZH, cells):
                want = role in perms.get(key, set())
                got = "✓" in cell
                check("  %s × %s：手冊%s、程式%s"
                      % (label, zh, "✓" if got else "—", "✓" if want else "—"),
                      want == got)

        # 只有醫師能做的兩件事，是整個飛輪的信任基礎，單獨再確認一次。
        check("gt.verify 僅醫師", perms.get("gt.verify") == {"physician"},
              perms.get("gt.verify"))
        check("annotation.submit 僅醫師", perms.get("annotation.submit") == {"physician"},
              perms.get("annotation.submit"))
        check("工程師與管理者皆無臨床量測權",
              "engineer" not in perms.get("measure.clinical", set())
              and "admin" not in perms.get("measure.clinical", set()))

    # ── 4b. 撤回同意必須真的送到後端 ──────────────────────────
    #
    # 2026-08-06 以前：BackendClient.withdrawConsent() 存在但**沒有任何呼叫端**，
    # 病患撤回後資料仍留在雲端訓練佇列。同意書上寫的「撤回後不再納入後續訓練」
    # 那句話當時做不到——而手冊如果照抄同意書，就是在幫忙散布一個不成立的承諾。
    check("撤回有呼叫後端 withdrawConsent",
          "backend.withdrawConsent" in allkt or ".withdrawConsent(" in allkt)
    cw = kt.get("ConsentWithdrawal.kt", "")
    check("有撤回重試機制（離線時不能就這樣算了）", "retryPending" in cw)
    check("失敗的撤回會被持久化", "addPendingWithdrawals" in cw)
    csel = kt.get("CaseSelectScreen.kt", "")
    check("撤回流程有接上 ConsentWithdrawal", "ConsentWithdrawal.pushToBackend" in csel)
    check("進入個案頁會補做未完成的撤回", "ConsentWithdrawal.retryPending" in csel)
    check("未完成的撤回常駐顯示", "pendingBanner" in csel)

    # ── 4c. 同意書的揭露 ─────────────────────────────────────
    #
    # 知情同意的內容不是排版問題。少一項揭露，簽名就不構成「知情」。
    cs = kt.get("ConsentSignatureScreen.kt", "")
    for item in ["會離開手機", "不會離開手機", "台灣境內", "保留多久", "隨時撤回", "無法回溯移除"]:
        check("同意書詳細說明含「%s」" % item, item in cs)
    # Compose 的 Text 不解析 Markdown——星號會原樣顯示在法律文件上。
    # ⚠ 只檢查**字串字面值**，不看註解：本專案的註解慣例就是用 ** 強調，
    # 把註解算進來會讓這條永遠是紅的，然後被停用。
    lits = re.findall(r'"((?:[^"\\\n]|\\.)*)"', re.sub(r"//[^\n]*", "",
                      re.sub(r"/\*.*?\*/", "", cs, flags=re.S)))
    md = [x for x in lits if "**" in x]
    check("同意書的顯示文字沒有殘留 Markdown 星號", not md, md[:2])

    # ── 5. 手冊本身的健全性 ─────────────────────────────────────
    check("手冊有 viewport（手機看不會爆版）", 'name="viewport"' in man)
    check("手冊有列印樣式（可印成 PDF 貼護理站）", "@media print" in man)
    check("手冊五個角色分頁都在",
          all(('data-r="%s"' % r) in man for r in ("all", "doc", "nur", "asi", "eng")))
    # 手冊不該連外：病房網路不穩，而最需要看手冊的時刻正是操作卡住的當下。
    ext = re.findall(r'(?:src|href)\s*=\s*"(https?://[^"]+)"', man)
    check("手冊不依賴任何外部資源（離線可用）", not ext, ext[:3])

    # ── 6. App 首頁真的有入口 ───────────────────────────────────
    ma = kt.get("MainActivity.kt", "")
    check("首頁有「使用說明書」入口", "使用說明書" in ma)
    check("首頁入口導向 manual 畫面", 'currentScreen = "manual"' in ma)
    check("manual 畫面有接上 ManualScreen", '"manual" -> ManualScreen' in ma)
    ms = kt.get("ManualScreen.kt", "")
    check("ManualScreen 載入的是 assets 內的手冊（離線）",
          "file:///android_asset/manual.html" in ms)

    print("\n%d 項檢查，%d 項失敗" % (TOTAL[0], len(FAILED)))
    if FAILED:
        print("失敗：")
        for f in FAILED:
            print("  · " + f)
        print("\n⚠ 兩邊都要查：可能是手冊過時，也可能是程式改壞了按鈕文字或權限。")
        return 1
    print("全部通過：手冊的按鈕名稱、訊息、組織顏色、角色權限都與原始碼一致，且離線可用。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
