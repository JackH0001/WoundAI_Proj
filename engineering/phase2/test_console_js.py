# -*- coding: utf-8 -*-
"""語法檢查：**主控台內嵌的 JavaScript 必須解析得過**。

    python engineering/phase2/test_console_js.py

## 為什麼需要這一支

2026-08-19：主控台部署後**完全無法登入**。原因不是登入邏輯，是這一行：

    const list = opts.map(o => `  ${o.v} = ${o.t}`).join("\\n");

`api_console.py` 的 `_PAGE` 是**非 raw** 的三引號字串。原始碼裡寫單反斜線加 n，
Python 會把它解成**真的換行**，而 JS 的雙引號字串不能跨行——於是

    .join("
    ");

**一個語法錯誤會讓整個 `<script>` 區塊解析失敗**，所有函式都不存在，
包括登入。畫面看起來完全正常（HTML 照樣渲染），按鈕按下去毫無反應。

更難堪的是：修這個 bug 時寫的**註解本身**也踩了同一個坑——註解裡提到那個序列，
一樣被切成兩行，第二行以反引號開頭又開了一個 template literal。

## 這一類問題的形狀

  · Python 語法檢查**過**（`_PAGE` 只是一個字串）
  · 端點測試**過**（`/console` 回 200，HTML 完整）
  · 只有瀏覽器真的去解析那段 script 才會炸，而且炸得很安靜

唯一擋得住的地方是把 JS 抽出來丟給真正的 JS 解析器。

## 相依

需要 `node`。沒有的話這支測試會**明說跳過**而不是假裝通過——
一個沉默跳過的檢查等於沒有檢查。
"""
import ast
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FLASK_DIR = os.path.abspath(os.path.join(HERE, "..", "..", "Backend", "Flask"))


def main():
    p = os.path.join(FLASK_DIR, "api_console.py")
    src = open(p, encoding="utf-8").read()
    pages = [ast.literal_eval(n.value) for n in ast.walk(ast.parse(src))
             if isinstance(n, ast.Assign)
             and getattr(n.targets[0], "id", "") == "_PAGE"]
    if not pages:
        print("FAIL  找不到 _PAGE")
        return 1
    page = pages[0].replace("<!--ROLE_OPTIONS-->", '<option value="x">x</option>')
    print("PASS  取得 _PAGE（%d 字元）" % len(page))

    blocks = re.findall(r"<script>(.*?)</script>", page, re.S)
    if not blocks:
        print("FAIL  _PAGE 裡找不到 <script> 區塊")
        return 1
    print("PASS  找到 %d 個 <script> 區塊" % len(blocks))

    node = shutil.which("node")
    if not node:
        # 明說跳過。這支測試的價值全在真的解析過一次，
        # 沒有 node 就沒有價值——不要用一個綠燈掩蓋它。
        print("\n⚠ 找不到 node，**無法驗證 JS 語法**。")
        print("  這支測試在沒有 node 的環境下不具意義；CI 上請確保 node 可用。")
        print("  （不視為失敗，但也不代表通過。）")
        return 0

    failed = 0
    for i, js in enumerate(blocks, 1):
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                         encoding="utf-8") as f:
            f.write(js)
            tmp = f.name
        r = subprocess.run([node, "--check", tmp], capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        os.unlink(tmp)
        if r.returncode == 0:
            print("PASS  第 %d 個 script 區塊語法正確（%d 字元）" % (i, len(js)))
        else:
            failed += 1
            print("FAIL  第 %d 個 script 區塊語法錯誤：" % i)
            for ln in (r.stderr or "").strip().split("\n")[:6]:
                print("        " + ln)

    # 直接對付成因：`_PAGE` 是非 raw 字串，所以原始碼裡的單反斜線 n
    # 會被 Python 解成真換行。JS 裡要換行必須寫雙反斜線。
    # 這裡掃的是**解譯後**的 page：雙引號字串內出現真換行就是錯的。
    bad = []
    for ln_no, line in enumerate(page.split("\n"), 1):
        stripped = re.sub(r"\\.", "", line)
        if stripped.count('"') % 2 == 1 and re.search(r'\.\w+\("$|= "$|\("$', line.rstrip()):
            bad.append((ln_no, line.strip()[:60]))
    if bad:
        failed += 1
        print("FAIL  有雙引號字串疑似跨行（Python 把單反斜線 n 解成真換行）：")
        for n, l in bad[:5]:
            print("        行 %d: %s" % (n, l))
    else:
        print("PASS  沒有跨行的雙引號字串")

    print("")
    if failed:
        print("⚠ 這一類錯誤 Python 語法檢查看不到、端點測試也看不到——")
        print("  `/console` 照樣回 200 且 HTML 完整，只有瀏覽器解析那段 script 時才炸，")
        print("  而症狀是「按鈕沒反應」，不是任何一種錯誤訊息。")
        return 1
    print("全部通過：主控台的 JS 解析得過。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
