# WoundLite 隔離 Linux 建置驗證方案

日期：2026-10-04 起草；2026-10-05 更新。狀態：使用者已授權的一次 Linux Cloud Build 成功，尚未部署 Lite Cloud Run。

## 已完成執行（2026-10-05）

建置 `bf8aef7b-2992-4932-9026-d833b4928155`，asia-east1，三步 SUCCESS，84.579 秒。最終來源 manifest SHA-256 `16abf4b8a7da325105eb918be5cc4c625c2fd94dbda0cfc7b4d335d40dc706a0`，plan SHA-256 `94a562b6d67686457224fa5e2001b4d69b82c121809ae889a4b8b7e660980410`；取代下方起草版雜湊，不能混用。gcloud CLI 534 不接受 E2_STANDARD_2，因此以官方 REST API 提交相同配置，僅執行一次雲端工作。

真實三個 ONNX 模型推論、canonical golden、合成 raw RGB-D 回執／清除及未配置驗證時拒絕上傳均通過。建置前 61 項有效權限核對為拒絕，完成後桶層級建置授權移除、專用身分停用。計算運算費約 US$0.00846，非帳單。沒有映像推送、Cloud Run 部署或正式服務變更。這次使用暫存 LocalStore，並未驗證 Apple 或 GCS 線上全鏈路。

證據：repo 外 `woundai-competitive-review-20261003/cloud-build-20261005/validation.json`、`build-final.json`、`cleanup-validation.json`；保留策略見 `docs/lite_research_retention_plan.md`。下方保留原規劃與當時本機驗證，閱讀狀態以本節為準。

## 本次目的與邊界

驗證實際部署封裝在 Linux amd64 上能安裝鎖定的相依、啟動真實 Flask 程式、載入三個 ONNX 模型，以及通過合成 RGB-D 保存／回執／撤回探針。本次不部署 Cloud Run、不推送映像、不修改正式或示範服務、不讀取正式密文。不能取代 Apple App Attest、GCS 真實持久化、實機拍攝精度或上架驗收。

## 可覆核輸入

- 工作 HEAD：`d7e36175020f041bef39b130f811862613902180`，工作樹有未提交修改；不能把 HEAD 當成候選包內容證據。
- 候選內容由固定清單與 SHA-256 manifest 綁定，manifest SHA-256：`b2a1e7e14769d2a3206596136ea10df8e85ff760acccd4aeadfc6ea6c2792356`。
- plan SHA-256：`e8ed8d3b4b157d07779c54b022c483d60707dba329c25c03f1511c1e897b5d05`。
- 本機證據目錄：`/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-competitive-review-20261003/lite-build-plan-20261004`。
- 完整指令與 Cloud Build JSON：該目錄的 `review/plan.json`。候選來源、探針及合成 fixture 在 `review/submission/`。
- `gcloud meta list-files-for-upload` 實際列出的 49 個檔案與指定包完全相符；這項檢查未執行上傳。

## 請求授權的資源

專案僅限 `woundai-jackh001`（編號 `421209514056`），區域 `asia-east1`。

| 資源 | 名称／用途 |
| --- | --- |
| 專用建置身分 | `woundai-lite-build@woundai-jackh001.iam.gserviceaccount.com` |
| 建置桶 | `gs://woundai-lite-build-421209514056`，只放此次來源與建置日誌；統一桶層級存取、禁止公開、soft delete 0 |
| 自訂角色 | `projects/woundai-jackh001/roles/woundaiLiteBuildObjects` |
| IAM 綁定 | 只在上述建置桶授予上述身分此自訂角色，不作專案層級角色綁定 |
| 一次 Cloud Build | `E2_STANDARD_2`、1200 秒逾時；容器探針使用 `--network=none`、唯讀根目錄、暫存目錄及移除 Linux capabilities |

自訂角色僅含 `storage.buckets.get`、`storage.objects.get`、`storage.objects.list`、`storage.objects.create`、`storage.objects.delete`。授權範圍為建置來源與日誌桶；不授予 Secret Manager 或執行服務權限。

目前預設 Cloud Build 身分與正式服務相同，故此次不使用預設身分。唯讀核對確認上述三個新名稱均不存在。既有資源如在執行前出現，將停下而非沿用。

## 執行前後的必要核對

1. 重新核對專案編號、必要 API、資源名稱不存在、來源與探針所有雜湊。
2. 重新讀取專案及上層 IAM。群組、網域、公開、聯合或未知主體，或建置身分直接取得上層角色，均拒絕。
3. 建立後讀回專用身分、自訂角色與桶 IAM；核對有效權限不能讀取正式／示範密文與資料。UNKNOWN、查詢錯誤或非明確拒絕不能算通過。
4. 通過才上傳這 49 個檔案並執行一次建置；遠端第一步再核對 manifest、探針和 fixture 雜湊。
5. 保存 build ID、日誌及結果；若失敗，先分析，不擅自反覆建立付費工作。

`plan_lite_cloud_build.py` 是離線方案產生器，不是執行器。上述 IAM 有效權限等前置檢查仍需執行時逐項落實；目前祖先 IAM 檢查通過，不能當作完整有效權限或跨專案冒用鏈證明。

## 驗證結果

- 建置方案測試 9/9、來源包測試 5/5、封裝前處理測試 10/10。
- static mobile logic、parity、`git diff --check` 均 exit 0。
- 以最終送建置包中的探針、fixture、來源包，在 macOS 執行實際三模型推論、Flask 路由拒絕與合成 raw RGB-D 回執／清除：exit 0。
- 這是原生 macOS rehearsal，**尚非 Linux 容器驗證成功**。新測試已加入本地 workflow 設定，尚未推送或產生新的 GitHub CI 結果。

## 成本與停止方式

按 Cloud Build 公開 E2 standard 2 費率 US$0.006／分鐘估算，20 分鐘計算費約 US$0.12（未扣免費額度）；另有儲存、日誌、網路等费用。逾時不是帳單硬上限，來源和日誌保留也會持續占用儲存。

取消僅針對本次返回的 build ID。取回證據後可移除此次精確桶綁定並停用專用身分；不自動刪除桶、角色或其他資源。

參照：[Cloud Build 定價](https://cloud.google.com/build/pricing)、[自訂建置身分與日誌設定](https://docs.cloud.google.com/build/docs/securing-builds/configure-user-specified-service-accounts)、[Storage IAM 權限](https://docs.cloud.google.com/storage/docs/access-control/iam-permissions)。
