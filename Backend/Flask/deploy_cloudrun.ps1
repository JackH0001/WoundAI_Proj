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
    [switch]$Setup
)

$ErrorActionPreference = "Stop"

function Say($msg) { Write-Host "`n▶ $msg" -ForegroundColor Cyan }
function Warn($msg) { Write-Host "⚠ $msg" -ForegroundColor Yellow }

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

gcloud config set project $ProjectId | Out-Null

if ($Setup) {
    Say "開啟必要的 GCP 服務"
    gcloud services enable run.googleapis.com artifactregistry.googleapis.com `
        storage.googleapis.com cloudbuild.googleapis.com secretmanager.googleapis.com

    Say "建立儲存桶 gs://$Bucket（$Region，統一存取控管）"
    # 統一存取控管：關掉物件層級 ACL，權限只由 IAM 決定。
    # 混用兩套權限模型是「以為關了其實還開著」最常見的來源。
    gcloud storage buckets create "gs://$Bucket" --location=$Region --uniform-bucket-level-access 2>$null
    if ($LASTEXITCODE -ne 0) { Warn "儲存桶已存在或建立失敗，繼續" }

    Say "產生密碼與 JWT 金鑰並存入 Secret Manager"
    # 密碼不經過磁碟、不進 shell 歷史，直接從管線送進 Secret Manager
    $chars = (48..57) + (65..90) + (97..122)
    $pw = -join ($chars | Get-Random -Count 32 | ForEach-Object { [char]$_ })
    $jwtKey = -join ($chars | Get-Random -Count 48 | ForEach-Object { [char]$_ })
    $pw | gcloud secrets create woundai-admin-password --data-file=- --replication-policy=automatic 2>$null
    if ($LASTEXITCODE -ne 0) { $pw | gcloud secrets versions add woundai-admin-password --data-file=- }
    $jwtKey | gcloud secrets create woundai-jwt-secret --data-file=- --replication-policy=automatic 2>$null
    if ($LASTEXITCODE -ne 0) { $jwtKey | gcloud secrets versions add woundai-jwt-secret --data-file=- }

    Write-Host "`n" -NoNewline
    Write-Host "═══════════════════════════════════════════════" -ForegroundColor Green
    Write-Host " 後端密碼（App 設定頁要填，只顯示這一次）：" -ForegroundColor Green
    Write-Host "   帳號：admin"
    Write-Host "   密碼：$pw"
    Write-Host "═══════════════════════════════════════════════" -ForegroundColor Green
    Warn "現在就記下來。之後可用 gcloud secrets versions access latest --secret=woundai-admin-password 取回。"
}

Say "部署到 Cloud Run（$Region）"
# 參數理由見 docs/deploy_cloudrun.md §4：
#   memory 2Gi  — 難例集成同時載三個 ONNX 模型；1Gi 會被 OOM kill 且不留例外
#   cpu 2       — 實測難例集成 796 ms；1 vCPU 會翻倍到使用者會放棄的程度
#   concurrency 4 — 推論是 CPU-bound，預設 80 會讓請求擠在兩顆核心上
#   max-instances 3 — 成本上限，避免被掃描機器人打到無限擴張
gcloud run deploy $Service `
    --source . `
    --region $Region `
    --allow-unauthenticated `
    --memory 2Gi `
    --cpu 2 `
    --timeout 120 `
    --concurrency 4 `
    --min-instances 0 `
    --max-instances 3 `
    --set-env-vars "WOUNDAI_STORE=gcs,WOUNDAI_GCS_BUCKET=$Bucket,WOUNDAI_GCS_PREFIX=flywheel" `
    --set-secrets "ADMIN_PASSWORD=woundai-admin-password:latest,JWT_SECRET_KEY=woundai-jwt-secret:latest"

$url = gcloud run services describe $Service --region $Region --format "value(status.url)"

Say "部署後驗證"
try {
    $h = Invoke-RestMethod "$url/api/health" -TimeoutSec 90
    Write-Host "  ✓ /api/health → $($h.status)"
} catch {
    Warn "健康檢查失敗（首次冷啟動可能較久，稍後重試）：$_"
}

# 舊的公開預設密碼必須已經失效。這條檢查存在的理由：
# 密碼曾經硬編碼在公開 repo 裡，如果 secret 沒掛好而程式又留著預設值，
# 服務就會用一個全世界都知道的密碼對外開放。
try {
    $r = Invoke-WebRequest "$url/api/auth/login" -Method POST -ContentType "application/json" `
        -Body '{"username":"admin","password":"woundai-admin"}' -SkipHttpErrorCheck -TimeoutSec 60
    if ($r.StatusCode -eq 200) {
        Warn "❌ 舊的公開預設密碼仍可登入！請立即檢查 ADMIN_PASSWORD secret 是否正確掛載。"
    } else {
        Write-Host "  ✓ 舊預設密碼已失效（HTTP $($r.StatusCode)）"
    }
} catch {
    Write-Host "  ✓ 舊預設密碼已失效"
}

Write-Host "`n服務網址：$url" -ForegroundColor Green
Write-Host "主控台　：$url/console"
Write-Host "`n下一步：App 主畫面 →「設定」→ 後端位址填上面的網址（含 https://）→ 帳號 admin + 上面的密碼 → 儲存 → 連線測試。"
