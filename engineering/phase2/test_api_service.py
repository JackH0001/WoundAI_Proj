"""參考服務層測試：回應對 OpenAPI 元件 schema 驗證 + graceful degrade。"""
import os, sys, io, base64, numpy as np, yaml, jsonschema
from PIL import Image
P0 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "phase0"); sys.path.insert(0, P0)
from model_registry import ModelRegistry
from feature_flags import FeatureFlags
import api_service as api
r=[]
def ck(n,c): r.append(bool(c)); print(("PASS " if c else "FAIL "),n)
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
spec = yaml.safe_load(open(os.path.join(ROOT,"openapi","annotation_segmentation.yaml"),encoding="utf-8"))
SCH = spec["components"]["schemas"]
def _nullable(o):
    if isinstance(o, dict):
        if o.get("nullable") and "type" in o and isinstance(o["type"], str):
            o["type"] = [o["type"], "null"]
        for v in o.values(): _nullable(v)
    elif isinstance(o, list):
        for v in o: _nullable(v)
    return o
_nullable(SCH)
def valid(obj, name):
    try: jsonschema.validate(obj, SCH[name]); return True
    except jsonschema.ValidationError: return False
reg = ModelRegistry(os.path.join(P0,"model_registry.json"))
flags_on = FeatureFlags(os.path.join(P0,"feature_flags.json"))
class Off:
    def is_enabled(self,_): return False
img = np.random.default_rng(3).integers(0,256,(200,260,3),dtype=np.uint8)

# /segment：stub 可用 -> ai_assistive + 合法 schema + 可解碼遮罩
out,http = api.handle_segment(img, flags_on, reg, image_id="imgA")
ck("/segment 200", http==200)
ck("/segment status=ai_assistive", out["status"]=="ai_assistive")
ck("/segment 符合 SegmentationResult schema", valid(out,"SegmentationResult"))
ck("/segment mask_png_b64 可解碼為 256x256", api._b64_to_mask(out["mask_png_b64"]).shape==(256,256))
# 旗標關閉 -> manual_fallback
o2,h2 = api.handle_segment(img, Off(), reg)
ck("/segment flag off -> manual_fallback 200", o2["status"]=="manual_fallback" and h2==200 and valid(o2,"SegmentationResult"))
# 無可用模型 -> unavailable 503
nofb={"prefer":"edge","edge_model":"segmentation.wsm","cloud_model":"segmentation.fusegnet","min_confidence":0.5}
o3,h3 = api.handle_segment(img, flags_on, reg, policy=nofb)
ck("/segment 無模型 -> unavailable 503", o3["status"]=="unavailable" and h3==503 and valid(o3,"SegmentationResult"))

# /annotations：先 segment(imgA) 已存初稿；提交醫師修邊
ed = np.zeros((256,256),bool); ed[40:200,50:210]=True
sub = {"image_id":"imgA","edited_mask_png_b64":api._mask_to_b64(ed),"editor_id":"dr_a","px_per_mm":3.0,"model_id":"segmentation.stub"}
ck("AnnotationSubmit 合法", valid(sub,"AnnotationSubmit"))
rec,hc = api.handle_annotations(sub)
ck("/annotations 201", hc==201)
ck("/annotations 符合 AnnotationRecord schema", valid(rec,"AnnotationRecord"))
ck("/annotations area_px == edited.sum", rec["area_px"]==int(ed.sum()))
ck("/annotations status pending_qc", rec["status"]=="pending_qc")
ck("/annotations correction_iou ∈ [0,1]", 0.0<=rec["correction_iou"]<=1.0)
# 無先前初稿 -> ai 空、仍合法
sub2 = {"image_id":"imgZ","edited_mask_png_b64":api._mask_to_b64(ed),"editor_id":"dr_b"}
rec2,_ = api.handle_annotations(sub2)
ck("/annotations 無初稿仍合法 + correction_iou=0", valid(rec2,"AnnotationRecord") and rec2["correction_iou"]==0.0)

# /annotation-tasks
api.handle_segment(img, flags_on, reg, image_id="imgPending")
tasks,ht = api.handle_annotation_tasks()
ck("/annotation-tasks 200 + tasks list", ht==200 and isinstance(tasks["tasks"],list))

ok=sum(r); print(f"\n{ok}/{len(r)} PASS"); sys.exit(0 if ok==len(r) else 1)
