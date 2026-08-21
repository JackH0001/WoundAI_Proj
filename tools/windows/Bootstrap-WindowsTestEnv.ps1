[CmdletBinding()]
param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
    [string]$PythonExecutable = "",
    [switch]$RefreshPythonDependencies,
    [switch]$SkipDotNet,
    [switch]$SkipNode
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
$toolsRoot = Join-Path $RepoRoot ".tools\windows"
$venvRoot = Join-Path $RepoRoot ".venv-windows"
New-Item -ItemType Directory -Force -Path $toolsRoot | Out-Null

function Resolve-Python {
    if ($PythonExecutable) {
        if (-not (Test-Path -LiteralPath $PythonExecutable)) {
            throw "PythonExecutable does not exist: $PythonExecutable"
        }
        return (Resolve-Path -LiteralPath $PythonExecutable).Path
    }
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        & $py.Source -3.11 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0) {
            return (& $py.Source -3.11 -c "import sys; print(sys.executable)").Trim()
        }
    }
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) { throw "Python 3.11-3.13 is required." }
    $version = & $python.Source -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
    if ([version]$version -lt [version]"3.11" -or [version]$version -ge [version]"3.14") {
        throw "Python 3.11-3.13 is required; found $version."
    }
    return $python.Source
}

$python = Resolve-Python
Write-Host "Using Python: $python"
if (-not (Test-Path -LiteralPath (Join-Path $venvRoot "Scripts\python.exe"))) {
    & $python -m venv $venvRoot
    if ($LASTEXITCODE -ne 0) { throw "Failed to create $venvRoot" }
}
$venvPython = Join-Path $venvRoot "Scripts\python.exe"
if ($RefreshPythonDependencies) {
    & $venvPython -m pip install --upgrade pip setuptools wheel
} else {
    & $venvPython -m pip install "pip==26.2.1" "setuptools==84.0.0" "wheel==0.48.0"
}
if ($LASTEXITCODE -ne 0) { throw "pip bootstrap failed" }
$requirementsFile = if ($RefreshPythonDependencies) {
    Join-Path $RepoRoot "requirements-windows-test.txt"
} else {
    Join-Path $RepoRoot "requirements-windows-test.lock.txt"
}
& $venvPython -m pip install -r $requirementsFile
if ($LASTEXITCODE -ne 0) { throw "Python dependency installation failed" }

if (-not $SkipDotNet) {
    $dotnetRoot = Join-Path $toolsRoot "dotnet"
    $localDotnet = Join-Path $dotnetRoot "dotnet.exe"
    $hasSdk8 = $false
    if (Test-Path -LiteralPath $localDotnet) {
        $hasSdk8 = [bool](& $localDotnet --list-sdks | Where-Object { $_ -match '^8\.' })
    }
    if (-not $hasSdk8) {
        $installer = Join-Path $toolsRoot "dotnet-install.ps1"
        Invoke-WebRequest -UseBasicParsing -Uri "https://dot.net/v1/dotnet-install.ps1" -OutFile $installer
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer -Channel 8.0 -InstallDir $dotnetRoot -NoPath
        if ($LASTEXITCODE -ne 0) { throw ".NET 8 SDK installation failed" }
    }
}

if (-not $SkipNode) {
    $nodeVersion = "20.19.5"
    $nodeBase = Join-Path $toolsRoot "node"
    $nodeDir = Join-Path $nodeBase "node-v$nodeVersion-win-x64"
    if (-not (Test-Path -LiteralPath (Join-Path $nodeDir "node.exe"))) {
        New-Item -ItemType Directory -Force -Path $nodeBase | Out-Null
        $zip = Join-Path $toolsRoot "node-v$nodeVersion-win-x64.zip"
        Invoke-WebRequest -UseBasicParsing -Uri "https://nodejs.org/dist/v$nodeVersion/node-v$nodeVersion-win-x64.zip" -OutFile $zip
        Expand-Archive -LiteralPath $zip -DestinationPath $nodeBase -Force
        Remove-Item -LiteralPath $zip -Force
    }
}

. (Join-Path $PSScriptRoot "Enter-WoundAITestEnv.ps1") -RepoRoot $RepoRoot
& (Join-Path $PSScriptRoot "Test-WindowsTestEnv.ps1") -RepoRoot $RepoRoot
