# Android 29 待傳建立與兩張影像輸出驗證 — 2026-10-16

## 結論

已重現 Android 28 的範例來源送件失敗，修正後完成 Android 29 r8 建置、保留資料更新 Alldocube 的獨立驗證版及隔離回歸測試。Play 安裝版仍為 28；29 尚未簽署／上架。真實 MMH 登入與保存收據驗收待使用者回報。本輪沒有變更後端、IAP、資料桶或雲端資料。

## 問題與修正

1. 快速量測頁允許 `sample`／`phantom` 送件，但待傳與歷史頁只豁免未歸戶 `phantom`，使 `sample` 在本機入列前被擋。修正前新增整合測試實際得到 `IllegalArgumentException: 紀錄或送件權限已變動，請重新開啟紀錄核對`。現在明確未綁定病患／個案的 sample／phantom 與 iOS 快速量測規則一致，保留原 source；已綁個案仍核對最新照護／訓練同意，external、缺來源及不明來源不因此取得豁免。
2. 共用 BackendClient 在驗證工作階段時暫時將 identity 清空。另一個已通過驗證的送件操作若再讀取這個欄位，可能被誤擋。MedicalSessionPool 現在可回傳該次已驗證的不可變身分快照；研究入列與送件使用它。仍在每次取得工作階段時請求伺服器驗證，保留帳號／環境 generation、最新同意、伺服器角色與保存收據檢查。沒有延長 token 效期、減少稽核或跳過驗證。
3. 拆開「紀錄版本不同」及「登入／同意未通過」錯誤，便於核對。使用者原始 phantom 失敗沒有裝置追蹤，不能宣稱已證明它完全由登入競態造成，仍需新版本真實送件驗收。
4. 保持同一次明確送出所確認的影像、遮罩、面積快照：成功入列後才啟動行政輸出；對外只有 image.jpg 與含測量數據的 overlay.jpg。未成功入列不宣稱有輸出；本機輸出成功也不代表雲端保存完成。

## 本輪實測

| 項目 | 結果 | 限制 |
| --- | --- | --- |
| 修正前 sample 重現 | 1/1 如預期失敗，同使用者錯誤 | 合成 fixture |
| MMH validation 單元測試 | 123/123，0 skipped | macOS Gradle JVM |
| MMH internal 單元測試 | 123/123，0 skipped | macOS Gradle JVM |
| 模擬器待傳／控制器／輸出 | 50/50 | 注入合成 HTTP 回覆，非真實雲端 |
| Alldocube 待傳／控制器／輸出 | 50/50 | 獨立測試資料，未讀寫使用者紀錄 |
| Android 系統資料夾 | 2/2 | 模擬器實際選資料夾、讀回、程序重啟及撤權測試 |
| 跨平台契約檢查 | 未宣告落差 0 | 已登記 Android 29／iOS 原始碼 27 的版號差異；非 UI 全面驗收 |
| 邏輯檢查 | 50/50 | 共用靜態／邏輯檢查 |
| validation APK／internal AAB | 建置成功 | AAB 未簽署 |

控制器測試涵蓋 MMH sample 與 phantom、相同修訂重複入列只送一次、上傳傷口／組織 PNG 與輸出快照相符、area_cm2 一致、新修訂不被舊收據標記、503 重試、錯誤收據拒絕、撤回及換帳號阻擋、資料夾失效時保留待傳及私密副本。新增登入測試確實讓另一個執行緒在重新驗證中清空 client.identity，確認先前取得的身分快照不受影響；後續驗證失敗仍拒絕新取得。

## 產物與後續

- Android 29 r8：com.woundai.app.mmhps20261007.validation；已以保留資料方式安裝。
- 待簽署 AAB SHA-256：`908d2d73a6c4d27d639fdd7cdafd2795d7ad38543a48e697a9e33a3734cd0452`。
- APK SHA-256：`a13dde119ef9676035032eacf1877694482f990d5f25995da1a38caed30c8f3a`。
- 實機驗收：回 MMH，用既有合成紀錄確認送出；回報最終保存訊息與兩張輸出。不必任意改遮罩或刪除紀錄。通過後才完成商店簽署／更新。
- 簽署使用既有 upload key，由使用者自行輸入本機密碼；未讀取或保存該密碼。
- Windows 本輪與 iOS 本輪編譯未執行；本輪沒有更動 iOS 程式。

## 合成示範登入說明

MMH 與合成示範使用獨立帳號、資料與待傳工作。根據最新示範部署紀錄 `demo-session-update/README.md`，示範只完成私有驗收，尚未開放手機入口或交付審查帳密；review01 是驗收帳號，不是已交付的可用密碼。MMH 的 dr01/admin 不會自動通用，請勿重新設定 MMH 密碼來嘗試。後續需完成示範入口與專用帳號交付。本輪沒有更改任何帳號。

## 參照

- `Android/app/src/main/java/com/woundmeasurement/app/pipeline/ClinicalResearchController.kt`
- `Android/app/src/main/java/com/woundmeasurement/app/pipeline/ClinicalAnnotationOutbox.kt`
- `Android/app/src/main/java/com/woundmeasurement/app/pipeline/MedicalSessionPool.kt`
- `Android/app/src/main/java/com/woundmeasurement/app/pipeline/ReviewTrainingConsent.kt`
- `Android/app/src/androidTest/java/com/woundmeasurement/app/pipeline/ClinicalResearchControllerTest.kt`
- 完整測試與產物：`woundai-institution-evidence-20261007/mmh-iap-update-20261011/release-continuation-20261015/android29-upload-fix/`
- 操作簡報：`woundai-user-guides-20261015/output/WoundAI-Android-操作指南-20261016-v7.pptx`，16 頁；沿用未變動介面的模擬器截圖，更新待傳、輸出與示範入口說明，明列 29 候選未上架。
