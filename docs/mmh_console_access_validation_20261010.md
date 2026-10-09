# MMH Mac 私有管理入口與冷啟動驗證

日期：2026-10-10（台北）。範圍為醫療端 MMHPS20261007，限模擬／測試資料。

## 結果

- 私有代理 HTTP／應用身分檢查 **11/11**：未授權遠端請求遭拒、本機 console 可達、只有 Google 入口授權仍不能讀帳號；MMH admin 登入後可讀，錯誤或移除 App token 仍拒絕。未在檔案、Git 或輸出保存密碼／token。
- 冷啟動 **6/6 證據條件**：Cloud Run 在 2026-10-10 00:36:02 台北記錄不同實例因 AUTOSCALING 啟動；該實例後續成功登入與讀帳號，既有兩個停用測試帳號仍停用。這是一次自然新實例觀察，未強制重啟，不能外推為多實例故障切換或備份恢復。
- 工具測試 **136/136**（其中本次代理工具 14 項），公開子庫及母庫各自執行。
- 真實代理限時驗證：設定 15 秒，觀察 localhost listener 成立，程序正常結束且 port 8765 關閉；包含前置雲端查核總耗時約 24.4 秒。
- 服務維持 `woundai-backend-mmhps20261007-00001-hqd`，後端來源 `7ed425d455e3e8526eadd230c226f327df3a82fc`，本次沒有重建映像、切流、改 IAM、鎖桶或新增帳號。

## Mac 使用方式

先安裝 Google Cloud CLI 的官方 `cloud-run-proxy` 元件，並以有權限的 Google 帳號登入 gcloud。此機已安裝元件。於 repo 執行：

```bash
python3 -B tools/open_mmh_console.py --check
python3 -B tools/open_mmh_console.py
```

工具先核對指定 project ID／編號及無上層組織、MMH 設定、IAM 檢查未停用、服務無直接授權及專案 IAM 沒有未知／公開／群組授權；查核失敗不開 listener。專案 IAM 檢查沿用部署守門，並非對所有直接使用者逐一重新做有效權限評估。官方 gcloud 代理限定 `127.0.0.1:8765`；最長一小時後關閉，亦可 Ctrl-C 提早停止。不提供可改目標或綁定公開位址的選項。

開啟 <http://127.0.0.1:8765/console>，再用 **`mmhps20261007:admin`** 登入。這是既有 bootstrap 管理者，密碼由管理者從 Secret Manager 的 MMH admin password 既有版本取得，只填登入欄，不貼進對話或文件。日常 dr01／ns01 等帳號尚未核發。Google 授權與 App 登入為兩道不同檢查。

本機入口只供這台 Mac 使用；一小時到期後重新執行工具即可。代理開啟期間，本機其他程序也可能連入，使用完應登出並關閉代理。**不可將 localhost 填入手機 App**，也不能只把私有服務 URL 填進既有 App 就宣稱可用；手機尚缺 Cloud Run 入口授權方案。

## 證據與範圍

本機證據目錄：`woundai-institution-evidence-20261007/mmh-access-20261010/`。

- `proxy-acceptance.json`：11 項 HTTP／登入檢查。該檔單獨不證明冷啟動。
- `startup-evidence.json`、`account-request-evidence.json`、`cold-start-account-retention.json`：啟動／請求關聯及六項條件；未公開原始帳號回應或研究影像。
- `proxy-ttl.json`、`proxy-ttl.log`：限時 listener 開閉實測。
- `combined-tools-tests.log`、`mother-tools-tests.log`：兩庫工具測試。

本次沒有重新驗完整 RGB-D、組織分類圖層或 WoundAI3D 匯入；這些仍依前次交接列為缺口。MMH 使用未鎖定稽核模式，不宣稱 WORM；IRB 尚未送件，不開放真實病患研究收案。

參照：[Google 官方私有服務代理](https://docs.cloud.google.com/sdk/gcloud/reference/run/services/proxy)、[開發者身分驗證](https://docs.cloud.google.com/run/docs/authenticating/developers)。
