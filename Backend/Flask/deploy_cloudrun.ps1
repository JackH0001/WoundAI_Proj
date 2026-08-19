# ⚠ 這個檔案必須以 **UTF-8 with BOM** 儲存。
#
# Windows PowerShell 5.1 在沒有 BOM 時會以系統 ANSI 字碼頁（繁中系統是 Big5）解讀 .ps1，
# 中文註解不只變成亂碼，某些位元組序列還會吃掉引號與大括號，導致整個檔案語法解析失敗
# （症狀是一連串「陳述式區塊中缺少 '}'」，而真正的原因與大括號無關）。
# 用編輯器另存時請確認選的是「UTF-8 with BOM / UTF-8-BOM」。
# 部署 WoundAI 後端到 GCP Cloud Run（彰化 asia-east1）
#
# 用法：
#   .\deploy_cloudrun.ps1 -ProjectId my-proj -Bucket woundai-flywheel-abc
#   .\deploy_cloudrun.ps1 -ProjectId my-proj -Bucket woundai-flywheel-abc -Setup
#
# -Setup 只在**第一次**用：開啟 API、建儲存桶、產生並存入密碼。
# 之後每次改程式只要跑不帶 -Setup 的版本。
#
# 詳細說明與排錯見 docs/deploy_cloudrun.md。

param(
    [Parameter(Mandatory = $true)][string]$ProjectId,
    [Parameter(Mandatory = $true)][string]$Bucket,
    [string]$Region = "asia-east1",
    [string]$Service = "woundai-backend",
    # 稽核專用桶（WORM）。預設沿用 harden_bucket.ps1 -Audit 的命名慣例。
    # ⚠ 一定要當成部署參數帶進來：`--set-env-vars` 是**整組覆蓋**，
    # 用 `run services update --update-env-vars` 另外設的值會在下次部署時被洗掉，
    # 而症狀是「稽核紀錄悄悄寫回刪得掉的主桶」——沒有錯誤、沒有警告。
    [string]$AuditBucket = "$Bucket-audit",
    # 記憶體上限。2Gi 足夠一般路由（只載 student），但**難例升級路由會再載入
    # a_unet 與 unetpp**，三個 ONNX session 同時在記憶體裡加上 OpenCV 與影像緩衝
    # 就可能超過 2Gi。Cloud Run 的 OOM 會直接殺掉容器 → 客戶端看到 503，
    # 而且**不會留下任何 Python traceback**，是最難自行歸因的一種失敗。
    [string]$Memory = "4Gi",
    [switch]$Setup,
    # 只跑部署後驗證，跳過建置與部署。
    # 存在的理由很實際：驗證段本身出錯時（例如用了某個 PowerShell 版本沒有的參數），
    # 若要重跑就得再等一次五分鐘的映像重建 —— 那個成本會讓人乾脆不驗。
    [switch]$VerifyOnly
)

# ⚠ 刻意**不設** $ErrorActionPreference = "Stop"。
#
# gcloud 在 Windows 上的進入點是 gcloud.ps1，它會繼承呼叫端的 ErrorActionPreference。
# 而那支包裝一開始會 `Test-Path` 探測內建 Python，在某些安裝方式下該路徑的 ACL 不給讀，
# 於是丟出「Access is denied」——**功能完全正常**（它會退回系統 Python），
# 但在 Stop 模式下這個原本無害的非終止錯誤會變成終止錯誤，整個部署會在第一個
# gcloud 呼叫就中止，而畫面上只有一句看起來與部署無關的 Test-Path 錯誤。
# 改成明確檢查 $LASTEXITCODE，該中止的地方自己 throw。
$ErrorActionPreference = "Continue"

# 直接叫 gcloud.cmd 繞過 .ps1 包裝，連那行噪音都不會出現。
# 找不到就退回 gcloud（PATH 上有就好）。
$script:GCLOUD = if (Get-Command gcloud.cmd -ErrorAction SilentlyContinue) { "gcloud.cmd" } else { "gcloud" }

