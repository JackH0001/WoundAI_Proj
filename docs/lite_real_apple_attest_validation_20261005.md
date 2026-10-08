# Lite 真實 Apple App Attest 實機驗證與解析器修復

日期：2026-10-05。候選工作樹，未提交／部署，未納入既有 TestFlight 包。

## 結果與範圍

iPhone 16 Pro Max 在隔離診斷 App（com.woundai.attestqa、開發環境）取得真實 Apple attestation 與兩筆 assertion。後端公開驗證入口使用固定 Apple 根憑證及實際 UTC 時鐘，成功验证裝置證明、receipt 與兩筆遞增計數器簽章。成功驗證時間 2026-10-05 04:31:38 UTC；receipt 仍在原有 300 秒有效視窗，未調整時鐘或放寬期限。

這是 Apple → 實機 → Mac 後端驗證器的真實密碼學驗證。不是 com.woundai.lite 的正式簽章、TestFlight production attestation，也不是 GCS／Cloud Run 上傳與撤回的端到端驗收。請求使用合成安裝代碼與空內容，不執行遠端 DELETE，不讀使用者傷口資料。兩筆簽章故意對同一合成請求產生，用於檢查 counter；HTTP 的 challenge 單次消耗另由整合測試驗證。

## 真實資料找出的三個問題

1. 開發環境 attestation 的 environment 為 development，但 Apple 簽章 receipt 第 7 欄為 sandbox。原本直接比較會拒絕正常註冊。現改為明確 development → sandbox、production → production；未知、大小寫變體及跨環境皆拒絕。缺少可選欄位時仍由已驗證的 attestation／公鑰綁定環境。
2. 本次 Apple assertions 的 flags 為 0xc0，37-byte header 後直接接簽章版本／類別 extension map，沒有 credential section。原本 0x81 mask 拒絕它。現只增加這個已實測的精確旗標形式；整段 tail 仍以嚴格 CBOR schema 解析，不跳過憑證區、不忽略尾隨位元組。其他未知／備份／UV／AT 組合仍拒絕。這是本次觀察到的相容性，不宣稱涵蓋所有 OS 版本。
3. 真實 Apple 簽章針對 nonce 使用 ECDSA/SHA-256。nonce 本身為 SHA256(authenticatorData || clientDataHash)。原程式用 Prehashed(nonce)，兩筆實機簽章皆拒絕；改為標準 ECDSA/SHA-256(nonce) 皆通過。獨立低階測試改驗 SHA256(nonce) 的 prehashed 等價形式；舊算法保留拒絕案例。三個整合測試檔的合成簽章亦對齊。

沒有加入「兩種算法任一成功即可」降級重試，沒有放寬 signature、challenge、App ID、版本、類別或 receipt 時效。正式設定仍只使用 production，接受類別仍為 TestFlight／App Store。

## 驗證

- 實機 XCTest：1 個流程通過，含真 Apple key attestation 及 2 次 assertion；修正前與重新取件兩次 xcresult 均保留。
- 真實回覆：attestation、receipt、兩筆 assertion 全通過；改動請求、重播最後 counter、development 證明套 production policy 三項皆拒絕。
- 本機回歸 9 檔 **179/179**：assertion 22、receipt 22、registration 25、request 7、state 29、enrollment 21、HTTP 22、service profile 17、fenced HTTP 14。
- HTTP 初跑因 sandbox 禁止 127.0.0.1 bind 失敗，取得本機回環埠執行權限後 22/22；未修改測試來略過。初次清單另含不存在的 test_lite_gcs_state，未列入已執行數字，GCS 真實驗收仍未完成。
- 6 個變異全部被偵測：回復舊環境比較、移除環境判定、回復舊 flags 限制、允許所有 flags、回復錯誤 Prehashed 算法、移除 signature 驗證。部分表現為正常 fixture 被 AssertionRejected（unittest ERROR），其餘為負向斷言失敗；已核對不是匯入或環境錯誤。
- 7 個受改動 Python 檔案語法解析通過。診斷 App 已移除；沒有刪除 WoundAI／WoundLite 或其資料。

## 證據與下一步

證據目錄：`/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-competitive-review-20261003/lite-apple-attest-20261005/`。

`validation.json`、`verification.json`、`regression.json`、`mutations.json`、`source-sha256.json` 固定本輪成果。失敗階段、原始 Apple 回覆與 xcresult 置於 repo 外且目錄僅擁有者可讀取；不貼出 key ID／receipt，不作公開測試 fixture。公開入口日後重跑舊 receipt 預期因超過 300 秒拒絕，不能將保存的回覆當成可重複使用的即時註冊。

既有 Linux Cloud Build 包不包含本輪修復，其來源雜湊保持原值；後續建置須建立新的來源清單。Windows／CI 尚未重跑，工作樹仍有既有未提交變更。

下一關：真正 Lite production App ID／簽章驗證，專用 GCS 的 challenge/counter 原子性與持久化，以及上傳 → 回讀 → 修訂 → 重試 → 撤回 → raw 封存排除。IRB 尚未送件；此證據不代表研究招募或公開上架條件已具備。

參照：[Apple 驗證流程](https://developer.apple.com/documentation/devicecheck/validating-apps-that-connect-to-your-server)、[receipt 格式](https://developer.apple.com/documentation/devicecheck/assessing-fraud-risk)、[開發與正式環境](https://developer.apple.com/documentation/devicecheck/preparing-to-use-the-app-attest-service)。實際 flags／sandbox 欄位與簽章結果以本輪真實 Apple 簽章回覆為證據，不從一般 WebAuthn flags 含義推斷其 payload 佈局。
