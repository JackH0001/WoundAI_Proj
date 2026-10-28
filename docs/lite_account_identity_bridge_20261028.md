# WoundAILite Apple 身分與服務帳號橋接：本機候選驗證

日期：2026-10-28。基準：a1c9dc6701c40e19756360b0a194500465f381bb。
沿用 draft PR #36；本輪沒有部署、更新商店、建立商品、真實登入或購買。
未讀寫既有研究影像、深度或稽核資料。這是新增候選組件，尚未接上正式啟動或 UI。

## 修正前後

| 項目 | 前：a1c9dc6 | 本輪候選 |
|---|---|---|
| 人員身分 | 服務帳號／appAccountToken 有可信注入介面，但 App Attest 不能證明同一個人 | 獨立驗證 Apple identity JWT，綁定當次 nonce/state、受信任 audience/issuer/signature/期限 |
| 跨裝置 | 合成可信帳號可共用，尚缺原生人員登入接點 | 同一已驗證 Apple subject 在同一命名空間得到同一服務帳號／UUID；不靠 email 或前端自報身分 |
| 首次開通 | 既有逐帳號穩定格式／create-only 核心 | 經身分驗證後接入原核心；既有帳號與當日使用量不重建 |
| 登入中斷 | 尚無這個原生登入 journal | 先保存 challenge，再顯示 Apple；先保存憑證，再確認；回覆丟失使用同一操作重試 |
| 登出競爭 | 尚無此層 | 伺服器 tombstone/CAS，加上手機 generation/CAS；challenge、Apple 提示、confirm 晚回皆不得重新啟用 |
| 儲存失敗 | 尚無此層 | 登出 journal 寫失敗仍嘗試伺服器撤銷；重啟須核對伺服器 status，不能只信舊 active 本機值 |
| 舊 Lite 映像 | 不含商業模組 | identity 預設 None，精確條件式載入；原打包閉包／隔離啟動仍通過 |

`App Attest → 安裝識別`、`Apple 登入 → 人員身分`、`appAccountToken → 訂閱關聯`
是三個不同用途。登入／登出不代表研究同意、撤回、恢復購買或轉移照片。

## 組件與不變條件

- `Backend/Flask/lite_account_identity.py`：受信任 JWKS 快照與 nonce 驗證、
  HMAC 命名空間帳號、不可變安裝關聯、短期工作階段、確認／登出稽核。
  不儲存原始 Apple JWT／email／subject；只留必要雜湊與關聯。
- `lite_attest_http.py`：只有顯式注入相同 service registry 才註冊四個 account 路由；
  每次仍須 App Attest。服務及購買路由另核對人員登入；研究撤回不依賴登入。
- `iOS/WoundLite/LiteAccountIdentity.swift`：原生 AuthenticationServices provider、
  受保護 journal 候選、精確 HTTPS 端點與回執核對。傳輸層不傳前端 subject、
  tier 或 Apple user 欄位作授權依據。
- 同一人重新登入、第二台裝置、升級／恢復購買都不重置已用量。
- 同安裝要改成另一個人時先拒絕，沒有隱性移轉。正式換人／解除綁定／刪帳號
  流程尚待設計，不能清 Keychain 或重設安裝來繞過。
- 登出可停止後續授權；已先獲准且在執行中的工作不保證即刻中斷，保留原回執。
- 登出 journal 與伺服器同時不可寫時只能顯示尚未確認，不能宣稱耐久撤銷完成；
  重啟仍要核對伺服器，且短期工作階段有期限。

## 本機驗證結果

| 驗證 | 結果 | 證據界線 |
|---|---:|---|
| 身分後端新增回歸 | 31 通過 | 本機產生 RSA／EC 簽章，不是真 Apple token |
| 完整 Lite 後端 | 1,079 通過 | 包含身分測試；與 prelaunch 有重疊，不可加總 |
| lite-prelaunch workflow 本機重播 | 1,047 次通過；17 步成功 | 不是新 commit 的 GitHub CI |
| iOS Lite Release 模擬器 | 323 通過、0 失敗、1 略過 | 23 個新身份／HTTP XCTest；略過仍為已記錄的真機 file-protection 檢查 |
| 舊映像打包與閉包 | 12 通過 | 已含在 prelaunch；移除候選模組仍可啟動 |
| 共用邏輯／平台契約 | 50 項、0 失敗；未宣告落差 0 | 不代表 Android 已有原生 Apple 購買／登入功能 |
| 發布契約 | 7 通過 | 本輪未升組建號或更新商店 |

