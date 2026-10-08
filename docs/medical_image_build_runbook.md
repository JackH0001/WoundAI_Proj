# 醫療映像明確建置與部署

2026-10-08：正式／demo 部署入口改用已完成且讀回驗證的映像 digest。這份變更不等於 MMH 已部署，不解除稽核、同意、IAM 或正式切流量的閘門。

## 建置

在乾淨且已覆核的 commit 上，使用 Python 3.11+：

```sh
python -B tools/medical_image_build.py prepare \
  --repo /absolute/path/to/WoundAI_Proj \
  --models /absolute/path/to/verified/models \
  --output /absolute/path/outside-repo/medical-image-plan \
  --git-commit <完整40位SHA>
```

這一步只產生本機計畫。沿用 `stage_lite_candidate.py` 的明列檔案、engineering vendor 清單與三個已釘選模型雜湊；共用打包器不代表啟用 Lite 端點，服務設定仍由各部署腳本限制。禁止把執行期目錄、影像、密文或整份 repo 複製進建置上下文。清單是精確允許名單；未來新增 runtime 模組須一起更新清單與測試。

檢視 plan.json、source manifest 與檔案清單後，以結構化 argv 執行其中 `submit_argv`（不是 eval 或 shell 字串）。它只使用以下已佈建資源：

- project woundai-jackh001、region asia-east1。
- SA woundai-medical-build@woundai-jackh001.iam.gserviceaccount.com。
- 來源桶 woundai-medical-build-421209514056。
- 映像庫 asia-east1/woundai-medical-build。
- CLOUD_LOGGING_ONLY、預設 worker、timeout 1200 秒；不是帳單硬上限。

一次提交後立刻保存 build ID。未知結果先查詢該 build，不要直接重送。建置在雲端先驗 manifest 與每個檔案雜湊，再執行含 canonical-byte gate 的 Dockerfile，成功才推送映像。本工具不部署、不授權 IAM、不讀密文。需另外驗證模型載入與合成 API 行為，不能把映像建置成功當成完整 App 驗收。

## 部署只接收不可變結果

兩支腳本新增 `-BuildId` 與 `-BuildManifestSha256`；真正部署時必填，VerifyOnly／PromoteCandidate 不要求重新建置。`tools/resolve_medical_image.ps1` 呼叫同目錄的 Python resolver，由 gcloud **重新讀取** build 與 Artifact Registry，要求：

1. 指定專案／地區／build UUID、SUCCESS、專用 SA。
2. 完整本機 Git commit 與準備時的 manifest SHA-256 相同。
3. 明確的建置步驟、釘選 builder、1200 秒 timeout、日誌配置；不得加入 secrets、額外步驟、環境參數或其他映像。
4. source bucket 及物件 generation 有效，與 resolved provenance 相同。
5. 單一映像结果與 Artifact Registry 的 sha256 digest 一致。

無法讀取、欄位不符、未完成、可變 tag、UNKNOWN 或命令失敗一律中止。resolver 唯一成功輸出為指定 medical repository 的 `@sha256:...`。Cloud Run 使用 `--image`，不會再次落回 Compute 建置身分。

正式腳本原本的 main／remote SHA、鎖定稽核紀元、runtime IAM、no-traffic candidate 及另行 promotion 閘門照常執行；demo 的獨立身分／密文／local store 及健康檢查也保留。新增參數不是部署授權，也不會替尚未建立的 MMH service 提供正式配置。

此版本只配置 woundai-jackh001 / asia-east1 的建置資源，其他 project／region 明確拒絕。Python 或 gcloud 不可用也會停止，不得退回隱式 source deploy。

## 驗證與跨平台限制

- 新 Python／PowerShell 合約測試：25/25，含配方／來源／digest 竄改、錯誤身分、雲端失敗、额外檔案及 PowerShell 退出碼。
- 既有 deployment static：5/5；demo isolation：48/48；PowerShell 假 gcloud 情境：15/15。
- Mac 上實際使用 PowerShell 7；Windows PowerShell 5.1 尚未重跑。保留 PS1 的 UTF-8 BOM 與 CRLF。
- Windows runner 的 engineering 測試入口已納入新測試；CI 也直接執行新工具測試。
- owner_guard 仍將 Backend／engineering 判為 Windows 所有。本次依 Jack 已授權由 Mac 完成必要後台對齊而例外提交，沒有修改 ownership 規則，不能稱 owner_guard 通過。

## 10:32 App 27 實測基線的限制

舊服務 505ff2e：登入 200、care/attest 404、classify 200；同秒可見一個新增 JPEG 物件。新 App 要求 persisted=true，舊服務沒有此欄位，因此不綁定 image_id。手機更新時間軸不等於雲端訓練標註已入庫。未看到對應 annotation／depth 請求；這次不是完整 RGB-D 或雲端標註驗收，也不應以降級 App 安全判斷修補。
