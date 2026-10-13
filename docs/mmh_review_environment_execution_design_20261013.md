# MMH 外部審查示範環境執行設計

使用者已選定獨立合成示範環境。新版 App 必須提供明確的示範入口，讓審查者可以完成量測、修邊、醫師確認、時間軸、標註與深度保存流程，同時保持 MMH 的資料與 Google 門禁隔離。本設計仍不可部署：示範登入方式已確定為獨立 WoundAI 帳號，專屬後端模式與 App 環境切換尚未完成，沒有建立新雲端資源的授權。

## 本輪確認的阻礙

2026-10-13 以 gcloud 唯讀列出 woundai-jackh001／asia-east1 的服務、桶及映像庫。現有服務為 woundai-backend、woundai-backend-demo、woundai-backend-mmhps20261007、woundai-mmh-access-manager；未見本設計的審查服務或三個審查桶。既有 woundai-medical-build 映像庫可供未來限定建置方案使用，無須另建映像庫。

不能直接沿用舊 demo：

1. `auth_users.py` 的 DEMO_SEED_ROLES 只有 nurse／assistant，且明確禁止 gt.verify 與 annotation.submit。它無法完成本次醫師確認與標註上傳驗收。
2. `deploy_demo_candidate.ps1` 固定使用 LocalStore；容器回收與多實例會失去資料連續性，不能據此驗收修訂、去重及深度收據。
3. `store.py` 的 mmh-unlocked-validation 模式只允許精確的 MMH service／org／三桶。不得借用此模式、冒用 MMH service 名稱或放寬它的既有檢查；需建立獨立的 synthetic-review-validation 模式與測試。
4. 手機仍需完成同一環境 context 的登入、同意同步與背景工作接線；已有 SQLite／影像／深度／outbox 與設定範圍，不等於環境切換已完成。

## 示範登入決策

2026-10-13 使用者已確認：只有獨立示範入口使用 WoundAI 示範帳號，MMH 仍維持 Google IAP 加上 WoundAI 身分。新的示範 Cloud Run 需要對外可呼叫，但每個資料端點仍須 WoundAI 驗證；這是新的入口權限，必須在具體部署方案中另行確認，不可沿用 MMH IAP 授權推定允許。

示範不要求 Google 帳號，不複製 MMH 帳密或取消 MMH 門禁。示範供審查及其他場域的合成操作驗證；這項產品方向不等於真實病患收案授權。

