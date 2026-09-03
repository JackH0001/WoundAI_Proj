# P0-4 canonical runtime 升版程序

`canonicalization_version` 包含 OpenCV 版本。staging 物件與 bind 會記錄該版本，prepare
與 repair 都會拒絕跨版本處理；因此升版前若 staging 未排空，可能留下不可 promote 的
`promotion_lost` 終局。這份程序是每次修改 Python base、OpenCV、JPEG 參數或 lock 時的
強制閘門。

## 1. 升版前（唯讀）

1. 停止 App 臨床持久化入口或安排維護窗。
2. 列出 `flywheel/staging/` 與 `flywheel/staging_meta/` 數量，不輸出病患鍵名到一般日誌。
3. 執行 `p0_4_staging_tools.py repair` 與 `sweep` 的 list-only 模式，逐筆依既有授權處置。
4. 只有 staging 與 staging_meta 都為 0 才可升版；否則中止。不得以刪 P、繞過版本檢查
   或批次改 receipt 的方式「排空」。

## 2. 建置證據

- `Dockerfile` 的 Python image 必須是完整 OCI digest。
- `requirements.lock` 必須以 `--require-hashes` 安裝。
- 映像建置必須執行 `python runtime_golden.py --verify`；golden 改變時，fixture、理由、
  版本號與 App 相容性必須同一 PR 覆核。
- 部署後 `/api/health` 的 `canonicalization_version` 與
  `canonicalization_golden_sha256` 必須逐字等於該 commit。

## 3. 升版後


1. 先用 sample/phantom 完成 classify → annotation → receipt → images 的 E2E。
2. 驗證 audit retention 已實讀且 locked、care receipt keyring configured。
3. Mac iOS 與 Android 各完成一次實機 App E2E。
4. P0-2、P0-5、IRB/DPA/網路控制等臨床前條件仍是獨立閘門；runtime 升版通過不等於
   臨床限制解除。

## 4. 稽核桶切換與鎖定（獨立於 runtime 升版）

新的 v4 稽核鏈不能接續開發期的舊命名／forked bucket。切換順序固定為：

1. 在名稱含 `smoke`、未鎖、無 retention policy 且空白的拋棄式 bucket 實跑
   `smoke_audit_chain_gcs.py`；測試桶不得是主桶或最終稽核桶。
2. 以 `harden_bucket.ps1 -AuditBucket <new-clean-bucket> -Audit -Apply` 建立並讀回正式
   新桶，但尚不鎖；再次確認桶仍為空、project/location/IAM 正確。
3. 取得**指定該桶名稱**的不可逆授權後，才使用 `-LockRetention`。既有
   `<main>-audit` 開發桶保留為唯讀調查證據，絕不可鎖作為正式紀元。
4. `deploy_cloudrun.ps1 -AuditBucket <new-clean-bucket>` 的 preflight 必須實讀 7 年
   retention + `isLocked=true`，否則不會部署。

完成第 1–4 步仍不解除臨床資料限制；它只滿足 P0-4 稽核基礎設施的一項前提。
