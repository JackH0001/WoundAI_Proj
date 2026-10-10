# MMH 私有部署與合成 GCS 驗收

2026-10-09。MMHPS20261007 已依使用者授權，以未鎖桶的特殊驗證模式部署。
本結果是私有後端階段驗收，不是機構手機發行、IRB 核准或完整 WoundAI3D 驗收。

## 來源與服務

- project：`woundai-jackh001`，region：`asia-east1`。
- origin：`https://woundai-backend-mmhps20261007-z4kgfkob4a-de.a.run.app`。
- revision：`woundai-backend-mmhps20261007-00001-hqd`。
- 後端來源：`7ed425d455e3e8526eadd230c226f327df3a82fc`。
- 映像 digest：`sha256:814d266e84f4a50b36363923e8a262aa97f4eaa18e6a5588ac27331231ab996b`。
- 部署計畫 SHA-256：`8b2274148302aad4800155391441ed5af4583c1673d369f6acfa7ade4db02876`。
- runtime：`woundai-mmhps20261007-runtime@woundai-jackh001.iam.gserviceaccount.com`。
- medical profile、org `mmhps20261007`、Lite API 關閉；min=0、max=1、concurrency=1、2 CPU、4 GiB。
- `mmh-unlocked-validation`；實際 retention=0、locked=false。沒有 WORM 宣稱，也沒有鎖桶。
- 保持私有 IAM 呼叫檢查；匿名請求已確認拒絕。一般手機不能只輸入 App 密碼就直連。

## 實際結果

| 驗證 | 結果及範圍 |
|---|---|
| 即時有效 IAM | 264/264；之後再次讀回 metadata、IAM／資源清單，未發現變動才 create |
| Cloud Run | operation 完成、無 error；確切映像、環境、私有政策及 revision 設定通過 |
| 線上合成流程 | 45/45；實際服務與 GCS，不是替身 store |
| 三類資產 | JPEG 5,800 bytes、組織 PNG 791 bytes、Float32 LE 深度 768 bytes 逐位元讀回一致 |
| 同意與權限 | 無照護 receipt 不保存；缺研究同意拒絕；nurse 不可醫師標註／上傳深度；外 org 登入拒絕 |
| 重送與破損輸入 | 相同標註 duplicate_skipped；相同深度不標為替換；截斷深度、缺內參參照及零深度拒絕，原良好資料保留 |
| 撤回 | trainable 由 1 變 0；再次標註／深度拒絕；影像讀取 410；合成 JPEG 隔離讀回一致 |
| 帳號及稽核 | 臨時 physician／nurse 已停用；原 physician token 被拒絕；稽核鏈完整性及驗證事件記錄通過 |
| 舊服務 | 舊正式、demo 的 revision／流量／映像／執行身分與部署前讀回相同 |

同映像先前容器測試為 39/39 HTTP、19/19 MMH 守門、三模型有限值輸出。
線上 classify 使用合成圖的 `seg=color` 路徑；不能把它當成線上 ONNX 精確度測試。
建立請求及權杖修正後部署工具測試 121/121；HTTP/1 讀回修正後兩庫為 122/122。
修正經過詳見 [工具驗證](mmh_create_request_validation_20261009.md)。

## 未完成與使用界線

- 未實測冷啟動帳號留存、機構手機入口、iOS／Android 安裝與匯出。
- max=1 是上限，不能宣稱絕無多實例／併發。
- 原始深度為合成 16×12、0.3 m；保存格式／內參完整不等於真實 RGB-D 配準、時間同步或 LiDAR 精確度已驗證。
- 完整 pose、資產版本關聯、3D 組織匯入、封存撤回仍須逐項驗收。
- 撤回是排除訓練／拒絕存取與隔離；沒有宣稱所有原始深度已物理刪除。
- 未重跑研究模型、未開放公開招募；IRB 尚未送件，限模擬／測試資料。
- 日常 dr01／ns01／as01／eng01／admin01 尚未核發；不可使用已停用的臨時驗收帳號。

完整登入、桶與權限規劃見 [環境與帳號手冊](mmhps20261007_environment_and_accounts.md)。
原始證據保存於 Mac 的 `woundai-institution-evidence-20261007/mmh-unlocked-main-20261008/`，
包含 `deployment-create-refresh-20261009/`、`live-acceptance/result.json`、
`services-after-private-acceptance.json`；不把帳密或原始研究影像放入 Git。

