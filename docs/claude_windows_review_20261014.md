# Windows Claude 協作覆核交接（2026-10-14）

本文件交付程式碼覆核及除錯，並非部署／合併／臨床使用核准。請在各 repo 新的乾淨工作目錄 fetch 指定覆核分支，先記錄完整 HEAD。不要 checkout/clean 使用者原本有未提交資料的工作目錄。

## 三庫邊界

- `JackH0001/WoundAI_Proj`：`codex/mmh-latency-export-review-20261014`，公開共用後端、iOS、Android。前基準 `86a0e328f9051977a386e3157186652134747ec7`（main）；新增修正含 iOS 入口繁忙重試、研究送件面積一致性、兩圖輸出、稽核有界滑動讀取與 Android 入口重試對齊。
- `JackH0001/WoundAI`：`codex/shared-refresh-20261014`，私有母庫。前基準 `410687e5f543fc5fe8fb987a569e2d52b478b345`。共用檔案對齊公開候選；保留私有模型、研究資料與專屬 CI。禁止反向把母庫全部合入公開庫。
- `JackH0001/WoundAI3D`：`codex/progress-checkpoint-20261014`，commit `51aacd01100838d64f76350e3e6443e8de717e49`，父 `f70954a`。只保全 110 個來源／測試／設定檔；研究讀回文件及影像未加入。此為 WIP，並未宣稱完整 3D 臨床模型可用。Mac 原工作樹與原分支未移動。

完整 SHA 以本次 PR 與 repo 外交接 manifest 為準；覆核回報務必綁定該 SHA，而非泛稱 main。

## 先跑的檢查

使用 repo 鎖定依賴建立隔離 Python 環境，勿使用機構雲端憑證或真實資料。PowerShell 範例（Python 指向該環境）：

```powershell
$ErrorActionPreference = 'Stop'
$ReviewPython = '.\.venv\Scripts\python.exe'
$ReviewOut = Join-Path $env:TEMP ('woundai-review-' + [guid]::NewGuid())
& $ReviewPython -B tools/windows/run_python_tests.py --repo . --out $ReviewOut
if ($LASTEXITCODE -ne 0) { throw 'Engineering validation failed' }
& $ReviewPython -B -m unittest discover -s tools -p 'test_*.py'
if ($LASTEXITCODE -ne 0) { throw 'Tools validation failed' }
& $ReviewPython -B tools/verify_logic.py
if ($LASTEXITCODE -ne 0) { throw 'Mobile logic failed' }
& $ReviewPython -B tools/parity_check.py
if ($LASTEXITCODE -ne 0) { throw 'Parity failed' }
```

Android 請用 JDK 17（需同時指定 Gradle JVM，避免既有 Java 25 daemon），跑 `:app:testMmhValidationUnitTest`、`:app:assembleMmhValidation`、`:app:compileMmhInternalKotlin`。iOS 與 WoundAI3D 的 Apple Framework／實機測試不可在 Windows 假報通過；Mac 證據與 Windows 結果分開列示。

## 必須刻意改壞的案例

1. 稽核：中段 hash／prev、同名不同 generation、讀取中換代、清單變更、網路讀取失敗、並行 append 競爭均應拒絕；四個工作者／八個 pending 不可無界增加。每次 append 仍讀全部歷史 bytes。
2. Google 入口：只對 GET health 的 429/503 最多三次，2秒/4秒等待；401/403、重新導向、非 MMH JSON、取消及網路失敗不可套同一重試。不得把規則搬到標註 POST。
3. 研究送件：image、傷口／組織 masks、area 與每次修訂一致；完整回執核對與原始深度原有流程不得被「外部只輸出兩張圖」限制。
4. 外部輸出：只含 image、overlay；overlay 顯示最新確認面積與疊圖；MRN＋日期時間資料夾、缺 MRN 替代名、防路徑穿越、重送新資料夾、自訂目錄授權失效回饋。不得讓 MRN 進研究封包。
5. 3D：原始 RGB-D 與方向／內參／時間契約及修訂關聯，缺深度明示；照片匯入不可宣稱3D完成。WIP 分支應另跑自己的 Python／Swift 回歸與 SSOT 稽核。

## 已有結果與待驗項目

公開／母庫各 111/111 個工程測試檔；公開 tools 360 項（1 skip）；logic 50/50，parity 未宣告差異0。WoundAI3D Core 1031 項，9 skip、0 failure，初次舊 build 受 Finder resource fork 干擾，改乾淨 scratch build 後成功。

離線慢讀取 benchmark 353→182ms（-48.5%）；不能推論雲端所有請求或手機量測也快48.5%。完整指標見 `mmh_latency_pipeline_validation_20261014.md`。稽核讀取總量仍隨歷史增加，尚需候選雲端實測。

目前 iOS 36 僅本機安裝；最新 Android 修改尚未由 Play 發布。本輪不改雲端流量、IAP／IAM、鎖桶或資料。須通過覆核、候選雲端完整性／延遲 A/B、兩端實機及商店簽章驗證後，才將對應版本交付內測。獨立免 Google 示範環境仍列後續工作，不等同 MMH 正式服務。

請回報：各 repo 完整 SHA、P1/P2 的檔案與行號、重現步驟、實跑通過／失敗／跳過數量、Windows 與無法執行的 Apple 測試邊界；不要自行部署或更動雲端資料。
