# -*- coding: utf-8 -*-
"""靜態檢查：**Compose 裡會讓 App 閃退、而堆疊追蹤指不出兇手的寫法**。

    python engineering/phase2/test_compose_safety.py

## 為什麼需要這一支

2026-08-07 實測：進入 DB v5 之前建立的個案時間軸 → App 直接閃退。堆疊是：

    java.lang.IndexOutOfBoundsException: Index -1 out of bounds for length 0
      at java.util.ArrayList.remove(ArrayList.java:559)
      at androidx.compose.runtime.Stack.pop(Stack.kt:26)
      at androidx.compose.runtime.ComposerImpl.exitGroup(Composer.kt:2333)
      at androidx.compose.runtime.ComposerImpl.end(Composer.kt:2499)
      at androidx.compose.runtime.ComposerImpl.endRoot(Composer.kt:1483)

**整份堆疊裡沒有任何一行是我們的程式碼。** 全部都在 Compose runtime 內部，
因為錯誤是在 composition 結束、要收攤時才被發現的——那時真正犯錯的那個
composable 早就跑完了。

兇手是 `WoundTimelineScreen.kt` 的：

    Column(modifier) {
        ...
        if (!hasAny) { Text("…"); return@Column }   // ← 這一行
        ...
    }

`Column` 是 inline composable，編譯器在 lambda 前後插入
`startReplaceableGroup` / `endReplaceableGroup`，而 `return@Column` 會跳過結尾那一個，
群組堆疊從此不平衡。

而它**只在特定資料下才會執行**（該個案所有量測的 tissue_* 皆為 null），
所以潛伏很久：v5 之後建立的個案有組織資料、不走那條分支，測試時完全正常。
早期建立的病患正是最少被打開的那些——也正是最晚才會踩到的那些。

## 這一類問題的共同形狀

編譯得過、Lint 不警告、大多數資料下正常、崩潰時堆疊指不出兇手。
唯一擋得住的地方是原始碼層面的樣式檢查。
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.abspath(os.path.join(
    HERE, "..", "..", "Android", "app", "src", "main", "java",
    "com", "woundmeasurement", "app"))

FAILED = []
TOTAL = [0]


def check(name, ok, detail=""):
    TOTAL[0] += 1
    print(("PASS  " if ok else "FAIL  ") + name + (("   " + str(detail)) if detail else ""))
    if not ok:
        FAILED.append(name)


def strip(s):
    """去註解。註解裡會刻意寫出反例（就是為了記住不該怎麼寫），不能算數。"""
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    return re.sub(r"//[^\n]*", "", s)


# Compose 的容器型 composable。在它們的 content lambda 裡提早 return 就會壞。
# 這份清單不必窮舉——列出專案實際用到的即可，日後用到新的再加。
CONTAINERS = [
    "Column", "Row", "Box", "Surface", "Card", "ElevatedCard", "OutlinedCard",
    "Scaffold", "LazyColumn", "LazyRow", "BoxWithConstraints", "FlowRow",
    "AlertDialog", "ModalBottomSheet", "Dialog",
]


def main():
    if not os.path.isdir(APP):
        print("找不到 App 原始碼：%s" % APP)
        return 1

    files = {}
    for root, _, fs in os.walk(APP):
        for f in fs:
            if f.endswith(".kt"):
                files[f] = strip(open(os.path.join(root, f), encoding="utf-8").read())
    check("讀得到 App 的 Kotlin 原始碼", len(files) > 10, "%d 個檔案" % len(files))

    # ── 1. composable lambda 裡不得提早 return ──────────────────────
    #
    # 這是本次閃退的直接成因。要用 if/else，不要用 return@Xxx。
    hits = []
    for f, s in sorted(files.items()):
        for c in CONTAINERS:
            for m in re.finditer(r"\breturn@%s\b" % c, s):
                hits.append("%s:%d return@%s" % (f, s[:m.start()].count("\n") + 1, c))
    check("沒有任何 return@<容器型 composable>", not hits, hits[:4])

    # ── 2. Modifier.weight 的參數必須恆為正 ────────────────────────
    #
    # Compose 內部是 `require(weight > 0)`。傳 0 或負數會拋 IllegalArgumentException，
    # 而權重常常是算出來的（比例、分數），算到 0 是很容易發生的事。
    #
    # 這裡只找「明顯用變數當權重且看不到守衛」的情況，不求精確——
    # 目的是逼人在那一行旁邊寫清楚為什麼它不會是 0。
    for f, s in sorted(files.items()):
        for m in re.finditer(r"\.weight\(\s*([A-Za-z_][\w.]*(?:\.toFloat\(\))?)\s*[,)]", s):
            expr = m.group(1)
            ln = s[:m.start()].count("\n") + 1
            # 往前找 3 行內有沒有 > 0 的守衛
            ctx = "\n".join(s.split("\n")[max(0, ln - 4):ln])
            guarded = re.search(r">\s*0", ctx) is not None
            check("%s:%d weight(%s) 有正值守衛" % (f, ln, expr), guarded,
                  "找不到 `> 0` 判斷" if not guarded else "")

    # ── 3. 本次崩潰的具體防線 ─────────────────────────────────────
    wt = files.get("WoundTimelineScreen.kt", "")
    check("WoundTimelineScreen 的組織圖改用 if/else",
          "if (!hasAny) {" in wt and "} else {" in wt and "return@Column" not in wt)

    print("\n%d 項檢查，%d 項失敗" % (TOTAL[0], len(FAILED)))
    if FAILED:
        print("失敗：")
        for x in FAILED:
            print("  · " + x)
        print("\n⚠ 這些寫法編譯得過、Lint 不警告，而崩潰時堆疊裡不會有我們的程式碼。")
        return 1
    print("全部通過：沒有提早 return 出 composable lambda，weight 皆有正值守衛。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
