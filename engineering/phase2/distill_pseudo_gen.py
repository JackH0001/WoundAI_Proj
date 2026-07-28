# -*- coding: utf-8 -*-
"""偽標籤產生器:用 A∪U 集成當老師,對**無 GT** 的傷口照產生軟遮罩,並做品質把關。

為什麼要把關:偽標籤蒸餾最大的風險是「把老師的錯誤當真理教給學生」。
老師 A∪U 在未見過臨床照 n=5 上 Dice 0.924,但那是少數易例;
對低對比/破碎/分布外影像它一樣會崩。**沒有 GT 可以檢查,只能靠不確定性訊號自我篩選。**

把關訊號(全部免 GT):
  1. TTA 一致性     — 原圖 vs 水平翻轉的預測 IoU。老師不穩的樣本這裡會塌(最強訊號)
  2. 遮罩內平均機率 — 老師自己有多確定
  3. 邊界模糊比例   — 機率落在 [0.3,0.7] 的像素佔遮罩比;糊成一團代表沒真的分出來
  4. 面積合理性     — 太小(雜訊)或幾乎整張圖(失控)都丟
  5. 連通元件數     — 碎成一堆小塊通常是誤判紅色皮膚/發炎

用法(本機或沙箱,只需 onnxruntime):
    python distill_pseudo_gen.py --src "C:/dev/WoundAI_weights_archive/**/image" --out D:/pseudo
    python distill_pseudo_gen.py --src DIR1 --src DIR2 --montage       # 產出目視抽查大圖
    python distill_pseudo_gen.py --src DIR --dry-run                   # 只掃檔案不推論

輸出:
    <out>/soft/<stem>.npy        256x256 float16 軟標籤(通過把關者)
    <out>/pseudo_manifest.json   每張的指標與通過/退回原因
    <out>/montage_*.png          目視抽查(--montage)

⚠ 通過率若 <40%,別硬用——那代表老師在這批資料上也不行,該換資料或先擴老師。
"""
import argparse, glob, json, os, sys

import numpy as np

try:
    import cv2
except ImportError:
    cv2 = None

# ===== 把關門檻(純函式,便於單元測試與日後調參) =====
GATE = {
    "tta_iou_min": 0.70,        # 翻轉一致性
    "mean_prob_min": 0.60,      # 遮罩內平均機率
    "fuzzy_ratio_max": 0.50,    # 邊界模糊像素 / 遮罩像素
    "area_frac_min": 0.005,     # 0.5% (256² 約 328 px)
    "area_frac_max": 0.60,
    "max_components": 3,        # 面積 ≥ 最大塊 10% 的連通元件數
    "thr": 0.40,                # 與 registry/SSOT 的 student 門檻一致
}


def _iou(a, b):
    u = np.logical_or(a, b).sum()
    return float(np.logical_and(a, b).sum() / u) if u else 1.0


def gate_metrics(prob, prob_flip=None, thr=None, cfg=None):
    """由老師機率圖算出免-GT 的品質指標。回 dict(含 accept 與 reasons)。

    @param prob      原圖的老師機率(HxW, 0..1)
    @param prob_flip 水平翻轉再翻回來的老師機率;None 則跳過 TTA 檢查
    """
    cfg = {**GATE, **(cfg or {})}
    thr = cfg["thr"] if thr is None else thr
    m = prob > thr
    n = m.sum()
    area_frac = float(n / prob.size)
    mean_prob = float(prob[m].mean()) if n else 0.0
    fuzzy = float(((prob > 0.3) & (prob < 0.7)).sum() / n) if n else 999.0
    tta = _iou(m, prob_flip > thr) if prob_flip is not None else 1.0

    comps = 1
    if n and cv2 is not None:
        num, lab, stats, _ = cv2.connectedComponentsWithStats(m.astype(np.uint8), 8)
        areas = sorted(stats[1:, cv2.CC_STAT_AREA], reverse=True) if num > 1 else []
        comps = sum(1 for a in areas if a >= 0.1 * areas[0]) if areas else 0

    reasons = []
    if area_frac < cfg["area_frac_min"]: reasons.append(f"遮罩過小 {area_frac:.4f}")
    if area_frac > cfg["area_frac_max"]: reasons.append(f"遮罩過大 {area_frac:.2f}(疑似失控)")
    if mean_prob < cfg["mean_prob_min"]: reasons.append(f"老師不確定 平均機率 {mean_prob:.2f}")
    if fuzzy > cfg["fuzzy_ratio_max"]: reasons.append(f"邊界糊 {fuzzy:.2f}")
    if tta < cfg["tta_iou_min"]: reasons.append(f"翻轉不一致 IoU {tta:.2f}")
    if comps > cfg["max_components"]: reasons.append(f"碎成 {comps} 塊")
    return {"area_frac": round(area_frac, 5), "mean_prob": round(mean_prob, 3),
            "fuzzy_ratio": round(fuzzy, 3), "tta_iou": round(tta, 3), "components": comps,
            "accept": not reasons, "reasons": reasons}


