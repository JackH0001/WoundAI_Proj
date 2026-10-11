# MMH 管理功能來源整合與驗證（2026-10-11）

## 問題與整合範圍

唯讀查詢 GitHub `JackH0001/WoundAI_Proj`，main 為 `b7f9dc2b28bc4a270c2df531838df18c69cfff07`（已合併 PR #23）。目前本機分支與它的共同祖先為 `9a4b9e49f4142fb419d0a4bccfb54cacd856f094`；本機並非已包含最新 main，不能直接將本機完整樹覆蓋回公開庫。

已下載該固定 main SHA 並在本機整合，沒有推送或遠端合併。兩處衝突經差異核對後採用 main 已發布的新增行為：

- `api_flywheel.py` 加入 legacy 2D 簽章重建與原收據回傳；同一已證明的旧2D紀錄重送不新增列，不猜測舊RGB-D資產。原有不可覆寫修訂資產及操作者／機構區分保留。
- `test_medical_revision_assets.py` 保留兩項 main 新增的舊收據與拒絕損毀／改動內容測試；未刪除本機原有測試。

IAP候選部署程式、對應17項測試、修訂版深度方向測試與PR #23證據文件也回到整合樹。新版管理session、帳號CAS、稽核、UI，以及iOS／Android尚未提交公開庫的進度均保留。

整合執行發現 `tools/test_mmh_account_versions.py` 直接以CI命令執行時，無法匯入 `Backend.AccessManager`。補入以腳本位置解析的repository root，避免依賴外部PYTHONPATH；修正後同一命令9/9通過。

## 本輪實際驗證

| 範圍 | 結果 |
| --- | --- |
| 醫療修訂資產／歷史收據 | 19/19 |
| 深度方向保存 | 7/7 |
| 保留IAP的候選部署檢查 | 17/17 |
| 帳號CAS／本機與模擬GCS競態／真實Flask整合 | 9/9 |
| 即時管理session端點 | 7/7 |
| 管理服務固定依賴回歸 | 117/117 |
| 管理介面登入狀態 | 8/8 |
| 醫療映像建置／來源證據守門 | 24通過、1跳過（本機沒有PowerShell） |

合計208通過、1跳過。這是本機整合驗證，不是GitHub CI、Linux容器建置或雲端部署結果。測試只使用合成資料／記憶體及本機測試儲存。

## 後續來源提交策略

完整分支同時包含手機UI、Google登入、圖示與新管理服務，不宜讓單一管理部署PR承載全部差異。下一步以已核對main為底，抽出管理服務、必要後端session/CAS、建置allowlist、CI與對應文件為獨立來源覆核；手機差異另外保留並對齊。新推送／PR範圍需按實際SHA與檔案清單確認，不沿用之前只限定PR #23個別commit的推送授權。

雲端仍是先前既有revision／IAP政策；沒有新增管理服務、管理SA、OAuth、IAM、帳號異動或流量切換。帳號CAS跨入口切換要求見 `mmh_access_management_simplification_20261011.md`。

## 獨立管理覆核分支

已以 `b7f9dc2b28bc4a270c2df531838df18c69cfff07` 建立 `codex/mmh-access-manager-20261011`，從本機整合提交 `3ad03ce` 抽出36個檔案：AccessManager、後端session與帳號CAS、既有主控台revision接線、來源allowlist、切換檢查、CI及兩份文件。沒有iOS／Android原始碼、圖示或商店設定差異；完整手機進度仍在原整合分支，不刪除或覆蓋。

在此獨立checkout重跑：管理117、帳號版本9、session7、機構邊界13、建置context5、切換gate8、UI8，合計 **167項通過**。既有RBAC、admin console、store abstraction三支腳本均exit0；console合成fixture仍有既有短HMAC key警告，不是正式憑證。未將此結果當成Windows、Linux容器或雲端端到端證據。

此PR包含機構帳號儲存格式變更；合併來源不能等同授權部署。產生CAS紀錄前仍須完成跨入口／外部寫入者協調、受控備份及重播、有效權限與服務驗收，並單獨確認必要流量／IAM變更。新分支尚未推送或建立PR。
