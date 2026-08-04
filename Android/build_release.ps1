# 產出可分享給測試者的 APK。
#
# 用法：
#   .\build_release.ps1 -Setup          # 第一次：產生簽章金鑰（只需一次）
#   .\build_release.ps1                 # 之後：建置並輸出 APK
#
# ⚠ 本檔須以 UTF-8 with BOM 儲存（Windows PowerShell 5.1 否則以 Big5 解讀而語法全爛）。

param(
    [switch]$Setup,
    [string]$KeystoreDir = "$env:USERPROFILE\.woundai_keys"
)

$ErrorActionPreference = "Continue"
$root = $PSScriptRoot
$ksPath = Join-Path $KeystoreDir "woundai-release.jks"
$propsPath = Join-Path $root "keystore.properties"

function Say($m) { Write-Host "`n▶ $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "⚠ $m" -ForegroundColor Yellow }

# keytool 跟著 JDK 走。用 JAVA_HOME 而不是靠 PATH——PATH 上可能是別版的 Java。
$keytool = if ($env:JAVA_HOME) { Join-Path $env:JAVA_HOME "bin\keytool.exe" } else { "keytool" }

if ($Setup) {
    Say "產生發布簽章金鑰"
    if (Test-Path $ksPath) {
        Warn "金鑰已存在：$ksPath"
        Warn "**不要重新產生**。換了金鑰之後，測試者必須先移除舊版才裝得上新版"
        Warn "（Android 以簽章判斷是否為同一個 App 的更新），而移除＝所有本機病歷與加密影像一起消失。"
        exit 1
    }
    New-Item -ItemType Directory -Path $KeystoreDir -Force | Out-Null

    Write-Host "`n金鑰密碼會用來保護簽章檔。遺失就**再也無法發佈更新**——" -ForegroundColor Yellow
    Write-Host "使用者只能移除重裝，而那會清掉他們手機上的所有病歷。請存進密碼管理器。" -ForegroundColor Yellow
    $sec = Read-Host "`n設定金鑰密碼（至少 6 字元，不會顯示）" -AsSecureString
    $pw = [Runtime.InteropServices.Marshal]::PtrToStringBSTR(
            [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
    if ($pw.Length -lt 6) { throw "密碼太短（keytool 要求至少 6 字元）" }

    & $keytool -genkeypair -v -keystore $ksPath -alias woundai `
        -keyalg RSA -keysize 4096 -validity 10950 `
        -storepass $pw -keypass $pw `
        -dname "CN=WoundAI, OU=Research, O=WoundAI, L=Taipei, C=TW"
    if ($LASTEXITCODE -ne 0) { throw "keytool 產生金鑰失敗" }

    # keystore.properties 放在 Android/ 底下但**不進版控**（.gitignore 已排除）。
    # 密碼寫檔是必要之惡：Gradle 要讀得到。至少讓它離開 repo 與 shell 歷史。
    $content = @"
storeFile=$($ksPath -replace '\\','\\')
storePassword=$pw
keyAlias=woundai
keyPassword=$pw
"@
    [IO.File]::WriteAllText($propsPath, $content, (New-Object Text.UTF8Encoding $false))

    Write-Host "`n✓ 金鑰已建立：$ksPath" -ForegroundColor Green
    Write-Host "✓ 設定已寫入：$propsPath（不進版控）" -ForegroundColor Green
    Warn "請把 $ksPath 另外備份到安全的地方。它遺失＝這個 App 再也無法更新。"
    exit 0
}

if (-not (Test-Path $propsPath)) {
    throw "找不到 $propsPath。請先執行： .\build_release.ps1 -Setup"
}

Say "建置 release APK"
Push-Location $root
try {
    & .\gradlew :app:assembleRelease
    if ($LASTEXITCODE -ne 0) { throw "建置失敗（見上方 Gradle 輸出）" }
} finally { Pop-Location }

$apk = Join-Path $root "app\build\outputs\apk\release\app-release.apk"
if (-not (Test-Path $apk)) { throw "找不到產出的 APK：$apk" }

# 帶版本與日期的檔名。測試者手上同時有兩三個 apk 時，看檔名就知道哪個新——
# 全都叫 app-release.apk 的話，「你裝的是哪一版」會變成排錯時的第一道障礙。
$verName = (Select-String -Path (Join-Path $root "app\build.gradle") -Pattern 'versionName\s+"([^"]+)"').Matches[0].Groups[1].Value
$stamp = Get-Date -Format "yyyyMMdd"
$outDir = Join-Path $root "_dist"
New-Item -ItemType Directory -Path $outDir -Force | Out-Null
$dest = Join-Path $outDir "WoundAI-$verName-$stamp.apk"
Copy-Item $apk $dest -Force

$sizeMB = [math]::Round((Get-Item $dest).Length / 1MB, 1)
Write-Host "`n✓ APK：$dest（$sizeMB MB）" -ForegroundColor Green

Say "簽章驗證"
$apksigner = Get-ChildItem "$env:LOCALAPPDATA\Android\Sdk\build-tools" -Recurse -Filter "apksigner.bat" -ErrorAction SilentlyContinue |
             Sort-Object FullName -Descending | Select-Object -First 1
if ($apksigner) {
    & $apksigner.FullName verify --print-certs $dest
    if ($LASTEXITCODE -eq 0) { Write-Host "  ✓ 簽章有效" -ForegroundColor Green }
    else { Warn "簽章驗證失敗——這個 APK 裝不起來" }
} else { Warn "找不到 apksigner，略過簽章驗證" }

Write-Host @"

給測試者的說明（連同 APK 一起傳）：
  1. 手機「設定 → 安全性」允許安裝未知來源的應用程式
  2. 安裝後開啟 → 主畫面「設定」→ 填後端位址與帳號密碼（另外提供）
  3. 按「連線測試」，看到 ✅ 才能開始
  4. **目前只可使用範例圖、模擬圖或自己的傷口照**；真實病人影像須待 IRB 核准

⚠ 這是 release 版（非 debug），adb 無法讀取 App 私有資料。
   若要給的是 debug 版（app-debug.apk），請注意它可被 adb run-as 讀取私有目錄，
   僅適合範例／模擬圖驗證，不可用於真實個案。
"@ -ForegroundColor Gray
