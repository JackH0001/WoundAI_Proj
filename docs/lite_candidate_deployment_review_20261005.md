# WoundLite 獨立候選服務部署覆核（尚未執行）

日期：2026-10-05。目的：把既有候選原始 RGB-D、修訂去重、App Attest、GCS 撤回整合推進到可實機驗收的環境，之後銜接 Lite TestFlight。IRB 尚未送件，僅限合成／模擬資料與受控測試，不代表公開研究收案。

## 目前證據

已唯讀確認 GCP 憑證可用，專案 woundai-jackh001／421209514056 正常。現有正式與 demo Cloud Run revision 未改；Lite 服務、新桶、runtime 身分、salt secret、自訂角色及專用映像庫名称尚未使用。先前 build 身分仍停用、臨時桶授權已移除，自訂角色內容一致；祖先 IAM 預檢通過。新身分尚未建立，不能宣稱其有效權限已通過。

- 新包 49 檔，含本日實機發現的三項 App Attest 修復；source manifest SHA-256：`834ef602ba701be0b826a9cbb0c8c17bb4472acb6d836c185d5893aa7d44ce53`。
- 方案 SHA-256：`9bdec74783ea1e9ee10af8a6b67966c0c7c2c4b8205c75577d29971a8541758d`。
- 方案：`/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-competitive-review-20261003/lite-candidate-deploy-20261005/review/plan.json`。
- 尚無新 image digest。成功完成一次建置後必須讀回 digest，再以 `plan_lite_candidate_deploy.deploy_argv()` 綁定；函式拒絕 tag、完整外來 image 路徑、無效 digest。
- `tools/plan_lite_candidate_deploy.py` 是離線方案產生器；產生檔案不執行 gcloud、不代表授權或部署成功。

## 精確資源與权限

全部在 woundai-jackh001，儲存／映像／服務區域 asia-east1。

| 資源 | 名稱／配置 |
|---|---|
| 新 Cloud Run | woundai-lite-candidate；先維持私有 IAM |
| 新執行身分 | woundai-lite-runtime@woundai-jackh001.iam.gserviceaccount.com |
| 新媒體桶 | woundai-lite-media-421209514056 |
| 新安全狀態桶 | woundai-lite-security-421209514056 |
| 新 Artifact Registry | woundai-lite-candidate；映像 backend，部署使用 digest |
| 新 Secret Manager | woundai-lite-ip-salt，僅 runtime 可讀 version 1；隨機產生且不輸出明文 |
| 2 個自訂角色 | woundaiLiteMediaObjects、woundaiLiteSecurityObjects，只綁各自新桶 |
| 重用 build 身分／桶 | 已停用 woundai-lite-build 與 woundai-lite-build-421209514056，驗證原設定後暫時啟用／授權一次建置 |

Runtime 僅能 get bucket、讀／新增／替換物件；媒體另需 list。替換 GCS 物件需要 create＋delete，這是建立撤回零位元組標記所需，不授予 bucket 或 IAM 修改權。建置身分只暫授新映像庫 writer 與原建置桶角色；不得讀 Lite 的媒體／安全桶或 salt。沒有 project 層級 runtime/build 角色授權。

桶採 Standard、UBLA、禁止公開、無 retention／versioning／soft delete／lifecycle。安全狀態與撤回標記不能用日期清掉。此配置只適用新 Lite 候選桶，不影響既有臨床、稽核或舊測試桶政策。

## 低成本執行配置

2 vCPU／4 GiB、1 worker；concurrency 1、min instances 0、revision max instances 1、request-based billing、CPU throttling、關閉 startup CPU boost、120 秒逾時。不用 GPU、排程暖機或每天重建。4 GiB 是保守候選值，實測峰值記憶體與延遲後再評估下修；目前不能宣稱是已實測的最低成本配置。

max instances 1 是上限設定，不保證永遠只有一個實例／執行緒／在途寫入。仍依靠 GCS generation／challenge-counter 原子更新與持久化撤回封鎖，不能用單實例代替正確性。

個人每日成功量測 5 次；全站每分鐘 60、每日 2000 個一般授權嘗試，撤回另有相同額度的獨立通道。每次量測假設約 6 個請求（challenge、上傳、修訂等），不是 2000 次影像推論／日。先以 10 位受控測試者估算；50 人每日各 5 次約 1500 請求／日，仍須預留註冊與重試。100 人每日各 5 次可能超額，擴大前須實測及調整。

## 月預算試算（美元，未扣免費額度）

