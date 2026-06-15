"""整合測試：以 Flask test_client 走完 HTTP 端點（不開真實 port）。"""
import os, sys, io, base64, numpy as np, yaml, jsonschema
from PIL import Image
HERE=os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0,HERE)
from app import create_app
r=[]
def ck(n,c): r.append(bool(c)); print(("PASS " if c else "FAIL "),n)
ROOT=os.path.join(HERE,"..","..")
spec=yaml.safe_load(open(os.path.join(ROOT,"openapi","annotation_segmentation.yaml"),encoding="utf-8"))
SCH=spec["components"]["schemas"]
def _nl(o):
    if isinstance(o,dict):
        if o.get("nullable") and isinstance(o.get("type"),str): o["type"]=[o["type"],"null"]
        for v in o.values(): _nl(v)
    elif isinstance(o,list):
        for v in o: _nl(v)
    return o
_nl(SCH)
def valid(obj,name):
    try: jsonschema.validate(obj,SCH[name]); return True
    except jsonschema.ValidationError: return False
app=create_app(); c=app.test_client()
# 合成影像 -> PNG bytes
buf=io.BytesIO(); Image.fromarray(np.random.default_rng(4).integers(0,256,(180,240,3),dtype=np.uint8)).save(buf,format="PNG"); buf.seek(0)
resp=c.post("/segment",data={"image":(buf,"w.png"),"image_id":"imgHTTP"},content_type="multipart/form-data")
j=resp.get_json()
ck("POST /segment 200", resp.status_code==200)
ck("/segment status ai_assistive", j["status"]=="ai_assistive")
ck("/segment 回應符合 schema", valid(j,"SegmentationResult"))
ck("/segment 回傳 mask_png_b64", "mask_png_b64" in j)
# 提交修邊
ed=np.zeros((256,256),bool); ed[40:200,50:210]=True
b=io.BytesIO(); Image.fromarray(ed.astype(np.uint8)*255).save(b,format="PNG")
sub={"image_id":"imgHTTP","edited_mask_png_b64":base64.b64encode(b.getvalue()).decode(),"editor_id":"dr_a","px_per_mm":3.0}
resp=c.post("/annotations",json=sub); rec=resp.get_json()
ck("POST /annotations 201", resp.status_code==201)
ck("/annotations 符合 AnnotationRecord schema", valid(rec,"AnnotationRecord"))
ck("/annotations area_px == edited.sum", rec["area_px"]==int(ed.sum()))
ck("/annotations correction_iou ∈ [0,1]", 0.0<=rec["correction_iou"]<=1.0)
# tasks
resp=c.get("/annotation-tasks")
ck("GET /annotation-tasks 200", resp.status_code==200 and isinstance(resp.get_json()["tasks"],list))
# 缺 image 參數 -> 400
ck("/segment 缺 image -> 400", c.post("/segment",data={},content_type="multipart/form-data").status_code==400)
ok=sum(r); print(f"\n{ok}/{len(r)} PASS"); sys.exit(0 if ok==len(r) else 1)
