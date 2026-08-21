[CmdletBinding()]
param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
$toolsRoot = Join-Path $RepoRoot ".tools\windows"
$venvRoot = Join-Path $RepoRoot ".venv-windows"

$dotnetRoot = Join-Path $toolsRoot "dotnet"
if (Test-Path -LiteralPath (Join-Path $dotnetRoot "dotnet.exe")) {
    $env:DOTNET_ROOT = $dotnetRoot
    $env:NUGET_PACKAGES = Join-Path $toolsRoot "nuget-packages"
    $env:DOTNET_CLI_TELEMETRY_OPTOUT = "1"
    $env:DOTNET_NOLOGO = "1"
    $env:Path = "$dotnetRoot;$env:Path"
}

$nodeRoot = Get-ChildItem -LiteralPath (Join-Path $toolsRoot "node") -Directory -ErrorAction SilentlyContinue |
    Sort-Object Name -Descending | Select-Object -First 1
if ($nodeRoot) {
    $env:Path = "$($nodeRoot.FullName);$env:Path"
}

$androidHomeCandidates = @(
    $env:ANDROID_SDK_ROOT,
    $env:ANDROID_HOME,
    (Join-Path $env:LOCALAPPDATA "Android\Sdk")
) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
if ($androidHomeCandidates.Count -gt 0) {
    $env:ANDROID_SDK_ROOT = $androidHomeCandidates[0]
    $env:ANDROID_HOME = $androidHomeCandidates[0]
    $env:Path = "$(Join-Path $androidHomeCandidates[0] 'platform-tools');$env:Path"
}

$javaCandidates = @(
    $env:JAVA_HOME,
    "C:\Program Files\Android\Android Studio\jbr"
) | Where-Object { $_ -and (Test-Path -LiteralPath (Join-Path $_ "bin\java.exe")) }
if ($javaCandidates.Count -gt 0) {
    $env:JAVA_HOME = $javaCandidates[0]
    $env:Path = "$(Join-Path $javaCandidates[0] 'bin');$env:Path"
}

$venvPython = Join-Path $venvRoot "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    throw "Python test environment is missing: $venvPython. Run tools/windows/Bootstrap-WindowsTestEnv.ps1 first."
}
$env:VIRTUAL_ENV = $venvRoot
$env:Path = "$(Join-Path $venvRoot 'Scripts');$env:Path"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$testTemp = Join-Path $toolsRoot "temp"
New-Item -ItemType Directory -Force -Path $testTemp | Out-Null
$env:TEMP = $testTemp
$env:TMP = $testTemp

Write-Host "WoundAI test environment active"
Write-Host "  repo:    $RepoRoot"
Write-Host "  python:  $venvPython"
Write-Host "  dotnet:  $dotnetRoot"
Write-Host "  java:    $env:JAVA_HOME"
Write-Host "  android: $env:ANDROID_SDK_ROOT"
