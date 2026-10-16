# Android 29 待傳建立與兩張影像輸出驗證 — 2026-10-16

## 結論

已重現並修正 Android 28 的範例來源送件失敗。後續使用者截圖指出 r9 設定頁仍顯示 r7／build 28；檢查確認 APK 及商店 AAB 的已編譯程式含舊版號字串，雖然 manifest 已是 29。已停止發布 r9，改為全新輸出目錄、停用建置快取及 Kotlin 增量編譯的 r10，設定頁直接讀取已安裝套件版本。平板已保留資料更新至 r10，並直接核對畫面顯示 build 29。Play 安裝版仍為 28，29 尚未發布。r9 簽章有效但產物已排除發布；r10 需重新簽署及真實送件驗收。本輪沒有變更後端、IAP、資料桶或雲端資料。

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

## r9 歷史實測（不是 r10 驗收結果）

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

2026-10-16 使用者回報已簽署後，實際檔案檢查未找到 r9 已簽署 AAB 或完成收據。r9 封裝漏帶 VerifyMmhBundle.java，會使最終驗證失敗而不發布輸出。已補齊工具並在詢問密碼前加入依賴檢查；未改 AAB、upload key 或密碼。簽署工具 17/17 測試通過，含一次性測試金鑰的實際簽署、錯誤簽章、篡改、未簽項目及缺檔不詢問密碼；使用 r9 工具讀回既有 28 正式上傳檔，1024 個項目全由預期憑證簽署。使用者其後完成簽署，獨立讀回 1024 個 payload 項目皆由預期 upload 憑證簽署，且與未簽署內容一致。但後續發現舊版號殘留，該 r9 簽署產物只保留為證據，不發布。

ADB 讀回：獨立驗證套件 29／1.0-mmh-validation-r9，Play 套件 28／1.0-mmh-internal。已開啟獨立驗證版；未覆寫 Play 或刪除舊紀錄。


## r10 版號修正與乾淨建置

- 實際證據：r9 validation DEX 含 `App 1.0-mmh-validation-r7 (build 28)`，r9 商店 AAB DEX 含 `App 1.0-mmh-internal (build 28)`；manifest／產生的 BuildConfig 則為 29。這證明編譯產物與版本設定不一致；增量編譯殘留是可能原因，尚未以最小案例獨立證明快取失效機制。
- 設定頁改由 PackageManager 讀取執行中套件的 versionName／versionCode，避免使用已內嵌的版本常數。新增畫面測試另以反射核對 BuildConfig 與 PackageManager，避免測試也內嵌同一錯誤常數而假通過。
- 本次採全新 buildDirectory、`--no-build-cache`、`-PverifiedReleaseBuild=true`（關閉 Kotlin incremental）。APK 與 AAB 均未找到兩個舊版號 banner；兩者都有新的版本讀取函式。
- MMH validation 與 internal 單元測試各 **129/129**，0 skipped；模擬器版號／相機／送件／輸出 **52/52**。平板送件／輸出 **50/50**；第一次新增畫面測試受到裝置既有登入流程干擾，未取得 Compose hierarchy，失敗紀錄保留，不隱匿為通過。
- 實際開啟平板 App 設定，畫面與 UI hierarchy 均顯示 `1.0-mmh-validation-r10 (build 29)`，與 PackageManager 一致。另將畫面測試改為獨立偏好設定，避免觸發使用者 Google／WoundAI 登入。
- 以上送件／輸出測試使用隔離的合成資料與 HTTP 回覆，不等於 r10 真實雲端已驗收；真實相機光學對焦、Play 舊兩筆紀錄仍待實機核對。
- r10 未簽署 AAB SHA-256：`a3b06752724e116e5a07c5dd4a2974fecf43a18568991c5ab5210a0c7abd079f`。
- 簽署封裝含 Python 工具與 Java 驗證器，已通過輸入完整性與工具預檢，未索取密碼。新產物需要使用者用既有 upload key 重新簽署。
- 證據：`release-continuation-20261015/android29-version-r10/`（建置、測試紀錄、平板設定截圖、SHA-256、簽署工具）；r9 目錄新增 `DO-NOT-PUBLISH.txt`。

隔離後的版號畫面測試：平板 **1/1**、模擬器 **1/1** 通過。測試保留 ActivityResultRegistryOwner，僅替換測試偏好設定以避免讀取真實登入資料；中間隔離測試的註冊介面錯誤與編譯修正紀錄也保留。正式 APK／AAB 未因測試修正改變。平板合計 50 項送件／輸出及 1 項版號檢查通過；不將初次失敗寫成一次 51/51 通過。
