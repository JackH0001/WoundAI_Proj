# MMH 專用服務部署設定與待決事項

2026-10-08。**這是離線產生、可覆核的部署規格，尚未執行部署或鎖桶。** 原有完整映像與 39 項執行期驗證見 [驗證報告](medical_runtime_validation_20261008.md)。

## 本輪發現及處理

現有 `Backend/Flask/deploy_cloudrun.ps1` 設定媒體／稽核桶，但沒有傳入 `WOUNDAI_INSTITUTION_ORG` 或 `WOUNDAI_SECURITY_BUCKET`；密文設定也仍使用舊平台名稱。`provision_runtime_identity.ps1` 同樣是既有正式服務流程。它們不能直接當成 MMH 三桶及獨立帳號部署入口。沒有機構設定時，候選 app 為相容舊服務而允許 legacy 行為，不能期待應用啟動自動修正部署遺漏。

新增 `tools/plan_mmh_runtime.py` 只產生本機方案，不呼叫 gcloud、不建立資源、沒有 apply 選項。輸出明確為 deployable=false。驗證器對機構／桶／密文／權限／公開狀態採精確比對；即使修改者重新計算方案雜湊，也不能放寬這些設定。

## 固定設定

| 項目 | 本次規格 |
|---|---|
| project／number | woundai-jackh001／421209514056 |
| region／service | asia-east1／woundai-backend-mmhps20261007 |
| runtime | woundai-mmhps20261007-runtime@woundai-jackh001.iam.gserviceaccount.com |
| 機構 | WOUNDAI_INSTITUTION_ORG=mmhps20261007 |
| Profile／儲存 | medical；WOUNDAI_ENABLE_LITE_API=0；WOUNDAI_STORE=gcs；prefix=flywheel |
| 媒體桶 | woundai-mmhps20261007-media-421209514056 |
| 帳號／安全狀態桶 | woundai-mmhps20261007-security-421209514056 |
| 稽核桶 | woundai-mmhps20261007-audit-421209514056 |
| 初始存取 | 私有驗收；不授予 allUsers；既有服務不得改動 |
| 容量設定 | min=0、max=1、concurrency=1、2 CPU、4 GiB、timeout=120 秒 |

容量設定以先前容器測試上限作起點；不是已完成負載測試，也不是帳單硬上限。max=1 只設定擴展上限，不保證任何時間都只有一個執行個體，不能取代資料併發控制。初期私有服務不能直接讓一般手機使用者登入；後續開放方式須另有驗收，不把取得 URL 當成 App 已可使用。

候選映像來源 `073732dbf6338c01073f911ec9f3bac8a7eaaf6a`，digest `sha256:777c6cfb7f097946fdf8ea7bfaf8684d1f45ced3b115129139c3d492292e7e8b`，來源 manifest `adbdbb210f7f354dab938b1f859b4506b8f2b4630d2f7a1c5da8ab5d36d3b1f8`。此來源仍在 Draft PR。日後合併改變來源 SHA，正式部署證據必須重新綁定，不能改 GIT_COMMIT 冒充另一來源。

四把**尚未建立**的專用密文：

- ADMIN_PASSWORD → woundai-mmhps20261007-admin-password:1
- JWT_SECRET_KEY → woundai-mmhps20261007-jwt-secret:1
- FLASK_SECRET_KEY → woundai-mmhps20261007-flask-secret:1
- CARE_RECEIPT_SECRET → woundai-mmhps20261007-care-receipt-secret:1

`:1` 是新建資源預定的第一個版本，不是已驗證存在的版本。實際執行前須逐一確認版本 ENABLED，固定數字版本，不接受 latest 或其他環境名稱。密碼／金鑰不寫入方案、版控或報告；care receipt 必須符合現有 keyring 格式。

## 規劃的限定 IAM

所有下列權限只綁定上述 runtime，綁在具名桶／密文資源，不綁成專案層資料權限：

| 資源 | 精確權限／角色 |
|---|---|
| 媒體桶 | storage.objects.create/delete/get/list；自訂角色 woundaiMmhRuntimeObjects |
| 安全狀態桶 | storage.objects.create/get/list；自訂角色 woundaiMmhSecurityAppend |
| 稽核桶 | storage.buckets.get＋storage.objects.create/get/list；自訂角色 woundaiMmhAuditAppend |
| 四把 MMH 密文 | 各自 secretmanager.secretAccessor |

這些是待驗收的最小權限規格，不是已通過真實 GCS 業務流程的宣稱。三桶 IAM、Secret IAM、上層／其他資源授權及冒用鏈都須再次查核；錯誤／UNKNOWN 不可視為拒絕證據。既有舊 runtime Editor 問題仍未解除，不能先授予 MMH 讀寫再宣稱完成隔離。

## 七年鎖定需要單獨決策

