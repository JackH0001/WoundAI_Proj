# -*- coding: utf-8 -*-
"""提交前守門：**這台機器不該改的東西，不要讓它進 commit**。

    python tools/owner_guard.py              # 提交前跑，非 0 就別提交
    python tools/owner_guard.py --sync       # 另外核對 iOS 是否與遠端一致
    python tools/owner_guard.py --platform mac

## 為什麼需要一支程式，而不是一條約定

2026-08-09 定下分工：**Mac＝iOS 唯一寫者，Windows＝Android／後端／訓練**。
理由很硬：Swift 只有 Mac 編得起來，Windows 寫 iOS 永遠是「沒編譯過的猜測」。

而這條約定在**同一天之內就被違反了兩次**：

  1. 合併前：Windows 工作區有 8 個未提交的 iOS 檔，擋住 `git merge --ff-only`。
  2. 合併後：同樣那幾個檔又被改回合併前的舊版本——
     `WoundEditView.swift` 從 1006 行退回 352 行，`MeasureFlowView.swift` 916 → 491，
     而 `RasterOps.swift`（與分支的 `MaskTrace.swift` 重複宣告 `rdp`/`on`）也回來了。

     那次如果直接 `git add -A`，會提交一個約 1200 行的 iOS 倒退，
     **而且沒有任何錯誤訊息**——commit 會成功，push 會成功，
     直到有人在 Mac 上按下 build 才發現重複宣告編不過，
     那時已經很難分辨哪一版是對的。

兩次都不是誰不小心。**跨機器工作時，「記得不要改」不是一個可靠的機制**——
編輯器會自動存檔、還原備份會覆蓋、平行的工作階段互相不知道對方動了什麼。
唯一擋得住的是提交前跑一支會回傳非 0 的程式。

## 這支程式檢查兩件事

  · **所有權**：這台機器改的檔案，有沒有落在它不該碰的目錄
  · **同步**（`--sync`）：不屬於這台機器的目錄，內容是否與遠端分支逐位元相同

第二項才抓得到上面那次倒退——所有權檢查只看「有沒有改」，
而那次的檔案是「改回舊版」，改動本身完全合法，錯的是方向。
"""
import argparse
import os
import subprocess
import sys

# 目錄 → 唯一寫者。比對時取**最長前綴**，所以 tools/ 可以覆寫 Android/ 之類的規則。
OWNERS = [
    # workflow 也要分寫者：ios.yml 只有 Mac 驗得了、android.yml 只有 Windows 驗得了。
    # 一台機器改另一台的 CI，錯誤要等對方推下一筆才會浮現。
    (".github/workflows/ios.yml", "mac"),
    (".github/workflows/android.yml", "windows"),
    ("iOS/", "mac"),
    ("Android/", "windows"),
    ("Backend/", "windows"),
    ("engineering/", "windows"),
    ("Windows/", "windows"),
    ("tools/", "both"),
    ("docs/", "both"),
]
DEFAULT_OWNER = "both"          # 根目錄的 .gitignore、COMMIT_MSG.txt 等

# `--sync` 要核對的子樹 → 該子樹的權威來源 ref
SYNC_REFS = {"iOS/": "origin/ios-verify"}


def sh(args):
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or ""), (r.stderr or "")


def sh_z(args):
    """走 `-z`（NUL 分隔、原始位元組）的版本。

    ⚠ git 預設會把**非 ASCII 檔名**用雙引號包起來並轉成八進位跳脫
    （`"iOS/\\345\\256\\211..."`）。本工具第一版沒處理，於是
    `iOS/安裝測試指南.md` 與 `iOS/首次編譯預檢_2026-08-08.md` 兩個**明明有追蹤**的
    檔案被跳過，再被目錄掃描當成「多出來」報出來——第一次實戰就誤報兩筆。

    一支會誤報的守門程式，用不了幾次就會被當成雜訊略過，那比沒有更糟。
    `-z` 不做跳脫也不加引號，直接消滅整類問題。
    """
    r = subprocess.run(args, capture_output=True)
    if r.returncode != 0:
        return r.returncode, [], (r.stderr or b"").decode("utf-8", "replace")
    parts = r.stdout.split(b"\0")
    return 0, [p.decode("utf-8", "replace") for p in parts if p], ""