# ===== 老師推論 =====
def build_teacher(onnx_dir):
    import onnxruntime as ort
    sa = ort.InferenceSession(os.path.join(onnx_dir, "a_unet.onnx"), providers=["CPUExecutionProvider"])
    su = ort.InferenceSession(os.path.join(onnx_dir, "unetpp.onnx"), providers=["CPUExecutionProvider"])
    return (sa, sa.get_inputs()[0].name), (su, su.get_inputs()[0].name)


def _one(sess_in, img256):
    s, i = sess_in
    r = img256.astype(np.float32) / 127.5 - 1        # a_unet/unetpp 的既定前處理([-1,1] RGB)
    o = np.squeeze(s.run(None, {i: r[None]})[0]).astype(np.float32)
    if o.ndim == 3: o = o[..., 0]
    return 1 / (1 + np.exp(-np.clip(o, -30, 30))) if (o.min() < 0 or o.max() > 1) else o


def teacher_prob(t_a, t_u, img256):
    """A∪U 0.5/0.5 機率融合(EVIDENCE_LEDGER 2026-06 決策)。"""
    return 0.5 * _one(t_a, img256) + 0.5 * _one(t_u, img256)


def _stem_for(path):
    """由路徑組出可當檔名的 stem:取最後兩層目錄 + 檔名。
    只用「父目錄_檔名」不夠——`--src "**/image"` 這種寫法下每個父目錄都叫 image,會大量碰撞。"""
    parts = os.path.normpath(path).replace("\\", "/").split("/")
    tail = parts[-3:] if len(parts) >= 3 else parts
    stem = "__".join(tail[:-1] + [os.path.splitext(tail[-1])[0]])
    return "".join(c if (c.isalnum() or c in "_-") else "_" for c in stem)[:120]


def collect(srcs, exts=(".png", ".jpg", ".jpeg", ".bmp"), recursive=False):
    """展開 --src。支援三種寫法,且 **glob 指到目錄也算數**(常見寫法 `.../**/image`):
       1) 目錄            → 取其中的影像
       2) glob 命中目錄    → 取每個目錄中的影像
       3) glob 命中檔案    → 直接收
    recursive=True 時目錄再往下遞迴(預設關,避免把 labels/ 一起吸進來)。"""
    out = []
    for s in srcs:
        cands = [s] if os.path.isdir(s) else glob.glob(s, recursive=True)
        for c in cands:
            if os.path.isdir(c):
                pat = os.path.join(c, "**", "*") if recursive else os.path.join(c, "*")
                for e in exts:
                    out += glob.glob(pat + e, recursive=recursive)
            elif os.path.splitext(c)[1].lower() in exts:
                out.append(c)

    # stem 必須唯一:碰撞時補路徑雜湊,**絕不靜默丟檔**(丟了就永遠不知道少訓練了什麼)
    import hashlib
    seen, uniq = {}, []
    for p in sorted(set(out)):
        stem = _stem_for(p)
        if stem in seen:
            stem = f"{stem}_{hashlib.sha1(p.encode('utf-8')).hexdigest()[:6]}"
        seen[stem] = p
        uniq.append((stem, p))
    return uniq


GT_DIRNAMES = ("labels", "label", "masks", "mask", "gt", "annotations")


def sibling_gt(path):
    """這張圖是否已經有人工 GT(同層有 labels/ 等姊妹目錄且同名檔存在)。

    有 GT 的影像**通常不該進偽標籤**:偽標籤是為了覆蓋「沒有 GT、模型沒看過」的分布;
    對已標註集產偽標籤，學到的只是老師對已知資料的複述,student 早就從真值學過了。
    例外:已知 GT 品質差(retrain_merged 稽核出 28 可疑/5 空GT)時,可用 --include-labeled 蓋過。"""
    d = os.path.dirname(path)
    parent, base = os.path.dirname(d), os.path.basename(path)
    stem = os.path.splitext(base)[0]
    for lab in GT_DIRNAMES:
        for cand in (os.path.join(parent, lab, base), os.path.join(parent, lab, stem + ".png")):
            if os.path.exists(cand): return cand
    return None


def file_sha(path, chunk=1 << 20):
    import hashlib
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b: break
            h.update(b)
    return h.hexdigest()


