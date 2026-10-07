# WoundAI iOS — 建置與發布

> **更新：2026-10-05。** 醫療版 1.0 (26) 已成功上傳，App Store Connect 外部測試群組讀回「正在測試」。
> 1.0 (27) 已上傳且內外部群組皆 IN_BETA_TESTING，包含拍攝導引與修邊手勢修復；尚未確認實機安裝。
> Lite 公開服務與研究收案配套仍未完成。

## 1. 一次性設定

```bash
brew install xcodegen        # 專案產生器
cd iOS
xcodegen generate            # 由 project.yml 產出 WoundMeasurementApp.xcodeproj
open WoundMeasurementApp.xcodeproj
```

**為什麼改用 XcodeGen**：舊的 `.xcodeproj` 是手工維護的，而 2026-06 寫的 `Pipeline/`
那 8 個檔案**一個都沒有被加進去**——它們躺在磁碟上三個月，編譯器從來沒看過，git diff
看起來一切正常。手工 pbxproj 新增檔案要同時改四個區段，漏一個就是靜默失效。改成目錄
萬用字元之後，放進資料夾就在 build 裡。

`project.yml` 是唯一真實來源；**不要**手動改產出的 `.xcodeproj`，下次 `xcodegen generate`
會覆蓋掉。

## 2. 簽章

`project.yml` 已寫入 `DEVELOPMENT_TEAM: LY2F24ZM68`（沿用舊專案的設定）。若換了 Apple
開發者帳號，改那一行即可。

- Bundle ID：`com.woundai.app`（2026-09-20 定案；Lite 為 `com.woundai.lite`）
  ⚠ Android 套件名維持 `com.woundmeasurement.app` 不動——換掉會斷開 Play 商店的既有版本鏈。
  ⚠ `PhiCrypto` 的 Keychain service 字串也維持舊值，理由見該檔註解。
- 部署目標：iOS 17.0
- `project.yml` 的醫療版預設 build 為 **27**，WoundLite 為 **33**；以該檔為準。
- Build 26 曾以命令列覆寫封存並已上傳。本輪已核對 ASC 最新版為 26，將 project.yml 與 docs/PARITY.md 更新至候選 27。

**每次上傳使用新的 build number。** Android 與 iOS 是獨立發版；差異必須在 docs/PARITY.md 指定確切版本、理由與對齊條件，不以升號冒充功能相同。

## 3. 建置

```bash
# 先跑測試（模擬器）
xcodebuild test \
  -project WoundMeasurementApp.xcodeproj \
  -scheme WoundMeasurementApp \
  -configuration Release ENABLE_TESTABILITY=YES \
  -destination 'platform=iOS Simulator,id=<本機可用 simulator UUID>'

# 封存與匯出
xcodebuild archive \
  -project WoundMeasurementApp.xcodeproj \
  -scheme WoundMeasurementApp \
  -archivePath build/WoundAI.xcarchive
```

封存後需另以 `method=app-store-connect`、`destination=export` 匯出 IPA，核對最終 Distribution 簽章後才可上傳。網站登入與 Xcode → Settings → Accounts 是不同登入狀態。若匯出回報 No Accounts／missing Xcode-Username，先恢復 Xcode 帳號，再重試既有 archive；不要把開發簽章 archive 當作可直接發布的 IPA。

## 4. 目前的功能邊界（誠實清單）

### 可用

- 後端登入（RBAC 角色與權限）、健康度檢查、服務降級警示
- `POST /api/v1/classify` 全欄位解析（五階段 + 品質指標 + 色準增益）
- **校正框目視複核**——ArUco 沒有「認錯了」這個錯誤狀態，這是唯一防線
- 病患 / 傷口個案 / 雙層知情同意 + 手寫簽名
- PII 本機加密（Keychain AES-GCM）、病歷號 HMAC 指紋查重
- 撤回同意 **與重新取得同意**，各自獨立的離線重試佇列
- PUSH 計分、組織分型（金標逐值驗證通過）
- 加密影像儲存、SQLite 病歷庫（schema 對齊 Android Room v6）

