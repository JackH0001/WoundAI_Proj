# 系統輸出資料夾與重啟驗證 — 2026-10-14

接續 [確認上傳時同步輸出](mmh_submission_export_validation_20261014.md)。本輪補足本機系統文件供應商的操作證據，修正 iOS 舊名稱介面測試，沒有更動雲端、商店版本或真機資料。

## Android

在英文 Pixel_7／sdk_gphone16k_arm64 參考模擬器，以真正的 DocumentsUI 選取 primary:Documents，取得持續讀寫授權。測試透過實際 PrivateExportSettings 與 PrivateExports API 建立合成副本、交付修訂資料夾、逐檔讀回並核對 SHA-256。

兩階段用分開的 instrumentation 程序執行，中間以 adb am force-stop 停止 App：第一階段1/1通過；第二階段1/1通過。重新建立 store 後仍讀到相同 tree URI 與持續授權，能讀回先前檔案及再交付另一份；撤銷 SAF 授權後 deliver 必須拋錯，加密私密副本仍可讀。僅刪除本測試產生的獨立檔案／資料夾，還原測試設定。

测试类：PrivateExportFolderSystemTest。此系統 UI 測試限定乾淨的參考模擬器、無既有自訂資料夾，並使用英文 DocumentsUI；不是對所有廠牌／語言／第三方供應商的驗收。先跑 a_selectFolderAndDeliverVerifiedBytes，再停止 App 程序，最後跑 b_afterProcessRestartReadbackThenRevoke。不可將兩階段在同一程序執行的結果稱為程序重啟驗收。

## iOS

在 iOS 26.5 模擬器使用系統「檔案」選擇器，選取「我的 iPhone」內的本機資料夾。testSelectLocalFolderForRestartValidation：1/1通過，包含真正的 App terminate／launch 後設定仍存在。

接著另一次 xcodebuild hosted test 執行：InstitutionExportTests＋InstitutionPrivateExportsTests 11/11通過、沒有略過。testPersistedSystemFolderDeliversAfterProcessRestart 使用前次系統選擇器保存的 bookmark，解析與取得 security scope，實際呼叫 deliver，再逐檔讀回比較內容；清除本次合成檔及測試選項。若未先執行系統選取測試，此單項會明確 skipped，不能算作文件供應商驗收。

新增實際 BackendClient.annotationPayload encoder 對照：輸出保留實際送件 payload、原生遮罩及已保存分數；混用影像、個案、輪廓或組織比例時拒絕產生副本。這不以手工拼的 HTTP 成功 fixture 代替真實 encoder。

iOS 既有三項入口／設定 UI 回歸亦重跑；修正輸出開關名稱、系統選擇器記住前次位置時的取消導航，以及持續保存的模擬器開關前置狀態。初次失敗與修正後結果分開保留，未放寬 Google／WoundAI 的實際登入門檻。最終3/3通過；結果見同目錄 ios-ui-verified.log。

## 範圍限制

- 證明本機系統供應商授權、App 程序重啟及可讀回的內容；未證明真機第三方供應商、雲端同步服務、離線供應商或作業系統重新開機情境。
- 原始感測器全解析度影像、PACS、自訂資料夾獨立持久工作佇列不在本輪完成範圍。研究工作已有自己的持久待傳與保存回執；行政副本成功不能替代研究保存成功。
- 未重新宣稱工程111/111或JVM106/106為本輪新實測；這些是前一輪證據。本輪主要新增系統文件供應商與 encoder／介面驗證。

## 發布來源唯讀核對

2026-10-14 GitHub 讀回：公開庫 main = 85775e8613b8a4680f8df2d0eca2e292dd383fd9；母庫 main = 410687e5f543fc5fe8fb987a569e2d52b478b345。公開庫 PR #24 仍 OPEN／Draft，head 57c6d0bfc296364b47999d713a11e828f14673af，mergeStateStatus=DIRTY。新功能仍在本機 dc7165c 基底加凍結補丁鏈，不在已安裝／已發布的 TestFlight33／Play26 中。

後續必須完成來源整合、Windows 後端覆核與提交、新 client/backend 成對候選部署、實機完整送件讀回，再更新商店。不能直接把 PR #24 的舊綠燈或兩個 main 的存在當成最新版發布證據。

證據目錄：woundai-institution-evidence-20261007/mmh-iap-update-20261011/output-folder-system-validation-20261014。保留補丁與前置 SHA、來源重建比對、兩階段 Android 與 iOS 日誌、GitHub 唯讀結果與 SHA256SUMS。
