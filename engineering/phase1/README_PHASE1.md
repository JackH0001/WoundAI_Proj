# Phase 1 骨架（半自動分割資料飛輪 + 規則式臨床決策）
- `annotation_pipeline.py`：AI 初稿＋醫師修邊 → 標註紀錄（修邊即標註；correction_iou 量化修正幅度）→ 進訓練佇列。
- `clinical_rules.py` + `rules_params.json`：規則式嚴重度分數卡與治療方向建議（可解釋、臨床可調、無需模型）。
- `test_phase1.py`：自測（CI 用）。
- API 契約見 `openapi/annotation_segmentation.yaml`（/segment、/annotations、/annotation-tasks）。
原則：分割/分類為輔助、附信心、可被醫師覆寫；修正回饋成訓練資料。
