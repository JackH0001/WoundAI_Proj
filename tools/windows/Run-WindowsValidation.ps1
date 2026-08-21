[CmdletBinding()]
param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
    [string]$WoundAI3DRoot = "C:\dev\WoundAI3D",
    [switch]$Quick,
    [switch]$SkipDotNet,
    [switch]$SkipAndroid,
    [switch]$SkipWoundAI3D
)

$ErrorActionPreference = "Continue"
$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
. (Join-Path $PSScriptRoot "Enter-WoundAITestEnv.ps1") -RepoRoot $RepoRoot

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$out = Join-Path $RepoRoot "artifacts\windows-test\$stamp"
New-Item -ItemType Directory -Force -Path $out | Out-Null
$failures = [System.Collections.Generic.List[string]]::new()
$stageResults = [System.Collections.Generic.List[object]]::new()

function Run-Stage([string]$Name, [scriptblock]$Action) {
    Write-Host "`n=== $Name ===" -ForegroundColor Cyan
    $timer = [System.Diagnostics.Stopwatch]::StartNew()
    & $Action
    $timer.Stop()
    $exitCode = [int]$LASTEXITCODE
    $passed = ($exitCode -eq 0)
    $stageResults.Add([ordered]@{
        name = $Name
        passed = $passed
        exit_code = $exitCode
        duration_seconds = [Math]::Round($timer.Elapsed.TotalSeconds, 3)
    })
    if (-not $passed) {
        $failures.Add($Name)
        Write-Host "$Name FAILED (exit $exitCode)" -ForegroundColor Red
    } else {
        Write-Host "$Name PASSED" -ForegroundColor Green
    }
}

$python = Join-Path $RepoRoot ".venv-windows\Scripts\python.exe"
Run-Stage "environment doctor" { & (Join-Path $PSScriptRoot "Test-WindowsTestEnv.ps1") -RepoRoot $RepoRoot }
Run-Stage "Python engineering tests" {
    $runnerArgs = @((Join-Path $PSScriptRoot "run_python_tests.py"), "--repo", $RepoRoot, "--out", $out)
    if ($Quick) { $runnerArgs += "--quick" }
    & $python @runnerArgs
}
if (-not $Quick) {
    Run-Stage "isolated backend HTTP integration" {
        $runtime = Join-Path $out "backend-http-runtime"
        $flywheel = Join-Path $runtime "flywheel"
        New-Item -ItemType Directory -Force -Path $runtime, $flywheel | Out-Null
        $serverOut = Join-Path $out "backend-http-server.stdout.log"
        $serverErr = Join-Path $out "backend-http-server.stderr.log"
        $oldFlywheel = $env:WOUNDAI_FLYWHEEL_DIR
        $oldAdmin = $env:ADMIN_PASSWORD
        $oldSecret = $env:FLASK_SECRET_KEY
        $env:WOUNDAI_FLYWHEEL_DIR = $flywheel
        $env:ADMIN_PASSWORD = "woundai-admin"
        $env:FLASK_SECRET_KEY = "windows-test-only-secret"
        $server = $null
        try {
            $server = Start-Process -FilePath $python `
                -ArgumentList @((Join-Path $RepoRoot "Backend\Flask\app.py")) `
                -WorkingDirectory $runtime -RedirectStandardOutput $serverOut `
                -RedirectStandardError $serverErr -WindowStyle Hidden -PassThru
            $ready = $false
            foreach ($attempt in 1..60) {
                if ($server.HasExited) { break }
                try {
                    $health = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:5000/api/health" -TimeoutSec 2
                    if ($health.StatusCode -eq 200) { $ready = $true; break }
                } catch { Start-Sleep -Milliseconds 500 }
            }
            if (-not $ready) {
                Write-Host "Backend did not become healthy. See $serverErr" -ForegroundColor Red
                $global:LASTEXITCODE = 1
            } else {
                & $python (Join-Path $RepoRoot "engineering\phase2\test_backend_http.py") `
                    --url "http://127.0.0.1:5000" `
                    --img (Join-Path $RepoRoot "Windows\test_upload_image.jpg")
            }
        } finally {
            if ($server -and -not $server.HasExited) { Stop-Process -Id $server.Id -Force }
            $env:WOUNDAI_FLYWHEEL_DIR = $oldFlywheel
            $env:ADMIN_PASSWORD = $oldAdmin
            $env:FLASK_SECRET_KEY = $oldSecret
        }
    }
}
Run-Stage "cross-platform parity" { & $python (Join-Path $RepoRoot "tools\parity_check.py") }
Run-Stage "ownership guard" {
    & $python (Join-Path $RepoRoot "tools\owner_guard.py") --platform windows
}
Run-Stage "static mobile logic" {
    & $python (Join-Path $RepoRoot "tools\verify_logic.py")
}

if (-not $SkipDotNet) {
    Run-Stage ".NET restore/build/test" {
        $oldAppData = $env:APPDATA
        $isolatedAppData = Join-Path $RepoRoot ".tools\windows\appdata"
        New-Item -ItemType Directory -Force -Path (Join-Path $isolatedAppData "NuGet") | Out-Null
        $env:APPDATA = $isolatedAppData
        Push-Location (Join-Path $RepoRoot "Windows")
        try {
            & dotnet restore "WoundMeasurementSystem.sln" --configfile (Join-Path $RepoRoot "tools\windows\NuGet.Config")
            if ($LASTEXITCODE -ne 0) { return }
            & dotnet build "WoundMeasurementSystem.sln" -c Release --no-restore
            if ($LASTEXITCODE -ne 0) { return }
            & dotnet test "Tests\WoundMeasurement.Tests.csproj" -c Release --no-build --logger "trx;LogFileName=test-results.trx" --collect "XPlat Code Coverage" --results-directory (Join-Path $out "dotnet")
        } finally {
            Pop-Location
            $env:APPDATA = $oldAppData
        }
    }
}

if (-not $SkipAndroid) {
    Run-Stage "Android JVM unit tests" {
        Push-Location (Join-Path $RepoRoot "Android")
        try { & .\gradlew.bat --no-daemon testDebugUnitTest } finally { Pop-Location }
    }
}

if (-not $SkipWoundAI3D -and (Test-Path -LiteralPath $WoundAI3DRoot)) {
    Run-Stage "WoundAI3D Windows-portable tests" {
        & $python (Join-Path $WoundAI3DRoot "scripts\phantom_validation\test_phantom_validation_analyzer.py")
    }
}

$result = [ordered]@{
    generated_at = (Get-Date).ToString("o")
    repo = $RepoRoot
    output = $out
    quick = [bool]$Quick
    stages = @($stageResults)
    failures = @($failures)
    passed = ($failures.Count -eq 0)
}
$result | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $out "windows-summary.json") -Encoding utf8

if ($failures.Count -gt 0) {
    Write-Host "`nFAILED stages: $($failures -join ', ')" -ForegroundColor Red
    Write-Host "Reports: $out"
    exit 1
}
Write-Host "`nAll selected Windows validation stages passed." -ForegroundColor Green
Write-Host "Reports: $out"
