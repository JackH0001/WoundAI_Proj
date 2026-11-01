# WoundAILite 原生服務後端組合：本機候選驗收

基底：`b1b99a91b5f7a401ca53abe7afac50c17e992484`。本輪保持 PR #36 draft；
不合併、不部署、不更新商店，不處置真實資料或金鑰。

## 修正前後

之前各入口已能個別注入，但沒有一個組合入口同時核對帳號、訂閱、額度、
登入續期、服務結果與刪帳的共同狀態。既有 `app:app` 仍使用舊 Lite 入口。

新增 `Backend/Flask/lite_native_backend.py`：受信任啟動程式明確呼叫
`compose_native_backend(enabled=True, ...)`，再呼叫回傳物件的 `create_app()`，
取得獨立 Flask WSGI 應用。沒有環境變數自動尋找憑證或自動開啟功能。
未明確啟用、缺元件或設定不符時拒絕建立。這是可在本機完整運作的候選入口，
不是現行 Cloud Run 入口已切換的宣告。

組合內容：App Attest、同一穩定帳號 registry、耐久訂閱政策／額度啟用狀態、
Apple 登入與事件、每帳號加密憑證、續期、原生購買／恢復、辨識與跨日恢復、
刪帳受理與進度。`DispositionProgress.advance()` 僅能由受信任 worker 呼叫，
不註冊 HTTP 路由；研究 API、清理與啟用路由在安全閘門前即拒絕，不建立研究 writer。

## 啟動核對

- App Attest 維持 production 驗證（TestFlight 裝置證明）；訂閱明確限定
  Sandbox。登入 audience、OAuth client ID、App Attest bundle、訂閱 bundle
  必須一致為 `com.woundai.lite`。Sandbox 訂閱不等同開發版 App Attest。
- runtime 與 rollout 必須共用同一 `ServiceReleaseReader`，來源、映像、
  政策與 quota manifest 要相符；ledger 必須使用該 rollout 的 admission guard。
- 20 個新原生狀態／稽核位置必須完整提供且互相獨立，也不能與既有安全、
  隱私、帳號、訂閱、額度及 Apple proof 位置重疊。SQLite 以解析後實體路徑
  核對；GCS 以桶名與 prefix 核對，父子 prefix 也視為重疊。
- 這些核對不是雲端 IAM 驗收。不能因此宣稱能讀真實 GCS、IAM 最小權限
  已通過或 MMH／Demo 雲端隔離已驗收。
- 靜態接線錯誤在元件寫入設定標記前阻擋；元件自身遇到既有設定／稽核不符
  仍會拒絕啟動。若中途出現儲存失敗，保留已寫標記，使用原設定續接；不清空重建。
- 啟動不呼叫政策啟用、rollout、Apple 或 KMS，不增加／銷毀金鑰。
  已暫停的服務可啟動以處理既有帳號刪除受理，但不放行新的辨識。

## 驗收與可重跑方式

`engineering/phase2/test_lite_native_backend.py`：33 項，包括啟動錯配防護、
帶 App Attest 簽章的 HTTP 登入、免費 2 次、訂閱當日總額 10 次、交易恢復、
重開後讀回、續期、跨午夜、結算回覆遺失、登出、研究撤回獨立性、刪帳受理、
晚到辨識結果、暫停期間刪帳及 GCS CAS adapter（合成 transport）。

`tools/lite_native_backend_probe.py` 產出可機讀結果：

- 先用免費 2 次，再訂閱、恢復購買並辨識第 3 次：模型 3 次、扣額度 3 次；
  每日總額 10，不另外增加 10 或重設已用次數。
- 跨午夜恢復、結果已保存但結算失敗、登入續期三個情境各只執行模型 1 次、
  計入使用 1 次；保留原操作或原日封鎖證據。
- 刪帳 HTTP 回覆 202 待處理；沒有呼叫 Apple 撤銷或 KMS destroy。
  另以合成 worker 執行後，3 個服務結果封存、1 次 Apple 撤銷，KMS 僅呈現
  `DESTROY_SCHEDULED`，仍不宣稱帳號完全刪除。
- 原 JPEG bytes 與研究隱私紀錄不變；稽核留下，不輸出 token／原始影像。

安裝既有測試相依後，從 repo 根目錄執行：

```sh
PYTHONPATH=tools:engineering/phase2:Backend/Flask:. WOUNDAI_STORE=local python -m unittest -v test_lite_native_backend
PYTHONPATH=tools:engineering/phase2:Backend/Flask:. WOUNDAI_STORE=local python tools/lite_native_backend_probe.py --output evidence/native-backend-probe.json
```

使用真實密碼學檢查與合成信任根、SQLite 及 fake KMS/GCS transport；不是
Apple Sandbox、雲端或手機實測，也不驗證模型／面積精確度。GitHub workflow
已加入此套件與 probe；是否已在新 commit 執行成功以 CI 回執為準。

本機最終重跑：完整 Lite **1,674 項**、prelaunch **1,642 項**，均零失敗；
兩套有重疊，不可相加。prelaunch 的 56 個測試／probe 命令完成，包含新整合
33 項、舊映像打包隔離 13 項。初次執行的 5 個 loopback 錯誤是受限環境
不准建立 `127.0.0.1` listener；保留失敗紀錄，取得本機連線執行權後完整重跑
通過。沒有因此略過測試、放寬產品邏輯或取得雲端權限。

## 仍需完成的切換工作

1. 現行 `app:app`、映像允許清單、鎖定相依及手機一般啟動維持原狀。
   下一步需準備候選映像 profile、正式 provider/config loader、最小權限與
   來源／映像 pin，不能只將本檔複製進舊映像就宣稱可部署。此輪舊 Lite 的
   打包隔離測試仍必須通過，以避免再次發生 Claude B1。
2. 現有 quota cutover 在**切換 UTC 當天**要求已核定基準清冊。名單外新帳號
   即使完成登入仍回 503；次日起才可安全自動建立當日零基準。驗收明確保留
   這個限制，不藉此假定沒有舊用量。正式開放應排在完成切換後下一 UTC 日，
   或另行實作與覆核「確無舊用量」的首次上線程序。不能直接提前開放一般使用者。
3. 個人 KMS key version 仍需另外供應，未有金鑰的帳號不能登入此候選。
   試辦／一般開放前，需核定受信任供應流程、費用、保管與災復。
4. 完整刪帳仍待歷史版本／備份、研究關聯、身分與訂閱保留規則，以及真實
   provider／裝置驗收。服務刪帳不等於取消 Apple 訂閱，不得把 `pending` 改成完成。
5. 真實 Apple 登入、Apple Sandbox S0–S9、App Attest origin、裝置更新與候選
   部署需另行具體授權。沒有新增研究同意，也不替使用者登入或購買。

## Claude 覆核重點

請對照上述基底，核對工廠 preflight 是否能阻擋不同帳號／政策／儲存接線；
確認 signed HTTP 測試沒有以 stub 取代帳號、訂閱、ledger 或刪帳判斷；核對
fake provider 的界線、跨日原日封鎖、重送次數及未完成處置欄位。原生與 Android
程式本輪未變更，先前 iOS CI 數字不可當成本輪重新跑的證據。
