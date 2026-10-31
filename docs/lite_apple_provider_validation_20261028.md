# WoundAILite Apple 公鑰與帳號變更通知：候選驗證

日期：2026-10-28。基準：`9edbda8cce4a8d3a19fc39eff5679bd1d5e9fdd4`。
沿用 draft PR #36；本輪只完成本機程式與測試，尚未推送本輪來源、部署、更新
商店、註冊 Apple callback、啟用登入 entitlement、建立商品或購買。

## 修正前後

| 項目 | 前一版 | 本輪候選 |
|---|---|---|
| Apple 公鑰 | 需注入最長一小時的固定快照，過期即拒絕 | 明確注入 `RefreshingAppleIdentityVerifier`；只 GET Apple 固定 HTTPS 公鑰端點；驗證格式後快取，未知 kid 可觸發受限更新 |
| 公鑰輪替與斷線 | 沒有取得／更新 provider | 同行程並行只更新一次；預設 TTL 900 秒、更新最小間隔 30 秒；過期無替代金鑰時拒絕新驗簽，保留待確認登入 |
| Apple 撤銷授權／刪 Apple 帳號 | 手機檢查 credential state；後端沒有通知處理 | 嚴格驗證通知 JWT，再依個別人員建立耐久阻擋水位；已登入的各裝置及晚到 confirm 都要核對 |
| 亂序／重送／回應遺失 | 沒有通知回執 | jti 先 create-only 綁定內容，再寫阻擋狀態、稽核與回執；同事件重送取得同回執，舊事件不降低水位 |
| 撤銷後重新授權 | 尚無此後端規則 | consent-revoked 之後明確取得較新 Apple identity token 才可登入，帳號、appAccountToken、已用額度不重建；account-deleted 保持停用 |
| 啟用保護後回退 | 未定義 | 耐久設定禁止不含通知 authority 的新版實例重新啟動；本版已在跑但未注入 authority 的實例也拒絕服務。更舊二進位仍須在部署時先排空／隔離，不能靠此欄位約束舊程式 |

`Apple 授權撤銷`、`研究資料撤回`、`購買退款`、`App 帳號刪除`各有不同意義。
本輪只停用服務授權，不刪手機紀錄、研究影像／深度、訂閱帳本或既有稽核。
研究撤回端點不依賴人員登入，已用 HTTP 測試確認撤銷後仍可呼叫。

## 實作與不變條件

- `Backend/Flask/lite_apple_identity_provider.py`：固定
  `https://appleid.apple.com/auth/keys`，正常 TLS 憑證驗證、不跟隨 redirect、不讀
  JWT 的 jku/x5u、不傳使用者 token。回應最多 64 KiB／8 把支援金鑰。已知 key
  的壞簽章不觸發更新；未知 kid 不能造成每請求都讀 Apple。
- 背景更新失敗時，原快照僅能用到原期限，不能延長；wall-clock 及 monotonic
  期限均核對。驗簽完成後再次確認快照未變更／到期。冷啟動或過期時無法取得
  公鑰會回未確認，不能退回無簽章登入。既有尚有效的短期工作階段不因一次
  公鑰讀取失敗被當成帳號刪除，也不會因此延長。
- `Backend/Flask/lite_apple_account_events.py`：個別人員狀態以 HMAC 命名空間
  hash 索引，不掃所有人員。原始 JWT、email、Apple subject 不寫入事件狀態；
  稽核保留事件／內容 hash、種類、時間與結果。
- 通知需核對 issuer、精確 audience、簽章、iat、jti 與 events。支援物件及
  JSON 字串 events。通知的 nonce／exp 規格不同於 identity token；不混用兩者。
- 有效撤銷先耐久寫入阻擋狀態，再完成稽核回執，避免稽核寫失敗時繼續授權。
  各階段失敗不回成功；同一已簽署事件可續接。若耐久 claim／阻擋儲存本身
  不可用，只能回 503 等重送，不能宣稱已完成撤銷；需監控及獨立對帳。
- session 的 Apple issued-at 證據採 create-only 保存。啟用通知保護後，舊工作
  階段若缺此證據，須明確重新登入，不能補猜時間。通知水位使用 `<=` 拒絕
  同秒舊授權；同秒重新同意可能需稍後再明確登入。
- `consent-revoked` 只阻擋不晚於事件的授權；後續明確新授權保留原帳號。
  `account-deleted` 為持續停用，email-enabled 不能解除。已在執行、先前已取得
  授權的工作不保證即刻中斷；本輪保證後續 admission 與 confirm 核對。
- HTTP 路徑 `/api/v1/lite-apple/account-events` 只在可信 identity/events 明確
  注入時註冊；由 Apple JWS 驗證，不要求手機 App Attest。它不提供瀏覽或管理
  能力。一般手機服務路徑仍須 App Attest 與人員 session。舊 Lite 映像仍預設
  關閉商業／身分模組，隔離啟動和打包閉包測試保持通過。

## 本機驗證

