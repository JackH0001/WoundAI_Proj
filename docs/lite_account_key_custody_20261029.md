# Lite 每帳號 Apple 憑證金鑰：本機合成驗收

父來源：`0ac63a361c84de615e2d35d3df6552c87d75fdcf`。本輪是刪帳資料處置的必要前置作業，不代表完整刪帳已完成。PR 維持 draft；未部署、未建立 KMS 資源、未更新商店，沒有刪除使用者或研究資料。

## 修正前後

原本 `GrantCipher` 使用共享的明確 AES 金鑰環。撤銷 Apple token、移除目前憑證欄位後，只要保留金鑰，歷史加密副本仍可解密。新版 opt-in `AccountGrantCipher` 為每個 scoped Apple 帳號建立一把隨機 256-bit DEK，以專屬、固定的 KMS version 包裝。DEK 只以包裝後的形式存入獨立狀態庫；每次使用解包，不保留明文快取。授權首次交換與後續 refresh-output 使用同一帳號金鑰。

同一探針對父來源與新版重跑：舊來源在移除目前憑證後仍可解開備份；新版先確認全部 Apple grant 撤銷，再經模擬 KMS `DESTROYED`，直接取備份的 wrapped DEK 呼叫 provider 也無法解包。另一帳號仍可解密。此項繞過 App 的政策封鎖核對，避免把「程式拒絕」誤稱為「金鑰已消失」。實驗使用真實 AES-GCM、SQLite CAS 與本機 KMS REST 替身，**不是 Google KMS 實機驗收**。

## 介面與保護

- `GoogleGrantKeyProvider` 只接受顯式注入的 authenticated session/key ring。固定 HTTPS endpoint、禁 redirect、連線/讀取 timeout、回應大小上限；不讀取預設憑證。
- 資源名稱由 scope/person 的完整 SHA-256 派生，不包含 Apple subject 或 email。固定 version 1，不跟隨 primary。只接受 Google 產生、SOFTWARE、不可 reimport 的對稱金鑰；傳輸核對 CRC32C。
- 不含 create/rotate/import/restore 操作。**帳號金鑰須先由受信任註冊／供應流程建立**，未完成會在一次性 Apple code exchange 前停止。該正式供應流程、IAM、資源配額與費用仍待驗收。
- 並行首次寫入只採用 CAS 勝出的 wrapped DEK；回覆遺失讀回勝出資料，不建立另一套有效金鑰。
- 新 vault 會保存 durable crypto-mode。舊 vault 不能直接啟用此模式，新 vault 不能退回共享金鑰。歷史 ciphertext 遷移、舊實例退場及舊金鑰處置仍須另外規劃。
- `destroy_after_revocation` 只供受信任執行器，無 HTTP route。它核對同一 `AccountDeletion`、關閉帳號及 frozen inventory、全部 grant 已撤銷，再先寫不可變稽核與銷毀 fence。
- provider 回覆遺失時，重試先查狀態；`DESTROY_SCHEDULED` 不算完成。只有固定 version 讀回 `DESTROYED` 才標記 `grant_key_unrecoverable=true`，且仍是 `account_deleted=false`、`data_disposition=pending`。
- 銷毀意圖同時阻擋舊 wrapped-key 資料庫快照。單純還原資料庫或把 scheduled KMS key 恢復 ENABLED，不會重新放行憑證。災復必須先重播獨立的關閉／銷毀稽核，不能把所有庫任意還原至舊時間。
- Python 不保證明文 buffer 的記憶體歸零。正式清理必須先停止舊 worker、排空 in-flight 請求與記憶體副本；本輪不宣稱完成此項營運條件。

## 測試與重跑

使用 repo pinned dependencies、`PYTHONPATH=tools:engineering/phase2:Backend/Flask:.`、`WOUNDAI_STORE=local`：

```sh
python -m unittest -v test_lite_grant_key_custody
python tools/lite_grant_key_custody_probe.py --output key-custody.json
python -m unittest discover -s engineering/phase2 -p 'test_lite_*.py' -v
```

41 項新測試涵蓋實際 grant capture/refresh/revoke 連接、跨帳號/授權/AAD、並行 DEK 建立、CAS 回覆遺失、key 缺失、舊格式拒收、KMS metadata/CRC、關閉中晚到的 Apple 憑證、排程/銷毀區分、稽核故障與恢復、讀取中銷毀、歷史備份及其他帳號保持可用。完整 Lite 1,482 項通過；prelaunch 1,450 次、23 步驟通過，兩組有重疊不可加總。沒有修改 iOS，不重複計入上輪 401 項 native 測試。

## 上線前仍需處理

1. 真實 KMS 合成帳號驗收、runtime/provisioner/destruction worker 分離 IAM；正式註冊先供應金鑰，不能用此元件直接上線而讓新帳號卡住。
2. 庫存及金鑰供應 ledger、單一帳號版本輪替/歷史版本全集、現有共享金鑰資料遷移、雙人核准與災復演練；不得把「有 README」當成程序已執行。
3. Apple token 撤銷／未知 exchange 或 refresh 的人工處置與完整帳號資料目錄；service result、訂閱綁定、研究撤回/法定稽核留存的處理不得省略。
4. 實際 worker drain、KMS destruction window、Google 備份刪除時間與狀態顯示。App 不能顯示「全部資料已刪除」。
5. 真實 Apple Sandbox、App 內完整刪帳、原生全流程及 deployment composition 仍待驗收。本輪沒有啟用預設功能或實際清理。

## 成本與供應選擇

依查核當日官方價目，SOFTWARE active key version 約 US$0.06／月，密碼操作 US$0.03／10,000 次；DISABLED 與 DESTROY_SCHEDULED 仍計入 active。每個**保存 Apple 憑證的帳號**一個版本，1,000 個約 US$60／月、10,000 個約 US$600／月，未含其他儲存、網路與運算費。沒有假定 Autokey 免費額度適用自訂帳號金鑰。匿名本機使用不會自動配置這類金鑰。

此成本須納入免費兩次與 US$1.99 方案試算，不能因本機測試通過就啟用。若成本不接受，可評估獨立可刪的密鑰存放服務，但必須同時解決所有版本/備份/災復副本，不能退回「刪除當前 wrapped DEK 就算銷毀」。正式供應數量與維護方案尚未定案。

## 原廠參照（2026-10-29 查核）

- [KMS encrypt：可指定精確 CryptoKeyVersion、AAD 與 CRC32C](https://docs.cloud.google.com/kms/docs/reference/rest/v1/projects.locations.keyRings.cryptoKeys/encrypt)
- [KMS destruction：先進 DESTROY_SCHEDULED，等待後才 DESTROYED](https://docs.cloud.google.com/kms/docs/reference/rest/v1/projects.locations.keyRings.cryptoKeys.cryptoKeyVersions/destroy)
- [金鑰狀態及刪除時程](https://docs.cloud.google.com/kms/docs/key-states)：DESTROYED 是客戶無法復原的邏輯狀態；官方另列從 destruction 起至全部 active/backup 系統移除的時間，本輪不把邏輯不可解密等同物理備份已刪。
- [KMS 官方價格](https://cloud.google.com/kms/pricing)
- [Apple App 內刪帳要求](https://developer.apple.com/support/offering-account-deletion-in-your-app/)
