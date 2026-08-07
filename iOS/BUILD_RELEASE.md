# WoundAI iOS — 建置與發布

> **前提：需要一台 macOS 機器。** iOS 的編譯與簽章只能在 macOS 上做（Xcode 工具鏈是
> 封閉的，沒有 Windows 或 Linux 版）。這份程式碼是在 Linux 沙箱裡寫的，**尚未經過任何
> 編譯器驗證**——已做的驗證見文末「已驗證 / 未驗證」。

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

- Bundle ID：`com.woundmeasurement.app`
- 部署目標：iOS 17.0
- `CURRENT_PROJECT_VERSION`：目前 **19**，與 Android `version.properties` 的
  `versionCode` 對齊。

⚠ **每次發布都必須遞增 build number。** 兩個平台的版號要能互相對照——排錯時的第一個
問題永遠是「你裝的是哪一版」，而兩邊版號各走各的話這題答不出來。

## 3. 建置

```bash
# 先跑測試（模擬器）
xcodebuild test \
  -project WoundMeasurementApp.xcodeproj \
  -scheme WoundMeasurementApp \
  -destination 'platform=iOS Simulator,name=iPhone 15'

# 封存與匯出
xcodebuild archive \
  -project WoundMeasurementApp.xcodeproj \
  -scheme WoundMeasurementApp \
  -archivePath build/WoundAI.xcarchive
```

**第一次編譯必然會有錯。** 這份程式碼沒有經過編譯器，型別推導、SwiftUI 的
`some View` 推斷、`actor` 的隔離規則都可能有需要調整的地方。把錯誤貼回來即可逐項修。

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

### 尚未實作

- **修邊畫面（筆刷塗抹）**。因此 `doctor_verified` 永遠是 `false`，
  「送出訓練標註」按鈕維持停用並顯示原因。這是**刻意的 fail-closed**：
  一筆從未被人看過的 AI 輸出不該以「醫師已驗證」進入訓練集。
  這是 iOS 進入飛輪收案的最後一塊。
- 端上分割（`UNet256.mlmodel` 不在 repo 裡）與端上 ArUco
  （`opencv2.xcframework` 不在 repo 裡）。兩者與 Android 現況相同：一律走後端。
- 時間軸趨勢圖、我的送件清單、App 內使用說明書。

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
| **Swift 編譯 / 型別檢查** | — | ❌ **未做**（沙箱無 macOS） |
| **SwiftUI 執行期行為** | — | ❌ 未做 |
| **與真實後端的端到端** | — | ❌ 未做 |

驗證腳本：`tools/verify_logic.py`、`tools/swift_audit.py`（皆可離線重跑）。

這兩支腳本抓到過三個真實缺陷：`WoundAnalyzer` 引用已刪除的型別、文件註解裡的
`/*` 讓整個 `BackendClient.swift` 673 行被 Swift 的巢狀註解吞掉、以及舊 target 那
58 組命名衝突。它們不能取代編譯器，但它們抓的正是編譯器要到 Mac 上才會告訴你的事。
