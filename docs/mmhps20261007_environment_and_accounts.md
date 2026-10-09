# MMHPS20261007 機構版：環境、登入與帳號交付說明

核對日期：2026-10-09。**MMH 私有服務已部署，264/264 有效 IAM、45/45 真實 GCS 合成流程通過；尚未開放一般手機入口或完成機構 App 發行。** 本文涵蓋 WoundAI 醫療端 iOS／Android 機構特殊版，不包含 WoundLite 民眾版。

目前結果與來源綁定見 [私有部署與合成驗收](mmh_private_acceptance_20261009.md)。
舊正式與 demo 的 revision、流量、映像及執行身分讀回皆未變動。
MMH 明確使用 `mmh-unlocked-validation`，無保留政策且未鎖定；不宣稱 WORM。
高合規路徑仍維持七年鎖定守門。本階段 IRB 尚未送件，限模擬／測試資料。

## 目前已確認與尚未建立的項目

| 項目 | 目前結果 |
|---|---|
| GCP project | `woundai-jackh001` |
| 規劃區域 | `asia-east1` |
| 機構顯示代碼 | `MMHPS20261007` |
| 帳號 org（已驗證） | `mmhps20261007`；後端只接受小寫英數／連字號 |
| MMH API／服務 origin | `https://woundai-backend-mmhps20261007-z4kgfkob4a-de.a.run.app`；私有 IAM 入口 |
| MMH 瀏覽器登入網址 | 上述 origin 加 `/console`；須先具備 Cloud Run 入口授權，只有 App 密碼無法直接進入 |
| MMH 媒體／安全狀態／稽核桶 | 三桶已使用；含已撤回的合成驗收紀錄及稽核，未鎖定 |
| MMH 帳號 | bootstrap admin 登入已驗證；臨時醫師／護理帳號已停用，下表日常帳號仍未核發 |
| iOS 專用 target | `WoundAIInstitution`，bundle `com.woundai.app.mmhps20261007` |
| iOS 後端設定 | `InstitutionInfo.plist` 的 `WoundAIInstitutionBackendURL` 為空；拒絕沿用其他環境 |
| iOS 本機匯出 | App「檔案」中的 `MMHPS20261007/Exports`；目前不含深度、不代表已上傳雲端 |
| Android | internalTest 尚待接入機構專用後端，不能把舊後端當 MMH |

以上服務查核限指定 project 的 asia-east1；桶清單為該 project 全部位置。2026-10-07 已建立下列三個桶與專用執行身分；MMH service 已完成私有合成驗收；機構版設定仍未填入可供手機使用的入口。建置與有效 IAM 反例見 [MMH 第一階段驗證](mmh_foundation_validation_20261007.md)。

既有平台用途不同，**不是 MMH 新環境**：

- 舊醫療平台：`https://woundai-backend-z4kgfkob4a-de.a.run.app`；既有 App 亦使用 `https://woundai-backend-421209514056.asia-east1.run.app`。
- demo：`https://woundai-backend-demo-z4kgfkob4a-de.a.run.app`，限合成／模擬資料測試。demo 帳號、密碼及資料不會自動同步到 MMH。
- 不應拿舊平台或 demo 的 `/console` 為 MMH 建帳號。

## 資源清單與目前狀態

以下資源已建立並完成指定範圍驗收；私有服務不是一般手機可直接登入的公開入口：

| 用途 | 建議名稱 | 交付前必須讀回 |
|---|---|---|
| Cloud Run 醫療 API＋console | `woundai-backend-mmhps20261007` | 真實 HTTPS origin、revision、映像 digest、流量分配 |
| 專用執行身分（限定資源授權已驗證） | `woundai-mmhps20261007-runtime` | 實際 SA email、限定資源 IAM、無其他環境有效權限 |
| 媒體／標註／深度（合成讀寫已驗證） | `woundai-mmhps20261007-media-421209514056` | location、禁止公開、uniform IAM、版本／完整性策略 |
| 帳號及安全狀態（合成帳號已驗證） | `woundai-mmhps20261007-security-421209514056` | 密碼只存雜湊、跨實例持久化、IAM、備份與恢復 |
| 稽核（雜湊鏈已驗證，未鎖定） | `woundai-mmhps20261007-audit-421209514056` | 實際保留與鎖定狀態；保留不等於已不可逆鎖定 |

部署程式將 users.jsonl 路由至 WOUNDAI_SECURITY_BUCKET，機構 GCS 設定要求三桶不同。指定 IAM、稽核鏈及真實 GCS 合成流程已驗證；帳號冷啟動留存尚未實測。三桶為 ASIA-EAST1／STANDARD／uniform IAM／禁止公開；無 retention、lifecycle、versioning 或 soft delete。已移除新桶的 projectEditor／projectViewer 預設授權、保留 Owner；專案層舊執行身分 Editor 已移除，指定的刪桶／冒用權限矩陣通過；此結果不等於所有主體或跨專案多跳冒用鏈均已排除。手機只呼叫 HTTPS API，不能填入 `gs://` 當後端位址，也不能持有儲存桶金鑰。不得沿用寬權限預設 Compute 身分。本案已選擇未鎖定模式，不執行不可逆鎖定。

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

## 建帳號與登入方式（先完成私有入口授權）

1. 真正 MMH origin 已列於上表，但 Cloud Run 仍要求 Google IAM 呼叫授權；一般手機只帶 App JWT 不足以進入。先完成機構入口設計及驗收，再更新 iOS 專用設定與 Android internalTest，驗兩端皆未落回舊正式／demo。
2. 在該環境初始化管理者，確認帳號持久化及稽核寫入成立，再建立其餘帳號。POST `/api/v1/users` 必須明確指定 `org: mmhps20261007`、user、role；使用 `generate_password: true` 可由伺服器產生一次性顯示的隨機密碼。密碼不寫入 Git、手冊、PR 或聊天室。
3. **本次候選修正**：WOUNDAI_INSTITUTION_ORG=mmhps20261007 由服務啟動設定綁定。省略 org 的 console 建帳號及 bootstrap 會使用該機構；外部 org 被拒絕。bootstrap 的實際帳號為 `mmhps20261007:admin`，不是 admin01；後續可建立具名管理者並停用 bootstrap。此 org 綁定已在線上驗證；舊平台維持原行為。
4. 瀏覽器須先透過已授權的私有入口，才可在 `MMH origin/console` 登入。App 設定中的後端位址填 **origin 本身**（不加 `/console`、不加 `/api`）；帳號填完整 `mmhps20261007:dr01` 等身分。原本已登入其他環境者先登出，不能沿用舊 token／網址。
5. 專用 iOS 版的位址可能由發行設定鎖定；若仍顯示未設定，須由新版 App 配置修正，不能把一般醫療 App 的改址當成 MMH 專用版驗收。
6. 驗收時比對登入回應 identity／org／role／perms。帳號已建立、能登入、權限拒絕正確、冷啟動仍存在，四項分開記錄。帳號停用後不得只測新登入；還要驗舊 token 的失效行為。

## 完成後實際交付表

後端私有驗收已完成；機構 App 交付仍須補齊下列項目：

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
- 上述 2026-10-07 結果為本機測試；2026-10-09 線上驗收另涵蓋 admin 登入、臨時 physician／nurse、停用後舊 token 拒絕，不能外推為所有日常帳號均已核發。

歷史 42/51 未通過結果已由後續 [Editor 撤權驗證](mmh_editor_migration_validation_20261008.md) 51/51，以及本次 264/264 部署矩陣取代。這些矩陣範圍不同，保留歷史證據但不混用數字。
