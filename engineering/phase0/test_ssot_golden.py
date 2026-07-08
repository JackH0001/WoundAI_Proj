# -*- coding: utf-8 -*-
"""Golden SSOT 回歸：釘住已驗證的前處理/選型決策，防止無證據翻案。
若本測試失敗＝有人動了 SSOT——請先更新 docs/EVIDENCE_LEDGER.md（附評測證據），再同步更新本檔釘值。"""
import json, os, hashlib, sys
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from preprocess_consistency import preprocess

P = json.load(open(os.path.join(HERE, "preprocessing.json"), encoding="utf-8"))
POL = json.load(open(os.path.join(HERE, "..", "phase2", "routing_policy.json"), encoding="utf-8"))

# ---- 決策釘值（改動須連動 EVIDENCE_LEDGER.md）----
PIN = {
    "wsm":      {"normalize": "[0,1]",    "channel_order": "BGR", "layout": "NHWC", "input_size": [224, 224], "threshold": 0.5},
    "smp":      {"normalize": "imagenet", "channel_order": "RGB", "layout": "NCHW", "input_size": [256, 256], "threshold": 0.3},
    "student":  {"normalize": "imagenet", "channel_order": "RGB", "layout": "NCHW", "input_size": [256, 256], "threshold": 0.4},
    "fusegnet": {"normalize": "imagenet", "channel_order": "RGB", "layout": "NCHW", "input_size": [512, 512], "threshold": 0.5},
}
GOLDEN_HASH = {  # 固定 seed 輸入的前處理輸出 sha256[:16]（位元級釘死）
    "wsm": "b69bc8d596c9cf84",
    "smp": "5383094cdf3cba4d",
    "student": "5383094cdf3cba4d",
    "fusegnet": "5ab5b7a60f2ffe78",
}
ROUTING_PIN = {"edge_model": "segmentation.student", "fallback_model": "segmentation.stub"}

fails = []
def ck(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: fails.append(name)

for m, pin in PIN.items():
    cfg = P["models"][m]
    for k, v in pin.items():
        ck(f"{m}.{k} == {v}（證據見 EVIDENCE_LEDGER）", cfg.get(k) == v)
    sz = cfg["input_size"][0]
    u8 = np.random.default_rng(42).integers(0, 256, (sz, sz, 3), dtype=np.uint8)
    x = preprocess(u8, cfg).astype(np.float32)
    h = hashlib.sha256(np.ascontiguousarray(x).tobytes()).hexdigest()[:16]
    ck(f"{m} 前處理位元級 golden hash", h == GOLDEN_HASH[m])

for k, v in ROUTING_PIN.items():
    ck(f"routing_policy.{k} == {v}", POL.get(k) == v)

print(f"\n===== golden SSOT：{'全數通過' if not fails else '失敗 ' + str(len(fails)) + ' 項——改 SSOT 前請先更新 docs/EVIDENCE_LEDGER.md 並附證據'} =====")
sys.exit(1 if fails else 0)
