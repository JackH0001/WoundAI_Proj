# 醫療後端建置身分：2026-10-08 階段驗證

本次完成獨立建置身分的合成 smoke；沒有部署應用程式、切換既有服務流量或移除舊 Compute Editor。MMH 尚未達到隔離驗收，不提供可登入網址或已建立帳號的宣稱。

## 實際資源及權限

專案 woundai-jackh001（421209514056），地區 asia-east1。

| 資源 | 已讀回的配置 |
|---|---|
| 建置 SA | woundai-medical-build@woundai-jackh001.iam.gserviceaccount.com |
| 來源桶 | woundai-medical-build-421209514056；STANDARD、ASIA-EAST1、UBLA、禁止公開、soft delete 0 |
| 映像庫 | asia-east1/woundai-medical-build；DOCKER |
| 桶授權 | builder 僅 objectViewer；移除新桶預設 projectEditor/projectViewer 便利授權，保留 Owner |
| 映像庫授權 | builder 僅此 repository 的 artifactregistry.writer |
| 專案授權 | builder 僅 logging.logWriter；前後比對只新增此預期 binding/member |

來源桶沒有自動清理、retention 或 versioning；本次微小合成來源與映像保留作覆核，不將既有 Lite 清理規則自動套用於這裡。Cloud Build 的預設身分仍是 Compute，本次成功來自 config 明確指定新 SA，不能說預設身分已遷移。

## 實際雲端驗證

- 新 builder 的 Policy Troubleshooter v3 矩陣 **13/13 通過**：來源讀取、專用映像庫上傳、log 寫入允許；來源新增、舊映像庫上傳、三個 MMH 桶讀取、舊媒體桶讀取、兩把舊密文內容、MMH SA actAs/keyCreate 均拒絕。這是列出的直接權限檢查，不是任意跨專案冒用鏈的證明。
- Cloud Build **384ce4a0-d2e6-43d8-8635-87d857e32185 SUCCESS**，執行約 6.7 秒。
- 準備工具版本：`ba9e62285e430f472663c61e639249efd7061367`。上傳內容僅 Dockerfile、proof.txt、cloudbuild.json、.gcloudignore；沒有應用程式、模型、密文或研究影像。
- 合成文字 SHA-256：`7b6e18ed7bee08232b5ae4799bc0bad1555a18f8e77088ab5caa06f480f011a3`。容器映像內檔案抽出後逐位元組比對成功，日誌讀回 `MEDICAL_BUILD_SYNTHETIC_VERIFIED` 一筆。
- 映像：`asia-east1-docker.pkg.dev/woundai-jackh001/woundai-medical-build/smoke:ba9e62285e43`。
- 映像 digest：`sha256:9e051f95d8838d862923b379f5a1e038502e1f6ba1ed93e2a5da198bb4e40f01`，Cloud Build 與 Artifact Registry 讀回一致。
- 正式與 demo 的 runtime 身分、revision 與流量比對未變。

[Cloud Build 紀錄](https://console.cloud.google.com/cloud-build/builds;region=asia-east1/384ce4a0-d2e6-43d8-8635-87d857e32185?project=421209514056)

首次指定 E2_STANDARD_2 的準備設定被本機 gcloud schema 拒收；查詢確認沒有產生該次 build。修正為預設 worker，保留 300 秒 timeout 後才得到上述唯一成功建置。此 smoke 不代表完整模型映像的建置成本或執行效能。

## 本機與控制台查核

本次重新執行 `test_medical_build_smoke.py` 10 項、`test_verify_mmh_effective_iam.py` 14 項、`test_protect_mmh_buckets.py` 13 項，合計 **37/37**。不是 Windows 全套驗證，也不是手機端 RGB-D 驗收。

使用者登入舊醫療控制台後，UI 確認為 default:admin，Dashboard 可以讀取統計，資料鏈異常為 0。系統頁顯示 healthy、revision woundai-backend-00039-xdk、commit 505ff2e。這只驗證已登入讀取及狀態展示，沒有代替登入後合成量測／存檔／時間軸讀回測試。控制台可見的導覽沒有此寫入流程；已請使用者在原醫療 App 用合成測試圖完成基線，待測試時間與結果再核對。

舊 UI 仍顯示「WORM」；這是已知舊 revision 的固定敘述，不是 bucket 已鎖定的證據。已查明舊稽核桶有保留期但未鎖定，不能把該 UI 勾號寫成合規通過。

## 尚未放行的步驟

1. 現有正式及 demo PowerShell 部署入口仍使用 `run deploy --source`，沒有指定本次 builder。新建置 SA 對新來源桶／新映像庫的 smoke 成功，**不等於既有 source deploy 可直接沿用**。須改造明確建置／映像部署入口，並完成實際相容性驗證；不直接加參數或扩大角色掩蓋來源與映像庫不符。
2. 正式 App 已登入合成量測、儲存及時間軸讀回的基線尚待完成；health 和 Dashboard 均不足以取代。
3. 移除舊 Compute Editor 前仍須即時盤點工作負載／正在建置項目、確認必要直接權限；移除後重跑 51 項與同份端到端基線，失败依最新 IAM etag 只恢復原 member。
4. 舊權限矩陣最近結果仍是撤權前 **42/51**，不是本次 builder 的 13/13。MMH service、帳號、runtime 授權與正式資料收案均未放行。新稽核桶不可逆鎖定仍需另行明確授權。

## 本機證據位置

受控根目錄：`/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-institution-evidence-20261007/`。

- medical-build-provisioned-20261008.json
- medical-builder-effective-20261008.json
- medical-build-smoke-20261008-v2/plan.json
- medical-build-smoke-20261008-v2/build-readback.json
- medical-build-smoke-20261008-v2/artifact-readback.json
- medical-build-smoke-20261008-v2/verification-logs.json
- medical-build-smoke-20261008-v2/validation.json

原始 IAM、完整雲端讀回及來源封包資訊保留本機，不放入公開 repo。本文不包含密碼、token 或研究識別碼。
