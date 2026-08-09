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
    """已暫存＋未暫存＋未追蹤，全部算。未追蹤的最危險——它們不會出現在 diff 裡。"""
    rc, out, err = sh(["git", "status", "--porcelain", "-uall"])
    if rc != 0:
        print("git status 失敗：" + err.strip())
        return None
    paths = []
    for line in out.splitlines():
        if len(line) < 4:
            continue
        p = line[3:].strip().strip('"')
        # 改名是 "old -> new"，兩邊都要檢查
        for part in p.split(" -> "):
            part = part.strip().strip('"')
            if part:
                paths.append(part.replace("\\", "/"))
    return sorted(set(paths))


def check_ownership(platform):
    paths = changed_paths()
    if paths is None:
        return 1
    bad = [(p, owner_of(p)) for p in paths
           if owner_of(p) not in (platform, "both")]
    print("── 所有權 ──")
    print("  這台機器：%s ／ 有異動的檔案：%d" % (platform, len(paths)))
    if not bad:
        print("  ✓ 沒有動到別台機器負責的目錄")
        return 0
    print("  ✗ 以下 %d 個檔案的寫者是別台機器：" % len(bad))
    for p, who in bad[:40]:
        print("      %-70s （屬於 %s）" % (p, who))
    if len(bad) > 40:
        print("      …另外 %d 個" % (len(bad) - 40))
    print("\n  這些改動不該從這台機器提交。處置：")
    print("      git checkout -- <目錄>     # 已追蹤檔案還原成 HEAD")
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
        rc, out, _ = sh(["git", "ls-tree", "-r", ref, "--", prefix.rstrip("/")])
        if rc != 0:
            print("  ? 取不到 %s（沒 fetch 過？）—— 略過 %s" % (ref, prefix))
            worst = max(worst, 2)
            continue
        diff, miss, total = [], [], 0
        for line in out.splitlines():
            try:
                meta, path = line.split("\t", 1)
                sha = meta.split()[2]
            except (ValueError, IndexError):
                continue
            path = path.strip().strip('"')
            # git 對非 ASCII 檔名會輸出八進位跳脫；那種檔名這裡不比對，
            # 硬解跳脫容易解錯，而誤報比漏報更會讓人不再相信這支工具。
            if "\\" in path:
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
