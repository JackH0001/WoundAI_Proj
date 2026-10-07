# MMHPS20261007 機構版：環境、登入與帳號交付說明

核對日期：2026-10-07。狀態：**已建立三個空桶與執行身分；隔離及部署未完成／未開放登入**。本文涵蓋 WoundAI 醫療端 iOS／Android 機構特殊版，不包含 WoundLite 民眾版。

最新進度：[第二階段 IAM 驗證與權限阻斷](mmh_iam_validation_20261007.md)。三桶已直接綁定專用標籤，但 deny policy 尚未建立，標籤本身不會拒絕存取。已授權的專案範圍限時 denyAdmin 授予遭 Google 拒絕；讀回確認專案 IAM 未變、沒有殘留提升權限。隔離仍未通過，不能提供正式登入網址。

## 目前已確認與尚未建立的項目

| 項目 | 目前結果 |
|---|---|
| GCP project | `woundai-jackh001` |
| 規劃區域 | `asia-east1` |
| 機構顯示代碼 | `MMHPS20261007` |
| 帳號 org（規劃） | `mmhps20261007`；後端只接受小寫英數／連字號 |
| MMH API／服務 origin | **未建立，無可用網址** |
| MMH 瀏覽器登入網址 | **未建立**；驗收後為實際 origin 加 `/console` |
| MMH 媒體／安全狀態／稽核桶 | **三桶已建立，尚無應用資料；稽核未鎖定** |
| MMH 範例帳號 | **未建立**；下表是命名及權限規劃，不是已核發帳號 |
| iOS 專用 target | `WoundAIInstitution`，bundle `com.woundai.app.mmhps20261007` |
| iOS 後端設定 | `InstitutionInfo.plist` 的 `WoundAIInstitutionBackendURL` 為空；拒絕沿用其他環境 |
| iOS 本機匯出 | App「檔案」中的 `MMHPS20261007/Exports`；目前不含深度、不代表已上傳雲端 |
| Android | internalTest 尚待接入機構專用後端，不能把舊後端當 MMH |

以上服務查核限指定 project 的 asia-east1；桶清單為該 project 全部位置。2026-10-07 已建立下列三個桶與專用執行身分；尚未部署 MMH service，機構版設定未填入 URL。建置與有效 IAM 反例見 [MMH 第一階段驗證](mmh_foundation_validation_20261007.md)。

既有平台用途不同，**不是 MMH 新環境**：

- 舊醫療平台：`https://woundai-backend-z4kgfkob4a-de.a.run.app`；既有 App 亦使用 `https://woundai-backend-421209514056.asia-east1.run.app`。
- demo：`https://woundai-backend-demo-z4kgfkob4a-de.a.run.app`，限合成／模擬資料測試。demo 帳號、密碼及資料不會自動同步到 MMH。
- 不應拿舊平台或 demo 的 `/console` 為 MMH 建帳號。

## 資源清單與目前狀態

以下三個桶與 SA 已建立；Cloud Run 名稱仍是方案，**不是已可登入服務**：

| 用途 | 建議名稱 | 交付前必須讀回 |
|---|---|---|
| Cloud Run 醫療 API＋console | `woundai-backend-mmhps20261007` | 真實 HTTPS origin、revision、映像 digest、流量分配 |
| 專用執行身分（已建立，未加執行權限） | `woundai-mmhps20261007-runtime` | 實際 SA email、限定資源 IAM、無其他環境有效權限 |
| 媒體／標註／深度（空桶已建立） | `woundai-mmhps20261007-media-421209514056` | location、禁止公開、uniform IAM、版本／完整性策略 |
| 帳號及安全狀態（空桶已建立） | `woundai-mmhps20261007-security-421209514056` | 密碼只存雜湊、跨實例持久化、IAM、備份與恢復 |
| 稽核（空桶已建立，未鎖定） | `woundai-mmhps20261007-audit-421209514056` | 實際保留與鎖定狀態；保留不等於已不可逆鎖定 |

候選程式已將 users.jsonl 路由至 WOUNDAI_SECURITY_BUCKET，機構 GCS 設定要求三桶不同。本機測試通過，線上 IAM、稽核及部署配置尚待驗收。三桶為 ASIA-EAST1／STANDARD／uniform IAM／禁止公開；無 retention、lifecycle、versioning 或 soft delete。已移除新桶的 projectEditor／projectViewer 預設授權、保留 Owner；專案層舊執行身分的刪桶權限仍未解決，不能稱完成隔離。手機只呼叫 HTTPS API，不能填入 `gs://` 當後端位址，也不能持有儲存桶金鑰。不得沿用寬權限預設 Compute 身分。新稽核桶的不可逆鎖定另行確認，不因本文出現桶名就視為已授權鎖定。

## 各權限範例帳號（全部待建立）

這些帳號只作合成資料的驗證身分。真正使用時一人一帳號，不能多人共用 dr01／admin01 產生醫師背書。實際機構核准與資料範圍尚待確認，不把此表當成真實病患收案許可。

