"""Track A1 測試：分割推論轉接層的 graceful degrade（缺模型不偽造）與真實推論路徑（用 stub 驗證）。"""
import os, sys, numpy as np
P0 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "phase0")
sys.path.insert(0, P0)
from model_registry import ModelRegistry
from feature_flags import FeatureFlags
import seg_infer
r=[]
def ck(n,c): r.append(bool(c)); print(("PASS " if c else "FAIL "),n)

reg = ModelRegistry(os.path.join(P0,"model_registry.json"))
flags_on = FeatureFlags(os.path.join(P0,"feature_flags.json"))   # semi_auto_segmentation = true
class FlagsOff:
    def is_enabled(self,_): return False
img = (np.random.default_rng(1).integers(0,256,(300,400,3),dtype=np.uint8))

# 1) 旗標關閉 → disabled，不產遮罩
d = seg_infer.segment(img, "segmentation.stub", FlagsOff(), reg)
ck("flag off -> status disabled", d["status"]=="disabled")
ck("flag off -> mask None (不偽造)", d["mask"] is None)

# 2) 缺真實模型(wsm.onnx 不在協作 repo) → model_unavailable，不偽造  [安全關鍵]
d = seg_infer.segment(img, "segmentation.wsm", flags_on, reg)
ck("missing model -> status model_unavailable", d["status"]=="model_unavailable")
ck("missing model -> mask None (絕不偽造)", d["mask"] is None)

# 3) 分類模型同樣缺檔 → unavailable
ck("registry: wsm 不可用", not reg.is_available("segmentation.wsm"))
ck("registry: stub 可用", reg.is_available("segmentation.stub"))
ck("registry: tissue classifier 缺檔", not reg.is_available("tissue.classifier"))

# 4) stub 存在 + 旗標開 → 真實跑通推論路徑
d = seg_infer.segment(img, "segmentation.stub", flags_on, reg)
ck("stub -> status ok", d["status"]=="ok")
ck("stub -> mask 為 2D bool", isinstance(d["mask"],np.ndarray) and d["mask"].dtype==bool and d["mask"].ndim==2)
ck("stub -> mask 256x256 (符合 SSOT input_size)", list(d["mask"].shape)==[256,256])
ck("stub -> confidence ∈ [0,1]", d["confidence"] is not None and 0.0<=d["confidence"]<=1.0)
_thr = float(seg_infer._cfg("wsm").get("threshold", 0.5))
ck("stub -> 套用 SSOT threshold", abs(d["threshold"]-_thr)<1e-9)

# 5) 決定性
d2 = seg_infer.segment(img, "segmentation.stub", flags_on, reg)
ck("決定性：mask 相同", np.array_equal(d["mask"], d2["mask"]))
ck("決定性：confidence 相同", d["confidence"]==d2["confidence"])

# 6) UI 橋接 JSON
import tempfile,json
p=os.path.join(tempfile.gettempdir(),"draft_ok.json")
pl=seg_infer.draft_to_ui_json(d, p)
ck("draft json: w*h == data 長度", len(pl["data"])==pl["w"]*pl["h"])
ck("draft json: data 僅 0/1", set(np.unique(pl["data"]).tolist()) <= {0,1})
pl2=seg_infer.draft_to_ui_json(seg_infer.segment(img,"segmentation.wsm",flags_on,reg), p)
ck("draft json: 缺模型 -> draft None", pl2["draft"] is None)

ok=sum(r); print(f"\n{ok}/{len(r)} PASS"); sys.exit(0 if ok==len(r) else 1)
