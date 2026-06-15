"""Track A3+A4 測試：推論路由(邊緣/雲端/fallback)+信心門檻；端到端飛輪+再訓練佇列。"""
import os, sys, json, tempfile, numpy as np
P0=os.path.join(os.path.dirname(os.path.abspath(__file__)),"..","phase0")
sys.path.insert(0,P0)
from model_registry import ModelRegistry
from feature_flags import FeatureFlags
import inference_router as router
import pipeline_flywheel as fly
r=[]
def ck(n,c): r.append(bool(c)); print(("PASS " if c else "FAIL "),n)
reg=ModelRegistry(os.path.join(P0,"model_registry.json"))
flags_on=FeatureFlags(os.path.join(P0,"feature_flags.json"))
class Off:
    def is_enabled(self,_): return False
img=np.random.default_rng(2).integers(0,256,(280,360,3),dtype=np.uint8)

# ---- A3 路由 ----
d=router.route(img,Off(),reg)
ck("A3 flag off -> disabled", d["status"]=="disabled" and d["mask"] is None)
d=router.route(img,flags_on,reg)   # 預設策略：edge(wsm缺)->cloud(fusegnet缺)->fallback(stub)
ck("A3 邊緣/雲端缺 -> 退到 fallback(stub)", d["status"]=="ok" and d["path"]=="fallback")
ck("A3 使用模型為 stub", d["model_id"]=="segmentation.stub")
ck("A3 回傳 needs_review 布林", isinstance(d["needs_review"],bool))
# 信心門檻：拉高 min_confidence 強制複核
hi=dict(router.load_policy()); hi["min_confidence"]=1.0
ck("A3 min_conf=1.0 -> needs_review True", router.route(img,flags_on,reg,hi)["needs_review"] is True)
lo=dict(router.load_policy()); lo["min_confidence"]=0.0
ck("A3 min_conf=0.0 -> needs_review False", router.route(img,flags_on,reg,lo)["needs_review"] is False)
# 無任何可用模型（策略無 fallback 且 edge/cloud 缺）-> model_unavailable
nofb={"prefer":"edge","edge_model":"segmentation.wsm","cloud_model":"segmentation.fusegnet","min_confidence":0.5}
ck("A3 無可用模型且無fallback -> model_unavailable", router.route(img,flags_on,reg,nofb)["status"]=="model_unavailable")
ck("A3 路由決定性", router.route(img,flags_on,reg)["model_id"]==router.route(img,flags_on,reg)["model_id"])

# ---- A4 飛輪 ----
edited=np.zeros((256,256),bool); edited[60:180,70:200]=True
q=os.path.join(tempfile.gettempdir(),"retrain_queue.jsonl")
open(q,"w").close()
out=fly.run_flywheel(img,edited,flags_on,reg,"dr_a","img_fw_001",px_per_mm=3.0,queue_path=q)
rec=out["record"]
ck("A4 record 有 area_px", "area_px" in rec and rec["area_px"]==int(edited.sum()))
ck("A4 record correction_iou 存在", "correction_iou" in rec)
ck("A4 ai_available True (stub)", rec["ai_available"] is True)
ck("A4 draft_vs_gt 有 dice/iou", out["metrics"] is not None and "dice" in out["metrics"] and "iou" in out["metrics"])
ck("A4 routing_path=fallback 記錄於 record", rec["routing_path"]=="fallback")
ck("A4 佇列寫入 1 行", sum(1 for _ in open(q,encoding="utf-8"))==1)
fly.run_flywheel(img,edited,flags_on,reg,"dr_a","img_fw_002",px_per_mm=3.0,queue_path=q)
ck("A4 佇列累加為 2 行", sum(1 for _ in open(q,encoding="utf-8"))==2)
# graceful：旗標關閉 -> 無 AI 初稿仍可記錄（可靠幾何）
g=fly.run_flywheel(img,edited,Off(),reg,"dr_a","img_fw_003",px_per_mm=3.0,queue_path=None)
ck("A4 graceful: ai_available False", g["record"]["ai_available"] is False)
ck("A4 graceful: draft_vs_gt None 但仍有 area_px", g["record"]["draft_vs_gt"] is None and "area_px" in g["record"])
ck("A4 佇列每行為合法 JSON", all(json.loads(l) for l in open(q,encoding="utf-8")))

ok=sum(r); print(f"\n{ok}/{len(r)} PASS"); sys.exit(0 if ok==len(r) else 1)
