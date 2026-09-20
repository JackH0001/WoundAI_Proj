[CmdletBinding()]
param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
)

$ErrorActionPreference = "Continue"
$failures = [System.Collections.Generic.List[string]]::new()

function Check-Command([string]$Name, [scriptblock]$Probe) {
    try {
        $global:LASTEXITCODE = 0
        $value = & $Probe
        if ($LASTEXITCODE -ne 0) { throw "exit $LASTEXITCODE" }
        Write-Host "[OK] $Name  $value"
    } catch {
        Write-Host "[FAIL] $Name  $($_.Exception.Message)" -ForegroundColor Red
        $failures.Add($Name)
    }
}

$venvPython = Join-Path $RepoRoot ".venv-windows\Scripts\python.exe"
Check-Command "Python" { & $venvPython --version }
Check-Command "Python imports" {
    & $venvPython -c "import cv2, numpy, onnxruntime, flask, fastapi, yaml, jsonschema, PIL; assert hasattr(cv2, 'aruco'); print('cv2-aruco/numpy/onnxruntime/flask/fastapi OK')"
}
# 2026-09-20：這兩個探針原本把原生命令直接接進 `| Select-Object -First 1`。
# `Select-Object -First` 一拿到第一筆就中止上游管線，Windows PowerShell 5.1 會把
# 還在跑的原生程序砍掉，$LASTEXITCODE 變成 -1，於是 Check-Command 判成 FAIL——
# 但程式本身是好的（同一次驗證裡 Android Gradle 用同一個 JDK 建置成功）。
# 一個會對正常環境喊失敗的 doctor，下場是大家學會忽略它。
#
# PS 5.1.26100 實測（先把 $LASTEXITCODE 塞成 99 以分辨「沒跑」與「跑了」）：
#   java -version                            -> 0    不重導向、不接管線
#   $x = java -version 2>$null               -> 0    2>&1 本身無辜
#   $p = java -version 2>&1                  -> 0    重導向也無辜
#   @(java -version 2>&1)[0]                 -> 0    先收完再取，正確
#   java -version 2>&1 | Select -First 1     -> -1   ← 病灶
#   java --version    | Select -First 1      -> -1   ← 改用 stdout 也救不了
# 結論：兇手是「原生命令接 Select-Object -First」，與 stderr 無關。修法是先用
# @() 收完讓程序正常結束，之後的 Where/Select 作用在普通陣列上，沒有程序可砍。
Check-Command ".NET 8 SDK" { @(dotnet --list-sdks) | Where-Object { $_ -match '^8\.' } | Select-Object -First 1 }
Check-Command "Java" { @(java -version 2>&1)[0] }
Check-Command "ADB" {
    $adbExe = Join-Path $env:ANDROID_SDK_ROOT "platform-tools\adb.exe"
    if (-not (Test-Path -LiteralPath $adbExe)) { throw "missing $adbExe" }
    # Codex 的 Windows sandbox 會禁止直接啟動使用者 Android SDK 下的 adb，
    # 但 Gradle 仍可使用同一 SDK。Doctor 在此驗證可執行檔存在；實機連線另以
    # `adb devices -l`（一般終端或已核准的受管命令）驗收。
    "platform-tools present: $adbExe"
}
Check-Command "Node 20" { node --version }
Check-Command "Git LFS" { git lfs version }

if ($failures.Count -gt 0) {
    Write-Host "Environment incomplete: $($failures -join ', ')" -ForegroundColor Red
    exit 1
}
Write-Host "Windows test environment is complete." -ForegroundColor Green
