# WoundAI TestFlight 發布紀錄（2026-09-21）

## 發布識別

| 項目 | 值 |
| --- | --- |
| App | WoundAI（醫療／研究測試版） |
| App Store Connect App ID | 6814448065 |
| Bundle ID | com.woundai.app |
| 版本／建置 | 1.0（22） |
| 原始碼 commit | bdcabd4d6405655df5f72af38e4f840851cf5db1 |
| 開發者 Team | LY2F24ZM68 |
| 發布簽章 | Cloud Managed Apple Distribution |
| 最低版本／架構 | iOS 17.0／arm64 |
| 本地 Archive 工具 | Xcode 27.0，build 27A266a，iphoneos27.0 SDK |

[PR #12](https://github.com/JackH0001/WoundAI_Proj/pull/12) 保留 bundle ID 與 CI 修復的獨立提交。
[App Store Connect](https://appstoreconnect.apple.com/apps/6814448065/distribution) 與 [TestFlight](https://appstoreconnect.apple.com/teams/2d91bd06-cd1f-40ea-bcdf-16a6d6ed529f/apps/6814448065/testflight/ios) 需要團隊帳號登入。

## 已完成的驗證

- 本地與 [GitHub CI](https://github.com/JackH0001/WoundAI_Proj/actions/runs/35587966818)：兩版 Release 建置成功；XCTest 47 通過、0 失敗、0 跳過。
- owner_guard、差異格式、完整性與機密掃描通過。CI 已攔截管線失敗及零測試假綠燈。
- 醫療版簽章 Archive、App Store Connect IPA 匯出與上傳均成功。
- 2026-09-21 18:51（台北）完成上傳；Apple 處理完成，build 22 顯示「準備提交」。
- 匯出檔包含 App Icon、PrivacyInfo.xcprivacy、使用手冊與發布 provisioning profile；Archive 包含 dSYM。
- 發布簽章 entitlement：`get-task-allow=false`、`beta-reports-active=true`。
- 匯出選項 `testFlightInternalTestingOnly=false`，允許後續外部 TestFlight 分發。
- [醫療版隱私政策](https://jackh0001.github.io/WoundAI_Proj/woundai.html) HTTP 200。
- Release 後端 `/api/health` HTTP 200、healthy；當時服務仍為 commit `505ff2e` / revision `woundai-backend-00039-xdk`。健康檢查不等同於審查帳號登入與影像分析端到端測試通過。

本地匯出 IPA SHA-256：`4e2459349ae57ee9b85b1c8ccc5b21504f5639c5ccb846c1c5e27f645f15a6f7`。Apple 上傳流程會重新打包／簽署；此值識別本地保存的 IPA，不宣稱是 Apple 最終分發檔案的雜湊。

## 群組及外部審查狀態

- 已建立 `WoundAI Internal QA`，採手動選擇建置；build 22 已加入，狀態為「準備測試」。
- 已建立 `WoundAI 外部測試`。
- 尚未邀請測試人員，也未開啟公開邀請連結。
- Beta 描述、隱私政策 URL、審查操作步驟已準備；審查聯絡人、電話、回饋 email 與 demo 帳號有效登入資訊補齊後，才能儲存完整資料及送出 Beta Review。
- demo 密碼由專案負責人直接填入 App Store Connect；不得寫入 repo、PR、命令列參數或此文件。
- 本次狀態為「上傳並處理完成，待審查資料」；尚未通過 Beta Review，亦不代表外部測試者已可安裝。

## 可重現的封存／匯出方式

請將 DerivedData、Archive 和匯出 IPA 放在一般本地目錄，例如 `~/Developer/WoundAI_Release/`。本次 Documents 同步目錄會對 .app 加入 Finder/File Provider 中繼資料，導致 CodeSign 報 `resource fork, Finder information, or similar detritus not allowed`；改用 Developer 本地目錄後簽章成功。不要以停用簽章繞過此錯誤。

```bash
cd ~/Developer/WoundAI_Proj/iOS
xcodegen generate
xcodebuild archive \
  -project WoundMeasurementApp.xcodeproj \
  -scheme WoundMeasurementApp \
  -configuration Release \
  -destination 'generic/platform=iOS' \
  -archivePath ~/Developer/WoundAI_Release/Next/WoundAI.xcarchive \
  -derivedDataPath ~/Developer/WoundAI_Release/Next/DerivedData \
  -allowProvisioningUpdates
```

App Store Connect 匯出設定使用 `method=app-store-connect`、`signingStyle=automatic`、`teamID=LY2F24ZM68`、`testFlightInternalTestingOnly=false`、`manageAppVersionAndBuildNumber=false`。先以 `destination=export` 保存並核對 IPA，再以 `destination=upload` 上傳；均使用 `xcodebuild -exportArchive` 與 `-allowProvisioningUpdates`。

build 22 已使用。下一次有程式變更要上傳時，須確認 App Store Connect 中已用過的版號，再遞增 `project.yml` 的 build number、提交並重新建置；不要重複上傳不同內容的 build 22。

## 外測操作範圍與待驗證事項

先以範例、模擬圖或測試物件驗證設定登入、快速量測、校正與輪廓複核、測試個案／同意書、儲存與時間軸、斷線及恢復流程。相機及 LiDAR 需相容實機；模擬器的 47 項測試不能取代這些實機驗證。

待辦：確認 demo 帳號登入及雲端分析、服務版本與發布相容性、實機權限／記憶體／加密保存、Swift 6 actor isolation 警告，以及跨 bundle identity 的既有資料延續。TestFlight Beta Review 與正式 App Store 上架審查分開追蹤。
