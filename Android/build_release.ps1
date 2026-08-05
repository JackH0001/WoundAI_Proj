# 產出可分享給測試者的 APK。
#
# 用法：
#   .\build_release.ps1 -Setup          # 第一次：產生簽章金鑰（只需一次）
#   .\build_release.ps1                 # 之後：建置並輸出 APK
#
# ⚠ 本檔須以 UTF-8 with BOM 儲存（Windows PowerShell 5.1 否則以 Big5 解讀而語法全爛）。

param(
    [switch]$Setup,
    # 不遞增 versionCode（重建同一版時用，例如上一次建置失敗）。
    [switch]$NoBump,
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

# ── 版本遞增 ─────────────────────────────────────────────────────────
#
# 每次發布都必須有一個**比上一次大**的 versionCode。這不是慣例問題：
# Android 就是用它判斷「這是不是更新」，而版號沒動時裝置無從分辨新舊，
# 測試者也答不出「你裝的是哪一版」——那是排錯的第一個問題。
$verPath = Join-Path $root "version.properties"
if (-not (Test-Path $verPath)) { throw "找不到 $verPath" }

# ⚠ **`-Encoding UTF8` 不可省。**
#
# Windows PowerShell 5.1 的 Get-Content 預設用系統 ANSI 字碼頁（繁中是 Big5/cp950）解讀。
# 把一個含中文的 UTF-8 檔 Get-Content 讀進來再 WriteAllLines 寫回去，內容就毀了——
# 而且 cp950 的**前導位元組會吃掉後面那個 0x0A**，於是兩行被併成一行。
#
# 2026-08-05 就是這樣把 version.properties 毀掉的：`versionCode=2` 被黏到一行註解的尾巴，
# Java 的 Properties.load 因此跳過它、build.gradle 靜默退回 1，而**建置回報成功**。
# 現在 version.properties 已改成純 ASCII（中文說明搬到 docs/），這一行是第二道防線。
$verLines = @(Get-Content $verPath -Encoding UTF8)

$codeLine = @($verLines | Where-Object { $_ -match '^\s*versionCode\s*=' })
$nameLine = @($verLines | Where-Object { $_ -match '^\s*versionName\s*=' })
# 大聲失敗。前一版在這裡靜默把 $curCode 變成 0 → 遞增成 1 → 產出一個版號比裝置上還舊的 APK，
# 而使用者要到手機上顯示「應用程式未安裝」才會發現。
if ($codeLine.Count -ne 1) {
    throw "version.properties 裡找到 $($codeLine.Count) 行 versionCode（應為 1 行）。檔案可能損毀，請檢查：`n$verPath"
}
if ($nameLine.Count -ne 1) { throw "version.properties 裡的 versionName 不只一行或缺少。" }
$curCode = 0
if (-not [int]::TryParse((($codeLine[0] -split '=', 2)[1]).Trim(), [ref]$curCode) -or $curCode -lt 1) {
    throw "versionCode 解析失敗：'$($codeLine[0])'"
}
$verName = (($nameLine[0] -split '=', 2)[1]).Trim()
if (-not $verName) { throw "versionName 是空的。" }

if ($NoBump) {
    Say "版本 $verName ($curCode)（-NoBump，不遞增）"
} else {
    $newCode = $curCode + 1
    [IO.File]::WriteAllLines($verPath,
        ($verLines -replace '^\s*versionCode\s*=.*', "versionCode=$newCode"),
        (New-Object Text.UTF8Encoding $false))
    # 寫回後立刻重讀確認。這一步存在的理由就是上面那段慘案：
    # 「寫了但沒生效」在這裡完全沒有徵兆，直到 APK 檔名上的版號不對才看得出來。
    $back = @(Get-Content $verPath -Encoding UTF8 | Where-Object { $_ -match '^\s*versionCode\s*=' })
    if ($back.Count -ne 1 -or (($back[0] -split '=', 2)[1]).Trim() -ne "$newCode") {
        throw "版本寫回後驗證失敗（期望 versionCode=$newCode，讀到 '$($back -join '|')'）。version.properties 可能已損毀。"
    }
    $curCode = $newCode
    Say "版本遞增 → $verName ($curCode)"
}

Say "建置 release APK"
Push-Location $root
try {
    & .\gradlew :app:assembleRelease
    if ($LASTEXITCODE -ne 0) { throw "建置失敗（見上方 Gradle 輸出）" }
} finally { Pop-Location }

$apk = Join-Path $root "app\build\outputs\apk\release\app-release.apk"
if (-not (Test-Path $apk)) { throw "找不到產出的 APK：$apk" }

# 檔名帶 versionName、versionCode 與日期。測試者手上同時有兩三個 apk 時，
# 看檔名就知道哪個新——全都叫 app-release.apk 的話，
# 「你裝的是哪一版」會變成排錯時的第一道障礙。
$stamp = Get-Date -Format "yyyyMMdd"
$outDir = Join-Path $root "_dist"
New-Item -ItemType Directory -Path $outDir -Force | Out-Null
$dest = Join-Path $outDir "WoundAI-v$verName-b$curCode-$stamp.apk"
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

版本：$verName ($curCode)　套件名：com.woundmeasurement.app
（debug 版是 com.woundmeasurement.app.debug —— **不同套件名，兩者可並存**，
  所以裝 release 不需要先移除 debug，也不會互相覆蓋。）

裝不起來時的排查順序：
  手機 UI 一律只顯示「應用程式未安裝」，看不出原因。用 adb 才拿得到真正的錯誤碼：

      adb install -r "$dest"

  · INSTALL_FAILED_UPDATE_INCOMPATIBLE / SIGNATURE
        裝置上那個 com.woundmeasurement.app 是用**別的金鑰**簽的（例如更早的一份 APK）。
        Android 不允許換簽章更新——這是防止他人冒名覆蓋你的 App 的機制，不能繞過。
        只能先移除再裝：  adb uninstall com.woundmeasurement.app
        ⚠ **移除會清掉該 App 的全部資料**：個案、量測時間軸、加密影像。
          而且 Android Keystore 的金鑰隨移除一併銷毀，就算另外備份了資料庫檔，
          加密欄位（姓名、病歷號）也**永久解不開**。有真實測試資料就先匯出。
  · INSTALL_FAILED_VERSION_DOWNGRADE
        裝置上的版本比較新。用 adb install -r -d 允許降版，或改裝新版。
  · INSTALL_FAILED_VERIFICATION_FAILURE
        Play 保護機制擋下。設定 → Play 商店 → Play 保護機制 → 暫時關閉掃描，裝完再開回。
  · INSTALL_FAILED_INVALID_APK / 沒有訊息就結束
        傳輸過程截斷。比對檔案大小（$sizeMB MB）後重傳。
  · INSTALL_FAILED_INSUFFICIENT_STORAGE
        字面意思。

  想確認裝置上裝的是不是同一把金鑰簽的：
      adb shell dumpsys package com.woundmeasurement.app | findstr /C:"versionCode" /C:"signatures"

給測試者的說明（連同 APK 一起傳）：
  1. 手機「設定 → 安全性」允許安裝未知來源的應用程式
  2. 安裝後開啟 → 主畫面「設定」→ 填後端位址與帳號密碼（另外提供）
  3. 按「連線測試」，看到 ✅ 才能開始
  4. **目前只可使用範例圖、模擬圖或自己的傷口照**；真實病人影像須待 IRB 核准

⚠ 這是 release 版（非 debug），adb 無法讀取 App 私有資料。
   若要給的是 debug 版（app-debug.apk），請注意它可被 adb run-as 讀取私有目錄，
   僅適合範例／模擬圖驗證，不可用於真實個案。
"@ -ForegroundColor Gray