function Say($msg) { Write-Host "`n▶ $msg" -ForegroundColor Cyan }
function Warn($msg) { Write-Host "⚠ $msg" -ForegroundColor Yellow }
# ⚠ 函式名稱**不可**用兩三個字母的縮寫。
# PowerShell 的命令解析順序是「別名 > 函式 > Cmdlet > 執行檔」——**別名贏過函式**。
# 這支腳本原本把包裝函式取名 `Gc`，而那正是內建的 `Get-Content` 別名，
# 於是自訂函式永遠沒被呼叫過：每一行 `Gc config set ...` 實際執行的是
# `Get-Content config set ...`，錯誤訊息指向 Get-Content，完全看不出是命名衝突，
# 而且帳單檢查因此拿到 null 值，誤報成「尚未連結帳單帳戶」。
# 用動詞-名詞的完整命名（PowerShell 慣例）就不會撞到任何內建別名。
function Invoke-GCloud {
    & $script:GCLOUD @args
}
# ⚠ **不可**用 `Invoke-WebRequest -SkipHttpErrorCheck`。
# 那是 PowerShell 7+ 才有的參數；Windows PowerShell 5.1（多數人手上的版本）會丟
# 「找不到符合參數名稱 'SkipHttpErrorCheck' 的參數」。
#
# 這件事之所以嚴重，不是因為檢查跑不動，而是因為**它會假裝跑過了**：
# 原本的「舊預設密碼」檢查把呼叫包在 try/catch 裡，catch 分支直接印
# 「✓ 舊預設密碼已失效」——參數錯誤被 catch 吃掉，於是不論後端是什麼狀態
# 都會印出綠勾。一個永遠會通過的安全檢查比沒有檢查更糟：它讓人**停止懷疑**。
#
# 這支 helper 把三種結果分開回報，5.1 與 7+ 都適用：
#   Code > 0  伺服器有回應（不論 2xx/4xx/5xx）
#   Code = 0  根本沒連上（DNS / 逾時 / TLS）—— 絕不可當成「被拒絕」
function Get-HttpResult {
    param([string]$Uri, [string]$Method = "GET", [string]$Body,
          [string]$ContentType = "application/json", [int]$TimeoutSec = 60)
    $p = @{ Uri = $Uri; Method = $Method; TimeoutSec = $TimeoutSec
            UseBasicParsing = $true; ErrorAction = "Stop" }
    if ($Body) { $p.Body = $Body; $p.ContentType = $ContentType }
    try {
        $r = Invoke-WebRequest @p
        return [pscustomobject]@{ Code = [int]$r.StatusCode; Content = [string]$r.Content; Reached = $true }
    } catch {
        $resp = $_.Exception.Response
        if ($null -ne $resp) {
            $code = 0
            try { $code = [int]$resp.StatusCode } catch { }
            if ($code -eq 0) { try { $code = [int]$resp.StatusCode.value__ } catch { } }
            $body2 = ""
            if ($_.ErrorDetails -and $_.ErrorDetails.Message) { $body2 = [string]$_.ErrorDetails.Message }
            return [pscustomobject]@{ Code = $code; Content = $body2; Reached = ($code -gt 0) }
        }
        return [pscustomobject]@{ Code = 0; Content = $_.Exception.Message; Reached = $false }
    }
}

function Assert-GCloudOk($what) {
    if ($LASTEXITCODE -ne 0) { throw "$what 失敗（gcloud 退出碼 $LASTEXITCODE）。上面的訊息是原因。" }
}

