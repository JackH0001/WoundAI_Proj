# 設定 Cloud Scheduler 定時暖機，避免門診時段撞上冷啟動。
#
# 用法：
#   .\setup_warmup.ps1 -ProjectId woundai-jackh001
#   .\setup_warmup.ps1 -ProjectId woundai-jackh001 -Remove     # 移除
#
# ⚠ 本檔須以 UTF-8 with BOM 儲存（Windows PowerShell 5.1 否則以 Big5 解讀而語法全爛）。

param(
    [Parameter(Mandatory = $true)][string]$ProjectId,
    [string]$Region = "asia-east1",
    [string]$Service = "woundai-backend",
    [string]$JobName = "woundai-warmup",
    # 預設：週一到週五 06:00–20:00，每 10 分鐘一次。
    # Cloud Run 閒置約 15 分鐘會回收實例，10 分鐘的間隔留了餘裕。
    [string]$Schedule = "*/10 6-20 * * 1-5",
    [switch]$Remove
)

$ErrorActionPreference = "Continue"
$script:GCLOUD = if (Get-Command gcloud.cmd -ErrorAction SilentlyContinue) { "gcloud.cmd" } else { "gcloud" }
function Say($m) { Write-Host "`n▶ $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "⚠ $m" -ForegroundColor Yellow }
function Invoke-GCloud { & $script:GCLOUD @args }

Invoke-GCloud config set project $ProjectId | Out-Null

if ($Remove) {
    Say "移除暖機排程"
    Invoke-GCloud scheduler jobs delete $JobName --location $Region --quiet
    if ($LASTEXITCODE -eq 0) { Write-Host "  ✓ 已移除" } else { Warn "移除失敗（可能本來就不存在）" }
    exit 0
}

Say "啟用 Cloud Scheduler API"
Invoke-GCloud services enable cloudscheduler.googleapis.com
if ($LASTEXITCODE -ne 0) { throw "啟用 Cloud Scheduler API 失敗" }

$url = Invoke-GCloud run services describe $Service --region $Region --format "value(status.url)"
if (-not $url) { throw "取不到 Cloud Run 服務網址（$Service / $Region）" }
$target = "$url/api/health"
Write-Host "  暖機目標：$target"

Say "建立排程（$Schedule，台北時間）"
# ⚠ **--time-zone 一定要指定**。
# Cloud Scheduler 預設是 UTC，"6-20" 會變成台北時間 14:00–翌日 04:00——
# 也就是門診時段完全沒暖到，反而整夜在打。這種錯不會有任何錯誤訊息，
# 只會表現成「排程明明設了，白天第一次量測還是要等 30 秒」。
$common = @(
    "--location", $Region,
    "--schedule", $Schedule,
    "--time-zone", "Asia/Taipei",
    "--uri", $target,
    "--http-method", "GET",
    # 暖機請求本身很輕；逾時給足夠時間涵蓋冷啟動，否則排程會一直記為失敗
    "--attempt-deadline", "60s",
    "--description", "WoundAI: 門診時段保持 Cloud Run 實例常駐，避免醫師撞上冷啟動"
)

Invoke-GCloud scheduler jobs describe $JobName --location $Region 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) {
    Write-Host "  排程已存在，改為更新"
    Invoke-GCloud scheduler jobs update http $JobName @common
} else {
    Invoke-GCloud scheduler jobs create http $JobName @common
}
if ($LASTEXITCODE -ne 0) { throw "建立/更新排程失敗" }

Say "立即觸發一次以驗證"
Invoke-GCloud scheduler jobs run $JobName --location $Region
Start-Sleep 8
Invoke-GCloud scheduler jobs describe $JobName --location $Region `
    --format "table(name.basename(), schedule, timeZone, state, lastAttemptTime, status.code)"

Write-Host @"

已設定。要點：
  · 排程：$Schedule（台北時間），約每 10 分鐘打一次 /api/health
  · Cloud Scheduler 每個帳單帳戶每月 3 個工作免費，這只用掉 1 個
  · 暖機請求極輕（不做推論），對 Cloud Run 免費額度影響可忽略
  · 夜間與週末刻意不暖機——那時候沒人量測，讓它縮到零才不花錢

查看執行紀錄：
  gcloud.cmd scheduler jobs describe $JobName --location $Region
  gcloud.cmd logging read 'resource.type=cloud_scheduler_job' --limit 10

若門診時段改變，調整 -Schedule 後重跑本腳本即可（會自動改為更新）。
"@ -ForegroundColor Gray
