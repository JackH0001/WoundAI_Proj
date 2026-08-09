# -*- coding: utf-8 -*-
"""合併閘＋契約測試：**個案結案／刪除的規則，以及刪除時的順序**。

    python engineering/phase2/test_case_deletion_rules.py

## 這支測試的兩個身分

**一、合併閘。** 2026-08-08：刪除／結案與 ArUco phantom 過濾是在 **macOS** 那邊
做的，Windows 工作區完全沒有這些程式碼（實測 `deleteCaseIfEmpty` 等符號 0 個檔命中）。
兩邊各自可以編譯、各自測起來都正常——差異只有在合併時才會顯現，
而合併漏掉一半是**不會有任何錯誤訊息**的：少了 DAO 方法會編譯失敗（看得見），
少了 UI 按鈕只是「那顆鈕沒出現」（看不見，而且要等到有人想刪個案才會發現）。

所以這支測試在合併前**應該是紅的**。它由紅轉綠，就是「Mac 那批真的完整進來了」。

**二、規則的長期契約。** 刪除是不可逆操作，而這裡有三條規則的理由不寫下來就會被改掉：

  1. **有量測的個案不刪，只結案。** 個案代碼（WD-code）已經送到雲端、寫進稽核軌跡。
     刪掉本機那一列，雲端那些資料就變成孤兒——沒有任何東西指得回它是誰的傷口。
     結案是可逆的紀錄，刪除不是。
  2. **送過標註的量測不刪。** 那筆資料已經在雲端訓練佇列裡。本機刪掉只會讓
     「撤回」這件事變得不可能執行——撤回要靠 image_id，而 image_id 存在被刪的那一列裡。
     要移除雲端資料請走撤回同意，不是刪除。
  3. **先刪 DB 列，再刪檔案。** 反過來的話，刪檔成功、刪列前 App 被殺掉，
     資料庫就留下一列指向不存在的影像——時間軸讀到它會是空白或崩潰，
     而使用者完全不知道發生什麼事。反之（列沒了、檔還在）只是佔空間，
     由保存期限清理收尾即可。**寧可留下孤兒檔案，不要留下孤兒紀錄。**

## 為什麼是靜態檢查

刪除路徑要跑動態測試得先有 Room、有 Android runtime。而這三條規則全部是
**原始碼層面看得出來的形狀**，尤其第 3 條——它是兩行程式碼的先後順序，
執行起來在 99.9% 的情況下毫無差別，只有在那 0.1% 被殺掉的時候才會爆。
這種東西動態測試幾乎測不到，靜態順序檢查反而是最可靠的防線。
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.abspath(os.path.join(
    HERE, "..", "..", "Android", "app", "src", "main", "java",
    "com", "woundmeasurement", "app"))

FAILED = []
GATE_FAILED = []
TOTAL = [0]
_section = [""]


def check(name, ok, detail="", gate=False):
    # detail 一律是「為什麼這條紅了」的診斷，通過時印出來只會誤導
    # （本檔第一版就這樣印過 "PASS ... 找不到過濾邏輯"）。
    TOTAL[0] += 1
    print(("PASS  " if ok else "FAIL  ") + name + (("   " + str(detail)) if (detail and not ok) else ""))
    if not ok:
        (GATE_FAILED if gate else FAILED).append(name)


def gate(name, ok, detail=""):
    """合併閘項目：紅代表「Mac 的修改還沒進到這個工作區」。"""
    check(name, ok, detail, gate=True)


def strip(s):
    """去註解。註解裡會寫規則說明，含有要找的字串，不能算數。"""
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    return re.sub(r"//[^\n]*", "", s)


def read(*parts):
    p = os.path.join(APP, *parts)
    if not os.path.isfile(p):
        return None
    return strip(open(p, encoding="utf-8").read())


def body_of(src, fn_name):
    """粗略取出一個 Kotlin 函式的主體（靠大括號配對）。找不到回 None。"""
    m = re.search(r"fun\s+%s\b" % re.escape(fn_name), src or "")
    if not m:
        return None
    i = src.find("{", m.end())
    if i < 0:
        return None
    depth, j = 0, i
    while j < len(src):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[i:j + 1]
        j += 1
    return src[i:]


def main():
    if not os.path.isdir(APP):
        print("找不到 App 原始碼：%s" % APP)
        return 1

    case_dao = read("data", "dao", "WoundCaseDao.kt")
    meas_dao = read("data", "dao", "MeasurementDao.kt")
    repo = read("data", "repo", "CaseRepository.kt")
    sel = read("pipeline", "CaseSelectScreen.kt") or read("ui", "CaseSelectScreen.kt")
    tl = read("pipeline", "WoundTimelineScreen.kt") or read("ui", "WoundTimelineScreen.kt")
    vm = read("pipeline", "MeasureViewModel.kt")

    for label, s in (("WoundCaseDao", case_dao), ("MeasurementDao", meas_dao),
                     ("CaseRepository", repo), ("CaseSelectScreen", sel),
                     ("WoundTimelineScreen", tl), ("MeasureViewModel", vm)):
        check("讀得到 %s.kt" % label, s is not None)
    if repo is None or meas_dao is None:
        print("\n關鍵檔案讀不到，後續檢查無意義。")
        return 1

    # ── 1. DAO 層：刪除的能力要存在 ────────────────────────────────
    print("\n── 1 DAO ──")
    gate("WoundCaseDao 有 deleteById",
         bool(re.search(r"fun\s+deleteById\b", case_dao or "")),
         "找不到 `fun deleteById`")
    gate("MeasurementDao 有 getById（刪除前要先讀出檔案路徑）",
         bool(re.search(r"fun\s+getById\b", meas_dao)),
         "找不到 `fun getById`")
    gate("MeasurementDao 有 deleteById",
         bool(re.search(r"fun\s+deleteById\b", meas_dao)),
         "找不到 `fun deleteById`")
    # 既有能力：拒絕規則要靠它判斷
    check("MeasurementDao 有 getCountByCase（deleteCaseIfEmpty 的判準）",
          "getCountByCase" in meas_dao)

    # ── 2. Repository：規則本身 ───────────────────────────────────
    print("\n── 2 規則 ──")
    b_case = body_of(repo, "deleteCaseIfEmpty")
    b_meas = body_of(repo, "deleteMeasurementIfUnsubmitted")
    gate("CaseRepository 有 deleteCaseIfEmpty", b_case is not None)
    gate("CaseRepository 有 deleteMeasurementIfUnsubmitted", b_meas is not None)

    if b_case:
        # 函式名叫 "IfEmpty" 不代表真的檢查了。要看到它去數量測。
        counts = "getCountByCase" in b_case or "getMeasurementsByCase" in b_case
        gate("deleteCaseIfEmpty 真的去數量測筆數（不是只靠函式命名）", counts,
             "主體裡找不到 getCountByCase／getMeasurementsByCase")
        # 最危險的實作：查到有量測，卻順手把它們也刪了。
        cascade = re.search(r"(measurements|meas)\w*\.deleteBy|deleteMeasurementsByCase", b_case)
        gate("deleteCaseIfEmpty **不**連帶刪除量測（有量測就該拒絕，不是清乾淨再刪）",
             cascade is None, cascade.group(0) if cascade else "")

    if b_meas:
        gate("deleteMeasurementIfUnsubmitted 檢查 annotationSubmitted",
             "annotationSubmitted" in b_meas,
             "主體裡找不到 annotationSubmitted —— 那它憑什麼判斷送過沒有？")

        # ── 順序：先刪 DB 列，再刪檔案 ──
        # ⚠ 檔案刪除是走 LocalImageStore.delete(name)（密文檔以 UUID 命名，
        #   不是直接碰 java.io.File）。第一版只找 `File(` 與 `.delete()` 空括號，
        #   對 `imageStore.delete(m.imagePath)` 完全視而不見 → 假紅。
        i_row = min([m.start() for m in re.finditer(r"deleteById\s*\(", b_meas)] or [10 ** 9])
        file_ops = [m.start() for m in re.finditer(
            r"[Ss]tore\.delete\s*\(|File\s*\(|deleteRecursively", b_meas)]
        i_file = min(file_ops or [10 ** 9])
        has_both = i_row < 10 ** 9 and i_file < 10 ** 9
        gate("刪除同時處理 DB 列與檔案", has_both,
             "row=%s file=%s" % (i_row < 10 ** 9, i_file < 10 ** 9))
        if has_both:
            # 同一條規則 CaseRepository.purgeExpiredImages 已經遵守並寫明理由，
            # 這裡是把它變成「改不掉」而不是「希望下一個人記得」。
            gate("**先刪列再刪檔**（顛倒會留下指向不存在檔案的孤兒紀錄）",
                 i_row < i_file,
                 "檔案刪除出現在 deleteById 之前" if i_row >= i_file else "")
        # 只檢查真的有內容的路徑欄位。`dataPath` 全專案唯一的寫入是
        # MeasureViewModel:491 的 `dataPath = ""` —— 它是沒被啟用的欄位，
        # 要求清它只會製造噪音。（第一版就這樣要求了。）
        for f in ("imagePath", "rasterPath"):
            gate("刪除有清掉 %s" % f, f in b_meas,
                 "留下來會一直佔手機空間，而且沒有任何東西會再指向它")

    # ── 3. UI 入口 ────────────────────────────────────────────────
    print("\n── 3 入口 ──")
    if sel:
        gate("個案頁有「結案」入口", "結案" in sel)
        gate("個案頁有「刪除」入口", "刪除" in sel)
        # 實際文案是「請改用『結案』」，帶全形引號。第一版硬比對 "請改用結案"
        # 會假紅——**檢查文案時比對整串是最脆的做法**，抓語意骨架就好。
        gate("被規則擋下時告訴使用者改用結案（而不是只說失敗）",
             ("請改用" in sel and "結案" in sel),
             "找不到引導文字 —— 使用者會以為是 App 壞了")
        # 刪除入口必須走「限空個案」那條 repo 規則，不能直接呼叫 DAO。
        # （空個案沒有臨床資料可失去，因此不強制二次確認對話框；
        #   會毀掉影像的單筆量測刪除才強制，見下。）
        gate("個案刪除走 deleteCaseIfEmpty，而非直接呼叫 DAO",
             "deleteCaseIfEmpty" in sel and "deleteById" not in sel)
    if tl:
        # 這一項在 2026-08-08 是**已知未實作**：Mac 端明列為下一輪首項。
        # 留在這裡當提醒——時間軸是醫師唯一看得到單筆量測的地方，
        # 拍壞的那張若只能整個個案刪掉，等於逼人用更危險的操作。
        _tl_del = "deleteMeasurementIfUnsubmitted" in tl
        check("時間軸有單筆量測刪除入口", _tl_del,
              "【已知未實作】規則與 repo 方法就位，只差 UI；勿為此改動 Column 結構")
        # 這一條**只在上面那條做完後才有意義**，但要先寫下來：
        # 單筆刪除會真的銷毀那張傷口影像的密文檔，而它常常是某個時間點的唯一影像。
        # 個案刪除可以不確認（空的，沒東西可失去），這個不行。
        if _tl_del:
            check("時間軸單筆刪除有二次確認（會銷毀影像，且無法復原）",
                  "AlertDialog" in tl)

    # ── 4. ArUco phantom 過濾 ─────────────────────────────────────
    print("\n── 4 貼紙誤認排除 ──")
    if vm:
        b_an = body_of(vm, "analyzeViaBackend") or vm
        # ⚠ 這一條第一版寫成「body 裡有 phantom 就算過」——結果被既有的
        # `hintCap = c.phantomHint` 誤判成通過（那是後端回傳的提示欄位，
        # 跟過濾一點關係都沒有）。**關鍵字撞名是靜態檢查最常見的假綠。**
        # 改成必須看到「以貼紙框為依據、而且真的剔掉輪廓」兩件事同時成立。
        _by_quad = "markerQuad" in b_an
        _removes = re.search(r"\.filter(Not|Indexed)?\s*[({]|removeAll|剔除", b_an) is not None
        gate("analyzeViaBackend 依貼紙框剔除輪廓", _by_quad and _removes,
             "markerQuad=%s 剔除動作=%s（phantomHint 不算）" % (_by_quad, _removes))
        # 外擴是必要的：貼紙邊緣的陰影與列印毛邊會落在框外幾個像素，
        # 用貼紙的精確外框過濾會漏掉一圈，而那一圈正是最常被誤判成傷口的地方。
        gate("過濾框有外擴（1.15 或等價）",
             re.search(r"1\.15|0\.15|expand|inflate|外擴", b_an) is not None)
        gate("採用比例門檻而非全有全無（0.7）",
             re.search(r"0\.7", b_an) is not None,
             "沒有門檻的話，只要有一個點碰到貼紙就整個輪廓被剔掉")
    # 紅字提示不在 ViewModel 也不在時間軸，而在量測頁 MeasureValidationEntry.kt。
    # 第一版漏讀這個檔 → 提示明明做了卻報紅。**檢查「使用者看得到什麼」時，
    # 搜尋範圍要涵蓋所有會顯示它的畫面，而不是只有算出它的那一層。**
    entry = read("pipeline", "MeasureValidationEntry.kt")
    if vm or tl or entry:
        src_all = (vm or "") + (tl or "") + (entry or "")
        gate("被排除時有明確告知使用者（含可手動補畫）",
             "自動排除" in src_all and ("補畫" in src_all or "修邊" in src_all),
             "靜默排除等於騙人：真傷口被剔掉時沒人會知道")

    # ── 收尾 ──────────────────────────────────────────────────────
    print("\n%d 項檢查，合併閘 %d 紅、其他 %d 紅"
          % (TOTAL[0], len(GATE_FAILED), len(FAILED)))
    if GATE_FAILED:
        print("\n【合併閘未通過】以下項目在這個工作區找不到：")
        for x in GATE_FAILED:
            print("  · " + x)
        print("\n這批修改是在 macOS 上做的。若 Mac 端已 commit/push，"
              "請在本機 pull 後重跑；仍然紅表示合併漏了東西。")
    if FAILED:
        print("\n【規則檢查未通過】")
        for x in FAILED:
            print("  · " + x)
    if not GATE_FAILED and not FAILED:
        print("全部通過：刪除規則、先刪列再刪檔的順序、貼紙誤認過濾都在。")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
