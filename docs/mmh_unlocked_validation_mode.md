# MMH 特案未鎖桶建置

2026-10-08 Jack 決定：高合規環境採鎖桶稽核；有明確特案需求的環境可另案採未鎖桶建置。
本次僅啟用 `MMHPS20261007` 的隔離驗證路徑，不更改既有正式／demo 服務，也不鎖桶。
Mac 端後端修改依本次明確修改、測試、重建授權執行；既有 owner_guard 分工不因此全面放寬。

## 兩條路徑

| 項目 | 高合規正式路徑（預設） | MMH 特案驗證路徑（明確選用） |
|---|---|---|
| 模式 | `locked-seven-year`，未設值時沿用 | `mmh-unlocked-validation` |
| 桶政策 | 實讀已鎖定、220903200 秒保留 | 實讀無保留政策、未鎖定 |
| 歷史稽核寫入檢查 | 完整物件 generation manifest + 已驗證前綴／新增部分 | 每次全部讀回，驗雜湊鏈、generation，讀後再次比對 manifest |
| 新格與 receipt | 條件建立 `if_generation_match=0`，不覆寫 | 相同 |
| 對外說明 | 符合實測保留狀態才顯示 WORM | mutable audit, not WORM；health 顯示選用模式 |
| 模式轉換 | 不因失敗自動降級 | 不因桶被改成鎖定就自動升級；需另案新正式紀元 |

未鎖桶仍有稽核，但**不具不可竄改／不可逆保留保證**。高權限外部管理者可能刪改物件；
讀取與寫入間無法以應用程式消除所有外部競態。沒有獨立可信 checkpoint 時，冷啟動後無法
辨識已被整條重算且自洽的歷史鏈。雜湊鏈通過不是 IRB、法規或臨床用途核准。

## 本案精確範圍

- Project：`woundai-jackh001`（`421209514056`），region：`asia-east1`。
- 專用私有服務：`woundai-backend-mmhps20261007`，Cloud Run 注入的 `K_SERVICE` 必須相符。
- medical profile、Lite API 關閉、institution `mmhps20261007`、GCS prefix `flywheel`。
- media：`woundai-mmhps20261007-media-421209514056`。
- security：`woundai-mmhps20261007-security-421209514056`。
- audit：`woundai-mmhps20261007-audit-421209514056`。
- 執行身分：`woundai-mmhps20261007-runtime@woundai-jackh001.iam.gserviceaccount.com`。
- 保留三桶、四把獨立且鎖定版本號的密文與限定 IAM；audit/security 執行身分無 delete 權限。

其他特案不是設定任意機構名稱即可放行；應另案覆核其服務、桶、身分及部署計畫。
未識別模式、讀回失敗、錯誤身分／桶組合、任何非預期保留政策，都拒絕；沒有 LocalStore fallback。

## 操作與部署

使用既有 `tools/plan_mmh_runtime.py`，明確增加 `--audit-mode mmh-unlocked-validation`，
其他 image digest、source commit、manifest、四把 secret version 均仍需指定。
產生的 `spec_sha256` 必須重新覆核，不能沿用鎖桶計畫的雜湊。
`check_mmh_runtime_preflight.py` 與 `create_mmh_private_service.py` 根據完整精確計畫驗證，
仍要求來源已合併且等於 remote main、映像來源綁定、有效 IAM、空稽核桶及兩輪新鮮中繼資料檢查。
建立服務仍只允許 private create，不修改舊服務、不開放 allUsers、不更改 App 登入位址。

當驗證歷史稽核失敗，此行程會鎖住後续稽核寫入，health 的 store 說明顯示 `writes blocked`。
先保全錯誤證據、檢查物件與權限變動；不要用重啟清空快取來當修復方式。
本次沒有加入雲端清除、lifecycle、自動輪替或刪除撤回標記的工具。

## 成本與驗收界線

未鎖定模式每次新增稽核須 O(n) 歷史物件下載及兩次列舉；從空鏈累积 N 筆，歷史物件下載
約 N(N−1)/2 次，另計失敗重試、分頁和 receipt 讀回。這是初期小規模驗證取捨，不能沿用
鎖桶增量快取的成本／延遲估算。上線後需量測稽核筆數與請求時間；不能為省成本偷偷跳過驗證。
若超出操作延遲可接受範圍，另案設計受保護 checkpoint、分段驗證或資料庫，不自動放寬此門檻。

目前仍只允許合成／測試資料。未鎖桶部署不代表 iPhone 能直接存取私有服務；正式 App 入口、
帳號角色、GCS 持久保存、冷啟動、撤回、RGB-D 資訊完整性及跨平台輸出仍要各別驗收。
WoundAI3D 研究資格與完整 3D 可用性不得由此模式或單元測試推定。

## 可重現測試

```sh
python -B engineering/phase2/test_mmh_unlocked_audit.py
python -B tools/test_mmh_unlocked_deployment.py
python -B engineering/phase2/test_audit_chain_concurrency.py
python -B engineering/phase2/test_check_locked_epoch_gate.py
python -B tools/test_plan_mmh_runtime.py
python -B tools/test_check_mmh_runtime_preflight.py
python -B tools/test_create_mmh_private_service.py
python -B engineering/phase2/test_test_isolation_from_cloud.py
```

新模式測試使用記憶體 GCS 替身，不讀取真實資料、不呼叫雲端。雲端驗收證據應另記建置 SHA、
映像 digest、計畫雜湊、服務 revision、實際桶政策及合成流程結果。
