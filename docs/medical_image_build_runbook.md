# 醫療映像明確建置與部署

2026-10-08：正式／demo 部署入口改用已完成且讀回驗證的映像 digest。這份變更不等於 MMH 已部署，不解除稽核、同意、IAM 或正式切流量的閘門。

後續執行期驗證已完成：同一 digest 在無網路 Linux 容器內 39/39 合成流程及三模型推論通過，見 [執行期驗證報告](medical_runtime_validation_20261008.md)。該報告保留 LocalStore 與真實三桶部署的區別。

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

## 本次完整雲端映像驗證結果

來源 commit `073732dbf6338c01073f911ec9f3bac8a7eaaf6a`，manifest SHA-256 `adbdbb210f7f354dab938b1f859b4506b8f2b4630d2f7a1c5da8ab5d36d3b1f8`。明列 50 個程式／模型檔案，共 84,051,506 bytes；沒有手機照片、帳密或執行期資料。來源 Git 乾淨，三個模型雜湊逐一通過。

- [Cloud Build 85053fb9](https://console.cloud.google.com/cloud-build/builds;region=asia-east1/85053fb9-be4d-4407-af4a-968acf8edc5e?project=421209514056)：SUCCESS，2026-10-08 台北時間 10:53:30 至 10:55:38，約 128 秒。
- 雲端 manifest／逐檔雜湊檢查與 Dockerfile 的 canonical-byte gate 均通過，映像推送成功。
- 不可變映像：`asia-east1-docker.pkg.dev/woundai-jackh001/woundai-medical-build/medical@sha256:777c6cfb7f097946fdf8ea7bfaf8684d1f45ced3b115129139c3d492292e7e8b`。
- Python resolver 對真實 build 與 Artifact Registry 讀回成功；再以 PowerShell 7 的 Get-VerifiedMedicalImage 執行相同唯讀流程，輸出完全相同的 digest，exit 0。
- 來源 commit 073732d 的 GitHub CI 7/7 success。本機上述四套合计 93 個測試通過；沒有把 Windows PowerShell 5.1 計入。
- 雲端讀回仍只有既有兩個 service；沒有 MMH service，舊 Compute Editor 仍在。本次没有切流量、鎖桶、建帳號或讀取密文內容。

本映像是 PR 候選來源的完整建置驗證，不是已合併 main 的正式發行。部署腳本仍要求已覆核 main 與其相符的建置；日後 main SHA 改變必須重新綁定建置，不能將 Git 環境變數改成另一個 SHA 冒充。

本機原始證據：`/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-institution-evidence-20261007/medical-full-image-20261008/` 的 plan.json、build-final.json、artifact-readback.json、validation.json 與 runtime-readback.json。完整來源封包、IAM 與雲端回應留在本機受控目錄。
