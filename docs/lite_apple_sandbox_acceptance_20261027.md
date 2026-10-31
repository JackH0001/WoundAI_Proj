# WoundAILite Apple Sandbox 驗收步驟（準備版，尚未執行）

目的：把目前合成簽章／本機 StoreKit 的證據，接到真實 Apple Sandbox 的
購買、續訂、寬限期及帳號關聯驗證。這不是部署指令，也不表示可公開上市。
本輪沒有建立商品、登入測試帳號、購買、修改 Apple 設定或更新 TestFlight。

## 2026-10-29 原生組合入口補充

本機候選已有共用 `LiteNativeSession` 組合入口，將帳號、購買／恢復、服務辨識
與刪帳串接到可注入的設定／既有紀錄頁。詳見
[本機組合與驗證範圍](lite_native_session_20261029.md)。預設啟動仍不注入，
所以現有 App／TestFlight 沒有自動開啟新功能。合成 HTTP 回覆與本機原生測試
不等於 Apple Sandbox；本文件 S0–S9 仍待真實候選後端、簽署與裝置驗收。
先用原有合成照片驗收，不要求重拍或清除原紀錄／研究同意。

## 2026-10-28 接線準備補充

原生 Apple 登入、安裝映射與訂閱 token 的候選橋接已完成本機合成回歸，詳見
[lite_account_identity_bridge_20261028.md](lite_account_identity_bridge_20261028.md)。
這仍不等於真實登入完成：後續已新增 JWKS／撤銷、續期及原生候選 UI 的本機
測試，但完整刪帳、正式組合與部署接線仍待驗證。下列 S0–S9 目前仍未執行，
不需使用者先購買。
S0 必須另確認登入取消、重啟、登出與晚回、同人第二安裝、不同人不得繼承資料，
並確認研究同意未被登入或訂閱變更。登入 Apple ID 與 StoreKit Sandbox 的測試
交易環境是不同機制，不得把合成 Apple JWT 稱為 Sandbox 登入證據。

## 開始前的必要條件

2026-10-28 provider 補充：公開公鑰取得／快取與簽章帳號事件的本機候選及
合成驗收見 [lite_apple_provider_validation_20261028.md](lite_apple_provider_validation_20261028.md)。
尚未註冊真實 Apple 通知 URL，亦未啟用 App 的 Sign in with Apple entitlement。
新增 S0 前置項：先核對 entitlement／profile／primary App ID 與 audience，再
驗證公鑰過期／輪替、跨裝置撤銷、重複及晚到通知；重啟後仍須保留阻擋水位。
這些前置設定與真實事件傳送都另需限定方案，不能拿合成事件或唯讀公鑰 GET
當成 S0 通過。研究撤回保持獨立；Apple account-deleted 不得顯示成研究資料已刪除。

