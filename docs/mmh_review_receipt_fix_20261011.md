# MMH 31：已送件紀錄仍亮起補送按鈕（2026-10-11）

## 結論

這是 iOS 29 的狀態 bug，不是使用者操作錯誤。先前 52 項單元測試覆蓋上傳佇列，但未覆蓋時間軸導航快照，以及無修改再次存檔會清除回執的路徑。

兩個原因：

1. 時間軸保存一份 Measurement 快照，ReviewView 進入時直接使用；雲端成功後 SQLite 中的 annotationSubmitted 雖更新，導航快照可能仍為 false。onReceive 也無法保證覆蓋進頁初始化的全部時序。
2. MeasureViewModel.saveToTimeline 更新既有紀錄時無條件把 annotationSubmitted 設為 false，並重新產生深度 sidecar 的 captured_at。無內容變更也會變成可補送。

## 雲端證據（台北時間）

- 01:18:26：annotation_enqueued；01:18:34：depth_stored 首次。
- 01:21:52：annotation_duplicate，該次重按未新增相同標註。
- 01:22:01：depth_stored；後端確實再次收到深度，判定中繼資料或深度變更。
- 比對相同影像前後 depth_index：raw SHA-256 完全相同，metadata SHA-256 不同。原始幾何位元沒有變更。
- 先前讀回的 captured_at 為 01:17:50，重送後變成 01:19:58。這符合再次存檔時重建 sidecar 的實作；不能把重送說成完全無副作用，確實用了傳輸資源並改寫中繼資料。
- 01:26 另有一筆新送件，本報告不把那筆混作前述重複事件。

本輪只讀 MMH 桶及稽核事件，未刪除、回填或改寫雲端資料。

## 修正

- 時間軸點擊及詳情載入重讀 SQLite；詳情送出動作再讀一次，已確認或已刪除紀錄不排入新工作。
- 已確認時按鈕停用，顯示「標註已保存，無需補送」及一般完成色彩。
- 新的 ClinicalReviewRecord 在進頁及送件邊界都核對真值；若畫面遮罩與最新紀錄不同，先重載供使用者確認，不混用舊遮罩。
- 保存既有紀錄時比較影像綁定、個案、輪廓、尺寸、面積、滲液、組織、醫師確認及柵格內容。未修改則保留成功旗標；真的修改才清除舊回執。
- 原影像及原始深度／參數相同時沿用原本的影像、深度 sidecar 及上傳旗標，避免只因存檔時間變化產生新深度版本。比較中僅忽略舊格式 sidecar 的儲存時間 captured_at；不忽略原始深度位元、尺寸、內參或方向變更。

這不等同已修正「感測器拍攝時間」的完整來源契約；旧 captured_at 仍來自首次 sidecar 產生時間。也不會憑最新回執覆蓋已改過遮罩的紀錄。

## 驗證

- MMH 1.0(31) Debug：57/57 單元測試、2/2 UI 測試通過。
- 新增 5 項回歸測試：進頁前已確認、進頁後才確認、未送／不存在紀錄、無修改保存與臨床欄位變更、sidecar 時間與幾何變更的區分。
- 實機建置成功、codesign 通過；6 個本輪 iOS 異動檔與建置目錄位元組一致。
- owner_guard mac 與 git diff --check 通過。
- 30 為本輪第一階段修正版；最終版本為 31，請以設定的版本號辨識。
- 31 的「進時間軸後保持停用」及「無修改再存仍保持成功」仍需使用者實機確認；單元測試與雲端核對不能代替這一步。

證據目錄：`../woundai-institution-evidence-20261007/mmh-iap-enable-20261010/`

- review-repeat-audit-20261011.json
- review-depth-repeat-comparison.json
- review-receipt-tests-build31.log
- review-receipt-device-build31.log
- review-receipt-build31-source.json

原始深度仍為 registration=not_verified；本次修正不代表 3D 配準或臨床訓練資格驗收，也未推送／合併公開庫或母庫。
