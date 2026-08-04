# 安裝並開啟 WoundAI 到**實體 Android 裝置**（而不是模擬器）。
#
# 用法：
#   .\install_device.ps1                 # 自動挑唯一的實體裝置
#   .\install_device.ps1 -List           # 列出所有已連接裝置
#   .\install_device.ps1 -Serial R5CT...  # 指定序號（多台時）
#
# 為什麼需要這支：模擬器與實體裝置同時連著時，`gradlew installDebug` 會因為
# 「found more than one device」失敗，或者更糟——裝到你沒預期的那一台，
# 然後你在實體裝置上找不到剛改的東西，以為是程式沒生效。
#
# ⚠ 本檔須以 UTF-8 with BOM 儲存（Windows PowerShell 5.1 否則以 Big5 解讀而語法全爛）。

param(
    [string]$Serial,
    [switch]$List,
    [switch]$NoLaunch
)

$ErrorActionPreference = "Continue"
$sdk = "$env:LOCALAPPDATA\Android\Sdk"
$adb = "$sdk\platform-tools\adb.exe"
if (-not (Test-Path $adb)) { throw "找不到 adb：$adb" }

function Get-Devices {
    # `adb devices -l` 的輸出：<serial>\t<state> <key:value>...
    # 模擬器的序號一律是 emulator-xxxx，實體裝置是硬體序號。
    $out = & $adb devices -l
    $rows = @()
    foreach ($line in $out) {
        if ($line -match '^(\S+)\s+(device|unauthorized|offline)\b(.*)$') {
            $rows += [pscustomobject]@{
                Serial    = $Matches[1]
                State     = $Matches[2]
                Info      = $Matches[3].Trim()
                IsEmulator = $Matches[1].StartsWith("emulator-")
            }
        }
    }
    return $rows
}

$devs = Get-Devices

if ($List) {
    if (-not $devs) { Write-Host "沒有偵測到任何裝置。" -ForegroundColor Yellow }
    foreach ($d in $devs) {
        $kind = if ($d.IsEmulator) { "模擬器" } else { "實體裝置" }
        Write-Host ("{0,-22} {1,-14} {2}  {3}" -f $d.Serial, $d.State, $kind, $d.Info)
    }
    exit 0
}

# unauthorized 是最常見也最容易誤判的狀態：裝置有連上，但手機上的
# 「允許 USB 偵錯？」對話框還沒按確定。這時候 adb 看得到它卻裝不進去，
# 而 Gradle 的錯誤訊息不會提到授權。
$unauth = @($devs | Where-Object { $_.State -eq "unauthorized" -and -not $_.IsEmulator })
if ($unauth) {
    Write-Host "⚠ 偵測到未授權的裝置：$($unauth.Serial -join ', ')" -ForegroundColor Yellow
    Write-Host "  請看手機螢幕，會有「允許 USB 偵錯？」的對話框——按「允許」（可勾選一律允許）。"
    Write-Host "  若沒出現：拔掉重插，或到「開發人員選項」按「撤銷 USB 偵錯授權」再重插。"
    exit 1
}

$phys = @($devs | Where-Object { -not $_.IsEmulator -and $_.State -eq "device" })

if ($Serial) {
    $target = $Serial
} elseif ($phys.Count -eq 1) {
    $target = $phys[0].Serial
} elseif ($phys.Count -eq 0) {
    Write-Host "沒有偵測到已授權的實體裝置。" -ForegroundColor Yellow
    Write-Host "檢查：① USB 線支援資料傳輸（有些是純充電線）② 手機開發人員選項的 USB 偵錯已開啟"
    Write-Host "      ③ 手機的 USB 模式設為「檔案傳輸 / MTP」而非「僅充電」"
    & $adb devices -l
    exit 1
} else {
    Write-Host "偵測到多台實體裝置，請用 -Serial 指定：" -ForegroundColor Yellow
    foreach ($d in $phys) { Write-Host "  $($d.Serial)  $($d.Info)" }
    exit 1
}

$info = ($devs | Where-Object { $_.Serial -eq $target }).Info
Write-Host "目標裝置：$target" -ForegroundColor Cyan
if ($info) { Write-Host "  $info" -ForegroundColor Gray }
$sdkVer = (& $adb -s $target shell getprop ro.build.version.sdk) -replace '\s', ''
$model = (& $adb -s $target shell getprop ro.product.model) -replace '\s+$', ''
$abi = (& $adb -s $target shell getprop ro.product.cpu.abi) -replace '\s', ''
Write-Host "  $model / Android SDK $sdkVer / $abi" -ForegroundColor Gray

# ANDROID_SERIAL 是 AGP 認得的環境變數，設了之後 installDebug 只會裝到這一台。
# 不設的話多裝置就會失敗，而錯誤訊息不會告訴你該怎麼指定。
$env:ANDROID_SERIAL = $target

Write-Host "`n安裝中…" -ForegroundColor Cyan
Push-Location $PSScriptRoot
try {
    & .\gradlew :app:installDebug
    if ($LASTEXITCODE -ne 0) { throw "安裝失敗（見上方 Gradle 輸出）" }
} finally { Pop-Location }

if (-not $NoLaunch) {
    & $adb -s $target logcat -c
    & $adb -s $target shell am start -n com.woundmeasurement.app/.MainActivity | Out-Null
    Write-Host "`n已在 $model 上開啟 WoundAI。" -ForegroundColor Green
}

Write-Host @"

實體裝置要驗的重點（模擬器測不到的）：
  1. 設定 → 後端位址填 Cloud Run 網址（10.0.2.2 在真機上不存在，設定頁會紅字警告）
  2. 相機拍照 —— 模擬器沒有真的相機
  3. ArUco 標記偵測與 mm/px 尺度 —— 這是面積量測的基準，只有真機拍得出來
  4. 端上 ONNX 模型 —— 先前為避免模擬器原生庫閃退而不自動載入，真機上才驗得到

看錯誤：
  adb -s $target logcat -d *:E | Select-String "Room|AndroidRuntime|woundmeasurement"
"@ -ForegroundColor Gray