| 完整登入帳號（規劃） | 角色 | 用途 | 不具備的關鍵權限 |
|---|---|---|---|
| `mmhps20261007:dr01` | physician | 臨床流程、醫師確認、訓練標註 | 帳號管理、稽核管理 |
| `mmhps20261007:ns01` | nurse | 個案／同意、量測、儲存、統計 | 醫師確認、訓練標註、帳號管理 |
| `mmhps20261007:as01` | assistant | 臨床量測及檢視 | 儲存時間軸、個案／同意管理、統計、訓練標註 |
| `mmhps20261007:eng01` | engineer | 後端設定、統計與稽核檢視 | 帳號管理、臨床儲存、醫師確認、訓練標註 |
| `mmhps20261007:admin01` | admin | 帳號管理、後端設定、統計與稽核檢視 | 臨床量測／儲存、醫師確認、訓練標註 |

權限來源為 `Backend/Flask/auth_users.py` 的 PERMS。下表是程式宣告，不代表每個 permission 都已被所有 API 完整使用；整體權限仍須以端點拒絕測試驗收。`measure.sample` 在現有程式註解中明確未作為端點守門。`gcp.console` 是 App 權限名稱，**不會授予 Google IAM 或 Google 登入權**。

| permission | physician | nurse | assistant | engineer | admin |
|---|:---:|:---:|:---:|:---:|:---:|
| `patient.manage` | ✓ | ✓ | — | — | — |
| `measure.clinical` | ✓ | ✓ | ✓ | — | — |
| `record.save` | ✓ | ✓ | — | — | — |
| `gt.verify` | ✓ | — | — | — | — |
| `annotation.submit` | ✓ | — | — | — | — |
| `clinical.view` | ✓ | ✓ | ✓ | — | — |
| `measure.sample` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `backend.config` | — | — | — | ✓ | ✓ |
| `flywheel.stats` | ✓ | ✓ | — | ✓ | ✓ |
| `audit.read` | — | — | — | ✓ | ✓ |
| `user.manage` | — | — | — | — | ✓ |
| `gcp.console` | — | — | — | ✓ | ✓ |

`lite` 角色不是機構醫療人員，不核發在本範例表；其 PERMS 為空也不等於完全不能呼叫任何已登入端點。

## 建帳號與登入方式（部署驗收後才可執行）

1. 先讀回部署結果取得真正 MMH origin，更新 iOS 專用設定與 Android internalTest 機構設定，驗兩端皆未落回舊正式／demo。
2. 在該環境初始化管理者，確認帳號持久化及稽核寫入成立，再建立其餘帳號。POST `/api/v1/users` 必須明確指定 `org: mmhps20261007`、user、role；使用 `generate_password: true` 可由伺服器產生一次性顯示的隨機密碼。密碼不寫入 Git、手冊、PR 或聊天室。
3. **本次候選修正**：WOUNDAI_INSTITUTION_ORG=mmhps20261007 由服務啟動設定綁定。省略 org 的 console 建帳號及 bootstrap 會使用該機構；外部 org 被拒絕。bootstrap 的實際帳號為 `mmhps20261007:admin`，不是 admin01；後續可建立具名管理者並停用 bootstrap。以上尚未部署，舊平台仍維持原行為。
4. 瀏覽器在驗收後提供的 `MMH origin/console` 登入。App 設定中的後端位址填 **origin 本身**（不加 `/console`、不加 `/api`）；帳號填完整 `mmhps20261007:dr01` 等身分。原本已登入其他環境者先登出，不能沿用舊 token／網址。
5. 專用 iOS 版的位址可能由發行設定鎖定；若仍顯示未設定，須由新版 App 配置修正，不能把一般醫療 App 的改址當成 MMH 專用版驗收。
6. 驗收時比對登入回應 identity／org／role／perms。帳號已建立、能登入、權限拒絕正確、冷啟動仍存在，四項分開記錄。帳號停用後不得只測新登入；還要驗舊 token 的失效行為。

## 完成後實際交付表

交付時用**查證過的值**替換所有「待建立」：

- App 版號／build、bundle 或 package ID、下載或安裝來源。
- API origin、瀏覽器 `/console`、health URL、登入測試時間、伺服器 org。
- project／region、每個桶完整 `gs://` 名稱、用途、存取身分、公開防護、保留／鎖定／清理狀態。
- 後端 Git SHA、映像 digest、revision、前端 build 與部署的對應。
- 實際已建立帳號的 identity／role／enabled 狀態（不含密碼及密碼雜湊）；對照上表的允許／拒絕測試。
- 冷啟動留存、跨機構隔離、上傳修訂去重、每項資產讀回與撤回驗收。

完整 RGB-D、組織分類層與 3D 匯入仍有缺口，見本機 `rgbd_workflow_readiness_20261007.md` 證據報告與 [機構交接](medical_institution_handoff_20261007.md)。不能只憑 PR CI 全綠便改稱「正式收案環境已完成」。


## 本次文件與權限核對結果

- 2026-10-07 以隔離 LocalStore 執行：RBAC 37 項、admin console 59 項、lite role 30 項，全數通過；網址契約 7/7。
- 文件矩陣由 PERMS 產生，另逐格比對來源；範例 org/user 均符合實際格式驗證。
- 這些是本機角色／端點與文件的驗證，不是尚未存在的 MMH 線上帳號登入驗收。

最新最小權限遷移基線為 **42/51，未通過**；見 [遷移方案與逐項結果](mmh_runtime_migration_plan_20261007.md)。新 profile 增加服務帳號保護及舊功能存取控制，不能把新增檢查後的比例當成已完成權限修正。
