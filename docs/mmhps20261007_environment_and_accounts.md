# MMHPS20261007 機構版：環境、登入與帳號交付說明

核對日期：2026-10-11。MMH 私有服務與 Google/IAP 入口已完成既有驗證，iOS 1.0(32) 已安裝並啟動（本輪僅確認安裝／啟動）；先前 31 版的 dr01 手機登入、單筆標註與原始深度自動送件，以及真正修邊後的修訂已核對。31 版最新遮罩與深度保存通過。歷史遮罩不可覆寫與修訂綁定已完成本機修正及測試，但尚未部署後端；線上仍有共享遮罩問題。RGB-D 配準及中斷續傳尚未全面驗收，不能宣告整體研究管線完成。另見 [32 版修訂保存驗證](mmh_revision_assets_validation_20261011.md)。詳見 [31 版修訂驗收](mmh_build31_revision_validation_20261011.md)。本文件限醫療端 MMH，與 WoundLite 分開。

目前結果與來源綁定見 [私有部署與合成驗收](mmh_private_acceptance_20261009.md)。
最新 [MMH Google/IAP 入口驗證與待辦](mmh_google_entry_contract_20261010.md)。
先前 [Mac 私有入口與冷啟動驗證](mmh_console_access_validation_20261010.md)，提供限時本機管理入口；不是手機入口。
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
| MMH API／服務 origin | `https://woundai-backend-mmhps20261007-z4kgfkob4a-de.a.run.app`；私有 IAP 入口（原 revision 100%） |
| 本輪候選驗證入口 | `https://candidate---woundai-backend-mmhps20261007-z4kgfkob4a-de.a.run.app`（tag，不切預設流量） |
| MMH 瀏覽器登入網址 | 本輪使用候選 origin 加 `/console`；先以獲准 Google 帳號通過 IAP，再登入 MMH App 帳號。初期僅 Jack 有入口權；2026-10-11 Comet 已正常顯示候選 console 登入頁，MMH 管理者登入及 dr01 建立已由使用者完成 |
| MMH 媒體／安全狀態／稽核桶 | 三桶已使用；含已撤回的合成驗收紀錄及稽核，未鎖定 |
| MMH 帳號 | bootstrap admin 登入已驗證；臨時醫師／護理帳號已停用；dr01 已由 Jack 建立並回報手機登入成功，其餘範例帳號待核發 |
| iOS 專用 target | `WoundAIInstitution`，bundle `com.woundai.app.mmhps20261007` |
| iOS 後端設定 | `InstitutionInfo.plist` 已固定上述候選 URL；Google 登入後必須通過指定 MMH health 回應才開放 App 帳密；拒絕沿用其他環境 |
| iOS 本機匯出 | 29 起預設關閉；開啟後存入 App 加密私密空間，再從設定手動匯出。不含深度、不代表雲端成功；舊 Documents 副本不自動搬移 |
| Android | internalTest 尚待接入機構專用後端，不能把舊後端當 MMH |

以上服務查核限指定 project 的 asia-east1；桶清單為該 project 全部位置。2026-10-07 已建立下列三個桶與專用執行身分；MMH service 已完成私有合成驗收；iOS 已填入受保護的候選入口，手機 native token 已實測通過；機構帳密、單筆手機上傳及修訂已驗收，完整限制見本文開頭；Android 尚未核對此輪接續上傳。建置與有效 IAM 反例見 [MMH 第一階段驗證](mmh_foundation_validation_20261007.md)。

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

部署程式將 users.jsonl 路由至 WOUNDAI_SECURITY_BUCKET，機構 GCS 設定要求三桶不同。指定 IAM、稽核鏈及真實 GCS 合成流程已驗證；2026-10-10 已觀察一次自然新實例啟動後，管理者可登入、原帳號及停用狀態仍保留；不等於多實例故障切換或備份恢復驗收。三桶為 ASIA-EAST1／STANDARD／uniform IAM／禁止公開；無 retention、lifecycle、versioning 或 soft delete。已移除新桶的 projectEditor／projectViewer 預設授權、保留 Owner；專案層舊執行身分 Editor 已移除，指定的刪桶／冒用權限矩陣通過；此結果不等於所有主體或跨專案多跳冒用鏈均已排除。手機只呼叫 HTTPS API，不能填入 `gs://` 當後端位址，也不能持有儲存桶金鑰。不得沿用寬權限預設 Compute 身分。本案已選擇未鎖定模式，不執行不可逆鎖定。

