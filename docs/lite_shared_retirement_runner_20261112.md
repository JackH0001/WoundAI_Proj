# 私有共用金鑰退休操作入口（本機候選，2026-11-12）

前一版只有可注入的盤點／派送元件。本版將其接成可獨立封裝的單步 CLI：先檢查固定公開設定與要求，再核對執行工作的附加身分、保留限額、讀取既有資料、執行既有簽署與 CAS 防護。沒有 App 路由、排程或自動啟用。本輪不部署、不碰真實金鑰與研究資料。

## 操作與資料界線

| 模式 | 內容 | 寫入範圍 |
|---|---|---|
| `--validate-only` | 核對設定、要求、來源／映像雜湊與適用簽章 | 無；不取得憑證 |
| `prepare` | 指定來源盤點、實際 DEK 復原比對 | 分塊、雜湊固定的覆核證據與額度稽核；不改 KMS |
| `plan` | 讀原覆核、KMS 狀態，產生限時動作計畫 | 額度稽核 |
| `inspect` | 讀原派送與回執 | 額度稽核；不呼叫 KMS |
| `execute` | 獨立範圍覆核及操作員簽章、重核依賴、CAS 後單次派送 | 操作紀錄、稽核、最多原意圖的一次 KMS 變更 |
| `reconcile` | 獨立排空簽章後，只讀 KMS 核對不明結果 | 操作／稽核進度；不重送變更、不解開舊 DEK |

所有執行模式，包括 inspect，都先保留一筆有限的耐久額度；所以它們不是零寫入的 CLI。只有 validate-only 完全不開 provider。額度回覆遺失不能拿讀回值當成已取得執行權。

帳號、保管庫及其稽核四條路徑只能讀取；缺少原設定時停止，不新建資料。覆核與派送稽核只能建立新物件；派送狀態使用 generation CAS。復原副本僅列舉／讀取。工具禁止將可寫操作／額度前綴指到原資料或備份路徑。必須另外以實際 IAM 驗證同樣限制，程式的限制不是 IAM 證明。

復原用 KMS 介面只接受明列版本的 GET 與同一 CryptoKey 的 decrypt，不能 encrypt、建立或改金鑰。生命週期操作使用 `PinnedKeyLifecycle.with_access_token()`，401／503／斷線不 refresh 或重送；這次再把其每次傳輸納入同一 provider 額度。所有 Google token 僅由工作附加身分取得，不使用 ADC、操作者本機登入或私鑰檔案。原始 token 的雙時鐘期限與操作額度都維持生效。

## 續接與期限

`operation_namespace` 固定原覆核與派送路徑。每個獲准操作窗口另設 provider budget namespace／控制與稽核路徑；兩者不能重疊。如此原計畫／回執不會因隔日取得新限額而消失，也不會重設舊額度。限時授權過期只能停止；新的窗口、設定雜湊與執行都須重新覆核。

execute 要求有效的計畫與簽章；inspect／reconcile 可以核對舊計畫，但 reconcile 必須提供新的獨立排空證明。已完成操作的重播只讀原回執；結果不明先核對，不能重派或自動反向操作。restore 只取消銷毀排程、回到 DISABLED，不會啟用舊版本。

## 封裝與執行準備

`tools/stage_lite_retirement_candidate.py` 使用明列檔案清單；依賴掃描只檢查、不能自動擴大封裝。容器沿用固定 Python 映像與雜湊鎖定套件，以非 root 執行，沒有 web server 或模型。匯入驗證禁止網路，拒絕遺失、修改、額外檔案或符號連結。新的 CI 工作會建置並以 network-none/read-only 檢查未設定時拒絕執行；本機目前沒有 Docker，因此尚不能把容器建置算成通過。

CLI 範例只表示輸入格式，**不構成雲端執行授權**：

```sh
python -B tools/lite_shared_retirement_runner.py \
  --config /absolute/path/reviewed-config.json --config-sha256 REVIEWED_CONFIG_SHA256 \
  --request /absolute/path/reviewed-request.json --request-sha256 REVIEWED_REQUEST_SHA256 \
  --source FIXED_COMMIT --image FIXED_IMAGE_SHA256 --validate-only
```

部署提案仍需固定已建置映像、設定與每次要求、專用 SA／最小 IAM、全部來源與備份清冊、實際雙人簽署與排空依據、復原演練、執行上限與回復步驟。Cloud Run Job 應為一個 task、一個 parallelism、maxRetries=0；外部 supervisor 到期停止，不配置自動重跑／排程。SDK 和工具限額在傳輸邊界檢查，不能代替 OS 層強制截止。

真實 provider 尚未驗收，也沒有替全部帳號宣告刪除完成。指定來源掃描不等於發現所有離線／未申報副本；單一舊 KEK 銷毀不代表帳號在其他版本的副本都不可解。所有回執持續明示 `account_deleted=false`。

## 合成驗收及 Claude 重播

- 將既有兩個加密合成帳號及稽核移到 versioned GCS 替身，直接執行 CLI 使用的 run/build 核心。
- 覆核、停用、排程、取消；備份不完整會停止；四條原資料／稽核路徑逐位元保留。
- 停用已生效但回覆遺失：重播被擋，只派送一次；獨立 reconcile 後續接。完成回執再讀不增加 KMS 工作。
- 31 天後以新限額窗口核對同一原計畫，原派送／稽核位置不變，也不再變更 KMS。
- 不正確／過期簽章、來源、映像、路徑、金鑰、額度或缺少設定會停止。範圍外要求在取得憑證前拒絕。
- 真實 Google Client／AuthorizedSession 組合以合成 token 驗證無 ADC／自動 refresh；實際 Requests adapter 的 401 與限額驗證為單次傳輸。Google IAM／TLS／權限傳播不在合成測試證明內。

```sh
PYTHONPATH=Backend/Flask:tools:engineering/phase2 python -B -m unittest -v test_lite_shared_retirement_runner test_lite_retirement_package
PYTHONPATH=Backend/Flask:tools:engineering/phase2 python -B tools/lite_shared_retirement_runner_probe.py --output runner-probe.json
```

既有退休、派送及 HTTP 回歸需一併執行；各套件與完整 Lite 測試有重疊，不加總。本輪沒有修改 iOS 或 Apple 商品。

## 參照

Google 說明銷毀先進入排程期，預設 30 天但可依設定不同；在排程期可取消，真正銷毀後一般不能復原。程式依實際 metadata 回報，沒有把預設天數寫成使用者承諾。[Cloud KMS destroy/restore](https://docs.cloud.google.com/kms/docs/destroy-restore)

Cloud Run timeout 是每次 task attempt 的上限；重試會另開 attempt，因此本操作要求 maxRetries=0，且保留中斷後的原紀錄。[Cloud Run task timeout](https://docs.cloud.google.com/run/docs/configuring/task-timeout)
