# Lite 金鑰保管、復原與刪除規格（待實作／未部署）

本文件接續 Claude 對 `4d09307` 的覆核。它是操作設計，不是已完成 KMS 部署或可宣稱「全部刪除」的證據。不得把文字程序當作雲端保護已生效。現有 `GrantCipher` 仍使用共用的明確注入 AES 金鑰；本輪未搬動真實憑證、建立金鑰或清除研究資料。

## 責任與最小權限

每種環境各有獨立 keyring／Secret 集合，Lite、MMH、Demo 不共用。每次取用記錄目的、版本、核准人、執行人、時間、成功／失敗與回復結果；不記錄秘密值、Apple token 或可還原的明文。兩位不同人確認的操作有：匯出備援、復原、IAM 擴權、停用／銷毀金鑰、變更簽章信任清單。日常程式以工作負載身分取得最低權限，不使用人工下載的 SA JSON key。維護者不能同時任意讀憑證及修改刪除稽核。

| 金鑰 | 保管與使用 | 備援／輪替規則 |
|---|---|---|
| 身分 HMAC | 獨立 Secret 或受限 HMAC 服務，版本固定；只有身分橋接可用 | 加密離線備援由雙人管制。輪替必須保留舊版本與一對一身分遷移；不可用新 salt 把同一人建成新免費帳號或重置額度。先演練同人跨裝置及反向回復。 |
| Apple 憑證保管庫 | 目標為每帳號獨立 DEK；AAD 綁環境、帳號、grant、格式版本；DEK 由受限 KMS 包裝 | 備份只存密文、wrapped DEK、key version與內容雜湊。不要把明文 DEK 放日誌、core dump、容器層或一般備份。跨帳號解密必須拒絕。以下「可銷毀單位」仍須落實。 |
| Sign in with Apple `.p8` | Secret Manager 獨立版本；僅 client-secret 簽署程序可讀；短期 JWT | 產生時一次保存加密離線備援及 Apple key ID，雙人核對可復原。先建新 key、驗證登入／refresh／revoke，再停用舊 key；不以停用 app signing key 代替帳號刪除。 |
| App Store Server API key | 與登入 `.p8` 分開保管、角色及用途 | Sandbox／Production 分別驗證 issuer、key ID、bundle、環境；重疊驗證新舊 key 後輪替。不可讓 API 金鑰進 App 或 Git。 |
| 操作員簽章 key | 離線或硬體簽署；runtime只有公鑰 | 雙人核准固定 manifest、nonce及期限。保留舊公鑰驗證歷史；撤銷舊私鑰只能阻止新操作，不重寫舊稽核。 |
| observer 簽章 key | 專用觀測身分，不能發布服務或讀媒體 | 輪替公鑰版本及來源；監看失聯只告警，不因 30 秒逾時讓既有耐久啟用失效。禁止把 observer key 當 operator key。 |

人員與雲端 key ID 目前尚未指定，因此雙人流程尚未能實際執行。每季與每次重大輪替前需在合成環境完成復原演練；保留實測 RPO/RTO，未演練前不得填入虛構的保證值。

## 帳號資料金鑰不等於已可銷毀

Envelope encryption 會同時保存資料密文及 wrapped DEK。若共用 KEK 仍可用，備份中留下的 wrapped DEK 仍可解密；只刪目前資料列或目前 wrapped DEK 不足以做到密碼學抹除。這是依 [Google envelope encryption](https://docs.cloud.google.com/kms/docs/envelope-encryption) 所述結構推導的限制。

選定的待實作方案：每個帳號使用獨立、可銷毀的 KMS wrapping key version，包裝該帳號 DEK；不得讓其他帳號 DEK 使用這個可銷毀單位。帳號所有 grant／refresh-output、舊版本及復原副本，必須列入同一金鑰清冊。一般備份可以保留 wrapped DEK，但不能另有仍可用的 wrapping key、未登記舊 key 或明文 DEK 備份。KMS 成本、服務配額及刪除期間費用尚須試算，**未核准部署**。若改用共用 KEK，必須先證明所有 wrapped DEK 歷史／備份可刪除，否則不得宣稱已完成密碼學抹除。

銷毀流程：

1. 明確刪帳同意與耐久意圖 → 所有裝置及憑證建立者封鎖；新登入不得重新啟用。
2. 建立資料／憑證／金鑰目錄，停止新寫入，等待已開始的寫入完成並重查遲到結果。結果不明保持 pending。
3. 先撤銷可確認的 Apple token；若結果不明，不可假裝成功，也不能以此無限期阻止獨立的資料處置。
4. 核對每帳號 key 的唯一歸屬，清除受控程式的明文金鑰快取並停止持有者。兩人核准後排程銷毀該帳號所有 wrapping key version。
5. 狀態區分 `closure_pending`、`token_revocation_pending`、`key_destruction_scheduled`、`cryptographic_erasure_confirmed`、`data_disposition_pending/completed`。排程銷毀或 disable 都不是最終不可復原。
6. 核對 KMS 狀態後，使用保留的合成備份重播解密：被刪帳號必須失敗，其他帳號必須成功；記錄 provider 回執、範圍、版本、未解決項，不輸出秘密值。
7. 帳號服務資料、研究撤回、訂閱取消與手機紀錄分開處理。金鑰銷毀不會自動取消 Apple 訂閱，也不等於研究影像／法定保留稽核已刪除。

Google 的 scheduled-for-destruction 階段仍可復原；destroyed 表示客戶無法再恢復使用，但 provider 的所有基礎設施清除另有時程。不要在排程當下顯示「所有備份已永久清除」。參照 [Destroy and restore](https://docs.cloud.google.com/kms/docs/destroy-restore) 及 [Key states](https://docs.cloud.google.com/kms/docs/key-states)。

## 災復與回復演練門檻

- 只在隔離環境還原合成資料，開始時保持服務關閉。
- 還原資料前載入最新的帳號關閉／金鑰銷毀 tombstone；舊備份不得覆蓋較新的刪除狀態。
- 活躍帳號還原後身分、訂閱 original transaction、已用額度、研究 epoch及原始媒體雜湊不變。
- 已關閉帳號不可因還原舊 JWT、舊同意書或舊密文重新登入／上傳；已銷毀金鑰不可由備援恢復。
- 模擬 key unavailable、錯誤 key version、備援損壞、跨環境還原、程序中斷及重試；不得建立替代新身分掩蓋錯誤。
- 復原失敗保持關閉，撤回新增臨時權限，保留所有證據；真正 provider、IAM、KMS 與成本驗收前不能作正式刪帳完成宣告。

## 實作進度補註

後續已加入預設不啟用的每帳號 wrapped-DEK、固定 KMS version adapter 與銷毀狀態核對；見 [每帳號金鑰本機驗收](lite_account_key_custody_20261029.md)。只在本機替身上驗證，沒有建立或銷毀真實金鑰。上述營運規格仍須完成供應、IAM、雙人核准、版本輪替及災復演練；不能視為已部署。KMS 邏輯銷毀也不等於 Google 所有物理備份立即移除。
