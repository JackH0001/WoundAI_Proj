# MMH：舊執行身分最小權限遷移與回復方案

日期：2026-10-07（台北）。狀態：唯讀盤點與驗收程式完成；尚未變更既有服務／角色，尚未放行 MMH。

## 實際盤點

- 專案 `woundai-jackh001`（421209514056），Cloud Run 全區清單 2 個服務，jobs 0。
- `woundai-backend` 的 100% 流量在 `woundai-backend-00039-xdk`，仍為程式 505ff2e；使用預設 Compute 身分。
- `woundai-backend-demo` 使用獨立 `woundai-demo-run`，本次不變更。
- asia-east1 共 41 個 revisions，其中 39 個使用 Compute、2 個使用 demo 身分；不能只檢查目前服務模板，舊 revision／回復版本仍須考慮身分依賴。
- Cloud Build global 清單 0、asia-east1 清單 45 筆（查詢上限 50）；其中 44 次使用預設 Compute 身分，1 次使用已停用的 Lite build 身分。該 45 筆的狀態為 44 SUCCESS、1 FAILURE，未見 QUEUED／WORKING；這僅是盤點時點與查詢範圍。
- Cloud Build asia-east1 預設身分仍是 Compute。global／asia-east1 triggers 皆為 0；其他建置地區尚未盤點，不能宣稱全專案沒有 trigger。
- Cloud Scheduler 34 個支援地區皆完成查詢，合計 1 個 `woundai-warmup`，沒有設定 OIDC／OAuth service account。
- 五個使用者可列出的服務帳號，user-managed key 清單均為 0；不含系統管理金鑰，也不是所有跨專案冒用鏈檢查。
- 所有上述查詢成功，沒有以 API 失敗冒充空清單。未讀取密碼、secret versions 內容、研究影像或病患檔案。

## 舊服務依賴與保留的功能

舊身分：`421209514056-compute@developer.gserviceaccount.com`。

| 資源 | 現有直接授權／使用 | 遷移處理 |
|---|---|---|
| woundai-flywheel-jackh001 | 直接 storage.objectAdmin；另有 projectEditor 便利授權 | 初期保留直接授權，不刪資料；專用 runtime 日後改為經測試的 get/list/create/delete 自訂角色 |
| woundai-flywheel-jackh001-audit | 直接 storage.objectAdmin；另有 projectEditor 便利授權 | 初期保留相容性，另評估 append/get/list 精簡；不要把移除 Editor 說成已成為 append-only |
| woundai-admin-password | 直接 secretAccessor | 保留；不讀取值 |
| woundai-jwt-secret | 直接 secretAccessor | 保留；不讀取值 |
| run-sources-woundai-jackh001-asia-east1 | 最近舊身分建置的 source bucket | 改由專用 medical build 身分讀取 |
| asia-east1/cloud-run-source-deploy | 最近建置推送的映像庫 | build 身分限定該 repository writer；runtime 不取得推送能力 |
| Cloud Logging | 最近建置為 CLOUD_LOGGING_ONLY | build 身分需 logging.logWriter；不能只給來源桶和映像庫就宣稱可建置 |

最近使用舊身分的建置 `19dd96f4-976f-4846-91ad-816fbb4d4a64` 成功，使用 docker builder。此依賴清單來自該筆建置，不代表所有歷史建置都有相同配置。

## 執行順序與停止條件

1. 建立專用 medical build 身分方案，限定來源桶 objectViewer、指定 Artifact Registry repository writer、project logging.logWriter。不可借用已停用的 Lite build 身分。正式、demo 和機構的建置入口都必須明確指定此 build 身分，避免繼續落回預設 Compute。
2. 在獨立的合成測試建置中驗證來源下載、映像推送和日誌寫入；綁定來源 Git SHA、build ID、輸出 digest。未完成這三項，不能撤銷舊 Editor。建置成功不授予 deploy、actAs、secretAccessor 或 MMH 桶權限。
3. 核對當下無使用舊身分的 QUEUED／WORKING 建置，以及沒有新出現的工作負載。對正式服務先執行已登入的合成量測／存取驗收、記錄 baseline。health 200 不是完整的讀寫驗收。
4. 在既有直接資料／密文授權仍完整的條件下，讀取新 etag，只從 project roles/editor binding 移除該一位 member，保留其他 members、bindings 與條件。不得套用過期整份 IAM snapshot。這一階段可先保留 Cloud Run 的既有 runtime 身分，避免同時變更 revision、模型和稽核契約。
5. 完成 least-privilege profile 的 51 項 v3 權限查核，並重跑同一份已登入合成流程及 build smoke。任何 UNKNOWN、資源／主體回應不符、必要功能拒絕均失敗。若失敗，按下列回復程序處理，MMH 保持未開放。
6. 另行完成專用正式 runtime 與新 revision 的遷移。現有 `provision_runtime_identity.ps1`／`deploy_cloudrun.ps1` 要求新鎖定稽核紀元；舊 audit bucket 目前 retention=220903200 但沒有 isLocked，不能直接用既有腳本升版。不得改為跳過 locked 閘門，也不得將本次授權視為不可逆鎖桶授權。

