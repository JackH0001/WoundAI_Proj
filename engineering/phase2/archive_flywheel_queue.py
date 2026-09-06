# -*- coding: utf-8 -*-
"""飛輪佇列歸檔重置 —— 臨床收案開始前把驗證期資料清出主線。

## 為什麼要做這一步

`by_source.clinical` 是 n=20 臨床收案進度的分母。驗證期產生的樣本（範例圖、模擬圖、
以及**被誤標成 clinical 的範例圖**）留在佇列裡，會讓收案進度虛胖，而那個數字最後要寫進
IRB 報告與模型資料卡。開始收真實個案前把它歸零，之後每一筆 clinical 都可追溯到真實收案。

## 為什麼是「歸檔」不是「刪除」

`retrain_queue.jsonl` 是 append-only 的作業紀錄；刪掉等於毀證。這支腳本把它
**整批搬進 `archive/<標籤>/`** 並留下說明，主線重新開始；需要時可以原樣還原
（見輸出的 RESTORE 指示）。

`audit.jsonl` **刻意不搬**：稽核鏈要連續，斷在中間比雜訊更糟。歸檔前必須先以
`api_flywheel.audit_intent()` 記下授權與意圖，完成後由同一個版本化雜湊鏈記下結果；
本工具不得直接附加 JSON 行，否則會破壞 v4 鏈。

## 影像怎麼處理

影像檔名是內容 sha1，佇列被歸檔後就沒有任何紀錄引用它們。預設**一併搬進歸檔**
（`--keep-images` 可保留）。搬走的好處：之後 `image_reused` 的偵測不會把驗證期用過的圖
誤判成「先前已上傳」，而那個訊號正是用來抓「範例圖被當成臨床樣本」的。

## 用法

    python engineering/phase2/archive_flywheel_queue.py --dry-run     # 先看會動到什麼
    python engineering/phase2/archive_flywheel_queue.py --label pre_clinical_n20

⚠ **執行前請先停掉 `app.py`**：Windows 上檔案被開著就搬不動。
"""
import argparse
import json
import os
import re
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND_FLASK = os.path.abspath(os.path.join(HERE, "..", "..", "Backend", "Flask"))
DEFAULT_FW = os.path.join(BACKEND_FLASK, "flywheel")

MOVE_FILES = ["retrain_queue.jsonl", "withdrawn.jsonl"]
MOVE_DIRS = ["images", "quarantine"]
LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}\Z")


def count_lines(p):
    if not os.path.exists(p):
        return 0
    with open(p, encoding="utf-8") as f:
        return sum(1 for line in f if line.strip())