| 項目 | 結果 | 界線 |
|---|---:|---|
| 新公鑰／帳號事件及整合 probe | 46 通過 | 合成 RSA 簽章、暫存 SQLite、注入 HTTP；0 真實登入／購買 |
| 完整 Lite 後端 | 1,125 通過 | 相比前版 1,079 新增 46 項 |
| prelaunch workflow 本機重播 | 1,093 通過／18 步 | 相比前版 1,047 新增 46 項；17 組 unittest 命令 |
| 既有身分＋打包回歸 | 31＋12 通過 | 已包含以上集合，不能再加總 |
| Apple 官方公開公鑰唯讀 | 1 次成功，3 把公鑰通過格式解析 | 只取公鑰；不是 Apple 登入或 Sandbox 證據 |
| iOS／Android | 本輪未改原生程式、未重跑建置 | 前版 `9edbda8` 的 CI 不代表本輪新來源 CI；不得沿用為新測試結果 |

所有測試集合有重疊，不能加總。第一輪集中測試的替身錯誤與完整測試被沙箱
禁止綁定 localhost 的失敗日誌保留；最終 1,125／1,093 在允許本機回環連線的
環境完成。沒有為通過測試而移除失敗情境或放寬授權。

整合 probe 確認：兩台裝置共用原帳號、用量；撤銷阻擋兩台；事件重送不新增
回執；新授權後仍沿用同一 appAccountToken 及已用量；過期公鑰＋斷線拒絕
登入，同一待確認操作在更新成功後續接，不建立第二份帳號或用量。

## 正式接線仍需完成

1. 這是預設關閉的 provider 候選，未接公開 UI／部署；登入 session 仍最多
   15 分鐘。長期續期、授權碼換 token、伺服器憑證保存／撤銷尚未實作。
2. 本機 `iOS/WoundLite/WoundLite.entitlements` 目前只有 App Attest，沒有
   `com.apple.developer.applesignin`。Apple App ID／profile／團隊與服務群組設定
   尚未做本輪唯讀盤點，也沒有代為啟用；實機 S0 不可先宣稱通過。
3. Apple 通知 URL 尚未註冊，沒有真實 Apple webhook 到達證據。啟用前須固定
   audience／namespace／secret，列出儲存最小權限、保留政策、入口限流及回復
   方案；先用候選端點與指定帳號驗證，不改現行研究撤回端點。
4. TTL 900 秒、更新間隔 30 秒、通知簽發最久 7 日皆為本輪保守工程政策，
   不是 Apple 對重送時效或精確度的保證。超過通知窗口須告警及明確對帳；
   不自動清除拒收證據。8-key 上限或 Apple 回應格式變更也應告警並覆核。
5. 快取／鎖在單行程內，多 worker／多 replica 各自更新；正式環境需容量
   上限、冷啟動預熱、公鑰／通知失敗告警、持續重送與獨立對帳。Socket timeout
   與讀取 deadline 不能取代平台對 DNS／整體請求時限的限制。
6. 帳號切換／解除安裝關聯／App 帳號刪除與憑證清理仍待實作；本輪收到
   account-deleted 不等於已銷毀研究資料。事件 claim／稽核／回執只增不刪，
   未加入正式清理；不得順手啟動既有研究資料清理。
7. 保護只能約束理解新規則的程式；正式切換須排空舊 verifier／舊 worker，
   回復時停新工作並保留狀態，不能回退到會忽略通知水位的舊二進位。
8. 真實 Apple 登入、Apple Sandbox S0–S9、活躍 v1 資料盤點、observer HA，
   以及醫療 ba970b1-N1/N2/N4/L1/L2 均未在本輪完成。

## Claude 覆核重點與重播

優先檢查：未知 kid 是否可放大公鑰請求；過期／更新失敗是否使用 stale key；
同 jti 不同內容競爭；撤銷與 confirm／provision 競爭；稽核失敗後是否仍授權；
過舊事件是否覆蓋新同意；啟用保護後回退是否繞過；舊 Lite 映像是否保持可啟動。

```sh
export PYTHONPATH=tools:engineering/phase2:Backend/Flask:.
python -m unittest -v test_lite_apple_identity_provider test_lite_apple_account_events test_lite_apple_provider_probe
python tools/lite_apple_provider_probe.py --output /tmp/apple-provider-probe.json
python -m unittest discover -s engineering/phase2 -p 'test_lite*.py'
```

依賴及其餘重播指令沿用 `.github/workflows/lite-prelaunch.yml`。測試及 probe
不呼叫 Apple；公開公鑰唯讀連線結果在覆核包單獨標示，不混入合成驗收數字。

## Apple 原始參照

- [Fetch Apple's public key](https://developer.apple.com/documentation/signinwithapplerestapi/fetch-apple%27s-public-key-for-verifying-token-signature)：固定公鑰端點及依 kid 選 key；key 數量可能變動。
- [Processing account changes](https://developer.apple.com/documentation/signinwithapple/processing-changes-for-sign-in-with-apple-accounts)：簽章通知與四種事件；刪 Apple 帳號時仍需 credential-state 檢查。
- [Enable server notifications](https://developer.apple.com/help/account/capabilities/enabling-server-to-server-notifications)：primary App ID、通知 URL、TLS 設定。
- [TN3194](https://developer.apple.com/documentation/technotes/tn3194-handling-account-deletions-and-revoking-tokens-for-sign-in-with-apple)：撤銷 token、credential state 與帳號刪除的不同處理。