def detect_platform():
    if sys.platform == "darwin":
        return "mac"
    if sys.platform.startswith("win"):
        return "windows"
    return "linux"


def owner_of(path):
    best, owner = "", DEFAULT_OWNER
    for prefix, who in OWNERS:
        if path.startswith(prefix) and len(prefix) > len(best):
            best, owner = prefix, who
    return owner


def changed_paths():
    """已暫存＋未暫存＋未追蹤，全部算。未追蹤的最危險——它們不會出現在 diff 裡。

    `-z` 格式：每筆是 `XY <path>\\0`；改名或複製會多接一個 `<old path>\\0`。
    """
    rc, items, err = sh_z(["git", "status", "--porcelain", "-uall", "-z"])
    if rc != 0:
        print("git status 失敗：" + err.strip())
        return None
    paths, i = [], 0
    while i < len(items):
        it = items[i]
        i += 1
        if len(it) < 4:
            continue
        code, p = it[:2], it[3:]
        paths.append(p.replace("\\", "/"))
        if code[0] in ("R", "C"):      # 改名／複製的來源路徑另成一筆
            if i < len(items):
                paths.append(items[i].replace("\\", "/"))
                i += 1
    return sorted(set(paths))


def ref_blob(ref, path):
    """該路徑在權威 ref 裡的 blob hash；不存在回 None。"""
    rc, out, _ = sh_z(["git", "ls-tree", "-z", ref, "--", path])
    if rc != 0 or not out:
        return None
    try:
        return out[0].split("\t", 1)[0].split()[2]
    except (IndexError, ValueError):
        return None


def matches_authority(path):
    """這個檔案目前的內容，是否已經等於權威 ref 的版本（含「兩邊都不存在」）。

    ⚠ 沒有這一段的話，**把別人的子樹還原成他的權威版本**會被當成違規擋下——
    而那正是修復倒退時唯一該做的事。2026-08-09 第一次實戰就是這樣：
    `git checkout cb7f3a9 -- iOS` 之後守門仍報 5 個違規，人只好無視它提交。
    **被無視過一次的守門，之後就不再是守門了。**
    """
    ref = None
    for prefix, r in SYNC_REFS.items():
        if path.startswith(prefix):
            ref = r
            break
    if ref is None:
        return False
    want = ref_blob(ref, path)
    if not os.path.isfile(path):
        return want is None          # 刪掉了，而權威版本本來也沒有 → 一致
    rc, cur, _ = sh(["git", "hash-object", path])
    return rc == 0 and want is not None and cur.strip() == want


def check_ownership(platform):
    paths = changed_paths()
    if paths is None:
        return 1
    viol = [p for p in paths if owner_of(p) not in (platform, "both")]
    restored = [p for p in viol if matches_authority(p)]
    bad = [p for p in viol if p not in restored]

    print("── 所有權 ──")
    print("  這台機器：%s ／ 有異動的檔案：%d" % (platform, len(paths)))
    if restored:
        print("  ○ %d 個檔案雖屬別台機器，但內容已等於權威版本（還原，放行）：" % len(restored))
        for p in restored[:10]:
            print("      " + p)
        if len(restored) > 10:
            print("      …另外 %d 個" % (len(restored) - 10))
    if not bad:
        print("  ✓ 沒有把別台機器的東西改成非權威內容")
        return 0
    print("  ✗ 以下 %d 個檔案的寫者是別台機器，且內容與權威版本不同：" % len(bad))
    for p in bad[:40]:
        print("      %-70s （屬於 %s）" % (p, owner_of(p)))
    if len(bad) > 40:
        print("      …另外 %d 個" % (len(bad) - 40))
    print("\n  這些改動不該從這台機器提交。處置：")
    for prefix, ref in SYNC_REFS.items():
        print("      git checkout %s -- %s" % (ref, prefix.rstrip("/")))
    print("      未追蹤的新增檔請自行移出工作區（先備份）")
    return 1