# -VerifyOnly 時整段建置流程跳過。這裡不是「加速」——是讓驗證能獨立重跑，
# 因為一個要等五分鐘才能重試的檢查，實務上等於沒有檢查。
if (-not $VerifyOnly) {

    # ── 出發前檢查 ───────────────────────────────────────────────────────
    # 這兩個檔案是病人影像不進容器映像的唯一防線。缺了就中止——
    # 映像一旦推上登錄檔，之後刪 flywheel/ 也拿不回來，因為它已經在某個映像層裡。
    Say "檢查建置上下文"
    foreach ($f in @(".gcloudignore", ".dockerignore")) {
        if (-not (Test-Path $f)) { throw "缺 $f。沒有它，flywheel/ 的傷口影像會被上傳並烤進映像。中止。" }
    }
    if ((Get-Content .gcloudignore -Raw) -notmatch "flywheel") {
        throw ".gcloudignore 沒有排除 flywheel/。中止。"
    }
    Write-Host "  ✓ flywheel/ 與 *.db 已排除"

    # ── 部署身分：把「這一版是哪一版」帶進容器 ───────────────────────────
    #
    # 沒有這個的話，主控台只能顯示 Cloud Run 的 revision 序號，而序號回答不了
    # 「跑的是不是我剛推的那份程式碼」。這個專案已經被「看起來成功的部署」
    # 咬過兩次，兩次都是因為部署動作與執行中的程式碼之間沒有可比對的標識。
    $GitCommit = (& git rev-parse --short HEAD 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $GitCommit) {
        $GitCommit = "unknown"
        Warn "取不到 git commit（不在 repo 內？）——主控台會顯示「未帶入」"
    } else {
        # 有未提交的變更時要標出來：部署的是工作目錄，不是那個 commit。
        # 不標的話主控台會顯示一個**看似精確而實際不對**的 SHA。
        & git diff --quiet HEAD 2>$null
        if ($LASTEXITCODE -ne 0) {
            $GitCommit = "$GitCommit-dirty"
            Warn "工作目錄有未提交的變更——部署的內容與 $GitCommit 不完全相同"
        }
    }
    $DeployedAt = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    Write-Host "  ✓ 部署身分 $GitCommit @ $DeployedAt"

    Say "確認專案與帳單"
    Invoke-GCloud config set project $ProjectId | Out-Null
    # 帳單沒綁的話 Cloud Run 會以權限錯誤失敗，而訊息完全不會提到「帳單」——
    # 那是這條流程最常卡住也最難自行歸因的一步，所以先檢查再說。
    $billing = Invoke-GCloud billing projects describe $ProjectId --format "value(billingEnabled)" 2>$null
    if ($LASTEXITCODE -ne 0) {
        # 查詢本身失敗（多半是 Cloud Billing API 未啟用或帳號無 billing.viewer 權限）。
        # 這與「確定沒綁」是兩件事，不可混為一談——後者要去綁，前者要去開 API 或要權限。
        Warn "無法查詢帳單狀態（可能是 Cloud Billing API 未啟用或權限不足）。跳過檢查，直接嘗試部署。"
        Warn "若部署以權限錯誤失敗，請先執行： gcloud.cmd billing projects link $ProjectId --billing-account=<ACCOUNT_ID>"
    } elseif ($billing -ne "True") {
        # ⚠ here-string 的結束標記 "@ **必須頂到行首**，不能有任何縮排。
        # 縮排的話 PowerShell 找不到結尾，會把後面整份腳本都當成字串內容，
        # 而錯誤訊息會是遙遠某處的「缺少 '}'」——完全指不到這裡。
        throw @"
專案 $ProjectId 尚未連結帳單帳戶，Cloud Run 無法部署。
（免費額度仍需綁定帳單帳戶；額度內不會扣款。）

    gcloud billing accounts list
    gcloud billing projects link $ProjectId --billing-account=<上面列出的 ACCOUNT_ID>

綁好之後重跑本腳本。
"@
    }
    Write-Host "  ✓ 帳單已連結"

    if ($Setup) {
        Say "開啟必要的 GCP 服務"
        Invoke-GCloud services enable run.googleapis.com artifactregistry.googleapis.com `
            storage.googleapis.com cloudbuild.googleapis.com secretmanager.googleapis.com

        Say "建立儲存桶 gs://$Bucket（$Region，統一存取控管）"
        # 統一存取控管：關掉物件層級 ACL，權限只由 IAM 決定。
        # 混用兩套權限模型是「以為關了其實還開著」最常見的來源。
        Invoke-GCloud storage buckets create "gs://$Bucket" --location=$Region --uniform-bucket-level-access 2>$null
        if ($LASTEXITCODE -ne 0) { Warn "儲存桶已存在或建立失敗，繼續" }

        Say "產生密碼與 JWT 金鑰並存入 Secret Manager"
        # ⚠ **已存在就不重新產生**。
        # 重跑 -Setup 是常態（第一次部署失敗、修完再跑一次），若每次都新增一個版本，
        # 密碼就會在使用者不知情的情況下換掉——他手上抄的那組突然失效，
        # 而症狀是「App 顯示帳密不正確」，完全看不出是重跑腳本造成的。
        $chars = (48..57) + (65..90) + (97..122)
        Invoke-GCloud secrets describe woundai-admin-password 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Warn "密碼 secret 已存在，沿用現有的（不重新產生）。"
            Write-Host "  取回： gcloud.cmd secrets versions access latest --secret=woundai-admin-password"
        } else {
            $pw = -join ($chars | Get-Random -Count 32 | ForEach-Object { [char]$_ })
            # ⚠ **不可**用 `$pw | gcloud ... --data-file=-`。
            # PowerShell 送進管線時會在字串尾端補 CRLF，於是 Secret Manager 存的其實是
            # `<密碼>\r\n`。後果有兩層：容器拿到的 ADMIN_PASSWORD 帶著換行；而使用者用
            # `gcloud secrets versions access` 取回時 PowerShell 會把它切成**多行陣列**，
            # `ConvertTo-Json` 再把陣列序列化成 JSON list，後端就收到 list 而不是字串 → 500。
            # 改用 WriteAllText 寫暫存檔（不補換行），用完立刻刪除。
            $tmp = [IO.Path]::GetTempFileName()
            try {
                [IO.File]::WriteAllText($tmp, $pw, (New-Object Text.UTF8Encoding $false))
                & $script:GCLOUD secrets create woundai-admin-password --data-file=$tmp --replication-policy=automatic
                Assert-GCloudOk "建立密碼 secret"
            } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
            Write-Host "`n" -NoNewline
            Write-Host "═══════════════════════════════════════════════" -ForegroundColor Green
            Write-Host " 後端密碼（App 設定頁要填，只顯示這一次）：" -ForegroundColor Green
            Write-Host "   帳號：admin"
            Write-Host "   密碼：$pw"
            Write-Host "═══════════════════════════════════════════════" -ForegroundColor Green
            Warn "現在就記下來。之後可用 gcloud.cmd secrets versions access latest --secret=woundai-admin-password 取回。"
        }
        Invoke-GCloud secrets describe woundai-jwt-secret 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) {
            $jwtKey = -join ($chars | Get-Random -Count 48 | ForEach-Object { [char]$_ })
            $tmp2 = [IO.Path]::GetTempFileName()
            try {
                [IO.File]::WriteAllText($tmp2, $jwtKey, (New-Object Text.UTF8Encoding $false))
                & $script:GCLOUD secrets create woundai-jwt-secret --data-file=$tmp2 --replication-policy=automatic
                Assert-GCloudOk "建立 JWT secret"
            } finally { Remove-Item $tmp2 -Force -ErrorAction SilentlyContinue }
        }
    }

    # ── 執行服務帳號的授權 ─────────────────────────────────────────────────
    #
    # ⚠ **建立 secret 不等於 Cloud Run 讀得到它。**
    # Cloud Run 的 revision 是以「執行服務帳號」的身分跑的（預設是 Compute Engine 預設 SA），
    # 而那個帳號預設**沒有**讀取 Secret Manager 的權限。少了這一步，部署會在最後一刻失敗，
    # 訊息是一長串 `Permission denied on secret ... secret_key_ref`。
    #
    # 儲存桶同理，但更陰險：桶權限缺了**不會讓部署失敗**，服務照樣起來，
    # 直到第一次有人量測時才在寫入影像的那一行炸掉 —— 使用者看到的是「後端錯誤」，
    # 日誌裡是 403，而部署當下一切正常。所以兩個授權放在一起、每次部署都跑（本身冪等）。
    Say "授權 Cloud Run 執行服務帳號"
    $projNum = Invoke-GCloud projects describe $ProjectId --format "value(projectNumber)"
    if (-not $projNum) { throw "取不到專案編號，無法授權執行服務帳號。" }
    $runSa = "$projNum-compute@developer.gserviceaccount.com"
    Write-Host "  服務帳號：$runSa"

    foreach ($s in @("woundai-admin-password", "woundai-jwt-secret")) {
        Invoke-GCloud secrets add-iam-policy-binding $s `
            --member="serviceAccount:$runSa" `
            --role="roles/secretmanager.secretAccessor" --quiet | Out-Null
        if ($LASTEXITCODE -ne 0) { Warn "授權 secret $s 失敗（若非 -Setup 首次執行可忽略）" }
        else { Write-Host "  ✓ 可讀取 secret：$s" }
    }

    # objectAdmin：飛輪需要建立（存影像）、讀取、列出，以及**刪除**——
    # 撤回同意時把影像移進 quarantine，在物件儲存是「複製再刪除」，只有讀寫是不夠的。
    Invoke-GCloud storage buckets add-iam-policy-binding "gs://$Bucket" `
        --member="serviceAccount:$runSa" --role="roles/storage.objectAdmin" --quiet | Out-Null
    if ($LASTEXITCODE -ne 0) { Warn "授權儲存桶失敗——服務會起得來，但第一次量測寫影像時會 403" }
    else { Write-Host "  ✓ 可讀寫儲存桶：gs://$Bucket" }

    # 稽核桶（若已由 harden_bucket.ps1 -Audit 建立）。桶不存在就略過——
    # 沒有稽核桶時後端會退回主桶，功能正常但稽核紀錄是刪得掉的，health 的 store 欄位會誠實反映。
    Invoke-GCloud storage buckets describe "gs://$AuditBucket" --format="value(name)" 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Invoke-GCloud storage buckets add-iam-policy-binding "gs://$AuditBucket" `
            --member="serviceAccount:$runSa" --role="roles/storage.objectAdmin" --quiet | Out-Null
        if ($LASTEXITCODE -eq 0) { Write-Host "  ✓ 可寫入稽核桶：gs://$AuditBucket" }
        else { Warn "授權稽核桶失敗——稽核紀錄會寫不進去" }
    } else {
        Warn "稽核桶 gs://$AuditBucket 不存在，稽核紀錄將寫入主桶（刪得掉）。"
        Warn "  建立方式： .\harden_bucket.ps1 -ProjectId $ProjectId -Bucket $Bucket -Audit"
    }

    # ── 複製 engineering 模組到 vendor/ ───────────────────────────────────
    #
    # ⚠ Docker 的建置上下文只有 `Backend/Flask/`。app.py 從 `../../engineering` 載入
    # 組織分類與 PUSH 模組——那條路徑在**映像裡不存在**。本機開發完全看不出問題，
    # 直到部署上雲，classify 才以 `No module named 'wound_classifier'` 回 503，
    # 而登入與 stats 都是 200，健康檢查也全綠。
    #
    # vendor/ **不進版控**：同一份程式碼放兩個地方，遲早有人只改了其中一個。
    # 每次部署重新複製，來源永遠是 engineering/ 那一份。
    Say "複製 engineering 模組到 vendor/"
    $vendor = Join-Path $PSScriptRoot "vendor"
    $engRoot = Join-Path $PSScriptRoot "..\..\engineering"
    $needed = @(
        "phase2\wound_classifier.py",
        "phase1\clinical_rules.py",
        "phase2\aruco_calibrate.py",
        "phase2\verify_area_sheet.py",
        # 色準校正。漏了它 classify 不會壞——它被包在 try/except 裡——
        # 而是**安靜退回 gray-world 白平衡**，紅色被壓抑 ×0.78，肉芽被低估。
        # 這正是本專案最典型的失敗形狀：一切回 200，數字看起來合理。
        "phase2\color_calib.py",
        "phase0\preprocessing.json"
    )
    if (Test-Path $vendor) { Remove-Item $vendor -Recurse -Force }
    New-Item -ItemType Directory -Path $vendor | Out-Null
    $missing = @()
    foreach ($rel in $needed) {
        $src = Join-Path $engRoot $rel
        if (Test-Path $src) {
            Copy-Item $src (Join-Path $vendor (Split-Path $rel -Leaf)) -Force
            Write-Host "  ✓ $(Split-Path $rel -Leaf)"
        } else { $missing += $rel }
    }
    if ($missing) {
        # 缺檔就中止。讓它在這裡大聲失敗，而不是部署完才在手機上看到 503——
        # 那時候要從「分析失敗」一路追到「建置上下文不含 engineering/」，成本高得多。
        throw "engineering 缺少必要模組，classify 會在雲端 503：`n  " + ($missing -join "`n  ")
    }

    Say "部署到 Cloud Run（$Region）"
    # 參數理由見 docs/deploy_cloudrun.md §4：
    #   memory 2Gi  — 難例集成同時載三個 ONNX 模型；1Gi 會被 OOM kill 且不留例外
    #   cpu 2       — 實測難例集成 796 ms；1 vCPU 會翻倍到使用者會放棄的程度
    #   concurrency 4 — 推論是 CPU-bound，預設 80 會讓請求擠在兩顆核心上
    #   max-instances 3 — 成本上限，避免被掃描機器人打到無限擴張
    Invoke-GCloud run deploy $Service `
        --source . `
        --region $Region `
        --allow-unauthenticated `
        --memory $Memory `
        --cpu 2 `
        --timeout 120 `
        --concurrency 4 `
        --min-instances 0 `
        --max-instances 3 `
        --set-env-vars "WOUNDAI_STORE=gcs,WOUNDAI_GCS_BUCKET=$Bucket,WOUNDAI_GCS_PREFIX=flywheel,WOUNDAI_AUDIT_BUCKET=$AuditBucket,GIT_COMMIT=$GitCommit,DEPLOYED_AT=$DeployedAt" `
        --set-secrets "ADMIN_PASSWORD=woundai-admin-password:latest,JWT_SECRET_KEY=woundai-jwt-secret:latest"
    Assert-GCloudOk "Cloud Run 部署"


} else {
    Say "只跑驗證（-VerifyOnly，跳過建置與部署）"
    Invoke-GCloud config set project $ProjectId | Out-Null
}

# ⚠ Cloud Run 一個服務有**兩個等價網址**：
#   舊式 https://<服務>-<雜湊>-<區碼>.a.run.app
#   新式 https://<服務>-<專案編號>.<區域>.run.app   ← gcloud deploy 印的是這個
# `value(status.url)` 回的是舊式，於是腳本最後印的網址與部署過程印的不同，
# 而使用者手上 App 設定的又是另一個 —— 三個都能用，但看起來像是「網址變了」，
# 會讓人跑去改一個根本不需要改的設定。統一取新式。
#
# ⚠ **不要相信欄位名，要相信打得通。**
# 第一版用 `--format "value(status.urls[])"` 挑新式網址，但那個欄位在部分 gcloud
# 版本根本不存在——查詢靜默回空字串，偏好邏輯無聲失效，於是又印回舊式網址。
# 這種「查一個不存在的欄位」不會報錯，只會安靜地什麼都不做，是最難察覺的一類 bug。
# 改成：依文件規則組出新式網址，**實際打一次 /api/health**，200 才採用。
$url = Invoke-GCloud run services describe $Service --region $Region --format "value(status.url)"
$altUrl = $null
$projNumForUrl = Invoke-GCloud projects describe $ProjectId --format "value(projectNumber)"
if ($projNumForUrl) {
    $newStyle = "https://$Service-$projNumForUrl.$Region.run.app"
    if ($newStyle -ne $url) {
        $probe = Get-HttpResult -Uri "$newStyle/api/health" -TimeoutSec 30
        if ($probe.Code -eq 200) { $altUrl = $url; $url = $newStyle }
    }
}

Say "部署後驗證"
try {
    $h = Invoke-RestMethod "$url/api/health" -TimeoutSec 90
    Write-Host "  ✓ /api/health → $($h.status)"
    # 降級模式是「會回答的錯誤」——服務照常回 200 並給出面積，只是演算法完全不同。
    # 這種失敗不會有人注意到，所以部署當下就要攔。
    if ($h.status -ne "healthy") {
        Warn "❌ 後端處於降級模式：$($h.degraded_reason)"
        Warn "   量測結果不具臨床參考價值。請確認 requirements.txt 含 onnxruntime 且 models/ 內有 .onnx。"
    } else { Write-Host "  ✓ 分割模型已載入（非降級模式）" }
    # ── 端點真的掛上了嗎 ──────────────────────────────────────────
    #
    # blueprint 註冊被包在 try/except 裡，失敗只印一行 stdout。
    # 2026-08-19：`/api/v1/lite/segment` 因為註冊時參照了尚未定義的函式而
    # NameError，服務照常啟動、健康檢查全綠、**那條路一直 404**，兩輪沒被發現。
    #
    # 用 GET 去打一個只收 POST 的端點：**掛上了會回 405，沒掛上才是 404**。
    # 這一條分得出「端點存在但方法不對」與「端點根本不存在」，
    # 而後者正是那次事故的形狀。
    if ($h.blueprint_failures -and $h.blueprint_failures.Count -gt 0) {
        foreach ($bf in $h.blueprint_failures) {
            Warn "❌ 端點未註冊：$($bf.name) —— $($bf.error)"
        }
    }
    foreach ($ep in @("/api/v1/lite/segment", "/api/v1/annotation", "/api/v1/depth")) {
        $r = Get-HttpResult -Uri "$url$ep"
        if ($r.StatusCode -eq 404) {
            Warn "❌ $ep 回 404 —— 這條路**沒有掛上**（blueprint 註冊失敗），不是權限問題。"
        } elseif ($r.StatusCode -in 401, 403, 405) {
            Write-Host "  ✓ $ep 已註冊（GET → $($r.StatusCode)）"
        } else {
            Write-Host "  ? $ep → $($r.StatusCode)（非預期，但至少不是 404）"
        }
    }
    # classify 模組單獨檢查：登入與 stats 會照常 200，只有量測會 503，
    # 光看 status 綠燈是抓不到的（實際發生過）。
    if ($h.services.classify_modules -ne $true) {
        Warn "❌ classify 模組未載入 —— /api/v1/classify 會回 503（量測會失敗）"
    } else { Write-Host "  ✓ classify 模組已載入（組織分類 / PUSH / ArUco）" }
    # 儲存後端必須是 gcs，否則資料會隨 Cloud Run 實例回收而消失且無任何錯誤
    if ($h.store -notlike "gcs://*") {
        Warn "❌ 儲存後端是 [$($h.store)]，不是 GCS。Cloud Run 的容器檔案系統是暫時的，"
        Warn "   佇列與影像會在實例回收時消失。請確認 WOUNDAI_STORE=gcs 已設定。"
    } else { Write-Host "  ✓ 儲存後端：$($h.store)" }
    # 稽核桶接上時 describe() 會帶 WORM 字樣。設了環境變數卻沒帶，
    # 幾乎必然是「只換了環境變數沒重建映像」——舊映像的程式碼不認得這個變數。
    if ($h.store -notlike "*WORM*") {
        Warn "ℹ 稽核紀錄寫在主桶（刪得掉）。若已建稽核桶卻仍顯示此訊息，"
        Warn "   請確認是用 deploy_cloudrun.ps1 重建映像，而不是只跑 run services update。"
    } else { Write-Host "  ✓ 稽核軌跡寫入 WORM 桶" }
} catch {
    Warn "健康檢查失敗（首次冷啟動可能較久，稍後重試）：$_"
}

# 舊的公開預設密碼必須已經失效。這條檢查存在的理由：
# 密碼曾經硬編碼在公開 repo 裡，如果 secret 沒掛好而程式又留著預設值，
# 服務就會用一個全世界都知道的密碼對外開放。
$r = Get-HttpResult -Uri "$url/api/auth/login" -Method POST `
     -Body '{"username":"admin","password":"woundai-admin"}'
