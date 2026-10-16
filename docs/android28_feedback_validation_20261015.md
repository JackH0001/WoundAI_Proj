# Android Play 27 回報修正：快速量測、送件狀態與修邊手勢

日期：2026-10-15。基準來源 `0dcfb1e01e0c81366e6e829d163f3dbb65933347`。
本輪為 Android 28 / 驗證版 r6 候選；商店目前仍為 27。未更動後端、IAM、研究物件或商店版既有紀錄。

## 問題判定

1. **快速量測 UI 尚未對齊**：Android 的 SamplePickerScreen 仍有「模擬驗證／檢錯」、端上／後端開關、範例／模擬圖選擇和固定「拍照」。此次移除這些工程控制，只保留模擬圖開關，已有影像時顯示「重新拍照」。內部來源欄位仍保留；分析後先鎖定來源，避免把同一筆結果事後改標成不同來源。
2. **重複確認與保存競態**：前景按鈕只追蹤本機入列，背景送件尚在進行時就重新亮起。第二次確認若遇到雲端收據已把本機紀錄標為已送出，原先 require 會拋出 `Failed requirement.`，因而同時出現「已保存」和「失敗」。新版本依同一筆、同一修訂的待傳快照停用重複確認，保留待傳面板的重試入口；去重先識別既有工作，不因收據旗標已更新而誤報；失敗前重新核對保存狀態。
3. **修邊抖動與誤畫**：畫布上方提示高度隨遮罩內容變化，使畫布尺寸與座標換算在筆刷下改變；雙指同時平移／縮放的錨點計算也有偏差。第一指若先擴大 ROI，第二指進入時舊快照無法還原，會留下誤畫。此次固定提示區行數、修正錨點，並讓復原快照包含 ROI 幾何、原始邊界與自動組織基準。

這些缺口在 Play 27 的來源中存在，不能單憑畫面判定裝了舊版。主要是 Android 實作及 UI 對齊未完成。
iOS 來源已具備重新拍照、單一模擬圖開關、待傳鎖定、固定提示高度與縮放錨點／ROI 復原保護；本輪沒有重跑 iOS 實機，不能推論兩端所有手勢均已驗收。

## 實際驗證

| 項目 | 結果 |
| --- | --- |
| Android 驗證版 28/r6 建置 | 成功；已保留資料安裝至獨立 validation 套件 |
| 驗證版單元測試 | 117/117，無失敗或略過 |
| Play 內測組態 28 建置與單元測試 | AAB 成功，117/117；尚未上傳金鑰簽署 |
| Alldocube 實機合成回歸 | 32/32 |
| 兩端手冊一致性 | 107/107 |
| 契約對齊 | 未宣告落差 0；已登記的平台深度能力與版本差異保留 |
| Mac 所有權檢查、diff 空白檢查 | 通過 |

平板 32 項包含既有待傳安全測試、保存收據驗證、同筆再次確認去重、修改後新修訂不被誤擋、雙指誤畫回復、ROI 擴張復原、畫布位置穩定、快速量測控制和按鈕等待。
首輪 32 項有 1 項因測試在 runOnIdle 內再呼叫同步查詢而失敗；已修正測試呼叫方式，第二輪 32/32 通過。首輪與末輪紀錄均保留。

## 驗收界線與下一步

- 截圖的該筆雲端物件未在本輪重新讀回；上述結果證明本機競態原因及修正，不代表已替該筆完成雲端完整性驗收。
- 本輪未增加或重送臨床／研究資料；實機測試採合成資料與隔離套件。
- 新版 Play 28 尚需使用既有 upload key 簽署、上傳內測，再核對 Play 簽章安裝版的 Google／WoundAI 登入、送件收據及兩張行政輸出。
- 第一次 Google 登入需按第二次的問題尚未完成重現與驗收；不列為已修好。
- 契約檢查不是全介面等同保證。簡報截圖應於新版 UI 定稿後同步更新。

## 程式參照

- `Android/app/src/main/java/com/woundmeasurement/app/pipeline/SamplePickerScreen.kt`
- `Android/app/src/main/java/com/woundmeasurement/app/pipeline/MeasureValidationEntry.kt`
- `Android/app/src/main/java/com/woundmeasurement/app/pipeline/MeasureViewModel.kt`
- `Android/app/src/main/java/com/woundmeasurement/app/pipeline/ClinicalAnnotationOutbox.kt`
- `Android/app/src/main/java/com/woundmeasurement/app/pipeline/ClinicalResearchController.kt`
- `Android/app/src/main/java/com/woundmeasurement/app/pipeline/MeasurementReviewScreen.kt`
- `Android/app/src/main/java/com/woundmeasurement/app/pipeline/WoundEditScreen.kt`
- `Android/app/src/androidTest/java/com/woundmeasurement/app/pipeline/EditorInteractionRegressionTest.kt`
- `Android/app/src/androidTest/java/com/woundmeasurement/app/pipeline/QuickCaptureControlsTest.kt`
- `Android/app/src/androidTest/java/com/woundmeasurement/app/pipeline/ClinicalAnnotationOutboxTest.kt`
- `iOS/WoundMeasurementApp/UI/MeasureFlowView.swift`
- `iOS/WoundMeasurementApp/UI/WoundEditView.swift`
