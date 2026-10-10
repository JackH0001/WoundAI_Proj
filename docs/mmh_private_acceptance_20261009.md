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