if (-not $r.Reached) {
    # 連不上 ≠ 密碼失效。這兩者絕不可混為一談 —— 混了就是一個永遠會過的檢查。
    Warn "⚠ 無法連線，**舊預設密碼未經驗證**（不是通過）：$($r.Content)"
} elseif ($r.Code -eq 200) {
    Warn "❌ 舊的公開預設密碼仍可登入！請立即檢查 ADMIN_PASSWORD secret 是否正確掛載。"
} else {
    Write-Host "  ✓ 舊預設密碼已失效（HTTP $($r.Code)）"
}

# 管理端點必須 fail-closed。
#
# 這條檢查的存在理由很具體：新增一個路由時忘了掛 @jwt_required 是最容易犯、
# 也最不容易被發現的錯——本機測試時瀏覽器帶著 token，一切正常；
# 上線後它就是一個**任何人都能列出所有帳號**的公開端點。
# 部署當下用「不帶 token」打一次，是唯一能在事故前抓到它的時機。
Say "管理端點存取控制"
foreach ($ep in @("/api/v1/users", "/api/v1/audit",
                  "/api/v1/flywheel/records",
                  "/api/v1/flywheel/record/0123456789abcdef/preview.svg")) {
    $r = Get-HttpResult -Uri "$url$ep"
    if (-not $r.Reached) {
        Warn "⚠ $ep 連不上，**未經驗證**：$($r.Content)"
    } elseif ($r.Code -eq 200) {
        Warn "❌ $ep 未帶 token 竟回 200 —— 帳號清單/稽核是公開的，請立即檢查 @jwt_required。"
    } elseif ($r.Code -eq 404) {
        Warn "⚠ $ep 回 404 —— 這版映像沒有管理端點。若剛加了功能，請確認是重建映像而非只更新環境變數。"
    } else {
        Write-Host "  ✓ $ep 未帶 token → HTTP $($r.Code)（拒絕）"
    }
}
$c = Get-HttpResult -Uri "$url/console"
if (-not $c.Reached) {
    Warn "⚠ /console 連不上，**未經驗證**：$($c.Content)"
} elseif ($c.Code -eq 200 -and $c.Content -match 'id="tab-users"' -and $c.Content -match 'id="tab-audit"') {
    Write-Host "  ✓ /console 含四個管理頁籤（Dashboard・系統狀態・稽核・帳號）"
} elseif ($c.Code -eq 200) {
    Warn "⚠ /console 可開，但沒有管理分區 —— 這版映像是舊的主控台。"
} else {
    Warn "⚠ /console → HTTP $($c.Code)"
}

