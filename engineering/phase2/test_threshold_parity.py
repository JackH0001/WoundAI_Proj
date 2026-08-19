# -*- coding: utf-8 -*-
"""契約測試：**同一張機率圖，醫療版與民眾版必須切出逐像素相同的遮罩**。

    python engineering/phase2/test_threshold_parity.py

## 為什麼需要專屬檢查（Mac review 第 1 點）

`segment_wound_ai` 回的是**機率圖**，不是二值遮罩。要用 SSOT 裡該模型的
threshold 去切。2026-08-19 的教訓有兩層：

**第一層**：`segment_for_lite` 漏了門檻。`mask > 0` 會把整張圖當成傷口，
**而且不會有任何錯誤**——回應是 200、輪廓存在、只是荒謬。

**第二層**：即使補上了，那個運算式就有兩份拷貝（classify 一份、Lite 一份）。
只要有人改其中一邊，同一張照片在兩個 App 會得到不同遮罩，而兩邊各自都
「有套用 SSOT 門檻」，靜態檢查看不出來。使用者只會發現兩個 App
對同一張傷口說了不同的話——**而那是最難歸因的一種錯**。

## 這支測試怎麼守

1. **逐像素等值**：同一張合成機率圖，`student_threshold()` 切出來的遮罩
   與 Lite 實際走的路徑切出來的，必須完全相同（不是「差不多」）
2. **邊界值**：恰好等於門檻的像素兩邊要一致（`>` 還是 `>=` 的差別
   在真實資料上只影響幾個像素，但兩邊不同就是兩個答案）
3. **只有一份拷貝**：原始碼裡不得再出現第二個 `.get("threshold", ...)` 運算式

測「兩份拷貝有沒有同步」是治標。**讓拷貝不存在才是治本**，所以第 3 條才是主力。
"""
import os
import sys
import tempfile

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
    try:
        import numpy as np
    except ImportError:
        print("需要 numpy")
        return 1

    os.environ.setdefault("WOUNDAI_FLYWHEEL_DIR", tempfile.mkdtemp(prefix="woundai_thr_"))
    os.makedirs(os.environ["WOUNDAI_FLYWHEEL_DIR"], exist_ok=True)
    sys.path.insert(0, FLASK_DIR)
    # ⚠ 必須切到 Backend/Flask 再 import。app.py 在 module 層就會開 SQLite，
    # 而那個路徑是相對於 CWD 的——從別的目錄跑會得到
    # `disk I/O error`，訊息完全看不出真正的原因是工作目錄。
    # 部署時 CWD 就是那裡，所以這也比較接近實際情形。
    _cwd = os.getcwd()
    os.chdir(FLASK_DIR)
    try:
        import app as A
    except Exception as e:
        os.chdir(_cwd)
        print("FAIL  無法 import app.py：%s: %s" % (type(e).__name__, e))
        return 1
    finally:
        os.chdir(_cwd)
    check("import app.py 成功", True)
    check("有 student_threshold()（門檻的唯一來源）", hasattr(A, "student_threshold"))
    if not hasattr(A, "student_threshold"):
        return 1

    thr = A.student_threshold()
    check("門檻是合理的機率值", isinstance(thr, float) and 0.0 < thr < 1.0, thr)

    # ── 逐像素等值 ────────────────────────────────────────────────
    print("\n── 逐像素等值 ──")
    rng = np.random.default_rng(20260819)
    prob = rng.random((64, 48)).astype(np.float32)
    # 刻意塞入**恰好等於門檻**的像素。`>` 與 `>=` 的差別在真實資料上只影響
    # 幾個像素，但兩邊用得不一樣就是兩個答案——而那幾個像素永遠不會有人注意到。
    # ⚠ nextafter 必須在 **float32** 上算。第一版用 Python float（float64）算，
    # 存進 float32 陣列時又被捨回原值——於是「略高於門檻」的那一列其實等於門檻，
    # 測試報紅而程式碼是對的。
    # 機率圖是 float32，任何邊界值的建構都要在同一個精度下做，否則測的是捨入誤差。
    f32 = np.float32
    prob[0, :] = f32(thr)
    prob[1, :] = np.nextafter(f32(thr), f32(1.0))
    prob[2, :] = np.nextafter(f32(thr), f32(0.0))

    expected = prob > thr

    # Lite 實際走的路徑：把 segment_wound_ai 換成回傳這張機率圖，
    # 然後呼叫真正的 segment_for_lite（不是重寫一份等價邏輯——
    # 重寫的話測到的是我對它的理解，不是它本身）。
    orig = A.segment_wound_ai
    orig_esc = A.escalate_mask
    try:
        A.segment_wound_ai = lambda img: (prob, 0.9)
        # 關掉升級：這支測的是門檻，不是升級策略。
        A.escalate_mask = lambda img, m, w, h, policy="always": (m, {})
        got, info = A.segment_for_lite(np.zeros((64, 48, 3), np.uint8))
    finally:
        A.segment_wound_ai = orig
        A.escalate_mask = orig_esc

    check("Lite 的遮罩與門檻切出來的**逐像素相同**",
          got.shape == expected.shape and bool(np.array_equal(got, expected)),
          "不同像素 %d 個" % int(np.sum(np.asarray(got) != expected))
          if hasattr(got, "shape") else type(got))
    check("恰好等於門檻的像素被排除（與 classify 的 `>` 一致）",
          not np.asarray(got)[0, :].any(), "第 0 列應全 False")
    check("略高於門檻的像素被納入",
          bool(np.asarray(got)[1, :].all()), "第 1 列應全 True")
    check("略低於門檻的像素被排除",
          not np.asarray(got)[2, :].any(), "第 2 列應全 False")

    # 回傳型別：`_polygons_from_mask` 會做 np.asarray(mask, uint8)，
    # 拿到 tuple 就是 2026-08-19 那個 HTML 500。
    check("segment_for_lite 回的第一個值是陣列，不是 tuple",
          isinstance(got, np.ndarray), type(got))

    # ── 只有一份拷貝 ──────────────────────────────────────────────
    print("\n── 門檻只有一個來源 ──")
    src = open(os.path.join(FLASK_DIR, "app.py"), encoding="utf-8").read()
    import re
    # 去註解**與 docstring** 再數。
    #
    # ⚠ 第一版只去了 `#` 註解，結果數到 2 處——第二處是 `student_threshold()`
    # 自己的 docstring，裡面把舊的運算式當**反例**引用。
    # 我寫了一個反例，然後自己的檢查把那個反例當成違規。
    # 這與先前 `phantomHint` 撞名假綠、註解裡的 `\n` 弄壞 JS 是同一族：
    # **檢查器分不出「程式碼」與「談論程式碼的文字」。**
    code = re.sub(r'"""(?:.|\n)*?"""', "", src)
    code = re.sub(r"'''(?:.|\n)*?'''", "", code)
    code = re.sub(r"#[^\n]*", "", code)
    n = len(re.findall(r'\.get\(\s*["\']threshold["\']', code))
    check("原始碼裡只有一處計算 threshold（實際 %d 處）" % n, n == 1,
          "有兩份拷貝就會分岔，而兩邊看起來都『有套用 SSOT 門檻』")
    check("classify 用的是 student_threshold()",
          "thr = student_threshold()" in code)
    check("segment_for_lite 用的是 student_threshold()",
          "> student_threshold()" in code)

    print("\n%d 項檢查，%d 項失敗" % (TOTAL[0], len(FAILED)))
    if FAILED:
        print("失敗：")
        for x in FAILED:
            print("  · " + x)
        print("\n⚠ 兩條路徑用不同門檻時，兩邊各自都會**看起來正常**——")
        print("  症狀是同一張照片在兩個 App 得到不同面積，而沒有任何一端報錯。")
        return 1
    print("全部通過：門檻只有一個來源，兩條路徑逐像素一致。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
