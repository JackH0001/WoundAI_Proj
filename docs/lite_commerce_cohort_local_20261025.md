# WoundAILite 訂閱、廣告與後續固定收案選項

基準來源：`76327e5fbd6eeef7a67e650e621806f71bb98092`。本批僅本機實作，不部署、不公開上架、不更改目前雲端配額。

## 可操作範圍

用 `iOS/project.yml` 重新產生專案，選 `WoundLiteCommerceLab` scheme，於 **iOS 模擬器 Debug** 執行。設定 → 內部驗證 →「訂閱、廣告與固定收案（合成）」。商業與收案程式以 `DEBUG && targetEnvironment(simulator)` 隔離，真實裝置／Release 不包含此入口與程式。

* StoreKit 本機商品 `com.woundai.lite.plus.monthly`，US$1.99／月。商品、價格與週期讀自 `.storekit`；購買、恢復、退款及到期使用 Apple StoreKit Testing。本次不是 App Store Connect 商品或 TestFlight 交易驗收。
* 只接受已驗證且 environment 為 Xcode 的交易，不接受未驗證交易，不授予任何伺服器權益。手動恢復才呼叫 `AppStore.sync()`。
* 合成配額：免費每天 2 次、Plus 每天**共** 10 次。升級沿用當日使用量，不清零；重試與修訂不再次計為辨識。這不是建議每日傷口拍攝頻率。
* 第一方贊助廣告測試版位只出現在方案頁；Plus 不顯示。沒有廣告 SDK、廣告網路請求、追蹤、真實購物連結、傷口定向投放或收益。正式廣告供應商／直接贊助合約尚未設定。
* 合成研究策略：停用（預設）、已同意者全收（比較組）、固定 50%（後續改版選項）。方案與研究同意分開，任何測試購買不會勾選研究同意。

## 固定收案與未收案分流

1. 收案單位採「研究內的穩定使用身分」，所有傷口沿用同一決定，避免只收部分照片造成時間軸斷裂。現有 Lite 只能證明同一安裝／App Attest 身分，**不能宣稱已識別同一自然人或跨重裝、跨裝置去重**。
2. 首次符合條件且同意時建立決定，保留研究 ID、政策版本、是否收案。以 HMAC 的均勻分數做 50% 分流。本機使用公開合成 seed；正式環境必須改為伺服器祕密金鑰、CAS 保存與伺服器判定，不接受手機自報是否收案。
3. 決定一旦保存，換方案、換傷口、再次同意或新政策不會重新抽樣。新政策只適用於尚未決定的合格身分；既有決定保留原政策。
4. **未收案仍可辨識**：辨識服務資格與收案決定分開；結果及完整時間軸可保存在手機，RGB、深度、遮罩不得寫入長期研究庫。未收案不是辨識失敗，也不得誘導重試以重抽。
5. 實際非收案服務路徑仍需工程實作：目前同意者辨識會寫入研究媒體，不能只改 UI。未來需獨立暫存處理，成功／錯誤／斷線都清理；TTL、隔離桶、IAM、備份／軟刪除期限、記錄內容及失敗回復須先固定並驗收。保留必要配額、安全與同意稽核，不記錄原影像、遮罩或深度於一般日誌。未驗收前不得宣稱已實現雲端「不長期留存」。
6. 收案者需保留原 RGB-D、原 AI 遮罩、各次人工修訂與原始檔案參照。固定身分收案只能保障**已傳送資料**的時間軸一致，不能保證使用者持續拍攝、上傳，或每次皆有有效深度。
7. 撤回與重新同意沿用既有後端 fencing/epoch：舊批次不復活，舊上傳、修訂與清理不可跨到新批次。原決定不重抽，新同意只適用之後新拍攝。本機合成清理只刪合成研究陣列，沒有執行 GCS 清理；不能代替正式清理競爭驗收。

## 與目前成品的差異及啟用次序

| 項目 | 目前既有服務 | 本批新增 | 正式啟用尚需 |
|---|---|---|---|
| 辨識額度 | 後端預設成功 5／日，以實際設定為準 | 合成免費 2／Plus 10 | 原子額度保留、成功結算、訂閱服務端驗證 |
| 訂閱 | 無 | 真正 StoreKit 本機測試介面 | ASC 商品與價格、簽署交易驗證、帳戶綁定、退款／到期／伺服器通知、故障時不誤售 |
| 廣告 | 無 | 第一方測試版位及 Plus 隱藏 | 實際廣告供應、素材審查、費用／曝光結算、隱私與商店揭露 |
| 固定收案 | 無 | 合成決定與分流，可測試但預設停用 | IRB／同意核定、後端身分範圍、決定不可重抽、暫存路徑與清理驗收 |
| 研究撤回 | 現有服務的撤回／再同意機制 | 合成 epoch 與原決定持續性測試 | 正式端到端競爭及讀回，不能以合成測試取代 |

研究同意的商業條件尚待核定。這份實作不把購買或觀看廣告當成研究同意，不更改既有使用者權益、撤回狀態或雲端存取政策。

優先完成訂閱／廣告及服務額度；固定收案保留後續選項，不綁定這次上市首版。正式付費不得在伺服器尚不能交付額度時開放。不得以客戶端 `isPaid` 布林值、公開合成金鑰或本機測試回執授予正式額度。

## 覆核與重跑

本輪本機驗證（2026-10-25，Xcode 27.1／iOS 27 模擬器）：

* 新增 10 項測試通過（7 項分流／配額／廣告規則、3 項 StoreKit 生命週期）。10,000 個合成身分固定選入 4,938 個，實際觀察比例 49.38%。
* Release 既有 234 項通過、1 項實機檔案保護測試因模擬器條件略過；不得稱為全部實機驗收。
* Release 連結執行檔未含 `LiteCommerceLab`、`LiteCommerceStore`、`LiteLabCohort` 或 `LiteLabAssignment` 符號；商品設定僅在測試插件，不在 App resources。
* 初次測試有 StoreKit 設定未綁測試計畫、建置目錄附加屬性、購買回執早於權益查詢的問題；已修正，失敗日誌保留。到期情境明確先停止自動續訂，避免把正常續訂誤當成未撤銷權益。
* `.github/workflows/lite-commerce-lab.yml` 為**手動觸發 CI**，本輪未 push／未執行 GitHub CI，避免增加未確認的 macOS runner 費用。不能將本機通過報為遠端 CI 通過。

測試程式：`iOS/WoundLiteTests/LiteCommercePolicyTests.swift`、`LiteCommerceStoreTests.swift`。本機流程及結果另見 evidence report。JSON round-trip、固定決定、10,000 身分分布、未收案時間軸、撤回後重試／修訂、同意更新、配額升降與廣告位置均用合成資料。

舊獨立 Python 草稿 `woundailite-fixed-enrollment-20261025/cohort_reference.py` 尚未完成驗證，本批不使用它作正式實作或通過證據。

參照：
* [Apple：StoreKit Testing](https://developer.apple.com/documentation/xcode/setting-up-storekit-testing-in-xcode)
* [Apple：currentEntitlements](https://developer.apple.com/documentation/storekit/transaction/currententitlements)
* [Apple：交易驗證與完成交易](https://developer.apple.com/documentation/storekit/transaction)
* [Apple：AppStore.sync 必須由使用者操作觸發](https://developer.apple.com/documentation/storekit/appstore/sync())