候選程式的 GCS 稽核寫入目前要求：

- 只針對 `gs://woundai-mmhps20261007-audit-421209514056`。
- retentionPeriod 精確為 **220903200 秒（按 Google 年換算為七年）**，讀回 isLocked=true。
- 媒體／安全狀態桶、舊平台及 demo 不在鎖定範圍。

**七年是目前程式的稽核政策，不是本報告對醫療法規或 IRB 保存年限的判定。** Google 說明鎖定後不能取消或縮短保留期；物件保留期未滿前不能刪除／取代，桶需等所有物件滿期才能刪除，並會對專案加上防刪除 lien。[Google Bucket Lock](https://docs.cloud.google.com/storage/docs/bucket-lock)

Jack 尚未授權不可逆鎖定，因此本輪沒有設定保留期或呼叫 lockRetentionPolicy。若選擇繼續鎖定，批准內容應明確包含上述專案、單一稽核桶與 220903200 秒，並限於**隔離、稽核內容與部署前置檢查全部通過之後**執行。未通過不能因為拿到批准就提前鎖桶。若暫不鎖定，維持目前候選驗證；不降低 require_locked_audit_epoch 來繞過。

IRB 最後確認仍未送件。本階段只用合成／模擬資料，鎖桶或部署授權均不代表真實病患收案或資料使用核准。

## 本輪唯讀盤點

- Cloud Build v2 locations 列出地區，加 global，共 **44 個地區**。逐區完整分頁讀取 builds／triggers：48 筆建置、0 個 triggers、查詢時 0 個 QUEUED／WORKING／PENDING；全部位於 asia-east1，0 個 API 錯誤。
- 這消除了先前只查 global／asia-east1 的地區盲點。它不代表所有跨專案身分依賴已查清，也不保證盤點後不會有新建置；撤權前仍須即時重查。
- 讀回三桶及 runtime 身分符合既有基礎設定；稽核未鎖定。MMH service 不存在、MMH 密文數量 0，舊 Compute Editor 仍存在。
- 沒有建立／刪除資源、讀密文值、鎖桶或修改 IAM。既有 medical／demo 不變。

參照：[Cloud Build locations API](https://docs.cloud.google.com/build/docs/api/reference/rest/v2/projects.locations/list)、[分區 builds API](https://docs.cloud.google.com/build/docs/api/reference/rest/v1/projects.locations.builds/list)。

## 測試與可重現證據

```sh
python -B tools/test_plan_mmh_runtime.py
```

8/8 測試方法通過，含可變映像標籤、縮短 SHA、latest／非法密文版本、跨機構設定、三桶混用、local store、公開呼叫、放寬稽核、增加權限及自行修改 deployable 的拒絕案例。測試亦重新計算被竄改方案的雜湊，避免只驗 checksum 而沒有檢查內容；另確認修改某份方案不能污染下一份方案的權限常數。已納入 p0-4-audit 工作流程。

本次候選規格 SHA-256：`88de3d98ce339323c4a1bcd3cabc1b9ce18bd3f8469e9d7807de3948b8c675b9`。

本機證據位於 `/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-institution-evidence-20261007/mmh-deployment-preflight-20261008/`：runtime-plan.json、planned-secret-versions.json、build-region-inventory.json、resource-preflight.json，以及只含唯讀 API 的盤點腳本。方案產生器沒有雲端執行功能，不能單靠 plan 驗證通过就跳過上述證據。

下一步：按舊 505ff2e 的實際契約補齊可用性基線（不能要求 App 27 在不存在的 care/attest 上成功），完成最小權限遷移；覆核本規格後再實作受閘門保護的 MMH 專用執行入口。部署、真實 GCS 讀回、冷啟動帳號持久化及手機上傳驗收都仍未完成。


## MMH 稽核桶名稱相容性補驗證

既有 `check_locked_epoch_gate.py` 要求桶名含有 `epoch`，因此會拒絕已核准建立的 MMH 專用稽核桶。現在只對專案編號、asia-east1、medical profile、機構代碼及三個完整桶名全部相符的 MMH 組合放行名稱檢查；仍要求 GCS 實際專案／地區、七年保留、已鎖定、正式寫入閘門與全桶無物件。另核對實際建構的安全狀態桶，不能只相信環境變數。

`python -B engineering/phase2/test_check_locked_epoch_gate.py`：13/13 測試方法通過，包含 MMH 未鎖定、錯誤保留期、未知讀回、設定／實際桶身分不一致、非空桶與正式閘門失敗均拒絕。這是離線測試，真實 MMH 桶仍未鎖定，不能通過部署閘門。

兩個 engineering 檔依現行 owner_guard 仍歸 Windows；本次依 Jack 已明確授權 Mac 進行必要後端對齊的例外提交，未修改所有權規則，也不宣稱已完成 Windows 全套驗證。