## 2026-10-10：既有服務的零流量更新入口

`tools/stage_mmh_revision.py` 與首次建立服務的工具分開。只允許替換 MMH 的
映像 digest、GIT_COMMIT 及候選 revision 名稱，同時把預設流量釘在舊 revision
100%，新 revision 0%，加入私有 `candidate` 標籤供後續驗收。沒有 promote 選項，
不修改 IAM、桶、密文版本、機構或資源限制。Google 自動填入的預設 startup probe
也保留；未知容器設定會拒絕，避免更新容器陣列時意外移除設定。

執行前重新驗證已合併來源、映像配方及 digest、12 項更新適用的 metadata 条件、
限定資源授權及有效 IAM；沿用建立服務的第 13 項「服務不存在」檢查不適用於更新，
改由完整的既有服務設定、uid、generation、etag 與觀察流量驗證取代。
檢查後再讀回一次可變設定，只有與已確認方案完全相同才送 PATCH。逾時或不明回應
保留 outcome_unknown，不重送；每次使用新的專屬 journal，status 必須讀回 operation
及實際 trafficStatuses，不能把 PATCH 收件當成驗收完成。

```sh
python3 -B tools/test_stage_mmh_revision.py
python3 -B tools/stage_mmh_revision.py prepare \
  --old-plan /path/to/accepted-runtime-plan.json \
  --new-plan /path/to/new-runtime-plan.json --proposal /path/to/new-proposal.json
python3 -B tools/stage_mmh_revision.py check --proposal /path/to/new-proposal.json \
  --build-id BUILD_UUID --journal /path/to/new-check-journal
```

stage 需要同一方案的 `--confirm-sha256`，並再次執行上述查核。一般入口不切至候選版；
標籤網址仍要求 Google IAM，加上 App 的身分驗證。**零流量不等於零副作用**：
候選與舊版使用同三個桶，對標籤網址進行驗收仍可能寫入合成資料，必須獨立記錄。
`rollback_request()` 只產生移除候選標籤、維持舊版 100% 的流量回復請求；沒有自動執行
或資料回復功能，也不表示故障 revision 的完整恢復已在線上實測。

本輪離線 18 項測試通過；另以八種變異移除 etag、權限、資源清單、確認、觀察流量、
metadata、未知結果及密文版本守門，8/8 被斷言捕獲。這些是工具測試，不替代線上
新 revision 的 GCS／同意／撤回流程，也不替代實機 RGB-D 配準。

Google 參照：[Service PATCH](https://docs.cloud.google.com/run/docs/reference/rest/v2/projects.locations.services/patch)、
[etag 與 trafficStatuses](https://docs.cloud.google.com/run/docs/reference/rest/v2/projects.locations.services)。

### 同輪 CI 發現的 LocalStore 並行讀取缺口

公開庫 PR #22 的 `60a0cef` 在既有稽核並行測試失敗（186/200 筆），而母庫相同實作
當次通過。這是排程相關的競態，不能用單次綠燈消除。問題區域與已合併 main
`1c7daf3` 逐位元相同：`append_chained()` 與 `chain_tail()` 有共用鎖，但
`read_lines()`／`read_lines_fresh()` 可讀到尚未寫完的 JSON，導致合法寫入被拒絕。

修正讓完整稽核讀取共用同一把行程內 RLock；其他 JSONL 讀取不改。
新增測試將真正的 `append_chained()` 暫停在半筆已 flush 的 JSON，分別測試一般／
fresh 讀取、相對／絕對路徑、不同 LocalStore 實例。原版八項斷言失敗，修正版
八項通過；既有 8 執行緒 × 25 筆全部保存，並保留破損歷史拒絕、CAS 與 GCS 守門。
這不是跨行程檔案鎖，也未變更 GcsStore。

此修正從 Mac 提交 Backend／engineering，依本對話 Jack 授權 Mac 後端檢查提交及
必要對齊程序作為本次範圍例外；不修改 owner_guard 的一般所有權規則，亦不把
例外說成 owner_guard 原生通過。相關九支回歸腳本均 rc=0；Windows PowerShell
5.1 全套未在本輪執行。新提交仍需各庫 CI 通過及合併，現有映像不包含這項修正。
