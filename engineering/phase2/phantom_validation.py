"""Phantom 精度基準：已知真實面積(cm²) vs 量測面積 → area_err%。建立校正後面積精度基準（接 RUNBOOK）。"""
import numpy as np
from measure import measure_wound
def area_error_pct(measured, true_cm2):
    return abs(measured - true_cm2) / true_cm2 * 100.0 if true_cm2 else (0.0 if not measured else 100.0)
def run(cases):
    """cases: list of dict(image, mask, true_cm2, sticker_quad|assist_bbox)。回傳逐筆 + 摘要。"""
    rows = []
    for c in cases:
        m = measure_wound(c["image"], c["mask"], sticker_mm=c.get("sticker_mm", 20.0),
                          sticker_quad=c.get("sticker_quad"), assist_bbox=c.get("assist_bbox"))
        meas = m["area_cm2"]
        err = None if meas is None else area_error_pct(meas, c["true_cm2"])
        rows.append({"name": c.get("name", "?"), "true_cm2": c["true_cm2"], "measured_cm2": meas,
                     "area_err_pct": (None if err is None else round(err, 2)), "method": m["method"]})
    errs = [r["area_err_pct"] for r in rows if r["area_err_pct"] is not None]
    summary = {"n": len(rows), "n_measured": len(errs),
               "mean_area_err_pct": (round(float(np.mean(errs)), 2) if errs else None),
               "max_area_err_pct": (round(float(np.max(errs)), 2) if errs else None)}
    return {"rows": rows, "summary": summary}

def _load_image(p):
    from PIL import Image; return np.asarray(Image.open(p).convert("RGB"))
def _load_mask(p):
    from PIL import Image; return np.asarray(Image.open(p).convert("L")) > 127
def run_from_manifest(manifest_path, base_dir=None, out_csv=None):
    """讀實機資料 manifest(CSV) → 逐筆量測 area_err → 報告(CSV)+摘要。
    必要欄：name,image_path,mask_path,true_cm2；選填：sticker_x0..y1(assist bbox),sticker_mm,distance_cm,angle_deg,operator,device,lux,notes。"""
    import csv, os
    base = base_dir or os.path.dirname(os.path.abspath(manifest_path))
    META = ("distance_cm", "angle_deg", "operator", "device", "lux", "notes")
    cases, metas = [], []
    with open(manifest_path, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            c = {"name": row.get("name", "?"),
                 "image": _load_image(os.path.join(base, row["image_path"])),
                 "mask": _load_mask(os.path.join(base, row["mask_path"])),
                 "true_cm2": float(row["true_cm2"]),
                 "sticker_mm": float(row.get("sticker_mm") or 20.0)}
            if all(row.get(k) not in (None, "") for k in ("sticker_x0", "sticker_y0", "sticker_x1", "sticker_y1")):
                c["assist_bbox"] = tuple(int(float(row[k])) for k in ("sticker_x0", "sticker_y0", "sticker_x1", "sticker_y1"))
            cases.append(c); metas.append({k: row.get(k, "") for k in META})
    res = run(cases)
    for r, m in zip(res["rows"], metas): r.update(m)
    if out_csv:
        import csv as _csv
        cols = ["name", "true_cm2", "measured_cm2", "area_err_pct", "method"] + list(META)
        with open(out_csv, "w", newline="", encoding="utf-8") as fh:
            w = _csv.DictWriter(fh, fieldnames=cols); w.writeheader()
            for r in res["rows"]: w.writerow({k: r.get(k) for k in cols})
        res["out_csv"] = out_csv
    return res

def tissue_report(synth_dir, out_csv=None):
    """組織分類 GT 對照：對每個合成 case（*_meta.json + *_image + *_woundmask）跑分類，
    比對偵測 vs GT 之 necrosis/slough/granulation 比例與 dominant。回傳逐筆 + 摘要。"""
    import os, glob, json, csv as _csv
    from PIL import Image
    import wound_classifier as wc
    CLS = ("necrosis", "slough", "granulation")
    rows = []
    for mp in sorted(glob.glob(os.path.join(synth_dir, "*_meta.json"))):
        meta = json.load(open(mp, encoding="utf-8")); nm = meta["name"]; gt = meta["tissue_fraction_gt"]
        img = np.asarray(Image.open(os.path.join(synth_dir, nm + "_image.png")).convert("RGB"))
        m = np.asarray(Image.open(os.path.join(synth_dir, nm + "_woundmask.png")).convert("L")) > 127
        res = wc.classify(img, m); det = res["tissue_proxy"]
        row = {"name": nm}
        for k in CLS:
            row[f"gt_{k}"] = round(gt[k], 3); row[f"det_{k}"] = round(det[k], 3)
            row[f"abserr_{k}"] = round(abs(gt[k] - det[k]), 3)
        gt_dom = max(CLS, key=lambda k: gt[k])
        row["gt_dom"] = gt_dom; row["det_dom"] = res["tissue_dominant"]
        row["dom_match"] = {"necrosis": "壞死為主", "slough": "腐肉為主", "granulation": "肉芽為主"}[gt_dom] == res["tissue_dominant"]
        rows.append(row)
    n = len(rows) or 1
    summary = {"n": len(rows),
               "mean_abserr": {k: round(sum(r[f"abserr_{k}"] for r in rows) / n, 3) for k in CLS},
               "dom_match_rate": round(sum(1 for r in rows if r["dom_match"]) / n, 3)}
    if out_csv and rows:
        cols = list(rows[0].keys())
        with open(out_csv, "w", newline="", encoding="utf-8") as fh:
            w = _csv.DictWriter(fh, fieldnames=cols); w.writeheader()
            for r in rows: w.writerow(r)
    return {"rows": rows, "summary": summary}
