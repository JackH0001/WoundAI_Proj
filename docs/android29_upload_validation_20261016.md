# Android 29 待傳建立與兩張影像輸出驗證 — 2026-10-16

## 結論

已重現 Android 28 的範例來源送件失敗，修正後完成 Android 29 r9 建置、保留資料更新 Alldocube 的獨立驗證版及隔離回歸測試。Play 安裝版仍為 28；29 尚未簽署／上架。使用者回報新拍模擬圖成功，並確認舊兩筆失敗應是在 Play 28；尚未核對該成功筆的客戶端版本與雲端收據。舊紀錄須在 Play 原 App 保留資料更新後再驗收。本輪沒有變更後端、IAP、資料桶或雲端資料。

## 問題與修正

1. 快速量測頁允許 `sample`／`phantom` 送件，但待傳與歷史頁只豁免未歸戶 `phantom`，使 `sample` 在本機入列前被擋。修正前新增整合測試實際得到 `IllegalArgumentException: 紀錄或送件權限已變動，請重新開啟紀錄核對`。現在明確未綁定病患／個案的 sample／phantom 與 iOS 快速量測規則一致，保留原 source；已綁個案仍核對最新照護／訓練同意，external、缺來源及不明來源不因此取得豁免。
2. 共用 BackendClient 在驗證工作階段時暫時將 identity 清空。另一個已通過驗證的送件操作若再讀取這個欄位，可能被誤擋。MedicalSessionPool 現在可回傳該次已驗證的不可變身分快照；研究入列與送件使用它。仍在每次取得工作階段時請求伺服器驗證，保留帳號／環境 generation、最新同意、伺服器角色與保存收據檢查。沒有延長 token 效期、減少稽核或跳過驗證。
3. 拆開「紀錄版本不同」及「登入／同意未通過」錯誤，便於核對。使用者原始 phantom 失敗沒有裝置追蹤，不能宣稱已證明它完全由登入競態造成，仍需新版本真實送件驗收。
4. 保持同一次明確送出所確認的影像、遮罩、面積快照：成功入列後才啟動行政輸出；對外只有 image.jpg 與含測量數據的 overlay.jpg。未成功入列不宣稱有輸出；本機輸出成功也不代表雲端保存完成。

## 對焦提示與舊紀錄狀態

- CameraX 預覽支援點選對焦：黃色縮放圓表示正在對焦；只有已知可調焦鏡頭且 CameraX 回報成功才顯示綠色圓與確認文字。固定焦距／不支援／失敗均不顯示成功，6 秒無結果會解鎖並提示重試。成功提示 2.5 秒後清除，避免移動後仍沿用舊確認。
- 重複點選以 request generation 排除過期回覆；離開畫面取消要求、解除本畫面相機綁定。拖曳與雙指不被誤當單點對焦。對焦中暫停快門，未確認不代表能判定影像品質；仍須人工檢視。
- 六項對焦單元測試涵蓋成功、失敗、固定焦距、舊回覆、新要求及離開取消。模擬器實際 CameraX 預覽點選測試通過，未拍照或上傳；不代表 Alldocube 真實鏡頭對焦與光學品質已驗收。
- 使用者本輪確認舊兩筆「應該是 WoundAI MMHPS 28」。獨立 validation App 無法讀取 Play App 私有紀錄；本輪未讀、搬移或刪除那兩筆。先更新相同 Play App 再讀回原紀錄驗證，不要求任意重畫遮罩、不補造缺失的來源或原始組織基準。
- 初次相機測試編譯缺少 GrantPermissionRule 相依，改用既有 InstrumentationRegistry 的模擬器專用權限設定後完整重建通過，未新增正式版相依。

## 本輪實測

| 項目 | 結果 | 限制 |
| --- | --- | --- |
| 修正前 sample 重現 | 1/1 如預期失敗，同使用者錯誤 | 合成 fixture |
| MMH validation 單元測試 | 129/129，0 skipped | macOS Gradle JVM |
| MMH internal 單元測試 | 129/129，0 skipped | macOS Gradle JVM |
| 模擬器相機／待傳／控制器／輸出 | 51/51 | 相機實際預覽與點選；HTTP 為合成回覆，非真實雲端 |
| Alldocube 待傳／控制器／輸出 | 50/50 | 獨立測試資料，未讀寫使用者紀錄 |
| Android 系統資料夾 | 2/2 | 模擬器實際選資料夾、讀回、程序重啟及撤權測試 |
| 跨平台契約檢查 | 未宣告落差 0 | 已登記 Android 29／iOS 原始碼 27 的版號差異；非 UI 全面驗收 |
| 邏輯檢查 | 50/50 | 共用靜態／邏輯檢查 |
| validation APK／internal AAB | 建置成功 | AAB 未簽署 |

控制器測試涵蓋 MMH sample 與 phantom、相同修訂重複入列只送一次、上傳傷口／組織 PNG 與輸出快照相符、area_cm2 一致、新修訂不被舊收據標記、503 重試、錯誤收據拒絕、撤回及換帳號阻擋、資料夾失效時保留待傳及私密副本。新增登入測試確實讓另一個執行緒在重新驗證中清空 client.identity，確認先前取得的身分快照不受影響；後續驗證失敗仍拒絕新取得。

## 產物與後續

- Android 29 r9：com.woundai.app.mmhps20261007.validation；已以保留資料方式安裝。
- 待簽署 AAB SHA-256：`51d4d2a38914a476b522652665b79995ca8197ddcb987a6e4ab4106c5757d054`。
- APK SHA-256：`7fb7c6b26ff8b70e076da50327c10d1c337e0ccf97370bec94de7a739775b55d`。
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
- 操作簡報：`woundai-user-guides-20261015/output/WoundAI-Android-操作指南-20261016-v8.pptx`，16 頁；沿用未變動介面的模擬器截圖，更新待傳、輸出、對焦與示範入口說明，明列 29 候選未上架。

- r9 測試、固定 AAB、簽署工具：`release-continuation-20261015/android29-focus-r9/`；先前 r8 產物保留，請勿再以舊簽署腳本準備發布。

## 簽署工具封裝修補

2026-10-16 使用者回報已簽署後，實際檔案檢查未找到 r9 已簽署 AAB 或完成收據。r9 封裝漏帶 VerifyMmhBundle.java，會使最終驗證失敗而不發布輸出。已補齊工具並在詢問密碼前加入依賴檢查；未改 AAB、upload key 或密碼。簽署工具 17/17 測試通過，含一次性測試金鑰的實際簽署、錯誤簽章、篡改、未簽項目及缺檔不詢問密碼；使用 r9 工具讀回既有 28 正式上傳檔，1024 個項目全由預期憑證簽署。仍待使用者重新簽署 r9，不能將工具測試當成本次發布簽署成功。

ADB 讀回：獨立驗證套件 29／1.0-mmh-validation-r9，Play 套件 28／1.0-mmh-internal。已開啟獨立驗證版；未覆寫 Play 或刪除舊紀錄。
