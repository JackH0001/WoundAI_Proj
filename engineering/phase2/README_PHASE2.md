# Phase 2（實作）— Track A1+A2

- `seg_infer.py`：分割推論轉接層。串接 FeatureFlags + ModelRegistry + 前處理 SSOT。
  - **graceful degrade**：旗標關閉→`disabled`；缺模型→`model_unavailable`；兩者皆回 `mask=None`，**絕不偽造**。
  - 有模型→產 AI 初稿遮罩 + confidence；`draft_to_ui_json()` 轉成修邊 UI 可載入格式（對應 OpenAPI `/segment`）。
- `annotation_editor.html`（A2）：新增 `window.loadDraft({w,h,data,confidence})`，可吃後端 `/segment` 的真實初稿取代合成方塊；離線時維持合成 demo。
- `test_phase2.py`：17 項（旗標關閉/缺模型不偽造、stub 真實推論、256×256、confidence∈[0,1]、決定性、UI 橋接 JSON），已納入 CI。

> 註：真實權重（wsm.onnx / fusegnet.onnx）不進協作 repo；CI 以 `wsm_stub.onnx` 驗證推論路徑。生產環境由部署時拉取私有模型。

## Track A3+A4（新增）
- `inference_router.py`（A3）：邊緣優先（on-device wsm）→ 缺則雲端（fusegnet）→ 再缺則 stub；`confidence < min_confidence` 標記 `needs_review`。策略集中於 `routing_policy.json`（SSOT）。
- `pipeline_flywheel.py`（A4）：端到端 影像→路由初稿→醫師修邊→標註紀錄→再訓練佇列(jsonl)；以 `eval_harness.seg_metrics` 量初稿 vs 醫師 GT 的 Dice/IoU（修邊前品質），並記錄 `correction_iou`（修邊幅度）。無模型時 graceful：`ai_available=False`、僅記可靠幾何。
- `test_phase2_pipeline.py`：18 項（路由選擇/信心門檻/無模型回退、飛輪紀錄/佇列累加/graceful），已納入 CI。佇列 `*.jsonl` 為執行期資料，不進 repo。

## Track B（分類缺口務實補法，無黑箱模型）
- `wound_classifier.py`（B3）：以分割遮罩內的**色彩啟發式組織比例**（壞死/腐肉/肉芽，可解釋）+ 幾何，複用 `clinical_rules` 產規則式組織分型、嚴重度與治療建議。色彩組織為**粗估**，輸出標示信心 heuristic-low、需醫師確認；面積未校正則 `area_cm2=None`。B1 規則式為唯一啟用路徑，AI 分類旗標維持 OFF。
- `demo_render.py`：目視驗證渲染（原圖｜分割疊圖｜組織分型疊圖｜規則式結論）。
- `test_wound_classifier.py`：12 項（組織色彩分型、規則式嚴重度/治療、未校正標記、決定性），已納入 CI。
> 實例圖（真實傷口）：見 WoundAI 資料夾 `WoundAI_TrackB_實例驗證.png`（不進協作 repo）。

## Track A 收尾：參考服務層（讓 OpenAPI 契約可執行）
- `api_service.py`：框架無關地實作 `/segment`、`/annotations`、`/annotation-tasks`，接 router + 標註管線；維持 graceful degrade（ai_assistive / manual_fallback / unavailable→503）。遮罩以 PNG base64 傳遞。
- `test_api_service.py`：14 項，回應**對 OpenAPI 元件 schema 驗證**（含 nullable 正規化）+ 缺模型/旗標關閉行為，已納入 CI。

## 可執行服務
- `app.py`：Flask 包裝 `api_service`（`/segment` multipart、`/annotations` JSON、`/annotation-tasks`）。啟動 `python app.py`；生產可換 FastAPI + 認證。
- `test_app.py`：10 項整合測試（Flask test_client，不開真實 port），驗證 HTTP 狀態碼 + OpenAPI schema + 缺參數 400。已納入 CI。

## Track A 量測核心：校正貼紙 → 精確面積（cm²）
- `calibration.py`：貼紙 20mm（方形/圓形）→ px/mm。
  - **assisted（可靠、推薦）**：`calibrate_from_bbox`（框選貼紙）/`calibrate_from_two_points`（兩點已知距離）→ 精確 px/mm。
  - **auto（best-effort）**：`detect_color_corner_sticker`（R/B/G/Y 四角點，cv<0.05 才採信）/`detect_circle_sticker`（Hough）；雜亂照片不保證命中，低信心 → found=False、建議改 assisted（不偽造）。
  - `classify(..., px_per_mm=ppm)` 即輸出真實 `area_cm2`。
- `test_calibration.py`：9 項（assisted 精確值、合成貼紙自動偵測、缺貼紙 graceful），已納入 CI。
> 誠實：雜亂真實照片之全自動偵測尚不穩定（範例中 Bedsore_02 自動會誤鎖），故實例圖採 assisted 框選貼紙計算 cm²；這也是臨床上最務實可靠的方式。

## Track A 量測核心：透視校正（homography）
- `geometry.py`：以校正貼紙四角（影像座標，已知 20mm 方形）建 image→metric 單應，將傷口遮罩 warp 到正視 metric 平面再計面積 → **消除相機傾斜/貼紙非正視造成的面積偏差**。
  - `homography_image_to_metric()` / `measure_area_cm2()` / `measure_area_cm2_from_quad()`。
  - **以貼紙外框四角＝20mm 為唯一比例尺**，解決棋盤「每格 mm」格數歧義。
- `test_geometry.py`：5 項（平面面積、傾斜校正後回復真值、naive 對照、空遮罩、決定性），已納入 CI。
