# Claude 覆核交接：逐帳號 runtime 與原生購買橋接

基準 `af2e7ef`；承接使用者貼上的 Claude 2026-10-27 §2、§4 覆核。
本輪維持 draft，不合併、不部署、不更新商店、不建立商品或實際購買。

## 可重跑結果

- 完整 Lite Python：1,047項（前輪1,008，新增39）。
- prelaunch workflow本機逐步重播：1,015項（前輪976，新增39）；與完整Lite大幅重疊，**不可相加**。
- 新增：runtime16、購買後端22、打包guard1。原executor39項與probe仍通過。
- iOS Release simulator：300通過、0失敗、1既有實機檔案保護測試略過（前輪279；新增21）。
- 新StoreKit本機測試1通過：使用真正Product.purchase與appAccountToken，Xcode本機簽章，非Apple Sandbox；服務側為合成替身。
- 本輪遠端CI尚未觸發；不要沿用af2e7ef的9run／10job宣稱新來源成功。
- 醫療／Android本輪沒有新程式行為，不重新宣稱其先前數字是本輪實測。

## 修正映射及建議覆核點

1. `lite_subscription_runtime.py`：耐久逐帳號關卡，政策啟用、完成稽核、新鮮observer；不存在記憶體batch白名單。可信provision以create-only建立免費穩定資料。
   - `test_lite_subscription_runtime`：205帳號三批（100/100/5）、後註冊、批次暫停隔離、重啟讀取、claim稽核失敗續接。
   - `SubscriptionMigrationExecutor` 使用私有links／registry回呼，不覆寫其他executor與runtime。
   - 緊湊manifest header及每筆entry稽核避免100帳號超過單列16KiB；不要放大安全上限。
2. `lite_native_purchase.py`：只從已驗證安裝讀可信服務mapping；不得由客戶端指定token、subject、tier。
   - JWS官方驗簽＋Server API current權益／ownership；`/context`與`/confirm`沿App Attest簽章／防重放。
   - 同original跨裝置共用首次保留計畫，仍核對當前與原映射屬同一subject/token。
   - 交易ack不含quota／paidflag，重播不重設帳本；回應遺失／完成稽核遺失不另建關聯。
   - 後端22測試含真實Flask安全gate＋合成App Attest／Apple簽章；研究撤回狀態不被購買修改。
3. `LitePurchaseBridge`／`LiteStoreKitPurchaseProvider`／`LiteNativePurchaseView`：可信context先落盤再購買；事件與ack按序保留，服務確認後才finish；待核准、取消、錯帳號、斷線提示。
   - 同交易重新簽署JWS不視為新交易；語意欄位必須相同，保留首份證據。
   - HTTP固定origin／環境／路由；Xcode資料不能送真Sandbox HTTP。正式UI尚未接線。
4. 新format直接上線：僅合成`activate-fresh`已測；真實雲端v1空盤點未執行。任何舊資料必須顯式遷移，不能自動當free。
5. observer HA與告警已寫入正式部署規格，但未建服務／監控；短TTL保留。有效觀察暫失只拒新工作，恢復讀取後可放行。

## 探針前後與參照

舊版executor `af2e7ef`：同一合成fixture批次啟用後，新帳號仍拒絕（基準執行器檔與結果在覆核包）。
新版 `tools/lite_native_purchase_probe.py`：205帳號皆可用，新增帳號免費2；免費已用2後升級總10；第11次拒絕；恢復Apple查詢0；observer失效／恢復不改控制狀態。

Windows/Linux覆核：以基準repo為底，套用覆核包`change.patch`；先用包內`verify_packet.py`核對雜湊，再按README的測試指令。無需執行雲端命令、登入Apple或讀研究資料。

## 未結案且不隱藏

- 真實人類服務帳號登入／註冊提供者及換機恢復尚未接入；App Attest不能代替人類登入。本輪只完成既有可信mapping後的購買橋接。
- 真實Apple Sandbox S0–S9、實機保護、GCS持久層／observer高可用與告警演練、正式產品與政策設定盤點仍待授權與驗收。
- 現有legacy部署包刻意排除商業依賴；未產出可直接開啟commerce的新雲端映像。須另做最小權限候選組合與隔離驗收。
- 非預期中途政策改變／未知original轉移拒絕；不得用新帳號、清空帳本或另買繞過。
- 醫療 `ba970b1-N1/N2/N4/L1/L2` 未在本輪修正。

首次測試抓到的100帳號列大小、跨裝置計畫、打包可選依賴均已修正後重跑；失敗日誌另保留，沒有覆寫原研究資料。
參照：`docs/lite_native_purchase_bridge_20261027.md`、`docs/lite_apple_sandbox_acceptance_20261027.md`。
