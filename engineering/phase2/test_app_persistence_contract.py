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

## ⚠ 這支測試綠燈**不代表編得起來**

它只做文字比對，看不懂作用域。2026-08-06 實際發生過：本測試回報
「AnalysisPreview.kt：TissueSeg.classify 有帶 wbGains → PASS」，
而 Kotlin 編譯器回 `Unresolved reference: wbGains`——那個呼叫在一個
**輔助函式**裡，而參數是加在 composable 上的，兩者不同作用域。

編譯器是那一類錯誤的守門人，這支測試守的是另一類：**編得過、跑得動、
但語意已經漂掉**的情況。兩者不能互相取代。改完 App 一定要 `build_release.ps1`。

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
            # 簽名：(edited, all, iou, newArea, tissueFrac, raster)
            # `all` 是 2026-08-07 為了多處傷口加的——只用 edited 的話，
            # 第二個傷口會被標成背景，等於教模型「那不是傷口」。
            check("%s：onDone 綁滿 6 個參數" % name(p), len(params) == 6,
                  "實際 %d 個：%s" % (len(params), m.group(1).strip()))
            if len(params) == 6:
                for idx, what in ((1, "所有輪廓"), (4, "組織比例"), (5, "修邊柵格")):
                    check("%s：onDone 的%s參數沒被丟棄" % (name(p), what), params[idx] != "_",
                          "第 %d 參數＝%s" % (idx + 1, params[idx]))

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

    # ── 4d. 多處傷口的輪廓不得在任何一段被丟掉 ──────────────────────
    #
    # 同一肢體多處傷口是臨床常態。舊版三處各自獨立地只取最大連通元件，
    # 而醫師在修邊畫面明明兩個都標了。後果不只是「參照圖沒更新」——
    # **送進訓練集的 GT 會把第二個傷口標成背景**。
    wes_all = next((s for q, s in srcs.items() if name(q) == "WoundEditScreen.kt"), "")
    check("修邊追**所有**連通元件（不是只取最大）",
          "traceAllBoundaries" in wes_all and "traceLargestBoundary(" not in wes_all)
    vm = next((s for q, s in srcs.items() if name(q) == "MeasureViewModel.kt"), "")
    check("ViewModel 保存所有輪廓", "lastPolygons" in vm)
    check("applyEditedPolygon 收得下所有輪廓", "allPolygons" in vm)
    bc = next((s for q, s in srcs.items() if name(q) == "BackendClient.kt"), "")
    check("送出時帶 gt_polygons", "gt_polygons" in bc)
    check("送出時帶 area_cm2（面積以遮罩為真值，不由多邊形反算）", '"area_cm2"' in bc)
    check("classify 回應解析 wound_polygons（AI 的初始輪廓也要多個）",
          "wound_polygons" in bc)
    wes2 = next((s for q, s in srcs.items() if name(q) == "WoundEditScreen.kt"), "")
    check("修邊畫面的初始遮罩填**所有**輪廓", "initPolys.forEach { scanlineFill" in wes2)
    # ⚠ 只檢查參數名在不在是不夠的：`initialPolygons = emptyList()` 照樣含那個字串，
    # 而它等於完全沒傳。與 `resume = null` 是同一類弱點——
    # 一個「傳了但傳空的」參數在原始碼裡看起來完全正常。
    for p2, s3 in sorted(srcs.items()):
        for m in re.finditer(r"WoundEditScreen\(", s3):
            if name(p2).startswith("WoundEditScreen"):
                continue
            seg = s3[m.start():m.start() + 2600]
            got = re.search(r"initialPolygons\s*=\s*([A-Za-z_][\w.]*(?:\([^)]*\))?)", seg)
            check("%s：WoundEditScreen 有傳 initialPolygons" % name(p2), got is not None)
            if got:
                check("%s：initialPolygons 不是寫死的空清單" % name(p2),
                      got.group(1) not in ("emptyList()", "listOf()"), got.group(1))
    mrs = next((s for q, s in srcs.items() if name(q) == "MeasurementReviewScreen.kt"), "")
    check("時間軸回頭修邊用 PolygonJson 解析（舊解析法碰到多輪廓會解成空）",
          "parsePolygons(cur.gtPolygon)" in mrs)
    for p, s2 in sorted(srcs.items()):
        for m in re.finditer(r"AnalysisPreview\(", s2):
            if name(p).startswith("AnalysisPreview"):
                continue
            seg2 = s2[m.start():m.start() + 2600]
            g2 = re.search(r"polygons\s*=\s*([A-Za-z_][\w.]*(?:\([^)]*\))?)", seg2)
            check("%s：AnalysisPreview 有傳 polygons" % name(p), g2 is not None)
            if g2:
                check("%s：polygons 不是寫死的空清單" % name(p),
                      g2.group(1) not in ("emptyList()", "listOf()"), g2.group(1))

    # ── 5. peek 期間不得作畫 ───────────────────────────────────────────
    #
    # 「看不見自己塗了什麼卻塗得下去」的那一筆會直接進 GT。
    wes = next((s for p, s in srcs.items() if name(p) == "WoundEditScreen.kt"), None)
    check("WoundEditScreen.kt 存在", wes is not None)
    if wes:
        check("有 peek（按住看原圖）狀態", "collectIsPressedAsState" in wes)
        check("peek 期間停用作畫", re.search(r"canPaint\s*=.*!peeking", wes) is not None)
        # 組織分類列**完全不可以有顯示條件**——不論是 peek 還是工具。
        #
        # 它一出現／消失，畫布的 weight(1f) 高度就變，base() 跟著變，
        # 整張影像的縮放與位置一起跳。這個 bug 咬過兩次：
        # 先是 peek 強制切到「移動」讓它消失，修掉之後單純切工具仍然會。
        # 版面必須恆定，可用性用 enabled 控制。（詳見 test_compose_safety.py）
        check("組織分類列不隨 peek 消失", "tool == EditTool.TISSUE && !peeking" not in wes)
        check("組織分類列不隨工具消失", "if (tool == EditTool.TISSUE) {" not in wes)

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
