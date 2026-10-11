# Android 26 保存回覆、等待提示與圖示對齊驗證

日期：2026-10-11。範圍是醫療 Android 的本機候選修正，另更新 iOS 醫療／Lite 圖示。使用者已要求 Mac 端檢查、對齊與改版，並確認兩款圖示移除外圍淺綠區域。未推送、發布、修改雲端 IAM 或部署；不能當成 MMH Android 全流程驗收。

## 修正行為

- `AnnotationReceipt` 要求 HTTP 200、JSON、`enqueued` 或 `duplicate_skipped`、相符的 `code`／`image_id`，以及 16 位小寫十六進位 `annotation_receipt_id`。回覆有 error／issues、未知狀態、錯誤綁定或缺收據都不把本機紀錄標成已送出。舊後台沒有修訂收據也會拒絕確認；這是版本相容性限制，不能把更新 APK 等同更新後端。
- 標註主畫面和時間軸共用等待按鈕；送件中停用並顯示不定進度條，不虛構百分比。AtomicBoolean gate 阻擋重複發送。時間軸送件時停用修邊／返回，系統返回也攔住；成功更新本機標記後停用補送。失敗保留紀錄並允許重試。
- 此版本仍不是持久背景上傳佇列。強制關閉／程序終止不保證續傳；返回其他流程、換帳號與 payload 固定快照仍需更全面的 MMH 對齊驗收。既有重新修邊會將本機狀態改成待送，完全無修改時仍可能再送，後端必須去重。
- 設定頁可收起鍵盤，登入／連線時鎖定欄位和相關按鈕，顯示等待；登出清除本機儲存的登入資料。重新登入先清除该 BackendClient 舊 token。這不是撤銷所有已簽發 token 或其他存活 client 的伺服器登出。
- 医療 Android 與 iOS 使用相同 `IconSources/medical.png`；Lite 更新 `lite.png`。保留舊圖並記錄 SHA-256。圖稿是 imagegen 邊界調整，並非逐像素裁切，匯出時僅縮放／套平台圖示遮罩。
- Android 候選版號 26，iOS 一般醫療 27，獨立 MMH 32。版號並不代表功能等價。

## 本輪結果

| 驗證 | 結果／限制 |
|---|---|
| Android JVM 單元測試 | 31/31；其中保存回覆 23、送件 gate 3、既有計分／組織 5 |
| Pixel_7 AVD Android 17 UI | 2/2，0 skipped：等待期間阻擋點擊、失敗恢復重試、已保存停用 |
| Debug APK | 建置成功，versionCode 26；僅供模擬資料測試 |
| internalTest APK | 建置成功，**unsigned**；不是已簽署可發布的 MMH 版 |
| 16 KB | arm64 6/6 原生庫與 APK zip 對齊通過 |
| 圖示匯出 | 18/18 尺寸與 alpha 檢查、2/2 圖稿 SHA-256 相符；尚未安裝 iOS 新圖示 |
| verify_logic | 50/50 |
| 後端網址 guard | 7/7；Android release/internalTest 仍沿用原服務 |
| parity | 未宣告落差 0；10 項已宣告差異仍存在 |
| parity 回應／請求邊界回歸 | 4/4，包含新請求欄位仍報錯、遺失解析邊界拒絕 |

UI 首兩次未執行到按鈕斷言，原因為舊 Espresso 反射呼叫 `InputManager.getInstance` 在此模擬器失敗。採 Espresso 3.7.0／AndroidX test JUnit 1.3.0 後，同一組測試通過；非略過測試。[官方修正紀錄](https://developer.android.com/jetpack/androidx/releases/test#espresso_370)

parity 原先把 iOS `annotationReceipt` 中的 switch 狀態誤讀成上傳欄位。現限定 `annotationPayload` 範圍；未把假欄位加進例外清單。

證據在 repo 外：`../woundai-institution-evidence-20261007/android-alignment-20261011/summary.json`，附單元／UI XML、建置記錄及 APK 雜湊。初次合併建置記錄包含已說明的舊測試框架失敗，最終 UI 結果以 `android-ui-final-20261011.log` 與 XML 為準。

## 操作說明與剩餘差異

按送出後，看到「正在確認標註保存」就留在頁面等待；只有有效保存回覆且本機標記成功才算完成。如果顯示未確認，保留本機紀錄、核對後台版本後重試，不以重新修邊作為繞過方式。

Android 尚未實作／驗收 MMH Google 原生登入與 IAP、持久加密研究 outbox、原始深度續傳、和 iOS 同等的私有輸出預設。Android 沒有已啟用的深度採集，仍誠實送 `depth_source=none`；不得宣稱為完整 RGB-D。結果縮放與歷史圖像操作也未於本輪交付。需要另外完成 Android 簽署／OAuth 與實機合成資料驗收，才可視為 MMH 專用版。

本輪尚未做 Windows 驗證、Android 真機相機／雲端端到端驗收、iOS 新 icon 建置／安裝、雲端上傳端到端驗收或上架提交。


## Alldocube iPlay 60 mini Pro 實機補驗

使用者接上實機後，讀回 Android 14／API 34、page size 4096。原有 `com.woundai.app` 是 1.0-internal (25)，此次保留原套件及資料；另外安裝 `com.woundai.app.debug` 1.0-debug (26)。Debug 顯示名稱改為「WoundAI 驗證版」，不把它當已簽署的機構發布版。

實機 instrumentation **11/11，0 skipped**：

- 等待／防重複點擊與已保存停用 UI：2。
- `BackendClient.submitAnnotation` 實際 OkHttp 呼叫 loopback 假服務：6。有效保存／去重通過；HTTP 200 登入 HTML、影像不符、舊版未綁定回覆、503 搭配成功 JSON 均拒絕。此測試不走雲端，不使用真實憑證。
- Room 舊版升級保留資料與新建資料庫：3。使用 Debug 沙箱內獨立 `migration_test.db`，不讀寫原應用病歷庫。

建置過程找到 generated 目錄的六個 `* 2.java` 重複檔案，五個與原檔逐位元一致，另一個是殘留 versionCode 25 的 BuildConfig。已移除一致的衍生複本、將舊 BuildConfig 保存在 repo 外證據目錄，未刪來源或使用者資料；之後重建成功。僅能確定存在重複產物，未證明是哪個同步程式造成。

本輪沒有驗證實機拍攝面積精度、Google/IAP、雲端 image/mask/depth 保存或正式簽章更新。平板是 4 KB 裝置；APK 的 16 KB 靜態檢查不能當這台實機運行於 16 KB 的證據。

證據：`../woundai-institution-evidence-20261007/android-alignment-20261011/iplay60/`。

最終 Debug 名稱資源重建及安裝成功，ADB 讀回 1.0-debug (26)。Activity 啟動成功，但仍停在 Android 相機權限提示；尚未完成首頁／相機操作驗收。11 項測試後僅變更 Debug 顯示名稱資源，未變更上傳邏輯。最終 APK SHA-256 與安裝摘要見 `iplay60/summary.json`。

## 同日後續：MMH Android 原生登入

後續已新增獨立 `mmhValidation` 建置、固定 MMH 候選網址與 Google/IAP 入口檢查，使用者已回報 Google 與 WoundAI 兩層登入成功。雲端 Android 專用 health 探測 HTTP 200 亦已讀回。先前「尚未接入 Google」敘述保留為前輪狀態；最新範圍、51 項 JVM／16 項一般實機／2 項 MMH 設定頁測試，以及仍待雲端上傳驗收的限制見 [Android MMH 入口驗證](android_mmh_entry_validation_20261011.md)。
