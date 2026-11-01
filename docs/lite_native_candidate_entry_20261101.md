# WoundAILite 內部服務驗收版

使用 XcodeGen 的獨立設定檔建立內部測試版。一般版與內部版沿用同一 App 識別碼。一般版不編入候選入口，單改網址不會顯示該入口。大量測試均使用合成資料。

## 本機建置

在 iOS 目錄執行 `xcodegen generate --spec project.lite-sandbox.yml`。使用產生的 `WoundAILiteNativeSandbox.xcodeproj` 及 `WoundLite` scheme。在受控的建置設定填入 `WOUNDLITE_NATIVE_SANDBOX_ORIGIN`，值必須符合 `LiteNativeCandidateBinding.address`。本次地址是規劃給獨立候選服務的名稱，仍待部署核對。裝置建置缺少或誤填網址會停止。一般版從 `project.yml` 產生。

研究與個人辨識使用不同的服務網址、裝置登記範圍與本機工作紀錄。啟動候選版時只準備本機元件。首次從「設定 → 服務登入、訂閱與刪帳」進入，先讀取這一安裝儲存的登記狀態。全新或尚未完成登記時顯示手動登記。遇到保護鎖或資料異常時只提供重讀。已登記的裝置才顯示帳號、刪帳與訂閱入口。Apple 登入從目前操作視窗發起。

從紀錄檢視先前的辨識結果只會讀本機。使用者另選個人雲端辨識時，僅送選取的 JPEG。原傷口標註、深度、面積及研究同意保留在原流程。內部版的隱私資訊另列服務所用的使用者識別碼與購買紀錄。

## 可重做的本機核對

1. 在一般版及指定網址的內部版各執行一次 Release 的 `WoundLiteTests`。其中的服務往返使用合成回覆，記錄模型執行、購買、恢復及研究路徑的呼叫次數。
2. 在全新模擬器狀態執行 `WoundLiteNativeEntry` scheme，確認進入設定後顯示裝置登記。原本會把尚未登記誤當成需要查核刪帳，現在可先完成登記。模擬器無法證明實機 App Attest 登記成功。
3. 清空測試組態的網址並設定 `WOUNDLITE_UI_EXPECTATION=LITE_QA_MISSING_BINDING`，確認設定頁顯示缺少設定。本機量測紀錄仍可查看。一般組態可將同一 UI 測試目標加入獨立測試專案，測試執行器設定 `LITE_QA_ORDINARY`，確認提供候選網址也不顯示入口。
4. 如已在專用模擬器建立歷史面積驗收所用的三筆合成紀錄，可加上測試執行器參數 `WOUNDLITE_UI_EXPECTATION=LITE_QA_SAVED_FIXTURE`，從原紀錄確認登記前可查看本機資料，送出按鈕停用。該參數只選擇測試案例。

## 簽署與實機門檻

送到 TestFlight 前，需核對同一個 App ID 的 distribution profile 同時允許 Sign in with Apple 與 production App Attest，再核對實際簽章、指定的候選服務、商品、測試帳號與新版號。服務接受的組建號須與實際簽署檔一致。未簽署的實機架構建置只能用於檢查打包內容。通過前置條件後，以同一合成照片依序驗收免費額度、購買及恢復、跨裝置帳號隔離與到期處理。[S0–S9 操作及讀回條件](lite_apple_sandbox_acceptance_20261027.md)
