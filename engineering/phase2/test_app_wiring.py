# -*- coding: utf-8 -*-
"""靜態檢查：**app.py 的頂層接線不可以參照後面才定義的名字**。

    python engineering/phase2/test_app_wiring.py

## 這支測試對應的事故

2026-08-19：`/api/v1/lite/segment` 部署後一直 **404**。

    第 167 行   init_lite(segment_for_lite)      ← 執行
    第 1219 行  def segment_for_lite(...)        ← 定義

Python 的頂層是逐行執行的，所以那一行必然 `NameError`。而它被包在

    except Exception as _le:
        print(f"民眾版端點未載入: {_le}")

裡面——服務照常啟動、`/api/health` 全綠、主控台正常，**唯獨那條路不存在**。

從使用者端看到的症狀是「偵測不到傷口」。要從那裡走到「blueprint 沒註冊」，
中間隔著三層：App 顯示空結果 → 分不出是空結果還是 404 → 讀後端才發現註冊失敗。
這個 bug 存在了兩輪都沒被發現，**因為沒有任何東西會報錯**。

## 這一族病的共同形狀

  · 語法檢查過、單元測試過、健康檢查全綠
  · 契約測試也過——它注入 mock，不走 `app.py` 的頂層執行順序
  · 唯一的訊號是一行 stdout，而在 Cloud Run 上沒有人會為了確認端點在不在去翻日誌

與主控台那次「JS 語法錯誤讓整個 script 失效」是同一族：
**失敗被吞掉，系統看起來完全正常。**

## 守什麼

1. 頂層呼叫用到的名字，必須在該行**之前**就定義好
2. blueprint 註冊失敗要留進 `BLUEPRINT_FAILURES` 並讓 `/api/health` 降級
   （光印 stdout 等於沒說）
"""
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP_PY = os.path.abspath(os.path.join(HERE, "..", "..", "Backend", "Flask", "app.py"))

FAILED = []
TOTAL = [0]


def check(name, ok, detail=""):
    TOTAL[0] += 1
    print(("PASS  " if ok else "FAIL  ") + name + (("   " + str(detail)) if (detail and not ok) else ""))
    if not ok:
        FAILED.append(name)