| 測試者，每天各 5 次 | 基準 | 壓力情境 | 建議費用關注值 |
|---|---:|---:|---:|
| 10 人，1500 次／月 | 3.26 | 8.78 | US$10／月 |
| 50 人，7500 次／月 | 13.18 | 30.35 | US$40／月 |

基準：每量測累計計費 10 秒、5 MiB／筆；每天 10 次冷啟動各 30 秒。壓力：30 秒、10 MiB／筆；每天 30 次冷啟動各 60 秒。均假設每筆 50 Class A＋100 Class B GCS 操作、首月均勻增加資料、完整資料外網下載一次預留 US$0.15/GiB、映像 2 GiB、salt 一個版本。這些是規劃假設，不是量測到的每筆 API 計數／冷啟動性能或正式帳單；對帳後更新。

2 CPU＋4 GiB 每計費秒約 US$0.000058（CPU 0.000024/vCPU-s、RAM 0.0000025/GiB-s）；Cloud Run 請求 US$0.40/百萬。[Cloud Run 官方價目](https://cloud.google.com/run/pricing)

GCS Standard 以 US$0.02/GiB-month，Class A 0.005/千次、Class B 0.0004/千次估算；下載價格依目的地，不是固定 0.15。持續保存第 2 月起舊資料會累加，未實作安全退役之前不為壓低帳單而刪來源。[GCS 價目](https://cloud.google.com/storage/pricing)

映像儲存以約 US$0.10/GiB-month；同區部署不另計跨區搬移費。[Artifact Registry 價目](https://cloud.google.com/artifact-registry/pricing)。salt 以 US$0.06/active-version-month 估算，少量存取費另計。[Secret Manager 價目](https://cloud.google.com/secret-manager/pricing)

另加首次 Cloud Build，20 分鐘逾時按 0.006/min 估計運算 US$0.12；若失敗先分析、不自動重複付費建置。日誌、密文讀取、掃描、異常／惡意流量、稅及既有醫療/demo費用未包進上表。新服務 min=0 可免保留暖实例的待命費，仍有儲存等費用。額度／maxScale／費用提醒均不是硬帳單上限；此處沒有替使用者設定未授權的帳務通知或宣稱能自動截停費用。

## 執行順序及驗收

1. 重新核對方案及來源雜湊、帳務/API、新資源名稱和 build 既有設定，任何漂移停止。
2. 佈建新身分、2 桶、2 自訂角色、映像庫與一把新 salt；讀回精確 IAM。依 PR #14/15 的一跳邊界查有效拒絕：正式/demo 密文、資料、IAM 更改、專案與受保護政策點名的外部身分之 8 種冒用權限。UNKNOWN／錯 principal／錯 resource 全停止；Secret Manager 名稱用專案編號。這不是任意跨專案冒用鏈的證明。
3. 暫授 build 身分精確資源角色；一次 Linux 建置先跑來源雜湊、三模型、golden、原始 RGB-D 回執／清除、合成 Apple 簽章算法及錯誤拒絕探針，成功才推送映像。模型探針使用暫存 LocalStore，不能代替 GCS 測試。
4. 私有 Cloud Run 用成功結果 digest 部署；讀回帳號、secret version、環境、資源、IAM，做認證 health 與錯誤路由驗證。既有正式/demo不更新。建置證據取回後收回 build 精確臨時授權並停用。
5. 專用新桶合成測試：跨程序 CAS 恰好一個成功、失聯後回讀／重試、重建 coordinator、撤回後舊寫入不得復活；驗證 raw RGB-D、同意與最終修訂。
6. 私有驗收通過，再另記錄開放此服務的 invoker 與受控 TestFlight 測試授權。Lite 35 是本方案預留候選版號，尚未產生／上傳；正式 Apple App ID、production App Attest、有效 URL、實機完整流程及 Beta metadata 都通過才邀外測。
7. IRB、研究責任與隱私聲明，以及 raw 封存／撤回匯出閉環完成後，才決定公開研究招募及 App Store 發布。

## 本輪驗證

方案測試 10/10。新封裝來源及探針於 macOS 實跑三個 ONNX 模型、golden、App Attest 算法合成正反例、raw 回執／清除、未配置授權拒絕上傳全部通過。這不是新 Linux 工作或線上整合；沒有新雲端資源、映像推送或部署。證據位於 `/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-competitive-review-20261003/lite-candidate-deploy-20261005`。

新方案超出先前「一次建置、不推送映像、不部署」的明確範圍；執行前需 Jack 確認這些新增資源與私有部署。此限制來自前次方案的授權範圍，不是登入失效，也不是要求再次授權已完成的建置。