2026-10-08 更新：獨立 medical build SA、專用來源桶與映像庫已建立，13 項有效權限及一次合成 build smoke 通過，見 [建置驗證](medical_build_validation_20261008.md)。這次使用全新來源桶與映像庫，不授權 builder 存取上表的舊來源／舊映像庫；上表的遷移處理是早期方案，後續入口須對齊新資源。

2026-10-08 後續：完整建置入口已改造並完成一次完整醫療映像建置、digest 讀回及 PowerShell 7 真實驗證，見 [操作與驗證紀錄](medical_image_build_runbook.md)。目前仍在 Draft PR，尚未合併或部署。

尚缺：正式服務已登入合成端到端 baseline、其他建置地區／潛在跨專案使用核對。控制台已登入且可讀取統計與系統狀態，但沒有量測／存檔入口，不能替代 App 寫入驗收。這是可用性證據缺口，不是使用者未授權；因此尚未撤銷 Editor 或切換正式流量。

2026-10-08 續查：已完整分頁查詢 Cloud Build API 的 44 個地區，48 筆歷史建置、0 個 triggers、查詢當下無進行中建置，沒有 API 錯誤；其他建置地區這一項已補齊，跨專案依賴與舊服務業務 baseline 仍待完成。新增 [MMH 專用部署規格](mmh_runtime_deployment_review_20261008.md)：既有正式部署腳本未提供機構 org／安全桶及 MMH 專用密文，不能直接套用。本規格尚未執行，不新增鎖桶授權。

## 回復程序

- 變更前後保存 IAM etag、bindings 與雜湊，保留在本機受控證據目錄。
- 若移除 Editor 後既有功能失敗，重新讀取最新 IAM，確認差異後只恢復該原有 Editor member，保留期間他人新增的設定；用新 etag 提交並讀回。不得整份覆寫舊 policy。
- 回復代表重新出現本次已知 MMH 隔離缺口，必須將驗收標記失敗並持續阻擋 MMH；不能以「服務回復」算成隔離完成。
- 此階段不改 revision／流量；後續專用 runtime 切換另須綁定原 revision、digest、流量和必要權限回復，不得順便升版模型。

## 可重現驗證

```sh
python -B tools/test_verify_mmh_effective_iam.py
python -B tools/test_protect_mmh_buckets.py
python -B tools/verify_mmh_effective_iam.py --profile least-privilege --report /private/tmp/mmh-least-privilege.json
```

新 profile 保留舊 35 項 MMH 拒絕條件，將舊媒體桶 delete 控制改為必須拒絕；增加 MMH SA delete/disable/update 拒絕，以及舊媒體讀寫、稽核讀取／新增和兩把密文 access 必須允許。密文資源使用專案編號。舊 bucket-deny profile 保留原行為供比較，兩種證據不可混用。此矩陣只驗指定主體的直接權限，不是整個專案所有主體的封閉性證明。

本機測試 14+13=27 項通過；四種變異全部被捕獲（舊桶刪除被允許、少一項必要讀寫控制、密文用 project ID、允許刪除 MMH SA）。實際 51 項 baseline 結果另列於本文件末尾；它在撤權之前執行，不能當成遷移後驗收。

證據在 `woundai-institution-evidence-20261007/`：mmh-runtime-inventory.json、mmh-runtime-revisions.json、mmh-old-build-dependencies.json、mmh-dependency-*.json、mmh-least-privilege-baseline.*。原始 IAM／資源清單留本機，不上傳公開 repo。

參照：[Google Cloud Run service identity](https://docs.cloud.google.com/run/docs/securing/service-identity) 建議用較小權限取代預設 Editor，並先分析變更影響；[既有階段驗證](mmh_iam_validation_20261007.md)。

## 51 項雲端基線結果

REST v3 全部 51 項取得回應，42 項符合預期、9 項不符合，程序如預期 exit 1。沒有以 UNKNOWN 或失敗請求算通過。

| 未通過的能力 | 項數 | 結果 |
|---|---:|---|
| 刪除三個 MMH 桶 | 3 | 舊 Compute 身分 CAN_ACCESS |
| 刪除舊媒體桶 | 1 | 舊 Compute 身分 CAN_ACCESS；新 least-privilege 模式要求拒絕 |
| MMH SA actAs、建立 key | 2 | CAN_ACCESS |
| MMH SA delete、disable、update | 3 | CAN_ACCESS |

舊媒體 get/list/create/delete、舊稽核 get/list/create、兩把正式密文 versions.access 皆符合應允許的預期。這些是有效權限分析，沒有實際讀取密文內容或寫入資料，也不能替代應用程式端到端測試。

42/51 與上一階段 35/40 使用不同 profile，不能用兩個比例比較改善程度。本次未做 IAM 變更，新增四項未通過只是驗證範圍擴大。覆核後仍保持停止部署。

再次讀回 project IAM 與盤點起點完全一致，無 denyAdmin binding；證據 mmh-migration-iam-unchanged.json。