def main():
    src = open(APP_PY, encoding="utf-8").read()
    tree = ast.parse(src)

    # 頂層定義的名字 → 定義行號。只看 tree.body，因為那才是「執行順序」。
    defined = {}
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined[n.name] = n.lineno
        elif isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    defined.setdefault(t.id, n.lineno)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                defined.setdefault((a.asname or a.name).split(".")[0], n.lineno)
    check("解析得到 app.py 的頂層定義", len(defined) > 20, len(defined))

    # 頂層（含 try/if 等區塊內）的呼叫，其 Name 參數若是本檔頂層定義的函式，
    # 定義行號必須早於呼叫行號。
    #
    # 只查「本檔定義的名字」——外部 import 進來的東西不在這個風險裡。
    bad = []

    def scan(nodes):
        for n in nodes:
            # 函式/類別的**內部**不查：它們的 body 在被呼叫時才執行，
            # 那時整個模組早就載入完了。這個區別正是本測試會不會滿地假紅的關鍵。
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            for sub in ast.walk(n):
                if isinstance(sub, ast.Call):
                    for a in list(sub.args) + [k.value for k in sub.keywords]:
                        if isinstance(a, ast.Name) and a.id in defined:
                            if defined[a.id] > sub.lineno:
                                bad.append((sub.lineno, a.id, defined[a.id]))
                elif isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                    if sub.id in defined and defined[sub.id] > sub.lineno:
                        # 裝飾器與型別註解等也算，但上面的 Call 已涵蓋主要情況
                        pass

    scan(tree.body)
    check("頂層沒有『用在定義之前』的名字", not bad,
          "；".join("第 %d 行用了 %s（定義在第 %d 行）" % b for b in bad[:4]))

    # ── 註冊失敗必須現形 ────────────────────────────────────────
    print("")
    check("有 BLUEPRINT_FAILURES 清單", "BLUEPRINT_FAILURES = []" in src)
    n_append = src.count("BLUEPRINT_FAILURES.append(")
    n_try = src.count("app.register_blueprint(")
    # ⚠ 這裡曾經寫成 `>= n_try - 1`，容許漏一個。突變測試（拿掉 lite 的留痕）
    # 因此沒被抓到——**一個「差不多就好」的斷言剛好放過了要守的那件事**。
    # 註冊有幾處，留痕就要有幾處，沒有可以少的那一個。
    check("每個 register_blueprint 的失敗都有留痕（%d 個註冊 / %d 處留痕）"
          % (n_try, n_append), n_append >= n_try,
          "有註冊沒留痕的話，那條路 404 而健康檢查全綠")
    check("沒有 `except Exception: pass` 吞掉註冊",
          "except Exception:\n    pass" not in src,
          "連日誌都沒有的吞法")
    check("健康檢查把 endpoints_registered 納入 services",
          "'endpoints_registered': bp_ok" in src)
    check("註冊失敗會讓 status 變 degraded",
          "or (not bp_ok)" in src)
    check("degraded_reason 講得出是哪一個端點沒掛上",
          "端點**未註冊**" in src)
    check("回應帶出 blueprint_failures 明細（不必去翻容器日誌）",
          "'blueprint_failures'" in src)

    # ── segment_wound_ai 的回傳是 (prob, confidence)，每個呼叫端都要解包 ──
    #
    # 2026-08-19：`segment_for_lite` 寫成 `mask = segment_wound_ai(...)`，
    # 於是 mask 是一個 tuple。`if mask is None` 不成立，後面
    # `np.asarray(tuple, bool)` 一路炸到 `_polygons_from_mask()`——那裡沒有 try，
    # 回的是 Flask 預設 HTML 500，連 JSON 錯誤都沒有。
    # classify 的兩處呼叫都正確解包，只有新加的那處沒有。
    print("")
    # ⚠ 判準要看**語意**不是形狀。第一版只允許 `a, b = segment_wound_ai(x)`，
    # 結果把正確的 `out = segment_wound_ai(x)` 之後取 `out[0]` 也判成錯——
    # 假紅與假綠一樣會侵蝕對測試的信任。
    # 合法的有兩種：解包成兩個名字，或指派給一個名字之後有取 `[0]`。
    def _indexes_zero(scope, name):
        for s in ast.walk(scope):
            if isinstance(s, ast.Subscript) and getattr(s.value, "id", "") == name:
                idx = s.slice
                if isinstance(idx, ast.Constant) and idx.value == 0:
                    return True
        return False

    calls = []
    for fn in [x for x in ast.walk(tree)
               if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        for n in ast.walk(fn):
            if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call) \
                    and getattr(n.value.func, "id", "") == "segment_wound_ai":
                tgt = n.targets[0]
                if isinstance(tgt, ast.Tuple):
                    calls.append((n.lineno, True))
                elif isinstance(tgt, ast.Name):
                    calls.append((n.lineno, _indexes_zero(fn, tgt.id)))
                else:
                    calls.append((n.lineno, False))
    bare = [ln for ln, ok in calls if not ok]
    check("segment_wound_ai 的回傳沒有被當成單一遮罩使用（%d 處呼叫）" % len(calls),
          not bare,
          "第 %s 行把 (prob, conf) 指派給單一變數" % bare)
    # 上面那條擋得住 `mask = segment_wound_ai(...)`，但擋不住
    # `out = segment_wound_ai(...)` 之後正確地取 out[0]。所以再補一條語意檢查：
    # 民眾版必須套用 SSOT 門檻，否則機率圖被當成遮罩，整張圖都是傷口而不報錯。
    lite_fn = src[src.find("def segment_for_lite"):]
    lite_fn = lite_fn[:lite_fn.find("\n@app.route")] if "\n@app.route" in lite_fn else lite_fn[:3000]
    # 門檻已抽成 `student_threshold()`（唯一來源）。這裡只確認 Lite 有用它——
    # 逐像素等值與「只有一份拷貝」由 test_threshold_parity.py 負責。
    check("segment_for_lite 有套用 SSOT threshold（機率圖不可直接當遮罩）",
          "student_threshold()" in lite_fn,
          "少了門檻，mask>0 會把整張圖當傷口，而且不會有任何錯誤")

    # ── 匿名端點的最外層必須有 catch-all ──────────────────────────
    lite_src = open(os.path.join(os.path.dirname(APP_PY), "api_lite.py"),
                    encoding="utf-8").read()
    check("lite/segment 最外層有 catch-all（不吐 Flask 預設 HTML 500）",
          "def lite_segment():" in lite_src and "_lite_segment_impl()" in lite_src
          and "logger.exception" in lite_src)
    # 錯誤處理路徑壞掉是最難發現的一種壞：它只在出錯時執行，
    # 而出錯時沒有人在看它有沒有正常運作。第一版就忘了定義 logger。
    check("  而且 logger 真的有定義（否則 handler 自己會 NameError）",
          "logger = logging.getLogger" in lite_src)

    print("\n%d 項檢查，%d 項失敗" % (TOTAL[0], len(FAILED)))
    if FAILED:
        print("失敗：")
        for x in FAILED:
            print("  · " + x)
        print("\n⚠ 這一族錯誤語法檢查看不到、契約測試也看不到"
              "（它注入 mock，不走 app.py 的頂層順序），")
        print("  而症狀是「某條路 404」——從使用者端看是「功能壞了」，不是任何錯誤訊息。")
        return 1
    print("全部通過：頂層接線順序正確，且註冊失敗會在健康檢查裡現形。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
