# -*- coding: utf-8 -*-
"""原始碼契約測試：**醫師修邊的成果不得在任何一條路徑上被丟掉**。

    python engineering/phase2/test_app_persistence_contract.py

## 為什麼需要一個「讀原始碼」的測試

這一類 bug 已經發生兩次，兩次都是同一個形狀：

```kotlin
onDone = { newPoly, iou, newArea, _, _ ->   // ← 組織比例與修邊柵格被丟棄
```

Kotlin 編譯得過、Lint 不會警告、畫面上一切正常、還會顯示「✅ 已更新」。
醫師花十分鐘標好組織分區，按下完成，資料當場消失——而他要等到**下次進來**
才會發現，那時已經無從得知是自己沒存到還是系統吃掉了。

Compose UI 沒有便宜的自動化測試手段（要 instrumentation + 實機/模擬器），
但這個 bug 的特徵在原始碼層面是**看得出來的**：callback 參數位置被 `_` 佔掉、
呼叫端漏傳參數。所以在這裡守。這不能取代實機驗證，但它擋得住回歸——
而回歸正是這個 bug 最可能的來源（下次有人重構這段時很容易再寫成 `_`）。

## 守的四條線

1. 所有 `WoundEditScreen(` 呼叫端都要傳 `resume =` 且不是 `null`
   → 沒有它，回頭修邊時柵格由多邊形重建，組織分區消失、面積每次漂移 0.5%
2. 所有 `onDone` lambda 都要**綁完五個參數**（後兩個是組織比例與柵格）
   → 這是上面那個 bug 的直接特徵
3. 所有 `submitAnnotation(` 呼叫端都要傳 `tissueRaster`
   → 沒有它，醫師修好的組織遮罩永遠進不了訓練集，而畫面照樣顯示「已送出」
4. 有寫 `rasterPath =` 的地方就要有 `tissueGranulation =`
   → 兩者是同一次修邊的產物，只存一個會讓時間軸顯示修邊**前**的組織比例
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# 可用環境變數指向別處，用途是**突變測試**：把原始碼複製一份、故意寫回舊的
# `onDone = { a, b, c, _, _ ->`，確認這支測試真的會紅。
# 在有 bug 的程式碼上也會過的測試，比沒有測試更糟——它會讓人以為守住了。
APP = os.environ.get("WOUNDAI_APP_SRC") or os.path.abspath(os.path.join(
    HERE, "..", "..", "Android", "app", "src", "main", "java",
    "com", "woundmeasurement", "app"))

FAILED = []
TOTAL = [0]


def check(name, ok, detail=""):
    TOTAL[0] += 1
    print(("PASS  " if ok else "FAIL  ") + name + (("   " + str(detail)) if detail else ""))
    if not ok:
        FAILED.append(name)


def kt_files():
    for root, _, files in os.walk(APP):
        for f in files:
            if f.endswith(".kt"):
                yield os.path.join(root, f)


def strip_comments(s):
    """去掉註解，避免說明文字裡的範例被當成真的程式碼。

    這個測試的整個價值建立在「我看到的是會被執行的程式碼」上——
    而本專案的註解裡**刻意寫了很多反例**（就是為了記住不該怎麼寫）。
    不去註解的話，那些反例會讓測試永遠失敗，然後被停用。
    """
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    return re.sub(r"//[^\n]*", "", s)


def main():
    if not os.path.isdir(APP):
        print("找不到 App 原始碼：%s" % APP)
        return 1

    srcs = {p: strip_comments(open(p, encoding="utf-8").read()) for p in kt_files()}
    name = lambda p: os.path.basename(p)

    # ── 1. WoundEditScreen 的 resume 參數 ──────────────────────────────
    callers = {p: s for p, s in srcs.items()
               if "WoundEditScreen(" in s and not name(p).startswith("WoundEditScreen")}
    check("找得到 WoundEditScreen 的呼叫端", len(callers) >= 2,
          "共 %d 個：%s" % (len(callers), "、".join(sorted(name(p) for p in callers))))

    for p, s in sorted(callers.items()):
        for m in re.finditer(r"WoundEditScreen\(", s):
            seg = s[m.start():m.start() + 2500]
            has = re.search(r"resume\s*=\s*(\w+)", seg)
            check("%s：WoundEditScreen 有傳 resume" % name(p), has is not None)
            if has:
                check("%s：resume 不是寫死 null" % name(p), has.group(1) != "null",
                      "resume = %s" % has.group(1))

    # ── 2. onDone 必須綁滿五個參數 ─────────────────────────────────────
    #
    # 簽名：(polygon, iou, newArea, tissueFrac, raster) -> Unit
    # 後兩個被 `_` 吃掉，就是資料損失。
    for p, s in sorted(callers.items()):
        for m in re.finditer(r"onDone\s*=\s*\{([^\n]*?)->", s):
            params = [x.strip() for x in m.group(1).split(",")]
            check("%s：onDone 綁滿 5 個參數" % name(p), len(params) == 5,
                  "實際 %d 個：%s" % (len(params), m.group(1).strip()))
            if len(params) == 5:
                check("%s：onDone 的組織比例參數沒被丟棄" % name(p), params[3] != "_",
                      "第 4 參數＝%s" % params[3])
                check("%s：onDone 的修邊柵格參數沒被丟棄" % name(p), params[4] != "_",
                      "第 5 參數＝%s" % params[4])

    # ── 3. submitAnnotation 要送組織遮罩 ───────────────────────────────
    # ⚠ 只算**真正打到後端**的那一層。`vm.submitAnnotation(...)` 是 ViewModel 的轉發，
    # 它自己再去呼叫 BackendClient；把轉發也算進來會是誤報，而誤報會讓人停用測試。
    # 用 `imageW` 區分：只有 BackendClient 的簽名有這個參數。
    n_wire = 0
    for p, s in sorted(srcs.items()):
        for m in re.finditer(r"\w+\.submitAnnotation\(", s):
            seg = s[m.start():m.start() + 3000]
            if "imageW" not in seg:
                continue                       # ViewModel 轉發，不是 wire 呼叫
            n_wire += 1
            check("%s：submitAnnotation 有送 tissueRaster" % name(p),
                  "tissueRaster" in seg)
            check("%s：submitAnnotation 有送 tissueMaskPng" % name(p),
                  "tissueMaskPng" in seg)
    # 找不到任何 wire 呼叫的話上面兩條線等於沒守——那比失敗更危險，因為它是綠的。
    check("找得到真正打到後端的 submitAnnotation", n_wire >= 2, "共 %d 處" % n_wire)

    # ── 4. rasterPath 與組織比例必須成組寫入 ───────────────────────────
    #
    # 兩者是同一次修邊的產物。只寫柵格不寫比例，時間軸卡片與癒合趨勢會顯示
    # **修邊前**的組織比例，而畫面上完全看不出來。
    for p, s in sorted(srcs.items()):
        for m in re.finditer(r"rasterPath\s*=", s):
            seg = s[max(0, m.start() - 1800):m.start() + 1800]
            if "ADD COLUMN" in seg or "val rasterPath" in seg:
                continue                       # migration 與 entity 宣告不算寫入點
            check("%s：寫 rasterPath 處同時有寫 tissueGranulation" % name(p),
                  "tissueGranulation" in seg)

    # ── 4b. 白平衡增益必須貫穿整條鏈 ───────────────────────────────────
    #
    # 後端的組織比例是用**色卡白平衡**算的（見 app.py stage3b_colorcal）。
    # 端上若有任何一處退回灰世界，畫面上就會出現兩個不同的答案：
    # 結果欄說「肉芽 73%」而修邊底稿只把 21% 標成肉芽（實測數字）。
    # 醫師是在修邊畫面上做判斷的，而那份 GT 會進訓練集——
    # 所以不一致的代價不是「顯示怪怪的」，是訓練資料被污染。
    for p, s in sorted(srcs.items()):
        for m in re.finditer(r"TissueSeg\.classify\(([^)]*)\)", s):
            n = len(m.group(1).split(","))
            check("%s：TissueSeg.classify 有帶 wbGains" % name(p), n == 9,
                  "參數 %d 個（應 9）" % n)
    for p, s in sorted(srcs.items()):
        for fn in ("WoundEditScreen(", "AnalysisPreview("):
            if name(p).startswith(fn[:-1]):
                continue                       # 定義處不算呼叫端
            for m in re.finditer(re.escape(fn), s):
                check("%s：%s 有傳 wbGains" % (name(p), fn[:-1]),
                      "wbGains" in s[m.start():m.start() + 2600])
    wec = next((s for q, s in srcs.items() if name(q) == "WoundEditScreen.kt"), "")
    erc = next((s for q, s in srcs.items() if name(q) == "EditRasterCodec.kt"), "")
    check("EditRaster 建構時帶上 wbGains", "wbGains = wbGains" in wec)
    # 存了不讀等於沒存。從時間軸回頭修邊時就是靠這個欄位重建底稿。
    check("EditRasterCodec 對 wb_gains 有存也有讀", erc.count("wb_gains") >= 2,
          "出現 %d 次" % erc.count("wb_gains"))

    # ── 5. peek 期間不得作畫 ───────────────────────────────────────────
    #
    # 「看不見自己塗了什麼卻塗得下去」的那一筆會直接進 GT。
    wes = next((s for p, s in srcs.items() if name(p) == "WoundEditScreen.kt"), None)
    check("WoundEditScreen.kt 存在", wes is not None)
    if wes:
        check("有 peek（按住看原圖）狀態", "collectIsPressedAsState" in wes)
        check("peek 期間停用作畫", re.search(r"canPaint\s*=.*!peeking", wes) is not None)
        # 組織分類列若隨 peek 消失，版面會重排 → 畫布高度改變 → 影像跳動。
        row = re.search(r"if \(tool == EditTool\.TISSUE\) \{", wes)
        check("組織分類列的顯示條件只看工具、不看 peek", row is not None and
              "tool == EditTool.TISSUE && !peeking" not in wes)

    print("\n%d 項檢查，%d 項失敗" % (TOTAL[0], len(FAILED)))
    if FAILED:
        print("失敗：")
        for f in FAILED:
            print("  · " + f)
        return 1
    print("全部通過：修邊柵格、組織比例、組織遮罩在三條路徑上都不會被丟掉。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