整合 probe：同人兩安裝共用免費 2 次，第三次被拒；升級後保留已用 2 次，
最多當日總額 10 次，第 11 次被拒；第二裝置恢復沒有增加 Apple 查詢；登出後
晚到 confirm 被拒，另一台的既有工作階段仍保留，重新登入也不重置額度。
所有模型、Apple 與資料皆合成；外部雲端呼叫 0，真實 Apple Sandbox 未通過亦未執行。

回歸涵蓋錯簽章、nonce/audience/state、重複 claim、過期金鑰、回覆遺失、稽核
失敗、bootstrap 中斷、並行帳號建立、舊登出晚到、撤回獨立，以及本機磁碟失敗。
第一輪測試／建置失敗紀錄仍保留；最終證據以最後完整成功產物為準。

## 尚不能接正式環境的項目

1. 真 Apple 登入、Apple entitlement／團隊設定、正式 JWKS 輪替／更新與撤銷通知
   尚未驗收。快照最長一小時，過期拒絕；這是測試邊界，不是可用的高可用 provider。
2. 候選登入工作階段最長 15 分鐘；正式長期續期、撤銷與登出機制未完成，不能
   把現有短期核心直接當作一般民眾的完整登入體驗。
3. 尚未啟用公開 UI、正式依賴組合、服務入口或帳號政策。`allowService` 必須是
   使用者允許服務傳輸的條件，不能反過來依賴「已登入」，也不能等同研究同意。
4. 安裝帳號映射與刪帳號流程、憑證最小保存／清理政策、JWKS 可用性及告警須
   隨正式 provider 再驗收；身份 token 僅在受保護本機 journal 保存，不送分析工具。
5. 真實 Apple Sandbox S0–S9、實機跨裝置、StoreKit server signed payload 未執行；
   簽章合成測試及既有本機 StoreKit 測試不能替代。
6. 活動環境 v1 訂閱資料是否為空，仍沒有新增唯讀盤點證據；不推定可跳過盤點。
7. 醫療 ba970b1-N1/N2/N4/L1/L2 本輪沒有處理。

## 下一步與使用者操作

目前不需要使用者在手機登入或購買。先完成來源覆核／CI，再準備 provider
正式接線、金鑰更新、撤銷與帳號刪除流程；列出 Apple／候選後端實際缺項及
固定來源。任何雲端設定、Apple 商品、TestFlight 或實機流程，另取得必要授權。
完成後才提供指定組建與 Sandbox S0–S9 步驟；保留所有原紀錄，不要求刪 App。

## Claude 覆核與重播

優先核對：身分與安裝是否分開、跨裝置額度是否共用、nonce 重放是否拒絕、
登出在 challenge／Apple／confirm 各階段的晚回是否仍能重開、磁碟失敗後是否
誤信舊登入、舊部署是否因新增 import 無法啟動。覆核包只含程式、合成結果與雜湊。

```sh
export PYTHONPATH=tools:engineering/phase2:Backend/Flask:.
python -m unittest -v test_lite_account_identity test_lite_build_context
python tools/lite_account_identity_probe.py --output /tmp/account-identity-probe.json
```

其餘重播指令和依賴沿用 `.github/workflows/lite-prelaunch.yml`。
真實身份token、交易簽章、金鑰與研究資料不可寫進公開覆核包。

Apple 原始參照：
- [驗證使用者](https://developer.apple.com/documentation/signinwithapple/verifying-a-user)
- [nonce](https://developer.apple.com/documentation/authenticationservices/asauthorizationopenidrequest/nonce)
- [state](https://developer.apple.com/documentation/authenticationservices/asauthorizationopenidrequest/state)
- [appAccountToken](https://developer.apple.com/documentation/appstoreserverapi/appaccounttoken)
