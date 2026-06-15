"""分割評測 harness：seg_metrics(pred,gt) 純函式；run(pred_dir,gt_dir) 產出 eval_report.csv 與摘要。"""
import os, csv, glob, statistics
import numpy as np
def seg_metrics(pred, gt):
    pred = pred.astype(bool); gt = gt.astype(bool)
    inter = np.logical_and(pred, gt).sum()
    union = np.logical_or(pred, gt).sum()
    tp = inter; fp = np.logical_and(pred, ~gt).sum(); fn = np.logical_and(~pred, gt).sum()
    iou = inter / union if union else 1.0
    dice = 2 * tp / (2 * tp + fp + fn) if (2 * tp + fp + fn) else 1.0
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    ap, ag = int(pred.sum()), int(gt.sum())
    area_err = abs(ap - ag) / ag * 100 if ag else (0.0 if ap == 0 else 100.0)
    return dict(iou=float(iou), dice=float(dice), precision=float(prec), recall=float(rec),
                area_pred_px=ap, area_gt_px=ag, area_err_pct=float(area_err))
def _load(path, thr=127):
    from PIL import Image
    a = np.array(Image.open(path).convert("L"))
    return a > thr
def run(pred_dir, gt_dir, out_csv="eval_report.csv"):
    rows = []
    for p in sorted(glob.glob(os.path.join(pred_dir, "*.png"))):
        n = os.path.basename(p); g = os.path.join(gt_dir, n)
        if os.path.exists(g): rows.append((n, seg_metrics(_load(p), _load(g))))
    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["filename","iou","dice","precision","recall","area_pred_px","area_gt_px","area_err_pct"])
        for n, m in rows: w.writerow([n, m["iou"], m["dice"], m["precision"], m["recall"], m["area_pred_px"], m["area_gt_px"], m["area_err_pct"]])
    for k in ("iou", "dice", "area_err_pct"):
        v = [m[k] for _, m in rows]
        if v: print(f"{k}: mean={statistics.mean(v):.4f} median={statistics.median(v):.4f} n={len(v)}")
    return rows
if __name__ == "__main__":
    import sys; run(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "eval_report.csv")
