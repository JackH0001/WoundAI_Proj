# WoundAILite 原生購買橋接與逐帳號啟用（2026-10-27）

狀態：本機實作／合成驗證，維持 draft。未部署、未推送本輪來源、未建立商品、未購買、未更新商店或實機；原紀錄與稽核不變。
基準：`af2e7ef84c92278d4c58129116c598de470ce690`。Claude 來稿為使用者貼上的覆核內容；未聲稱已取得 Windows 報告檔。

## 本輪前後差異

| 項目 | 修正前 | 修正後／限制 |
|---|---|---|
| 批次外與新帳號 | 執行器記憶體名單決定所有服務放行 | 逐帳號耐久狀態、已啟用政策、完成稽核與新鮮觀察判定；每批100仍只是作業上限 |
| 多批次 | 第二個 executor 覆寫共用 callbacks／guard | 各批私有控制 reader，共用獨立 runtime；第二批暫停不影響第一批 |
| 新註冊 | 安裝映射存在但沒有穩定免費資料 | 受信任 `provision()` 以 create-only 引導免費資料；未完成可沿同計畫續接，不重設帳本 |
| 100帳號方案 | 完整方案會超過16 KiB狀態列 | 緊湊摘要＋逐項不可覆寫稽核；不提高安全大小上限、不遺失原方案 |
| 原生購買 | 舊 simulator lab 不傳服務 token | 新 opt-in StoreKit provider 由服務回傳固定 UUID，傳 `appAccountToken`；帳號不可由請求自行選擇 |
| 恢復與回覆遺失 | 沒有原生至後端橋接 | 先保存 context／交易，後端驗證與關聯完成後保存 ack，再 `finish()`；同交易重試不另建額度 |
| 同帳號兩裝置競爭 | 首次測試發現保留計畫綁不同裝置而衝突 | 保存首次計畫的原安裝，後續須核對同 subject/token 並重用原計畫，不轉移帳號 |
| 隔離部署 | 舊映像不含商業依賴 | 購買模組只在可信注入時載入；打包檢查精確識別 default-None guard，既有映像仍可獨立啟動 |

## 身分與授權邊界

`ServiceAccounts.provision()` 是可信註冊介面，**不是公開註冊 API**。
App Attest 只證明安裝；不以它當人類登入，不接受客戶端自選 subject、token、tier、originalTransactionId。
仍須先有經驗證的服務帳號與裝置映射。人類登入提供者、換機復原與解除關聯的正式整合未在本輪完成；不能直接對公眾啟用此組件。

`POST /api/v1/lite/purchase/context` 與 `/confirm` 只有在 `install_lite_attest(...,commerce=...,purchases=...)` 以相同 registry 注入時註冊。
使用既有 App Attest 規範化簽章、防重放與請求限流。context 為15分鐘；過期 context 不可開始新購買，但可核對已待核准／尚未完成的同筆交易。
confirm 只收 operation 和有上限的 Apple JWS；驗證簽章、bundle、environment、商品、PURCHASED ownership、token 與交易欄位，再用 Server API 檢查目前關聯。
同一 original 只能關聯同帳號。完成回執是歷史證據，**不是付費權益或額度**；真正的新辨識仍需當時有效權益。

iOS `LitePurchaseBridge` 使用既有加密保護／CAS journal；交易的不同 JWS 簽署時間可重播，但交易語意欄位必須相同，保留第一份證據。已確認交易不因恢復而另確認／扣次。
`LiteNativePurchaseView` 是需明確注入的 Sandbox 組合畫面，未接入正式 tab。預設 StoreKit adapter 僅接受 Sandbox；Xcode 環境只能由 Debug simulator 初始化。HTTP client 不接受 Xcode 憑證。

## 額度方案（仍未收費上線）

免費＋廣告：每日成功辨識上限2次。Plus 候選月費 US$1.99：每日成功辨識總上限10次，不是再加10次，也不是10美元。
當天免費用2次後升級，剩8次；相同服務帳號跨裝置共享帳本。退款、到期、恢復購買不清零已用量。
修邊、標註同步、讀舊結果及恢復購買不另扣成功辨識次數。失敗嘗試／準備操作另有限流；同一操作重試不重跑模型或重扣。
研究同意、固定收案、撤回與訂閱互不代替；購買路徑不寫研究同意或研究媒體。

## 直接穩定格式上線與遷移

依既有文件，商業服務尚未啟用；**這不是現行雲端資料庫確定沒有 v1 資料的證據**。本輪未做雲端盤點。
正式切換前需受限唯讀盤點所有已設定的 v1 命名空間、精確版本／世代及 writer 狀態；不能以缺少單一帳號資料判定全庫空白。
若真實盤點確定空白、舊 writer 已停並排空，才以獨立核准簽章 `activate-fresh` 啟用穩定格式，免匯入；有任何 v1 資料就沿顯式遷移。
遷移執行器保留給未來政策／商品更換；100為每次作業上限，沒有100位使用者上限。每批獨立 namespace、簽章、進度、觀察及失敗續接。
原始映射、舊關聯、每日帳本、回執不刪除；失敗停止該批新工作，保留 reader。不得回退到會忽略穩定關聯的舊 reader。

## Observer 正式部署規格（尚未建立）

- 有效簽章觀察TTL仍為30秒，不改成永久快取。runtime現在從耐久記錄讀取，沒有假稱快取／高可用已完成。
- 部署前至少兩個獨立排程／實例、相同受限觀察者角色，以有世代保護的寫入發布，建議每10秒；簽署金鑰與核准者分開。
- 同時維護政策啟用的來源 namespace 與仍在使用的批次 namespace。新批次不能覆寫第一批的觀察紀錄。
- 設定觀察 age>20秒、有效紀錄缺少／驗簽錯誤、503比率、writer未排空告警；告警路由須先演練。上述數字為工程部署目標，不是已實測SLA。
- 暫時讀不到或TTL過期：只拒絕新操作，保留原操作及資料；provider恢復後重新讀取即可放行，不把整個系統永久 paused。
- 觀察值矛盾（錯來源／映像、writer重啟、inventory變動）：停止推進並調查，不能延長TTL掩蓋。
- 正式提供者、IAM、跨區或雲端故障演練仍需獨立授權。不能把合成SQLite／fake GCS當作正式GCS驗收。

## Apple Sandbox 接續

見 `docs/lite_apple_sandbox_acceptance_20261027.md` S0–S9。現有商品、群組、API key、可信帳號登入、固定候選映像與裝置組建須先核對，另行取得部署／更新授權。
本輪本機 StoreKit 會實際呼叫 Product.purchase(options:)，但交易由Xcode測試環境產生；後端驗證使用獨立合成Apple憑證。兩者都不是 Apple Sandbox 成功。

參照：
- Apple `appAccountToken`：https://developer.apple.com/documentation/storekit/product/purchaseoption/appaccounttoken(_:)
- Apple `Transaction.finish`：https://developer.apple.com/documentation/storekit/transaction/finish()
- Apple Sandbox：https://developer.apple.com/documentation/storekit/testing-in-app-purchases-with-sandbox
- 基準PR：https://github.com/JackH0001/WoundAI_Proj/pull/36
