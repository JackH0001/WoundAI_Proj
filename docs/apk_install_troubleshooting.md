# APK 安裝失敗排查

手機 UI 一律只顯示「**應用程式未安裝**」，看不出原因。真正的錯誤碼要用 adb 才拿得到：

```powershell
adb install -r "C:\dev\WoundAI_Proj\Android\_dist\WoundAI-v1.0-b2-20260804.apk"
```

---

## 先確認一件事：debug 與 release 是**不同的套件**

| 版本 | 套件名 |
|---|---|
| debug | `com.woundmeasurement.app.debug` |
| release | `com.woundmeasurement.app` |

`applicationIdSuffix ".debug"` 讓兩者**可以並存**。所以：

- 裝 release **不需要**先移除 debug
- 兩者不會互相覆蓋，也不會因為對方而簽章衝突
- 手機上會出現兩個圖示，資料完全獨立

如果你以為是「debug 擋住 release」，那個方向是錯的——問題在別處。

---

## 錯誤碼對照

### `INSTALL_FAILED_UPDATE_INCOMPATIBLE` / `..._SIGNATURE`

裝置上那個 `com.woundmeasurement.app` 是用**別的金鑰**簽的（多半是更早一份 release APK，
或是在 `keystore.properties` 建立之前／重新產生金鑰之後建的）。

Android 不允許換簽章更新。這**不是**可以繞過的限制——它正是防止他人冒名發佈更新
覆蓋使用者安裝的機制。唯一的路是先移除：

```powershell
adb uninstall com.woundmeasurement.app
```

> ⚠ **移除會清掉該 App 的全部資料**：病患、個案、量測時間軸、加密影像。
> 而且 **Android Keystore 的金鑰隨移除一併銷毀**——就算你另外備份了 Room 資料庫檔，
> 姓名與病歷號那些加密欄位也**永久解不開**。
>
> 這是刻意的設計（PII 不離開手機的代價），但它意味著：
> **有真實測試資料時，移除前先把要留的東西匯出**。
> 目前可留的只有已上傳到後端的去識別紀錄（`WD-` 代碼、遮罩、面積），
> 那些在雲端佇列裡，不受手機移除影響。

### `INSTALL_FAILED_VERSION_DOWNGRADE`

裝置上的版本比你要裝的新。

```powershell
adb install -r -d "<apk>"    # -d 允許降版
```

會出現這個，通常代表 `versionCode` 曾經被手動改大又改小。
版本號現在由 `Android/version.properties` 統一管理，`build_release.ps1` 每次發布自動遞增，
不要手改。

### `INSTALL_FAILED_VERIFICATION_FAILURE`

Play 保護機制擋下未知來源的 APK。

設定 → Play 商店 → 個人資料照片 → Play 保護機制 → 齒輪 → 關閉「使用 Play 保護機制掃描應用程式」，
裝完之後**開回來**。

### `INSTALL_FAILED_INVALID_APK`、或指令沒有訊息就結束

傳輸過程截斷（用通訊軟體傳大檔特別常見）。比對檔案大小後重傳，
或直接用 `adb install` 從電腦裝，不經過任何傳輸軟體。

### `INSTALL_FAILED_INSUFFICIENT_STORAGE`

字面意思。

### `INSTALL_FAILED_NO_MATCHING_ABIS`

APK 只含 `arm64-v8a` / `armeabi-v7a`（見 `build.gradle` 的 `abiFilters`）。
x86 模擬器裝不起來——那是預期的，真機不受影響。

---

## 想知道裝置上現在裝的是什麼

```powershell
adb shell dumpsys package com.woundmeasurement.app | findstr /C:"versionCode" /C:"versionName"
adb shell pm list packages | findstr woundmeasurement
```

比對本機 APK 的簽章：

```powershell
$bt = (Get-ChildItem "$env:LOCALAPPDATA\Android\Sdk\build-tools" | Sort-Object Name -Descending)[0].FullName
& "$bt\apksigner.bat" verify --print-certs "<apk 路徑>"
```

`build_release.ps1` 每次建置都會跑這段並印出憑證，指紋不一樣就是換過金鑰。

---

## 版本號怎麼管

`Android/version.properties` 是唯一真實來源：

```properties
versionCode=2
versionName=1.0
```

`build_release.ps1` **每次發布自動遞增 `versionCode`**（`-NoBump` 可跳過，
用於上一次建置失敗要重建同一版時）。產出檔名是 `WoundAI-v1.0-b2-20260804.apk`，
測試者看檔名就知道哪個新。

### version.properties 為什麼是純 ASCII

它一度含中文註解，結果**被自己的建置腳本毀掉**：

Windows PowerShell 5.1 的 `Get-Content` 預設用系統 ANSI 字碼頁（繁中是 Big5/cp950）解讀。
把一個含中文的 UTF-8 檔讀進來再寫回去，內容就毀了——而且 cp950 的**前導位元組會吃掉
後面那個 `0x0A`**，於是兩行被併成一行。`versionCode=2` 因此被黏到一行註解的尾巴，
Java 的 `Properties.load` 跳過它、`build.gradle` 靜默退回 `1`，
而**建置回報成功、APK 也產出來了**——只是版號是錯的。

現在的防線有三層：

1. `version.properties` **只放 ASCII**（中文說明搬到這份文件）
2. `build_release.ps1` 用 `Get-Content -Encoding UTF8`
3. 解析失敗、找到多行、或寫回後重讀對不上，一律 **throw**，不再靜默退回預設值

第 3 點才是重點。前一版在解析失敗時把版號變成 `0 + 1 = 1`，
產出一個版號比裝置上還舊的 APK，而使用者要到手機顯示「應用程式未安裝」才會發現。

先前 `versionCode` 硬編碼成 `1` 且從沒改過。後果不只是「數字不好看」——
Android 是用 `versionCode` 判斷「這是不是更新」的：每份 APK 都自稱第 1 版，
裝置無從分辨新舊，而排錯的第一句「你裝的是哪一版」就沒有答案。

> ⚠ **`keystore.jks` 遺失＝這個 App 再也無法更新**，所有使用者都必須移除重裝（資料全失）。
> 現在就確認它有備份到 repo 之外的安全位置。`build_release.ps1 -Setup` 產生它時已經警告過一次。