def check_sync():
    """核對別人負責的子樹是否與遠端逐位元相同。

    用 blob hash 而不是 `git diff`：diff 會受換行設定（core.autocrlf）影響，
    在 Windows 上把整棵樹都報成有差異，等於沒有訊號。
    """
    print("\n── 與遠端同步 ──")
    worst = 0
    for prefix, ref in SYNC_REFS.items():
        rc, entries, _ = sh_z(["git", "ls-tree", "-r", "-z", ref, "--", prefix.rstrip("/")])
        if rc != 0:
            print("  ? 取不到 %s（沒 fetch 過？）—— 略過 %s" % (ref, prefix))
            worst = max(worst, 2)
            continue
        out = "\n".join(entries)     # 供下方 known 集合沿用同一份資料
        diff, miss, total = [], [], 0
        for line in entries:
            try:
                meta, path = line.split("\t", 1)
                sha = meta.split()[2]
            except (ValueError, IndexError):
                continue
            total += 1
            if not os.path.isfile(path):
                miss.append(path)
                continue
            rc2, cur, _ = sh(["git", "hash-object", path])
            if rc2 == 0 and cur.strip() != sha:
                diff.append(path)
        # 多出來的檔案也要報。`git checkout <ref> -- <dir>` **不會刪除** ref 裡沒有的檔案，
        # 所以「還原」之後多出來的那些會靜靜留下。2026-08-09 的 `RasterOps.swift` 就是這樣
        # 混進 main 的——它不會編譯失敗（`enum RasterOps` 與 `enum MaskTrace` 是兩個
        # 不同的命名空間，不衝突），只是一份沒人呼叫、卻看起來像正牌的重複實作。
        known = set()
        for line in out.splitlines():
            try:
                known.add(line.split("\t", 1)[1].strip().strip('"'))
            except IndexError:
                pass
        extra = []
        for root, _, fs in os.walk(prefix.rstrip("/")):
            for f in fs:
                p = os.path.join(root, f).replace("\\", "/")
                if p not in known and not p.endswith((".DS_Store", ".pyc")):
                    extra.append(p)

        if not diff and not miss and not extra:
            print("  ✓ %s 與 %s 完全一致（%d 檔）" % (prefix, ref, total))
            continue
        worst = 1
        print("  ✗ %s 與 %s 不一致（%d 檔中 %d 個內容不同、%d 個不存在、另有 %d 個多出來）"
              % (prefix, ref, total, len(diff), len(miss), len(extra)))
        for p in (diff + miss)[:20]:
            print("      改動／缺席  " + p)
        for p in extra[:20]:
            print("      多出來      " + p)
        print("    還原：git checkout %s -- %s" % (ref, prefix.rstrip("/")))
        if extra:
            print("    ⚠ 多出來的檔案 checkout 不會移除，要自己刪")
    return worst


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", choices=["mac", "windows", "linux"], default=None)
    ap.add_argument("--sync", action="store_true", help="另外核對別人負責的子樹是否與遠端一致")
    a = ap.parse_args()
    platform = a.platform or detect_platform()

    rc = check_ownership(platform)
    if a.sync:
        rc = max(rc, check_sync())
    print("")
    if rc == 0:
        print("通過：可以提交。")
    else:
        print("⚠ 請先處理上面的項目再提交。跨機器的倒退不會有錯誤訊息，")
        print("  它只會安靜地覆蓋掉另一台機器編譯驗證過的成果。")
    return rc


if __name__ == "__main__":
    sys.exit(main())
