"""FastAPI 整合測試（TestClient，不開真實 port）：契約 schema + graceful + Bearer 認證。"""
import os, sys, io, base64, numpy as np, yaml, jsonschema
from PIL import Image
from fastapi.testclient import TestClient
HERE=os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0,HERE)
r=[]
def ck(n,c): r.append(bool(c)); print(("PASS " if c else "FAIL "),n)
ROOT=os.path.join(HERE,"..",".."); spec=yaml.safe_load(open(os.path.join(ROOT,"openapi","annotation_segmentation.yaml"),encoding="utf-8"))
SCH=spec["components"]["schemas"]
def _nl(o):
    if isinstance(o,dict):
        if o.get("nullable") and isinstance(o.get("type"),str): o["type"]=[o["type"],"null"]
        for v in o.values(): _nl(v)
    elif isinstance(o,list):
        for v in o: _nl(v)
    return o
_nl(SCH)
def valid(o,n):
    try: jsonschema.validate(o,SCH[n]); return True
    except jsonschema.ValidationError: return False
os.environ.pop("WOUNDAI_API_TOKEN", None)             # 開發模式（不驗）
import importlib, app_fastapi; importlib.reload(app_fastapi)
c=TestClient(app_fastapi.create_app())
ck("GET /healthz 200", c.get("/healthz").status_code==200)
buf=io.BytesIO(); Image.fromarray(np.random.default_rng(7).integers(0,256,(160,200,3),dtype=np.uint8)).save(buf,format="PNG"); buf.seek(0)
resp=c.post("/segment",files={"image":("w.png",buf,"image/png")},data={"image_id":"imgF"})
j=resp.json()
ck("POST /segment 200", resp.status_code==200)
ck("/segment status ai_assistive", j["status"]=="ai_assistive")
ck("/segment 符合 schema", valid(j,"SegmentationResult"))
ed=np.zeros((256,256),bool); ed[40:200,50:210]=True
b=io.BytesIO(); Image.fromarray(ed.astype(np.uint8)*255).save(b,format="PNG")
sub={"image_id":"imgF","edited_mask_png_b64":base64.b64encode(b.getvalue()).decode(),"editor_id":"dr_a","px_per_mm":3.0}
resp=c.post("/annotations",json=sub); rec=resp.json()
ck("POST /annotations 201", resp.status_code==201)
ck("/annotations 符合 schema", valid(rec,"AnnotationRecord"))
ck("/annotations area_px == edited.sum", rec["area_px"]==int(ed.sum()))
ck("GET /annotation-tasks 200 list", c.get("/annotation-tasks").json().get("tasks") is not None)
# 認證模式
os.environ["WOUNDAI_API_TOKEN"]="secret-demo-token"
ca=TestClient(app_fastapi.create_app())
ck("認證開啟：無 header -> 401", ca.get("/annotation-tasks").status_code==401)
ck("認證開啟：正確 token -> 200", ca.get("/annotation-tasks",headers={"Authorization":"Bearer secret-demo-token"}).status_code==200)
ck("認證開啟：錯 token -> 401", ca.get("/annotation-tasks",headers={"Authorization":"Bearer wrong"}).status_code==401)
os.environ.pop("WOUNDAI_API_TOKEN", None)
ok=sum(r); print(f"\n{ok}/{len(r)} PASS"); sys.exit(0 if ok==len(r) else 1)