# ── 部署身分：雲端跑的到底是不是本機這一版 ──────────────────────────────
#
# 這是整份驗證裡最直接回答「部署有沒有生效」的一條。
# 先前兩次事故（漏裝 onnxruntime、URL 探測靜默失效）都不是功能報錯，
# 而是「部署完了，但跑的不是預期的東西」——那從功能表現上看不出來。
Say "部署身分"
$hb = Get-HttpResult -Uri "$url/api/health"
if (-not $hb.Reached) {
    Warn "⚠ /api/health 連不上，部署身分**未經驗證**：$($hb.Content)"
} else {
    try { $hj = $hb.Content | ConvertFrom-Json } catch { $hj = $null }
    $b = $hj.build
    if (-not $b) {
        Warn "⚠ /api/health 沒有 build 區塊 —— 這版映像是舊的（請重建，不是只更新環境變數）。"
    } else {
        Write-Host "  雲端 revision   $($b.revision)"
        Write-Host "  雲端 git commit $($b.git_commit)"
        Write-Host "  部署時間        $($b.deployed_at)"
        $local = (& git rev-parse --short HEAD 2>$null)
        if ($LASTEXITCODE -ne 0 -or -not $local) {
            Warn "  ⚠ 取不到本機 commit，無法比對"
        } elseif (-not $b.git_commit -or $b.git_commit -eq "unknown") {
            Warn "  ⚠ 雲端沒有 commit 標識 —— 這版是用舊腳本部署的，無法確認版本"
        } elseif ($b.git_commit -eq $local -or $b.git_commit -eq "$local-dirty") {
            Write-Host "  ✓ 與本機 $local 一致"
        } else {
            Warn "  ❌ 雲端是 $($b.git_commit)，本機是 $local —— **部署沒生效，或推錯了分支**。"
            Warn "     功能測試多半仍會通過，因為舊版也能跑。請重新部署後再驗一次。"
        }
    }
}

if ($altUrl) {
    # 兩個都印出來。使用者手上 App 可能設的是任一個，看到「只有一個」會以為要改設定。
    Write-Host "`n（另一個等價網址：$altUrl —— 同一個服務，兩者皆可用，不必改 App 設定）"
}

Write-Host "`n服務網址　：$url" -ForegroundColor Green
Write-Host "管理主控台：$url/console" -ForegroundColor Green
Write-Host @"

下一步
  1. 管理者：瀏覽器開 $url/console → 以 admin 登入 → 展開「帳號管理／稽核軌跡／系統狀態」
     （操作手冊：docs/admin_operations.md）
  2. 臨床測試者：App 主畫面 →「設定」→ 後端位址填上面的網址（含 https://）
     → 填各自的帳號密碼 → 儲存 → 連線測試
  3. 批次開通帳號：.\provision_users.ps1 -BaseUrl $url
"@
