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
        & $python (Join-Path $PSScriptRoot "run_backend_http_test.py") `
            --out (Join-Path $out "backend-http") --timeout 300
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
