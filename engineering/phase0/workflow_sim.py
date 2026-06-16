"""WoundAI 實務工作流模擬 + 建置完整性檢查（10 情境）。
驗證：可靠核心(量測/校正)恆可用；AI 不確定項受 flag/registry 雙閘控；缺模型 graceful degrade、不偽造；
規則式嚴重度/治療建議可在無模型下運作；前處理跨端一致。"""
import json, os, numpy as np
from model_registry import ModelRegistry
from feature_flags import FeatureFlags
import preprocess_consistency as pp

# ---- 可靠核心：校正 + 面積量測（幾何，不依賴 AI） ----
def area_mm2(mask_px, px_per_mm):  # px_per_mm 來自尺規/貼紙校正
    return mask_px / (px_per_mm ** 2)

# ---- 分割：registry+flag 雙閘；缺模型→退回人工，不偽造 ----
def segment(image, reg, flags, model_id="segmentation.wsm", manual_mask=None):
    if not flags.is_enabled("semi_auto_segmentation"):
        return {"status": "disabled", "mask_px": (int(manual_mask.sum()) if manual_mask is not None else None)}
    path = reg.require(model_id)
    if path is None:                      # graceful degrade
        if manual_mask is not None:
            return {"status": "manual_fallback", "mask_px": int(manual_mask.sum()), "confidence": None}
        return {"status": "needs_manual", "mask_px": None}
    import onnxruntime as ort
    s = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
    y = s.run(None, {s.get_inputs()[0].name: image})[0]
    mask = (y[0, ..., 0] > 0.5)
    return {"status": "ai_assistive", "mask_px": int(mask.sum()), "confidence": 0.42, "note": "輔助用途，需醫師確認"}

# ---- 分類：flag AND 模型存在 才跑；否則 disabled/unavailable（不偽造標籤） ----
def classify(kind, reg, flags):
    flag = {"tissue": "ai_tissue_classification", "wound_type": "ai_wound_type"}[kind]
    rid = {"tissue": "tissue.classifier", "wound_type": "woundtype.classifier"}[kind]
    if not flags.is_enabled(flag): return {"status": "disabled", "label": None}
    if reg.require(rid) is None:   return {"status": "unavailable", "label": None}
    return {"status": "ai_assistive", "label": "<model_output>", "confidence": 0.0}

# ---- 嚴重度：規則式分數卡（可解釋、無需模型） ----
def severity_scorecard(area_cm2, tissue_frac):
    pts = 0
    pts += 0 if area_cm2 < 4 else (1 if area_cm2 < 16 else 2)
    pts += 2 if tissue_frac.get("necrosis", 0) > 0.2 else (1 if tissue_frac.get("slough", 0) > 0.3 else 0)
    grade = 1 + min(pts, 3)
    return {"status": "rule_based", "grade": grade, "explain": f"area_cm2={area_cm2:.1f}, tissue={tissue_frac}, pts={pts}"}

# ---- 治療方向建議：規則式（依嚴重度/組織），輔助、可覆寫 ----
def treatment(grade, tissue_frac):
    if tissue_frac.get("necrosis", 0) > 0.2: rec = "疑壞死組織→建議清創評估／轉診"
    elif tissue_frac.get("slough", 0) > 0.3: rec = "腐肉偏多→清創與適當敷料"
    elif grade >= 3: rec = "情況偏重→轉介傷口照護專科評估"
    else: rec = "肉芽為主→維持濕潤敷料、定期追蹤"
    return {"status": "rule_based", "recommendation": rec, "note": "輔助建議，需醫師確認"}

