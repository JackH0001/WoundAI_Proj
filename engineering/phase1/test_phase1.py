import numpy as np, sys
from annotation_pipeline import make_annotation_record, _iou
from clinical_rules import severity_scorecard, treatment
r = []
def ck(n, c): r.append(bool(c)); print(("PASS " if c else "FAIL "), n)
ai = np.zeros((10,10), bool); ai[2:6,2:6] = True
ed = np.zeros((10,10), bool); ed[2:8,2:6] = True
rec = make_annotation_record("img1", ai, ed, "dr_a", model_id="segmentation.wsm", px_per_mm=2.0)
ck("annot area_px == edited.sum", rec["area_px"] == int(ed.sum()))
ck("annot area_mm2 correct", abs(rec["area_mm2"] - int(ed.sum())/4.0) < 1e-6)
ck("annot correction_iou == iou(ai,ed)", abs(rec["correction_iou"] - round(_iou(ai,ed),4)) < 1e-9)
ck("annot pixels_changed == xor", rec["pixels_changed"] == int(np.logical_xor(ai,ed).sum()))
ck("annot status pending_qc", rec["status"] == "pending_qc")
ck("sev small-clean -> grade 1", severity_scorecard(2.0, {})["grade"] == 1)
ck("sev large+necrosis -> grade 4", severity_scorecard(20.0, {"necrosis":0.3})["grade"] == 4)
ck("sev deterministic", severity_scorecard(10.0,{"slough":0.4}) == severity_scorecard(10.0,{"slough":0.4}))
ck("tx necrosis -> 清創/轉診", "清創" in treatment(4, {"necrosis":0.3})["recommendation"])
ck("tx granulation -> 敷料", "敷料" in treatment(1, {})["recommendation"])
ck("tx has 需醫師確認", "醫師" in treatment(1, {})["note"])
ok = sum(r); print(f"\n{ok}/{len(r)} PASS"); sys.exit(0 if ok == len(r) else 1)