1. 原生橋接現已在 `LitePurchaseBridge.swift`、`LiteStoreKitPurchaseProvider.swift`
   與 `lite_native_purchase.py` 完成本機實作／測試；舊 `LiteCommerceStore.swift`
   仍僅供實驗。新組件尚未接入正式 tab 或啟動設定，不能拿現有 TestFlight
   畫面當雲端付費驗收。須先完成可信服務帳號登入／裝置映射的正式接線，
   購買由伺服器提供固定 UUID，後端核對相同 token、bundle、環境與商品。
   Apple 的 appAccountToken 不等於 Apple ID 或 App Attest 安裝識別。
   [Apple appAccountToken](https://developer.apple.com/documentation/appstoreserverapi/appaccounttoken)
   詳見 `lite_native_purchase_bridge_20261027.md` 的逐帳號啟用與保留證據規則。
2. 唯讀盤點既有 App Store Connect 項目、商品、訂閱群組、帳號與 Server API
   金鑰權限。不要推定目前已有正式商品。缺項須先列具體變動讓使用者核准；
   不把合成 product ID 建成正式商品，也不因測試建立重複商品。
3. 準備獨立、限定 Sandbox 的候選後端，固定來源／映像、商品白名單與帳號
   名單，拒絕 Production 交易；關閉模型呼叫以外的研究留存，先用合成圖。
   需新增雲端設定、端點、費用或 TestFlight 組建時，另提出限定授權。
4. 先完成本文所述原生橋接與候選後端，才提供可安裝的固定組建。保存原紀錄，
   不刪 App、不清 Keychain、不撤回原研究資料來重建測試身分。
5. 由使用者在裝置自行登入專用 Sandbox 測試帳號；密碼與驗證碼不貼對話。
   如測試需要切換「媒體與購買項目」，先確認使用者同意並優先使用專用測試
   裝置，不要求登出整個 iCloud 帳號。

## Apple 時間設定與操作路徑

TestFlight App 自動使用 Sandbox。一般 TestFlight 訂閱目前每天續訂一次，
一週最多六次；測試不同速度、帳務重試或寬限期需使用 Sandbox Apple Account。
Apple 說明的切換路徑是「媒體與購買項目」登出正式帳號後，在 Developer 設定
登入 Sandbox 帳號。實際操作前再次核對當時 iOS 畫面。
[Apple TestFlight 測試說明](https://developer.apple.com/help/app-store-connect/test-a-beta-version/testing-subscriptions-and-in-app-purchases-in-testflight)

工程建議先用月訂閱＝30分鐘的速度，留足讀回與對照時間。App Store Connect
→ Users and Access → Sandbox → 專用帳號 → Subscription Renewal Rate；此速度下
月訂閱的帳務重試為1小時、寬限期為30分鐘。不要沿用一般 TestFlight 的一天
當作此帳號的期限。保留購買歷史；Apple 的 Clear Purchase History 無法復原，
本驗收不使用該功能。[Apple Sandbox 帳號設定](https://developer.apple.com/help/app-store-connect/test-in-app-purchases/manage-sandbox-apple-account-settings/)

寬限期需在 App 的 Subscriptions → Billing Grace Period 設為 **Only Sandbox
Environment**，由有權限者核准後操作，不能選 Production and Sandbox。
變更可能延後生效；以已簽署的實際期限及狀態為準，不硬編碼等待分鐘。
[Apple 寬限期設定](https://developer.apple.com/help/app-store-connect/manage-subscriptions/enable-billing-grace-period-for-auto-renewable-subscriptions)

## 使用者操作與工程讀回對照

下表是前置條件完成後的逐項驗收單；目前不用在現有 App 嘗試。
每項先讀回成功再接下一項，照片使用同一份合成標靶即可。

| 步驟 | 使用者操作 | 工程必須核對的通過條件 |
|---|---|---|
| S0 身分與保存 | 開指定測試組建，確認原紀錄仍在 | 新舊 service subject、appAccountToken、安裝關聯符合核准清單；不同帳號不得共用訂閱 |
| S1 免費基準 | 同一合成圖做兩次獨立辨識；再次開既有結果 | 免費2次共用帳本；讀舊結果不扣次數、不呼叫 Apple、不再跑模型；同意設定未變 |
| S2 購買 | 按測試月訂閱，核對測試環境後確認；取消則回報取消 | 真實 Sandbox JWS／Server API 驗證完成才給 Plus；每日總額10，先前已用2仍保留，不能變成另加10 |
| S3 回應遺失 | 由工程在限定測試代理丟棄一次回應，再按重試 | 同一 operation/result；模型與扣額度各一次；購買關聯不能重複建立 |
| S4 同帳號復原 | 在同帳號的第二個驗收安裝按恢復購買 | originalTransactionId 與 token 不變、共享已用量；不同服務帳號恢復必須拒絕，不自動轉移 |
| S5 續訂 | 等待一個加速續訂週期，重新查看權益 | 新 transaction、同一 original；不是新使用者、不清零當日已用量 |
| S6 寬限期 | 依 Apple Sandbox 控制模擬付款失敗；再恢復付款 | 狀態4需有效 renewal/transaction、相同環境／商品／original，且 gracePeriodExpiresDate 未過期；有效才保留10 |
| S7 重試／到期 | 讓寬限期結束或關閉續訂後等到期 | 已驗證無付費權益則回免費2；若當日已用超過2，餘額為0，舊結果仍可看；驗證暫不可用時顯示等待，不冒充已過期 |
| S8 中斷／待核准／退款 | 分別用 Apple 可用控制測試；無法觸發的項目明列未測 | 未完成交易不先給額度；退款讀回已驗證狀態後不重置已用；不以本機改狀態代替真實證據 |
| S9 政策遷移與回復 | 工程執行另經核准的限定帳號切換；使用者查看舊結果 | 準備、暫停、續接、啟用各有回執；停新辨識時舊結果仍可讀；不回復舊寫入程式 |

付款失敗控制依 Apple 最新 Sandbox 說明；無法產生某個狀態時標成未驗證，
不能把合成狀態4測試當作此項通過。
[Apple 失敗續訂與購買測試](https://developer.apple.com/documentation/storekit/testing-failing-subscription-renewals-and-in-app-purchases)

S9 若涉及商品改變，須先確認 Apple 的實際商品轉換及有效交易；資料庫政策
遷移不會替使用者購買、升級或換商品。沒有已驗證的新商品交易即停止遷移。

## 保存與停止條件

每項記錄組建／來源／映像／UTC時間、匿名帳號雜湊、operation/result雜湊、
used/pending前後值、模型呼叫數、Apple查詢數、簽章驗證結果及稽核回執。
完整真實 JWS、original transaction、token、金鑰及登入資訊只留受限證據區，
Claude／公開 Git 覆核只提供遮蔽索引與雜湊，不公開完整憑證。

若帳號混用、環境錯誤、資料讀回不一致、重複扣次或無法確認暫停，立刻停止
新工作並保存證據。不得清空資料、重設帳本或反覆購買來讓結果看似通過。
待所有已選項完成及未測項處置後，再評估是否提出部署／商店驗收授權。