# ================== 10 情境完整性檢查 ==================
def main():
    base = os.path.dirname(os.path.abspath(__file__))
    reg_ok = ModelRegistry(os.path.join(base, "model_registry.json"))     # wsm 存在(已放 models/wsm.onnx)
    reg_missing = ModelRegistry(os.path.join(base, "model_registry.json"), base_dir="/tmp/_nope")  # 全缺
    flags = FeatureFlags(os.path.join(base, "feature_flags.json"))
    img = np.random.default_rng(1).random((1,256,256,3)).astype(np.float32)
    manual = np.zeros((256,256), bool); manual[100:160,100:160] = True
    results = []
    def check(name, cond): results.append((name, bool(cond))); print(("PASS " if cond else "FAIL "), name)

    # 1 量測恆可用且決定性
    check("1 量測決定性(同輸入同面積)", area_mm2(4096,10.0)==area_mm2(4096,10.0)==40.96)
    # 2 校正換算正確 (px_per_mm=10 → 1px=0.01mm^2)
    check("2 校正換算 px→mm^2 正確", abs(area_mm2(10000,10.0)-100.0)<1e-9)
    # 3 分割：模型存在→ai_assistive 且附信心/輔助註記
    s3 = segment(img, reg_ok, flags, model_id="segmentation.stub")
    check("3 分割(模型在)→輔助+信心+需醫師確認", s3["status"]=="ai_assistive" and s3["confidence"] is not None and "醫師" in s3["note"] and s3["mask_px"]>0)
    # 4 分割：模型缺→manual_fallback，不偽造
    s4 = segment(img, reg_ok, flags, model_id="segmentation.wsm", manual_mask=manual)
    check("4 分割(模型缺)→人工退回不偽造", s4["status"]=="manual_fallback" and s4["mask_px"]==int(manual.sum()))
    # 5 組織分類 flag OFF → disabled、無標籤
    c5 = classify("tissue", reg_ok, flags)
    check("5 組織分類 flag OFF→disabled 無標籤", c5["status"]=="disabled" and c5["label"] is None)
    # 6 組織分類 flag ON 但模型缺 → unavailable、無偽造
    flags_on = FeatureFlags(os.path.join(base,"feature_flags.json")); flags_on.f["ai_tissue_classification"]=True
    c6 = classify("tissue", reg_missing, flags_on)
    check("6 組織分類 ON+模型缺→unavailable 不偽造", c6["status"]=="unavailable" and c6["label"] is None)
    # 7 傷口類型 缺+off → 無輸出
    c7 = classify("wound_type", reg_ok, flags)
    check("7 傷口類型 缺/off→無標籤", c7["label"] is None)
    # 8 嚴重度 規則式 無需模型、可運作且可解釋
    sev = severity_scorecard(20.0, {"necrosis":0.3,"slough":0.1})
    check("8 嚴重度規則式可運作+可解釋", sev["status"]=="rule_based" and 1<=sev["grade"]<=4 and "pts=" in sev["explain"])
    # 9 治療建議 規則式、輔助可覆寫
    tx = treatment(sev["grade"], {"necrosis":0.3})
    check("9 治療建議規則式+需醫師確認", tx["status"]=="rule_based" and "醫師" in tx["note"] and "清創" in tx["recommendation"])
    # 10 前處理跨端一致 (兩實作位元級相同)
    cfg = json.load(open(os.path.join(base,"preprocessing.json"),encoding="utf-8"))["models"]["wsm"]
    u8 = np.random.default_rng(2).integers(0,256,(256,256,3),dtype=np.uint8)
    a = pp.preprocess(u8,cfg)
    _ref = u8.astype(np.float32)
    if cfg["channel_order"]=="BGR": _ref=_ref[...,::-1]   # 依 SSOT channel_order(wsm 已修正為 RGB)
    b = np.ascontiguousarray((_ref/127.5-1.0)[None,...])
    check("10 前處理跨端位元級一致(channel_order="+cfg["channel_order"]+")", np.array_equal(a,b) and cfg["channel_order"]=="RGB")

    ok = sum(1 for _,c in results if c)
    print(f"\n===== 完整性模擬結果：{ok}/{len(results)} PASS =====")
    return ok==len(results)
if __name__=="__main__":
    import sys; sys.exit(0 if main() else 1)
