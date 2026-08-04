# 啟動 Pixel_7 模擬器 → 等開機 → 安裝並開啟 App。
#
# 用法：
#   .\run_emulator.ps1                # 啟動 + 安裝 + 開啟
#   .\run_emulator.ps1 -NoInstall     # 只啟動模擬器
#   .\run_emulator.ps1 -Avd Pixel_2   # 換一個 AVD
#   .\run_emulator.ps1 -List          # 列出所有 AVD
#
# ⚠ 本檔須以 UTF-8 with BOM 儲存（Windows PowerShell 5.1 否則以 Big5 解讀而語法全爛）。

param(
    [string]$Avd = "Pixel_7",
    [switch]$NoInstall,
    [switch]$List,
    # 跳過快照直接冷開機。模擬器上次被強制關閉時若正在存狀態，快照會損毀，
    # 下次啟動就卡在載入而完全沒有錯誤訊息（畫面停在黑屏或 Windows 說「沒有回應」）。
    [switch]$ColdBoot,
    # 軟體算圖。顯卡驅動與 QEMU 的組合問題是啟動卡住的第二常見原因，
    # 慢但幾乎不會卡。
    [switch]$SoftwareGpu,
    # 砍掉殘留程序與鎖檔。模擬器沒有正常結束時，AVD 目錄會留下 *.lock，
    # 下次啟動會誤判成「已有另一個實例」而卡住。
    [switch]$Clean
)

$ErrorActionPreference = "Continue"
$sdk = "$env:LOCALAPPDATA\Android\Sdk"
$emu = "$sdk\emulator\emulator.exe"
$adb = "$sdk\platform-tools\adb.exe"

if (-not (Test-Path $emu)) { throw "找不到模擬器：$emu（確認 Android SDK 路徑）" }
if (-not (Test-Path $adb)) { throw "找不到 adb：$adb" }

if ($List) { & $emu -list-avds; exit 0 }

if ($Clean) {
    Write-Host "清理殘留程序與鎖檔…" -ForegroundColor Cyan
    Get-Process qemu-system-x86_64 -ErrorAction SilentlyContinue | Stop-Process -Force
    Get-Process emulator -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep 2
    $avdDir = "$env:USERPROFILE\.android\avd\$Avd.avd"
    if (Test-Path $avdDir) {
        Get-ChildItem $avdDir -Filter "*.lock" -Recurse -Force -ErrorAction SilentlyContinue |
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "  ✓ 已清理 $avdDir 的鎖檔" -ForegroundColor Gray
    }
    & $adb kill-server 2>$null | Out-Null
}

# 已經開著就不要再開一台。多開會讓 installDebug 遇到多裝置而失敗，
# 訊息只會說「found more than one device」，看不出是自己重複啟動造成的。
$running = & $adb devices | Select-String "^emulator-\S+\s+device$"
if ($running) {
    Write-Host "模擬器已在執行中，略過啟動。" -ForegroundColor Gray
} else {
    # 變數名不可用 $args：那是 PowerShell 的自動變數（存未繫結的參數），
    # 覆寫它在某些呼叫情境下會有意外行為。
    $emuArgs = @("-avd", $Avd)
    if ($ColdBoot) { $emuArgs += "-no-snapshot-load" }
    if ($SoftwareGpu) { $emuArgs += @("-gpu", "swiftshader_indirect") }
    $extra = if ($emuArgs.Count -gt 2) { " （" + ($emuArgs[2..($emuArgs.Count - 1)] -join " ") + "）" } else { "" }
    Write-Host "啟動 $Avd$extra" -ForegroundColor Cyan
    Start-Process $emu -ArgumentList $emuArgs
    & $adb wait-for-device

    # boot_completed 才代表桌面可用；wait-for-device 只等到 adb 連得上，
    # 那時候安裝 App 會以 INSTALL_FAILED_* 失敗，而原因完全看不出來。
    Write-Host "等待開機完成…" -ForegroundColor Cyan
    $t = 0
    while (((& $adb shell getprop sys.boot_completed 2>$null) -replace '\s', '') -ne "1") {
        Start-Sleep 3; $t += 3
        if ($t -gt 300) { throw "等待逾時（5 分鐘）。模擬器可能卡在開機畫面，請手動確認。" }
    }
    Write-Host "  ✓ 開機完成（$t 秒）" -ForegroundColor Gray
}

if ($NoInstall) { exit 0 }

Write-Host "`n安裝 App …" -ForegroundColor Cyan
Push-Location $PSScriptRoot
try {
    & .\gradlew :app:installDebug
    if ($LASTEXITCODE -ne 0) { throw "安裝失敗（見上方 Gradle 輸出）" }
} finally { Pop-Location }

& $adb logcat -c
& $adb shell am start -n com.woundmeasurement.app/.MainActivity | Out-Null
Write-Host "`n已開啟 WoundAI。" -ForegroundColor Green
Write-Host "看錯誤： adb logcat -d *:E | Select-String `"Room|AndroidRuntime|woundmeasurement`""
