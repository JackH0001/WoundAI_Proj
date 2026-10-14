# Android MMH 登入回饋及修訂溯源候選驗證 — 2026-10-11

## 已確認與本輪範圍

使用者已在 r4（26）完成 Google／WoundAI 登入並返回設定驗證，無需重填密碼；仍感受到等待。此為使用者實機回報。平板保持 r4；本文件後述改動只在本機候選與模擬器驗證，未推送、未部署、未更新平板。

候選把登入驗證完成後的健康度／佇列等待改為「已登入，正在讀取雲端狀態」。附屬狀態讀取失敗不再抹除已成功的登入；Google 入口到期仍使狀態失效。沒有量測實機等待秒數，也未證明延遲由冷啟動造成。

## 已修正的資料路徑

- 資料庫 v6→v7 新增 nullable `annotationProvenance`，新量測保存原始模型、品質指標與送件備註；時間軸續送沿用。舊紀錄保持 unknown，不填造歷史來源。
- 原始組織分類基準另存在既有加密 PNG 資產的第二個平面，資料庫只記載格式旗標，不把組織像素寫入明文 metadata。舊 PNG 仍可解碼。
- 修邊續編保留已保存尺度及分類基準。內容完全未變時（精確像素及座標比較）回傳原快照，保留原輪廓、修改量、備註及送件狀態；不靠近似 IoU 判定相同。
- 既有分類基準未知且發生實際修改時，標示修改量未知並擋送，不把重新分類的結果假冒歷史基準。缺原始模型／品質溯源的舊紀錄，尚需保存收據核對與復原後才能補送。
- 修訂資產若無法保存，維持原筆紀錄；不把新輪廓與舊組織快照混在一起。

## 本輪實際驗證

| 項目 | 結果 |
| --- | --- |
| MMH APK 與測試 APK 編譯 | 通過 |
| JVM：InstitutionSession 20、AnnotationReceipt 23、ReviewTrainingConsent 9、AnnotationProvenance 4 | 56／56 |
| 模擬器：Migration 4、RasterProvenance 4、BackendSignInActions 4、ReviewControls 4 | 16／16 |
| v6 資料庫升級 | 病患／量測關聯、時間、面積、醫師確認、送件旗標及柵格路徑保留；新欄位 null |
| PNG 基準與旧快照 | 往返像素／計數保留；旗標錯配及畫布錯配拒絕 |
| 編輯器無修改完成 | 保留原快照實例、歷史修改量、尺度及面積 |

初輪模擬器 15 項中 1 項失敗：極小遮罩在輪廓追蹤前未先處理無修改路徑，未回傳完成事件。已改為在輪廓簡化前辨識原樣內容；重跑通過，再加入登入狀態測試後最終 16／16 通過。

## 尚未驗收

這不是雲端重送去重的全面通過報告。舊紀錄缺少原始溯源的安全復原、實際筆劃／撤銷與 ROI 擴張的完整互動回歸、平板端新版本及雲端同筆請求驗收尚待完成。沒有修改雲端歷史資料，也沒有把 Android 無深度裝置宣稱為 RGB-D 驗收通過。候選尚沿用 r4 建置識別，正式提供平板更新前須另定版號，不能與已安裝 r4 混用。

## 參照

- [本輪摘要與 APK 雜湊](/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-institution-evidence-20261007/mmh-iap-update-20261011/android-provenance-candidate/summary.json)
- [模擬器測試原始結果](/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-institution-evidence-20261007/mmh-iap-update-20261011/android-provenance-candidate/emulator-tests.log)
- [編譯結果](/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-institution-evidence-20261007/mmh-iap-update-20261011/android-provenance-candidate/build.log)
- [r4 登入驗收](android_mmh_login_state_validation_20261011.md)
- [r3 實際雲端修訂核對](android_mmh_review_ui_validation_20261011.md)
