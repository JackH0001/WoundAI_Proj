import sys, os, json, numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from annotation_pipeline import make_annotation_record
ai = np.zeros((10,10), bool); ai[2:6,2:6] = True
ed = np.zeros((10,10), bool); ed[2:8,2:6] = True
r = make_annotation_record("img1", ai, ed, "dr_a", model_id="segmentation.wsm", px_per_mm=2.0)
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "expected.json")
json.dump({"area_px": r["area_px"], "correction_iou": r["correction_iou"], "pixels_changed": r["pixels_changed"]}, open(out, "w"))
print("py:", r["area_px"], r["correction_iou"], r["pixels_changed"])