def imread_unicode(path):
    """cv2.imread 在 Windows 中文路徑會回 None(本專案有前例)→ 一律 fromfile+imdecode。"""
    if cv2 is None:
        from PIL import Image
        return np.asarray(Image.open(path).convert("RGB"))
    arr = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    return None if arr is None else cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", action="append", required=True, help="無 GT 影像目錄或 glob(可重複)")
    ap.add_argument("--out", default=os.path.join(os.environ.get("WOUNDAI_ARCHIVE",
                    "C:/dev/WoundAI_weights_archive"), "批次驗證工具", "pseudo_AU"))
    ap.add_argument("--onnx-dir", default=os.path.join(os.environ.get("WOUNDAI_ARCHIVE",
                    "C:/dev/WoundAI_weights_archive"), "onnx_export"))
    ap.add_argument("--exclude", action="append", default=[],
                    help="路徑含此字串就跳過(例:labels、mask、驗收基準目錄)")
    ap.add_argument("--recursive", action="store_true",
                    help="目錄再往下遞迴找影像(預設關,避免把 labels/ 一起吸進來)")
    ap.add_argument("--list-dirs", action="store_true",
                    help="只列出 --src 命中的目錄與各自影像張數/有無 GT(找不到檔案時先跑這個)")
    ap.add_argument("--include-labeled", action="store_true",
                    help="連已有人工 GT 的影像也產偽標籤(預設排除)。只有在刻意要蓋掉已知有問題的 GT 時才用")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--no-tta", action="store_true", help="關掉翻轉一致性檢查(不建議,那是最強訊號)")
    ap.add_argument("--montage", action="store_true", help="產出目視抽查大圖")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if a.list_dirs:
        from collections import Counter
        hits = [c for s in a.src for c in ([s] if os.path.isdir(s) else glob.glob(s, recursive=True))]
        dirs = sorted({c if os.path.isdir(c) else os.path.dirname(c) for c in hits})
        if not dirs:
            print(f"✗ --src 完全沒命中。逐段確認路徑是否存在:\n   {a.src}")
            return 1
        got = collect(a.src, recursive=a.recursive)
        cnt = Counter(os.path.dirname(p) for _, p in got)
        lab = Counter(os.path.dirname(p) for _, p in got if sibling_gt(p))
        print(f"  {'影像':>5} {'有GT':>5}  目錄")
        for d in dirs:
            n, l = cnt.get(d, 0), lab.get(d, 0)
            flag = "  ← 已標註集,不適合當偽標籤來源" if n and l == n else ""
            print(f"  {n:>5} {l:>5}  {d}{flag}")
        tot, totl = sum(cnt.values()), sum(lab.values())
        print(f"\n共 {len(dirs)} 個目錄、{tot} 張影像,其中 {totl} 張已有人工 GT")
        if tot and totl == tot:
            print("\n⚠ **全部都有 GT**——偽標籤是要覆蓋『沒 GT、模型沒看過』的分布,")
            print("  對已標註集產偽標籤只是複述 student 早就學過的東西,拉不動召回。")
            print("  請改指向無 GT 的傷口照池。")
        return 0

    files = collect(a.src, recursive=a.recursive)
    n_raw = len(files)
    for ex in a.exclude:
        files = [(s, p) for s, p in files if ex not in p.replace("\\", "/")]
    n_ex = n_raw - len(files)

    # 內容重複(同一張圖在多個資料夾各存一份)→ 只留一份,否則等於對同一樣本加權
    seen_sha, dedup, n_dup = {}, [], 0
    for s, p in files:
        try: sh = file_sha(p)
        except Exception: sh = p
        if sh in seen_sha: n_dup += 1; continue
        seen_sha[sh] = p; dedup.append((s, p))
    files = dedup

    # 已有人工 GT 的影像:預設排除(偽標籤是要補「沒 GT、沒看過」的分布)
    labeled = [(s, p) for s, p in files if sibling_gt(p)]
    if not a.include_labeled:
        files = [(s, p) for s, p in files if not sibling_gt(p)]
    if a.limit: files = files[:a.limit]

    print(f"掃到 {n_raw} 張;--exclude 濾掉 {n_ex}、內容重複 {n_dup}、"
          f"已有人工 GT {len(labeled)}{'(依 --include-labeled 保留)' if a.include_labeled else '(已排除)'}"
          f" → 候選 {len(files)} 張")
    if not files:
        if labeled and not a.include_labeled:
            print(f"\n✗ 候選為 0:掃到的 {len(labeled)} 張**全部已經有人工 GT**(旁邊就有 labels/)。")
            print("   偽標籤的用途是覆蓋『沒有 GT、模型沒看過』的分布——對已標註集產偽標籤,")
            print("   student 只會複述它早就從真值學過的東西,拉不動召回,還可能洩漏評測子集。")
            print("   → 請改指向**無 GT 的傷口照池**(臨床收案照、公開資料集未標註部分、歷史拍攝檔)。")
            print("   → 若是刻意要蓋掉已知有問題的 GT(如 retrain_merged 稽核出的 28 可疑/5 空GT),")
            print("     才加 --include-labeled,並在 EVIDENCE_LEDGER 說明理由。")
        else:
            print("✗ 沒有檔案。排查順序:")
            print("   1) 加 --list-dirs 看 --src 命中哪些目錄、各有幾張")
            print("   2) glob 指到目錄也可以(如 `.../**/image`);影像在更深層則加 --recursive")
            print("   3) 路徑含空白/中文請整串用引號包起來")
        return 1
    if labeled and a.include_labeled:
        print(f"⚠ 其中 {len(labeled)} 張已有人工 GT——確認你是刻意要用偽標籤蓋過既有 GT")
    if a.dry_run:
        for s, p in files[:10]: print("  ", s, "←", p)
        print("  ..." if len(files) > 10 else "")
        return 0

    for f in ("a_unet.onnx", "unetpp.onnx"):
        if not os.path.exists(os.path.join(a.onnx_dir, f)):
            print(f"✗ 找不到老師模型 {os.path.join(a.onnx_dir, f)}"); return 1
    t_a, t_u = build_teacher(a.onnx_dir)

    soft_dir = os.path.join(a.out, "soft"); os.makedirs(soft_dir, exist_ok=True)
    recs, keep_vis = [], []
    for k, (stem, path) in enumerate(files, 1):
        img = imread_unicode(path)
        if img is None:
            recs.append({"stem": stem, "path": path, "accept": False, "reasons": ["讀檔失敗"]}); continue
        i256 = cv2.resize(img, (256, 256)) if cv2 is not None else np.array(
            __import__("PIL.Image", fromlist=["Image"]).Image.fromarray(img).resize((256, 256)))
        p = teacher_prob(t_a, t_u, i256)
        pf = None
        if not a.no_tta:
            pf = teacher_prob(t_a, t_u, i256[:, ::-1].copy())[:, ::-1]
        m = gate_metrics(p, pf)
        m.update({"stem": stem, "path": path})
        recs.append(m)
        if m["accept"]:
            # 存「原圖與翻轉的平均」——TTA 融合的軟標籤比單次推論乾淨
            soft = p if pf is None else 0.5 * (p + pf)
            np.save(os.path.join(soft_dir, stem + ".npy"), soft.astype(np.float16))
            if len(keep_vis) < 24: keep_vis.append((i256, soft))
        if k % 25 == 0: print(f"  ...{k}/{len(files)}")

    ok = [r for r in recs if r["accept"]]
    rate = len(ok) / len(recs) if recs else 0
    from collections import Counter
    rej = Counter(r["reasons"][0].split()[0] for r in recs if not r["accept"] and r.get("reasons"))
    with open(os.path.join(a.out, "pseudo_manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"gate": GATE, "total": len(recs), "accepted": len(ok),
                   "accept_rate": round(rate, 3), "records": recs}, f, ensure_ascii=False, indent=2)

    print(f"\n=== 偽標籤把關結果 ===")
    print(f"  通過 {len(ok)}/{len(recs)}  ({rate:.1%})")
    for r, c in rej.most_common(): print(f"  退回 {r:<12} {c}")
    if ok:
        import statistics as st
        print(f"  通過者 TTA-IoU 中位 {st.median([r['tta_iou'] for r in ok]):.2f}、"
              f"平均機率中位 {st.median([r['mean_prob'] for r in ok]):.2f}、"
              f"面積比中位 {st.median([r['area_frac'] for r in ok]):.3f}")
    if rate < 0.40:
        print("\n⚠ 通過率 <40%:老師在這批資料上也撐不住,硬用只會把錯誤蒸餾進學生。"
              "\n  → 換資料來源、或先擴充老師(加 FUSegNet 之類)再回來。")

    if a.montage and keep_vis and cv2 is not None:
        rows = []
        for r0 in range(0, min(24, len(keep_vis)), 4):
            row = []
            for img, soft in keep_vis[r0:r0 + 4]:
                v = cv2.cvtColor(img, cv2.COLOR_RGB2BGR).copy()
                cs, _ = cv2.findContours((soft > GATE["thr"]).astype(np.uint8),
                                         cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                cv2.drawContours(v, cs, -1, (255, 0, 255), 2)
                row.append(v)
            while len(row) < 4: row.append(np.zeros_like(row[0]))
            rows.append(np.hstack(row))
        mp = os.path.join(a.out, "montage_accepted.png")
        cv2.imencode(".png", np.vstack(rows))[1].tofile(mp)
        print(f"\n目視抽查 → {mp}(務必看過再開訓;Dice 看不出來的錯誤,眼睛看得出來)")

    print(f"\n軟標籤 → {soft_dir}\n下一步: python distill_pseudo_train.py --pseudo {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
