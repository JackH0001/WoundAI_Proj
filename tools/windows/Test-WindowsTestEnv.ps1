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
Check-Command ".NET 8 SDK" { dotnet --list-sdks | Where-Object { $_ -match '^8\.' } | Select-Object -First 1 }
Check-Command "Java" { java -version 2>&1 | Select-Object -First 1 }
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
