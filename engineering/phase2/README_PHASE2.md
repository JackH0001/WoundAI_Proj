# Phase 2（實作）— Track A1+A2

- `seg_infer.py`：分割推論轉接層。串接 FeatureFlags + ModelRegistry + 前處理 SSOT。
  - **graceful degrade**：旗標關閉→`disabled`；缺模型→`model_unavailable`；兩者皆回 `mask=None`，**絕不偽造**。
  - 有模型→產 AI 初稿遮罩 + confidence；`draft_to_ui_json()` 轉成修邊 UI 可載入格式（對應 OpenAPI `/segment`）。
- `annotation_editor.html`（A2）：新增 `window.loadDraft({w,h,data,confidence})`，可吃後端 `/segment` 的真實初稿取代合成方塊；離線時維持合成 demo。
- `test_phase2.py`：17 項（旗標關閉/缺模型不偽造、stub 真實推論、256×256、confidence∈[0,1]、決定性、UI 橋接 JSON），已納入 CI。

> 註：真實權重（wsm.onnx / fusegnet.onnx）不進協作 repo；CI 以 `wsm_stub.onnx` 驗證推論路徑。生產環境由部署時拉取私有模型。