Apple 的 Before You Submit 要求提供可用示範帳號或完整示範模式，以及審查期間可用的後端。2.1(a) 另指出，因法律或安全義務無法提供帳號而改用內建示範模式時，需要 Apple 事先同意。因此優先採「可實際登入的獨立示範帳號」，不把只有錄影、靜態畫面或未核准內建模式當成完整審查入口。這是方案依據，不是通過審查的保證。[Apple App Review Guidelines](https://developer.apple.com/app-store/review/guidelines/#app-completeness)

## 擬新增資源

| 類型 | 擬定值 |
|---|---|
| Project／region | woundai-jackh001／asia-east1 |
| Cloud Run | woundai-mmh-review-demo |
| Runtime | woundai-mmh-review-runtime@woundai-jackh001.iam.gserviceaccount.com |
| Org | mmh-review-demo |
| 媒體桶 | woundai-mmh-review-media-421209514056 |
| 帳號與安全桶 | woundai-mmh-review-security-421209514056 |
| 稽核桶 | woundai-mmh-review-audit-421209514056 |
| Secrets | woundai-mmh-review-jwt-secret、flask-secret、care-receipt-secret、admin-password（各自完整前綴均為 woundai-mmh-review-） |
| 示範帳號 | review01，僅在 mmh-review-demo 擁有 physician 角色 |
| 初期資源 | 2 vCPU、4 GiB、min 0、max 1、concurrency 1、timeout 120 秒 |

三桶採統一桶層級存取與禁止公開物件。Runtime 對自己的媒體桶僅 get／list／create／delete；對安全桶僅 get／list／create；對稽核桶另允許 buckets.get。Runtime 只可讀自己的四個指定 secret 版本，不給專案廣泛角色、不給服務帳號冒用、不讀寫 MMH、Lite 或舊 demo 的資料／密文。新管理者與示範帳號須獨立建立，不複製 MMH 帳號內容或密碼。

不鎖桶、不設定 lifecycle 或自動刪除。新的合成稽核模式須清楚回報不具不可變性，並保留條件式追加、競態與內容核對。保存政策與清理流程需在正式執行方案中明訂；本設計不授權刪除既有資料。

## 服務功能與資料約束

- 機構綁定固定 mmh-review-demo，JWT／care receipt 金鑰獨立；MMH token 不可用於示範，示範 token 不可用於 MMH。
- 不提供公開註冊。示範 physician 不具有 user.manage、audit.read、backend.config 或 gcp.console 權限。
- 只供合成資料與操作練習，UI、資料 metadata、匯出皆標示示範；不放真實病患、也不接 WoundAI3D 研究訓練匯出。僅靠標示不能證明影像沒有個資，仍需制定測試資料與上傳限制。
- 醫師確認、修訂資產、重送去重、保存收據、RGB-D 對應沿用相同業務契約，不以假成功取代保存。示範也必須能在容器重啟後讀回資料與帳號。
- 不改舊正式、舊 demo、MMH 或管理服務的流量、IAP、OAuth、帳號或 bucket。

## 成本試算與限制

先以公開 request-based 表列的 CPU 0.000024 USD／vCPU秒及記憶體 0.0000025 USD／GiB秒作敏感度試算，2 vCPU／4 GiB 每活躍秒合計0.000058 USD。假設每次完整示範流程活躍30至60秒，不扣除同帳單共享的免費額度：[Cloud Run pricing](https://cloud.google.com/run/pricing)

| 每月完整示範流程 | 純運算 USD |
|---|---|
| 100 次 | 0.174 至 0.348 |
| 1,000 次 | 1.74 至 3.48 |
| 10,000 次 | 17.40 至 34.80 |

這不是全包月費，也不是已量到的處理時間。仍須加請求、GCS 容量與操作、映像保存、Secret Manager、日誌、流量、建置與稅費；額外冷啟動時間及異常流量另計。網頁目前顯示預設區域表，部署前須核對 asia-east1 的最終 SKU，不把預設表數字當成已核准預算。min 0 節省閒置費用但仍可能冷啟動；max 1 不是費用硬上限。

Cloud Build 表列 e2-standard-2 為0.006 USD／分鐘，20分鐘的單次建置運算示例為0.12 USD，尚未含映像與來源保存。真正機型、最長時間與提交次數須綁定建置方案。[Cloud Build pricing](https://cloud.google.com/build/pricing)

## 驗收與執行順序

1. 示範登入方式已確認；完成後端新 profile、seed／CAS 帳號與 App 固定環境 context。
2. 本機測試：同名影像／record ID、切換中舊回覆、同意撤回、背景上傳、token audience／org、錯誤 service／origin、修訂與 RGB-D 對應，皆不可跨環境。
3. 固定來源 SHA，完成覆核／CI；準備精確建置與資源套用命令、secret metadata 與回復步驟，才請求資源及部署授權。
4. 新資源建立後核對有效 IAM，包括繼承與冒用路徑；先私有驗證，再依所選入口方案確認對外可用範圍。
5. 合成資料完成首次登入、量測、醫師確認、存時間軸、標註／深度保存、相同資料重送、修訂保留與容器重啟讀回。
6. 新 iOS／Android build 用明確版本號，驗證環境切換及現有 MMH 資料保留，再更新 TestFlight／Play 與審查說明；商店受理、外測核准及商店安裝驗收分別留證。

本輪產生機械可讀草案 `synthetic-review-proposed-resources.json`，`deployable=false`，未綁定來源或映像 digest。SHA-256：15232f0def21c23cbd05f0e452bd6990e0627644e096ab911584de1f983b1abc。檔案與成本計算位於 `woundai-institution-evidence-20261007/mmh-iap-update-20261011/store-and-members/reviewer-boundary-20261013/`。

本輪是唯讀雲端查核與執行設計，沒有新增測試通過數字，亦未將前輪測試冒稱為本輪實測。待確認登入選項與既有來源推送問題仍保持待答。

## 2026-10-13 最新決策與整合關卡

使用者要求以最新 MMH 修正版整合兩平台、醫療後端與管理模組，再建立可長期用於合成示範的獨立環境；研究資料完整性是發布前的主要驗收條件。舊資料按用途及證據完整性分級，缺漏資料列隔離封存候選，不等於停用整個舊服務，亦未執行搬移或刪除。詳見 [整合驗證](mmh_full_alignment_research_validation_20261013.md) 與 [研究資料驗收矩陣](research_dataset_acceptance_matrix_20261013.md)。

新決策草案 `synthetic-review-selected-direction-20261013.json` 仍為 `deployable=false`、`apply_authorized=false`；SHA-256：2c550aac8001d39c260de77b16b3f878453a6b7baca9ec6484f02d76e3b8e7d7。舊草案保留作版本證據，不再用其中的待答登入欄位代表目前決策。
