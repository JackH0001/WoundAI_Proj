# MMH 第一階段建置與驗證（2026-10-07）

範圍：醫療端 WoundAI，機構 MMHPS20261007。基線為 PR #18 的 cbeafd58505ddaa845772926358be562480de1dc；獨立分支 codex/mmh-foundation-20261007。本階段完成資源基礎與候選程式，不是已部署、已開放登入或可收真實病患資料的聲明。

## 已實際建立

GCP project `woundai-jackh001`（421209514056），region `asia-east1`：

- `gs://woundai-mmhps20261007-media-421209514056`
- `gs://woundai-mmhps20261007-security-421209514056`
- `gs://woundai-mmhps20261007-audit-421209514056`
- `woundai-mmhps20261007-runtime@woundai-jackh001.iam.gserviceaccount.com`

三桶 STANDARD、uniform IAM、public access prevention enforced；soft delete=0，未設定 lifecycle、versioning 或保留政策。建立後逐一讀回；再次執行 foundation apply 的 created 清單為空。沒有應用資料上傳、密文建立、SA key、runtime 權限授予、Cloud Run 部署或帳號核發。

`provision_mmh_foundation.py` 只接受上述專案／名稱／設定。此工具不是完整隔離驗收；若日後設定 retention／lifecycle，不能再當成相同的空桶基礎重跑。

## 發現並處理的 IAM 問題

新桶自動帶入 projectEditor／projectViewer 便利授權。舊 `woundai-backend` 的實際 runtime 為 Compute 預設 SA，專案層具有無條件 roles/editor。首次 Troubleshooter 對 security 桶的 storage.objects.list 回 GRANTED。

已用 `restrict_mmh_bucket_iam.py`，以三桶政策的 etag 為條件，只移除三個新空桶的 Editor／Viewer 便利授權，保留 projectOwner 的兩項原有 Owner binding。未知／條件式 binding 或讀回不符會停止；未改動專案 IAM、舊服務、demo 或 Lite。

再向 Policy Troubleshooter 查詢，三個桶結果一致：

| 舊 production runtime 的權限 | 各桶結果 |
|---|---|
| storage.objects.list | NOT_GRANTED |
| storage.objects.get | NOT_GRANTED |
| storage.buckets.delete | **GRANTED（專案層 Editor）** |

上述只有具名舊 runtime、列出的三種權限與當下政策；不是所有主體／所有冒用鏈的完整證明。**MMH 隔離仍未完成**。下一階段要完成舊 runtime 最小權限遷移或其他經驗證的隔離措施，再授予 MMH runtime 限定資源權限。不可藉鎖桶取代 IAM 隔離，也不可直接移除運作中舊服務的 Editor 而不先驗證依賴。

## 候選程式

- `WOUNDAI_INSTITUTION_ORG=mmhps20261007` 啟動時驗格式，不能為 default／空值／Lite。未設定時保持舊服務行為。
- 專用服務的登入、bootstrap、帳號建立綁定 org；安全桶發現其他 org 帳號即拒絕。bootstrap 在建帳號前要求 audit intent 成功。
- 一般 JWT 與一次性碼交換都驗 org／sub／user 與目前帳號角色、停用狀態；無法取得帳號狀態則拒絕。尚不宣稱只改密碼就能撤銷全部既有 token。
- `users.jsonl` 與其分片導向 WOUNDAI_SECURITY_BUCKET；機構 GCS 模式要求媒體／安全／稽核三桶不同。帳號歷史不能 move。
- 既有 require_locked_audit_epoch 保持不變：GCS 稽核寫入仍要求讀回七年保留且 isLocked=true。新桶未鎖定，所以目前不部署、不建帳號。
- Lite 建置白名單補上共用 import；測試 runner 清洗新的機構／安全桶環境變數。

## 驗證

| 檢查 | 結果與限制 |
|---|---|
| 隔離 Python 全套回歸 | **103/103 測試檔通過**，87.722 秒；source_snapshot_same_after=true |
| MMH 機構、帳號、JWT、Store 單元／端點 | **13/13** |
| 真實 Flask app 子行程 HTTP 邊界 | **1/1**，涵蓋跨 org 登入、JWT、OTC、停用舊 token |
| foundation 建置防護 | **8/8** |
| 新桶 IAM 精確移除／etag／讀回防護 | **4/4** |
| 雲端讀回 | 資源與設定成功；舊 runtime 讀取已拒絕，刪桶仍允許，故隔離閘門未通過 |

103 支回歸在 Mac Python 3.12、PowerShell 7 跑；不冒充 Windows PowerShell 5.1 驗證。之後變更僅 IAM 工具、foundation 說明、workflow、文件與測試用隨機金鑰；新增 IAM 測試及全部 22 項既有 MMH 測試已重跑通過。新分支 CI 待 GitHub 實際結果，不能沿用 PR #18 的綠燈作為本次證据。

本輪 Backend/Flask、tools、workflow 的 Mac 修改遵循 Jack 在本串「授權必要對齊程序」及「先開始建置」授權；保留原 owner_guard 規則，不以 windows 參數冒充執行平台。未改動 Android 原工作目錄、舊 iOS 未提交資料、母庫 main 或公開 main。

本輪 17 個變更檔案的 Gitleaks 掃描無發現。全工作樹掃描曾標記既有測試／範例和本次合成 fixture；本次 fixture 改為執行時產生隨機測試金鑰，不新增豁免。變更檔案掃描通過不等於整個歷史已重新稽核。

可重現本輪新增測試（使用已安裝後端相依的 Python，各自獨立執行）：

```sh
python -B tools/test_mmh_institution.py
python -B tools/test_mmh_app_boundary.py
python -B tools/test_provision_mmh_foundation.py
python -B tools/test_restrict_mmh_bucket_iam.py
```

## 下一階段放行條件

1. 有效 IAM 隔離，包括讀、寫、刪除、政策修改與冒用路徑；先解決已重現的 Editor 刪桶權限。
2. 具名稽核桶保留／不可逆鎖定需 Jack 單獨確認；未收到該確認前不得鎖定。
3. 限定 IAM、密文、私有服務部署，讀回真正 origin、digest／revision、帳號持久化與允許／拒絕矩陣；之後才能填入 iOS／Android 與交付登入網址。
4. RGB-D／組織圖層完整性與 WoundAI3D 匯入仍有既有缺口：組織資產契約、不可變完整封包、配準與拍攝姿態資訊、Android 深度缺漏、raw RGB-D 封存覆蓋。建桶不等於修好這些缺口。

## 參照

- [MMH 環境與帳號交付說明](mmhps20261007_environment_and_accounts.md)
- [醫療端整合交接](medical_institution_handoff_20261007.md)
- 本機證據目錄：`/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-institution-evidence-20261007/`
- 資源：mmh-foundation-created.json、mmh-foundation-idempotent-readback.json。
- IAM：mmh-bucket-iam-restricted.json、mmh-old-runtime-security-list-troubleshoot.json、mmh-iam-effective-summary.json、mmh-iam-after-*.json。
- 測試：mmh-python-validation/python-summary.json、mmh-institution-tests.log、mmh-app-boundary-tests.log、mmh-foundation-tests.log、mmh-bucket-iam-tests.log。