## 為什麼需要 Google 與 WoundAI 兩層登入

| 登入 | 決定的事情 | 不代表什麼 |
|---|---|---|
| 個人 Google 帳號 | 是否列入 MMH 私有入口的獲准名單；通過 IAP 才能連到服務 | 不授予醫師、護理、管理權限，也不授予 GCP 專案管理權 |
| WoundAI 機構帳號，例如 dr01 | 屬於哪個機構、可執行哪些操作；標註稽核記錄實際應用帳號與角色 | 不能繞過 Google 入口；知道應用密碼不等於取得私有入口資格 |

操作順序：Google 登入並顯示入口驗證通過 → WoundAI 機構帳號登入 → 按角色執行量測、覆核、上傳或管理。

這是「入口准入＋應用角色」兩道授權，不是同一帳號的雙因素驗證，也不能據此宣稱 Google 帳號已與 dr01 強制一對一綁定。目前後端未發現對 Google subject 與應用帳號強制映射的實作；應用稽核中的醫師身分來自 WoundAI 帳號。正式人員需一人一帳號，dr01 僅為目前合成驗收帳號。

Google／WoundAI 密碼彼此獨立，舊後台／demo 也不會自動同步。瀏覽器與手機各自維持入口登入狀態。App 的「登出 Google 與雲端帳號」會清除這兩層的本機登入狀態；人員撤權則應由管理者分別處理入口名單及應用帳號，不能把本機登出當成撤銷所有已簽發權杖。

入口名單操作：Google Cloud → IAP → asia-east1 的 MMH 服務 → 資源權限，配置 `roles/iap.httpsResourceAccessor`。OAuth 仍在外部測試模式，新使用者另需列入 Google Auth Platform → Audience → Test users。角色帳號由 MMH 主控台管理；兩處都要驗收。

最新離線操作手冊：`iOS/WoundMeasurementApp/Resources/manual.html`（與 Android assets 同內容，以 MMH iOS 專節標明差異）。本次文件修訂尚未打包進已安裝的 32 版，須下一次建置才會出現在手機。

## 各權限範例帳號（dr01 已建立，其餘待建立）

這些帳號只作合成資料的驗證身分。真正使用時一人一帳號，不能多人共用 dr01／admin01 產生醫師背書。實際機構核准與資料範圍尚待確認，不把此表當成真實病患收案許可。

| 完整登入帳號（規劃） | 角色 | 用途 | 不具備的關鍵權限 |
|---|---|---|---|
| `mmhps20261007:dr01` | physician | 已建立；限本人合成驗收，手機登入成功；臨床流程、醫師確認、訓練標註 | 帳號管理、稽核管理 |
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

1. MMH origin 已列於上表，入口由 IAP 保護；一般手機只帶 App JWT 不足以進入。iOS 機構版自 1.0(28) 起固定候選 origin，先以獲准 Google 帳號通過 IAP，再登入本環境的機構 App 帳號。Android internalTest 尚待實作與驗收，不能沿用舊正式／demo。
2. 在該環境初始化管理者，確認帳號持久化及稽核寫入成立，再建立其餘帳號。POST `/api/v1/users` 必須明確指定 `org: mmhps20261007`、user、role；使用 `generate_password: true` 可由伺服器產生一次性顯示的隨機密碼。密碼不寫入 Git、手冊、PR 或聊天室。
3. **本次候選修正**：WOUNDAI_INSTITUTION_ORG=mmhps20261007 由服務啟動設定綁定。省略 org 的 console 建帳號及 bootstrap 會使用該機構；外部 org 被拒絕。bootstrap 的實際帳號為 `mmhps20261007:admin`，不是 admin01；後續可建立具名管理者並停用 bootstrap。此 org 綁定已在線上驗證；舊平台維持原行為。
4. 瀏覽器須先透過已授權的私有入口，才可在 `MMH origin/console` 登入。App 設定中的後端位址填 **origin 本身**（不加 `/console`、不加 `/api`）；帳號可填 `dr01`（本 MMH 服務自動補機構），或完整 `mmhps20261007:dr01`。`mmhps20261007` 單獨是機構代碼，不是使用者帳號。原本已登入其他環境者先登出，不能沿用舊 token／網址。
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