### 已實作、但仍需依角色與同意驗收

- 修邊畫面、組織筆刷、復原／重做、時間軸與圖表、紀錄重修及補送標註。是否能作醫師確認／訓練送出仍受帳號角色、同意與影像身分限制；不能因本機按過修邊就繞過伺服器閘門。
- 個案的醒目時間軸按鈕、結果／紀錄圖片雙指縮放與移動。

### 尚待配套或驗收

- 端上分割（`UNet256.mlmodel` 不在 repo 裡）與端上 ArUco
  （`opencv2.xcframework` 不在 repo 裡）。兩者與 Android 現況相同：一律走後端。
- 本次候選對實際後台的完整回歸、實機手勢驗收、App Store Connect 隱私問卷／審查資料對齊及外測啟用仍需逐項確認。

### 已隔離的舊程式碼

`_quarantine/` 底下是上一代架構。**不要直接加回 build**：靜態稽核在原本 89 檔的
target 內找出 **58 個頂層符號重複宣告**（`CloudAPIService` 宣告兩次、`ImagePicker`
宣告四次…），那份專案從來沒有編譯成功過。要救回其中某個功能，請逐一解掉命名衝突
再加進 `project.yml`。清單見 `docs/ios_legacy_quarantine.md`。

## 5. 已驗證 / 未驗證

| 項目 | 方式 | 結果 |
|---|---|---|
| PUSH 面積帶、組織子分、push_cases | 對 `push_golden.json` 逐值 | ✅ 通過 |
| 組織分型 rgb→code、OpenCV 8-bit HSV | 對 `tissue_golden.json` 逐值 | ✅ 通過 |
| SSOT 常數與 Android 一致 | 直接剖析兩邊的 generated 檔比對 | ✅ 通過 |
| 組織碼轉換方向（分類器碼 ↔ 修邊碼） | 映射表往返 | ✅ 通過 |
| 頂層符號重複宣告 | `tools/swift_audit.py` | ✅ 0 個 |
| 未終結區塊註解 | 同上 | ✅ 0 個 |
| 引用不存在的符號 | 同上 | ✅ 0 個 |
| `project.yml` 路徑與語法 | 逐路徑存在性 + YAML 解析 | ✅ 通過 |
| 醫療版 Release XCTest | 2026-10-05 Build 27 | 79/79 通過，0 失敗 |
| 醫療版 Release archive | 1.0 (26)，com.woundai.app | 成功；簽章驗證通過，隱私 manifest 與來源一致 |
| Distribution IPA 匯出／外測 | Build 26，2026-10-05 | 匯出、上傳成功；外部群組正在測試 |
| Lite Release XCTest | 2026-10-04 | 141/141 通過，0 跳過 |
| 實機／真實後台／TestFlight | 需對應實際候選版本 | 本輪未完成，不以單元測試取代 |

驗證腳本：`tools/verify_logic.py`、`tools/swift_audit.py`（皆可離線重跑）。

這兩支腳本抓到過三個真實缺陷：`WoundAnalyzer` 引用已刪除的型別、文件註解裡的
`/*` 讓整個 `BackendClient.swift` 673 行被 Swift 的巢狀註解吞掉、以及舊 target 那
58 組命名衝突。它們不能取代編譯器，但它們抓的正是編譯器要到 Mac 上才會告訴你的事。

本輪原始證據：repo 外 `woundai-competitive-review-20261003/medical-release26-final-20261004/validation.json`、`archive.log`、`export.log`、`medical-summary.json`；完整上架追蹤見 `docs/app_store_submission_plan.md`。

最新 Build 27 證據與待實機項目見 [發布驗證](../docs/medical_release27_validation_20261005.md)。
