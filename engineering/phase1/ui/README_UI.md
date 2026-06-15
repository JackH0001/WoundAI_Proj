# 半自動分割修邊編輯器（Phase 1 #1，資料飛輪雛形）
`annotation_editor.html`：載入合成底圖＋AI 初稿遮罩 → 醫師以筆刷修正邊界（畫/擦）→ 送出產生標註紀錄（POST `/annotations`，schema 見 openapi）。**修正即標註**：修正後遮罩＝GT 標籤，`correction_iou` 量化修正幅度。
- `annot_metrics.js`：前端度量（與 `annotation_pipeline.py` 等價）。
- `annot_metrics.test.js` + `gen_expected.py`：node 測試＋與 Python 跨語言一致性比對（CI 用）。
開啟：瀏覽器直接開 `annotation_editor.html`（同目錄需有 `annot_metrics.js`）。
