# 內部訂閱驗收的價格與交易恢復

在先前的程式中，購買頁只提示以 Apple 畫面為準。合成服務的確認回覆遺失後，再按一次訂閱，測試中的購買方法會被呼叫兩次。另一個案例在同一安裝使用不同的服務帳號恢復購買，舊的已完成回執會讓畫面誤顯示已確認。這兩項均可在父提交的原生購買流程重現，測試沒有產生真實帳單。

## 使用者看見的方案

候選購買頁從 StoreKit 讀取每月自動續訂商品，顯示名稱和當地完整價格。免費每日 2 次及 Plus 每日共 10 次都按服務端 UTC 日期結算。升級保留已用次數。在「設定 → 服務登入、訂閱與刪帳 → Plus 方案與價格」可先查看月費。完成裝置登記後，若尚未登入，方案頁會提供服務帳號登入入口。有效登入或可續期的原帳號才顯示購買及恢復操作，送出時仍由服務重新核對。商品報價無法取得時，已登入者仍可恢復既有購買。本階段只驗證無首購優惠的單月商品。本輪將同時追蹤最多 5 處傷口納入原 Plus 月訂閱。免費可追蹤 1 處。每一處保有獨立時間軸；結束追蹤後可換另一處，舊紀錄仍可查看及匯出 PDF。辨識次數由同帳戶的傷口合計。

付款前重新比對商品顯示名稱、價格、幣別及商店。報價不一致時先顯示新價格並等待再次點選。交易由 Apple 確認，帳號與可用額度由後端核對。購買頁另提供 Apple 訂閱管理連結。提交外部測試與公開上市前，需依核准的正式內容完成 App 內及商店頁的使用條款、隱私政策連結與雙語揭露核對。專案的隱私政策仍是供審閱的草稿。[Apple 訂閱說明](https://developer.apple.com/app-store/subscriptions/) · [隱私說明草稿](../site/privacy/woundlite.html)

### 11 月 2 日外測準備

App Store Connect 的 WoundAILite「App 資訊」顯示 Apple 標準許可協議。下一版從設定和訂閱頁可開啟該協議及離線雙語隱私說明，首次研究選擇畫面也可先閱讀隱私說明。閱讀頁不會登記裝置、購買或改寫同意。兩種 iOS 組態都把同一份 `site/privacy/woundlite.html` 加入 App；以 Git 檔案快照建置時，須同時包含 `iOS/` 與該 HTML。這些修改在已簽署的 build 40 之後。

正式送件前仍須由負責方核定內容並發布最新公開 URL，讀回繁中與英文內容，再核對 Apple 的隱私問卷及 TestFlight 資料。當日唯讀核對顯示，繁中 App 隱私權政策 URL 尚未填寫，既有公開頁仍是 8 月版。Plus 商品顯示「準備提交」，尚須用實際 Sandbox 裝置向 StoreKit 取得商品，才能確認測試者能讀取當地價格。不得以價格表、離線政策頁或模擬交易代替該項驗收。[Apple 測試資訊](https://developer.apple.com/help/app-store-connect/test-a-beta-version/provide-test-information/) · [Sandbox 商品疑難排解](https://developer.apple.com/documentation/technotes/tn3186-troubleshooting-in-app-purchases-availability-in-the-sandbox)

## 重試與帳號歸屬

* 使用者再次按訂閱時，程式先查 StoreKit 的既有權益及未完成交易。如果找到既有交易，沿用原交易核對。只有明確按恢復才呼叫 `AppStore.sync()`。
* 恢復時先檢查整批交易的帳號 token、商品與環境。已有本機回執時，還要核對當次服務帳號雜湊。回放已收到雲端確認但尚未完成 StoreKit 處理的交易時，後端以原操作編號查核存檔。
* 原生客戶端可在受 App Attest 保護的 `purchase/context` 送出 `mode=existing`，要求只讀既有操作。未提供此欄位沿用原協定。缺少既有操作回 409。交易確認也只接受既有操作，重讀不會建立缺少的資料列。新舊前後端配對應在候選部署時核對。

## Apple 簽章中的帳號代碼

購買交易、伺服器查得的交易及寬限期續訂資訊，都以同一個服務帳號 UUID 核對歸屬。Apple 將這個值定義為 UUID，標準十六進位表示容許大小寫。服務端在驗簽後用原帳號的小寫 UUID 比對，保留連字號位置及所有數位。原始簽章、帳號資料及既有回執以原格式保存。[Apple 的帳號代碼定義](https://developer.apple.com/documentation/appstoreserverapi/appaccounttoken) · [RFC 9562 第 4 節](https://www.rfc-editor.org/rfc/rfc9562.html#section-4)

合成簽章重播分別改變上述三處的字母大小寫。先前這三種情形都拒絕原帳號的購買確認。修正後三種皆可確認並重讀同一份回執。當日已用兩次時，升級後剩八次。不同帳號、連字號缺失及不符的續訂歸屬仍被拒絕。這項相容性有本機驗證；Apple Sandbox 真實交易的表示方式與結果仍待實機核對。

## 覆核入口

1. `LitePurchaseBridgeTests` 包含變價、同價跨商店、連按、目錄不可用、回覆遺失、不同 token、不同帳號雜湊、混合帳號批次及暫停後完成交易。
2. `LiteNativeStoreKitTests` 使用 Xcode 的本機月訂閱設定。查價後交易為零；完成一次購買、遺失確認後再按訂閱、恢復購買後，仍只有一筆交易，交易的 token 與服務端核發值相同。本機交易沒有實際扣款。
3. `engineering/phase2/test_lite_native_purchase.py` 透過帶簽章的測試請求檢查既有操作回放、缺少操作、錯誤模式及權限邊界。另用 `tools/lite_native_purchase_probe.py` 核對由免費升級後保留已用 2 次、第 11 次被拒及原帳號恢復。

真實 Apple Sandbox、帳號註冊、外測簽署、核准的商品與條款、裝置和雲端回執仍需依 [S0–S9](lite_apple_sandbox_acceptance_20261027.md) 驗收。原生付款的本機測試與實際對帳各自記錄。
