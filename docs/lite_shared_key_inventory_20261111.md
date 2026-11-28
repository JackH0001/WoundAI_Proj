# 共用金鑰：指定保管位置的實體版本唯讀盤點

本輪接續 `64a7ce7` 的分批換包準備。工具沒有接入公開 route、服務啟動或銷毀執行器，沒有環境變數自動開啟，也不發現或取得憑證。只在合成環境驗證，沒有掃描真實金鑰或研究資料。

## 修正的證據缺口

只讀目前帳號的 key row 不足以評估舊 KEK 依賴。關帳時的 tombstone、換包後的目前資料，不代表版本控制保存的舊 key row、initial proposal、尚未提交的 rewrap proposal 或備份鏡像已消失。

`SharedKeyInventory` 現在要求雜湊固定的來源清冊，列明唯一 primary 與最多七個 mirror 的 bucket、prefix、project number。primary 必須與現有 `RotatingGrantCipher` 的 `GCSStateStore` 一致；重疊位置、錯誤專案及未知命名都拒絕。每個來源依序：

1. 讀 bucket identity、metageneration、versioning、soft-delete、hold／retention／lifecycle 等政策 metadata。
2. 以 `versions=true` 逐頁列出目前與非目前物件，另以 `softDeleted=true` 列出軟刪除物件；不得混用兩旗標。
3. 對可讀物件使用 `generation` 與 `ifGenerationMatch` 固定版本，核對長度、CRC32C、嚴格 JSON、key slot 及原始稽核。輸出只含分類、雜湊、版本與數量，不含 wrapped DEK、grant envelope 或明文。
4. 再完整列一次兩種清單並讀政策；有可觀察的變動就回報 incomplete。未知格式、漏分頁、403／404／412／429、checksum 或稽核不符均不略過。

可辨識 mode、目前 key row、initial proposal、rewrap proposal、proposal tombstone、dependency reservation、proposal index。仍可解開的 wrapped DEK 分別計入目前、歷史、提案副本；同一內容的實體鏡像要計數，不能用內容雜湊去重而漏報備份。

## 保守狀態與界線

- `all_selected_material_observed` 只表示**所列來源**兩輪觀察一致，且無不可讀的軟刪除內容；不是全域／原子快照，更不是可銷毀判斷。狀態也不表示 observed dependencies 為零。
- `complete_global_inventory_verified`、`external_backups_verified`、`all_writers_drained`、`kms_destruction_authorized` 一律 false。兩輪清單不能證明沒有未列入的 Mac／離線備份、其他 bucket、過去使用不同 prefix 的資料，或最後一次觀察後才完成的寫入。
- 軟刪除內容不能直接下載。列到軟刪除時回報 `soft_payloads_unreadable`；該 API 回 400 時是 `soft_inventory_unknown`，即使目前 policy=0 也不能推論歷史不存在。工具不 restore、不 delete，不為了讓測試通過改政策。
- 持有 hold 的物件仍能盤點，報告標明 held，沒有因此獲得移除 hold 或刪除權限。
- 預設最多 2,000 次**邏輯**讀取、1,000 筆清單項目（含第二次觀察）、每種列表每輪20頁、16 MiB payload、API 邊界檢查300秒。邏輯 audit load 可能由 GCS adapter 轉成兩次 HTTP，不能當真實 API 次數／費用上限；reader 有連線5秒、讀取10秒及串流大小上限，硬性整體截止需由未來受限執行器負責。超限只留未完成證據。
- `GCSInventoryReader` 只用注入 session，固定 Google Storage JSON endpoint、GET、禁 redirect。操作者 IAM 仍須另外限制 `storage.buckets.get`、指定來源的 `storage.objects.list/get` 及原始 hash-only 稽核讀取。工具本身不能替代 IAM；本輪未授權或變更任何雲端權限。

## 合成驗收

38 項新測試含實際 requests URL／參數準備、串流上限，以及真實 AES、原 key custody 與 SQLite 稽核。GCS 儲存與 KMS／Apple transport 為替身，測試禁止 socket 網路。

另有可重跑的三帳號、兩個來源實驗：A 關閉、B 換至 V2、C 的 V3 proposal 已保存但 key CAS 中斷。primary 目前 key row 對 V1 的依賴是 0；完整物件盤點仍找到 V1 六份依賴（鏡像目前1、歷史3、提案2），V2四份（目前2、提案2），V3尚未提交提案1份。共28個物件版本的分類與獨立直接 JSON 枚舉相同。

原每個物件版本、hash-only 稽核、Apple grant、額度皆逐項比較保持。盤點沒有 KMS 呼叫／解密／恢復／刪除。獨立 AES 反例仍可解開 A 的舊副本，故不宣稱刪帳完成。正常、軟刪除與400未知三輪均保持銷毀未授權。

重播（既有測試依賴，不需雲端身分）：

```sh
PYTHONPATH=Backend/Flask:tools:engineering/phase2 python -B -m unittest -v test_lite_shared_key_inventory
PYTHONPATH=Backend/Flask:tools:engineering/phase2 python -B tools/lite_shared_key_inventory_probe.py --output inventory-probe.json
```

新 CI step 加入 `lite-prelaunch`，需授權推送後才能取得遠端證據；不得沿用父提交的成功 CI 宣稱本輪已跑。

## 尚未完成的正式驗收

完整來源清冊及其獨立核准、實際舊程序排空、真實 GCS metadata／版本讀回、備份保留政策、關帳刪除上限與 KMS disable／destroy 執行器仍待完成。也不改變 Apple Sandbox、個人服務部署、模型授權與外測審查尚待完成的狀態。原始研究資料與稽核持續保留。

## 官方參照

- [GCS soft delete](https://docs.cloud.google.com/storage/docs/soft-delete)：隱藏於一般清單、內容不能直接讀取；政策變更不會改寫過去物件的保留期。
- [Objects.list](https://docs.cloud.google.com/storage/docs/json_api/v1/objects/list)：versions 與 softDeleted 分開；短頁仍可能有 nextPageToken，分頁途中新增物件可能不在後續頁出現。
- [Objects.get](https://docs.cloud.google.com/storage/docs/json_api/v1/objects/get)：generation／ifGenerationMatch 與 media 條件。
- [KMS rotation](https://docs.cloud.google.com/kms/docs/rotate-key)：換 primary version 不會替舊資料重新加密或自動銷毀舊版。
