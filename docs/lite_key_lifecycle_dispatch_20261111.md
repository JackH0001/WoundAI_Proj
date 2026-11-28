# 共用金鑰：固定版本操作與結果不明時的恢復

本輪接續共用 KEK 換包準備與指定來源版本盤點，補上**私有、預設關閉的底層操作元件**。沒有接入服務啟動、公開 API、排程或刪帳完成回執；沒有取得憑證、操作真實 KMS、掃描研究資料或變更 TestFlight。

## 修正前後

先前可證明「主要位置目前沒有舊版依賴，歷史／提案／鏡像仍可能有副本」，但沒有固定版本的停用、銷毀排程及恢復操作紀錄。遇到操作回應遺失，不能靠重送推測是否完成。

現在每次操作需要固定來源、獨立狀態與稽核位置、指定舊版本及受保護的較新版本、原 metadata 雜湊、覆核文件雜湊、最多 15 分鐘的有效窗口，以及最多 5 分鐘的操作員簽章。只支援下列三種動作：

| 動作 | 必須先觀察到 | 預期讀回 | 限制 |
| --- | --- | --- | --- |
| 停用 | ENABLED | DISABLED | 不修改較新版本 |
| 排程銷毀 | DISABLED | DESTROY_SCHEDULED | 排程不等於銷毀，副本仍可能解開 |
| 取消銷毀排程 | DESTROY_SCHEDULED | DISABLED | 不順帶重新啟用 |

沒有建立、匯入、輪替、加解密、重新啟用或直接銷毀介面。KMS 的自動銷毀只會由之後的唯讀核對觀察，不能由本工具宣稱已擦除所有實體備份。

## 操作與恢復

1. 確認原 metadata 與固定方案一致，保存不可變的原方案、簽章及準備紀錄。
2. 以每個 KMS 版本唯一的 CAS 工作欄位取得執行權，才可送出一次變更要求。兩個程序競爭只有一方可送出。
3. 變更回應及再次 GET 都必須符合預期，才保存觀察回執。同一要求重播讀原回執，不再次變更 KMS；後續操作也不改寫舊回執。
4. CAS 回覆遺失、RPC 逾時或讀回不明，均停止。新的方案不能越過 pending／uncertain 工作；不能因尚未看到結果就重送。
5. 必須有另一把金鑰簽署的 60 秒內有效排空證明，綁定原要求與精確 checkpoint，才可唯讀 reconcile。若原 claim 寫入未落地，可用原始準備稽核恢復；原始證據缺失則拒絕，不能當作全新工作重設。
6. 核對仍為原狀態時，也只記錄觀察。要再操作須重新覆核狀態與方案、取得新的操作員簽章；不自動 rollback 或 retry mutation。

操作紀錄只保留公開 metadata、簽章、雜湊與狀態，沒有 wrapped DEK、Apple token 或金鑰明文。

## 本機驗證

33 項測試涵蓋雙程序競爭、簽章與期限、來源／版本／儲存位置 pin、稽核損壞、失敗前／後逾時、CAS 或回執回應遺失、跨程序重啟、獨立核對及舊回執不變。

可重跑探針使用兩個合成帳號、實際 AES 與 SQLite 稽核，HTTP 由 requests 的合成 adapter 攔截：

- A 已關閉、B 已換包到 V2；V1 停用已生效但模擬回應遺失。相同要求重播新增變更要求 **0 次**。
- 獨立簽章核對 V1 已停用；B 仍能讀資料，A 不會恢復使用權。
- 操作順序為 `disable → destroy → restore → destroy`，合計 **4 次合成變更**，每個明確操作各一次。
- 排程及取消排程後，用保留的舊副本獨立 AES 解密仍成功，避免把邏輯停止存取誤稱為不可解密。
- 合成 provider 最後移除 V1 金鑰材料；模擬 31 天後唯讀觀察 DESTROYED。這時原始 AES helper 不能再取得 V1，而 B／V2 仍可讀。**31 天只是模擬時鐘，不是承諾期限，也不是 Google 實體清除證據。**
- 原帳號保管紀錄、grant、稽核及額度不變。真實 KMS 呼叫、真實金鑰銷毀、雲端寫入均為 **0**。

重播（需現有測試依賴）：

```sh
PYTHONPATH=Backend/Flask:tools:engineering/phase2 python -B -m unittest -v test_lite_key_lifecycle_dispatch
PYTHONPATH=Backend/Flask:tools:engineering/phase2 python -B tools/lite_key_lifecycle_dispatch_probe.py --output lifecycle-probe.json
```

完整 Lite 套件包含上述測試；交接報告記載該次總數，不相加。這輪沒有 iOS 修改，沒有把先前 CI 當作這輪已通過。

## 接上真實環境前仍需完成

- **完整退休授權尚未建立。** `review_sha256` 只綁定文件，沒有自動驗證全域清冊內容、零依賴、存活帳號還原或外部備份。上層須先驗證全部來源、不可讀軟刪除的處置、hold／retention、寫入程序排空與原始依賴；底層不應直接成為公開 endpoint。
- 固定狀態／稽核位置可阻擋同一方案搬到另一位置重跑，但不是跨專案的全域鎖；正式 IAM 必須限制唯一執行身分與狀態位置。
- KMS mutation API 沒有這裡可用的 generation／etag 前置條件。前讀一致不等於原子核對；正式執行仍需要唯一操作員權限與獨立排空證據，不能假定兩次 GET 消除競爭。
- 元件本身不重試變更。正式注入的 HTTP／認證 transport 也必須關閉自動 mutation retry，並另驗證 SDK 的 token refresh 行為、總時限及真實 API 回應；合成 adapter 不足以證明這些性質。
- 兩把簽署金鑰在測試中獨立，不代表已由兩位真人分開保管。真實角色保管、復原、網路中斷處理與最小權限仍待核對。
- 觀察 DESTROYED 不代表外部副本已擦除。正式輪替週期、KMS 銷毀保留時間與外部備份保留政策尚未定案，不能向使用者承諾 30／60／90 天內完成。

因此所有輸出 `complete_inventory_verified`、`account_deleted`、`physical_backups_erased` 均為 false；現有 shared-key 刪帳進度仍為 `backup_pending`。完成退休日誌不等於完成帳號的全部資料清理。

## 官方參照

- [Google KMS：更新版本狀態](https://docs.cloud.google.com/kms/docs/reference/rest/v1/projects.locations.keyRings.cryptoKeys.cryptoKeyVersions/patch)：PATCH 及 `updateMask=state`。
- [Google KMS：排程銷毀](https://docs.cloud.google.com/kms/docs/reference/rest/v1/projects.locations.keyRings.cryptoKeys.cryptoKeyVersions/destroy)：空 request body，回傳版本狀態及排程時間。
- [Google KMS：取消銷毀排程](https://docs.cloud.google.com/kms/docs/reference/rest/v1/projects.locations.keyRings.cryptoKeys.cryptoKeyVersions/restore)：恢復到 DISABLED，不是 ENABLED。
- [Google KMS：金鑰狀態](https://docs.cloud.google.com/kms/docs/key-states)：區分停用、銷毀排程與已銷毀；生命週期不可用單一成功旗標取代。