def summarize_queue(p):
    """歸檔前先把要搬走的東西講清楚——按來源分類，因為 clinical 那格才是重點。"""
    by_source, codes = {}, set()
    if not os.path.exists(p):
        return by_source, codes
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                by_source["(格式損壞)"] = by_source.get("(格式損壞)", 0) + 1
                continue
            s = str(r.get("source") or "clinical")
            by_source[s] = by_source.get(s, 0) + 1
            if r.get("code"):
                codes.add(r["code"])
    return by_source, codes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--flywheel-dir", default=os.environ.get("WOUNDAI_FLYWHEEL_DIR") or DEFAULT_FW)
    ap.add_argument("--label", default=None, help="歸檔資料夾名稱；預設 reset_<YYYYMMDD_HHMM>")
    ap.add_argument("--keep-images", action="store_true",
                    help="影像留在原地（預設一併歸檔，避免 image_reused 誤判驗證期用過的圖）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--operator", default="",
                    help="執行歸檔的維運身分；非 dry-run 必填")
    ap.add_argument("--authorization-ref", default="",
                    help="單行授權參照；非 dry-run 必填並寫入 immutable audit intent")
    a = ap.parse_args()

    root = os.path.abspath(a.flywheel_dir)
    if not os.path.isdir(root):
        print("找不到 flywheel 目錄：%s" % root)
        return 2

    staging = os.path.join(root, "staging")
    pending = sorted(os.path.relpath(os.path.join(base, name), root)
                     for base, _, names in os.walk(staging) for name in names)
    if pending:
        print(json.dumps({"error": "staging_not_empty", "paths": pending}, ensure_ascii=False))
        print("Archive stopped. Review staging with the separate list-only sweep/repair tool.")
        return 2
    if os.environ.get("WOUNDAI_STORE", "local").lower() != "local":
        print("This archive tool is local-only; refusing to inspect a local path for a GCS dataset.")
        return 2

    label = a.label or ("reset_" + time.strftime("%Y%m%d_%H%M"))
    if not LABEL.fullmatch(label) or label in (".", ".."):
        print("--label 只能是 1–81 字元的 ASCII slug（英數開頭，可含 . _ -）。")
        return 2
    archive_root = os.path.realpath(os.path.join(root, "archive"))
    dst = os.path.realpath(os.path.join(archive_root, label))
    try:
        inside_archive = (os.path.normcase(os.path.commonpath((archive_root, dst)))
                          == os.path.normcase(archive_root))
    except ValueError:  # different drive on Windows
        inside_archive = False
    if not inside_archive:
        print("--label 解析後不在 archive/ 內，已拒絕。")
        return 2

    queue_p = os.path.join(root, "retrain_queue.jsonl")
    by_source, codes = summarize_queue(queue_p)
    n_img = len(os.listdir(os.path.join(root, "images"))) if os.path.isdir(os.path.join(root, "images")) else 0
    n_q = len(os.listdir(os.path.join(root, "quarantine"))) if os.path.isdir(os.path.join(root, "quarantine")) else 0

    print("flywheel : %s" % root)
    print("歸檔到   : %s" % dst)
    print("佇列     : %d 筆，%d 個代碼" % (count_lines(queue_p), len(codes)))
    for k in sorted(by_source):
        mark = "  ← 收案進度的分母" if k == "clinical" else ""
        print("    %-10s %d%s" % (k, by_source[k], mark))
    print("撤回墓碑 : %d 筆" % count_lines(os.path.join(root, "withdrawn.jsonl")))
    print("影像     : %d 張%s" % (n_img, "（保留原地）" if a.keep_images else "（一併歸檔）"))
    print("隔離區   : %d 張" % n_q)
    print("稽核軌跡 : %d 筆（**不搬動**，以版本化鏈寫 intent / outcome）" % count_lines(os.path.join(root, "audit.jsonl")))

    if a.dry_run:
        print("\n--dry-run：未做任何變更。")
        return 0

    if (not a.operator.strip() or not a.authorization_ref.strip()
            or any(c in a.authorization_ref for c in "\r\n<>")):
        print("非 dry-run 必須提供 --operator 與單行 --authorization-ref。")
        return 2

    if os.path.exists(dst):
        print("\n歸檔目錄已存在，換一個 --label 以免覆蓋既有歸檔：%s" % dst)
        return 2

    # Import only after the root is fixed. api_flywheel resolves its store at
    # runtime from this environment variable; importing it first would audit a
    # different data set when the caller supplied --flywheel-dir.
    os.environ["WOUNDAI_FLYWHEEL_DIR"] = root
    if BACKEND_FLASK not in sys.path:
        sys.path.insert(0, BACKEND_FLASK)
    import api_flywheel as fwmod
    detail = {
        "label": label,
        "queue_records": sum(by_source.values()),
        "images": n_img,
        "keep_images": bool(a.keep_images),
        "authorization_ref": a.authorization_ref,
    }
    try:
        fwmod.audit_intent(a.operator, "queue_archive", "-",
                           role="maintenance", org="maintenance", detail=detail)
    except Exception as exc:
        print("Archive stopped before filesystem changes: audit intent unavailable: %s" % exc)
        return 3

    os.makedirs(dst, exist_ok=True)

    moved = []
    for name in MOVE_FILES:
        src = os.path.join(root, name)
        if os.path.exists(src):
            shutil.move(src, os.path.join(dst, name))
            open(src, "w", encoding="utf-8").close()   # 重建空檔，端點才不會因缺檔而報錯
            moved.append(name)

    dirs = MOVE_DIRS if not a.keep_images else ["quarantine"]
    for d in dirs:
        src = os.path.join(root, d)
        if os.path.isdir(src) and os.listdir(src):
            shutil.move(src, os.path.join(dst, d))
            moved.append(d + "/")
        os.makedirs(src, exist_ok=True)

    note = os.path.join(dst, "NOTE.md")
    with open(note, "w", encoding="utf-8") as f:
        f.write("# 飛輪佇列歸檔：%s\n\n" % label)
        f.write("歸檔時間：%s\n\n" % time.strftime("%Y-%m-%dT%H:%M:%S"))
        f.write("## 為什麼歸檔\n\n")
        f.write("臨床收案（n=20）開始前把驗證期資料清出主線，讓 `by_source.clinical` "
                "從 0 起算真實收案進度。這個數字會寫進 IRB 報告與模型資料卡，"
                "混入驗證樣本會讓它失去意義。\n\n")
        f.write("## 內容\n\n")
        f.write("| 項目 | 數量 |\n|---|---|\n")
        f.write("| 佇列筆數 | %d |\n" % count_lines(os.path.join(dst, "retrain_queue.jsonl")))
        for k in sorted(by_source):
            f.write("| ─ source=%s | %d |\n" % (k, by_source[k]))
        f.write("| 撤回墓碑 | %d |\n" % count_lines(os.path.join(dst, "withdrawn.jsonl")))
        f.write("| 影像 | %d |\n" % (n_img if not a.keep_images else 0))
        f.write("\n## 還原方式\n\n```powershell\n")
        f.write("# 先停掉 app.py，再把檔案搬回去（會覆蓋目前的主線資料，請先自行備份）\n")
        f.write("Move-Item \"%s\\*\" \"%s\" -Force\n```\n" % (dst, root))
        f.write("\n## 注意\n\n")
        f.write("- `audit.jsonl` **未搬動**：稽核軌跡必須連續，斷在中間比雜訊更糟。\n")
        f.write("- 影像一併歸檔的用意：`classify` 的 `image_reused` 訊號用來偵測"
                "「範例圖被當成臨床樣本」，驗證期用過的圖留著會造成誤判。\n")

    try:
        fwmod.audit(a.operator, "queue_archived", "-",
                    "歸檔至 archive/%s：佇列 %d 筆(%s)、影像 %d 張。臨床收案前重置。" % (
                        label, sum(by_source.values()),
                        "、".join("%s=%d" % (k, v) for k, v in sorted(by_source.items())), n_img),
                    "maintenance", "maintenance")
    except Exception as exc:
        # The state transition has completed. Do not report a fully-audited
        # success or try to synthesize a raw audit line; surface the unmatched
        # intent for operator reconciliation instead.
        print("Archive completed but outcome audit is unavailable: %s" % exc)
        print("Do not treat this run as fully audited; reconcile the prior queue_archive_intent.")
        return 3

    print("\n已歸檔：%s" % "、".join(moved))
    print("說明檔：%s" % note)
    print("主線已重置。下一步：重啟 app.py，設定頁的佇列健康度應顯示臨床 0。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
