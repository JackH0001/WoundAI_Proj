# 上架計畫：醫療版 TestFlight ＋ 民眾版 App Store（2026-08-20 起草）

## 目前執行基準（2026-10-02）

本節取代下方歷史清單的狀態判定。iOS PR #16 原基準為 `17e0b07`；
後端 PR #15 已合併至 main `4005f392` 並部署 demo。iOS 在
`codex/ios-release-assets-20261001` 的獨立副本實作，未變更原 Developer checkout。

| E 項目 | 本輪成果 | 尚待驗收 |
|---|---|---|
| Icon | 使用者核定兩版重製圖；醫療版十字／鏡頭／傷口、民眾版十字／鏡頭／機械手。1024 PNG 已掛各 target | 實機主畫面視覺確認 |
| PrivacyInfo ×2 | 原本已存在並進 Resources；本輪修正 Lite 的 linked、DeviceID、Health，醫療深度歸健康資料 | ASC 問卷同步與營運方確認 |
| Release | 兩版 Release 建置；檢查成品 DEBUG、圖示、ATS 與 manifest；demo URL 已驗證 | App 預設仍是正式 URL，須在設定頁明確切換 demo；iOS 雲端端到端尚待驗收 |
| App Attest | 決策：在雙端契約成立後才接入雲端請求，見下方契約 | 後端 challenge／驗證／持久化、客戶端實作、真機測試 |
| 隱私政策 | `site/privacy/` 中英草稿依當前資料流重寫 | 聯絡窗口、IRB、保存期限、雲端日誌、法規覆核；未發布 |
| jetsam（歷史 task #40） | 共用序列載圖 actor、取消檢查、解碼池釋放、禁止原圖快取；修邊保留原座標 | LiDAR 實機壓力／記憶體峰值與 jetsam log，尚未結案 |

### 關鍵修正與證據界線

- 無帳號不代表匿名：`anon_id` 串聯同一安裝的資料，Lite 上傳標為 linked，無廣告追蹤。
- 醫療雲端量測本身會傳影像；②訓練同意是研究用途閘門，不能宣稱無②即不上雲。
- 政策移除概括 WORM、全資料 AES、原始 IP 絕不留存及已具一鍵撤回等不實保證。
- Lite 同意版本改為 `2026-10-01.1`；舊版選擇需重新閱讀，未取得新版同意時不走研究上傳。
- App Attest 是本專案公開匿名 API 的安全前提，不是所有 iOS App 一律必裝的 Apple 規定。
- demo01 應使用 nurse 最小流程角色，密碼由 Jack 保管；帳號可重建不等於資料持久化。
- TestFlight／Beta Review 不代表 IRB 核准，本階段只用 synthetic／phantom 資料。

### App Attest 雙端實作契約（待後端協作覆核，尚未實作）

1. 後端提供限時一次性 challenge（隨機 nonce、用途、過期時間），以及 register／assertion 驗證端點。challenge 與 key／counter 必須跨實例持久化，消費需原子化。
2. iOS 以 DCAppAttestService 建 key、對 challenge 雜湊做 attest；後端驗 Apple 憑證鏈、nonce、App ID prefix/bundle、環境及 credential ID。正式服務不得接受測試環境證明。
3. 每個受保護請求用版本化且兩端一致的編碼綁定 challenge、HTTP method、path、實際 request body 的 SHA-256。後端驗 assertion 與單調 counter，再執行操作，拒絕重放與重排。
4. 圈選上傳與資料撤回也要綁同一已登記 key／安裝身分；不能只保護 segment，或把 anon_id 當作任何人都能指定的刪除憑證。
5. 客戶端每個 key 的 assertion／發送序列化；環境不支援、key 遺失或驗證失敗時保留離線量測，不以未驗證匿名請求降級。裝置證明不取代使用者研究同意。
6. 驗收涵蓋 challenge 過期／重用、錯 bundle、錯環境、body/path 篡改、counter 重放、重新安裝、多實例及 key 撤銷；真機成功才宣稱完成。

Apple 依據：[App privacy details](https://developer.apple.com/app-store/app-privacy-details/)、
[Establishing app integrity](https://developer.apple.com/documentation/devicecheck/establishing-your-app-s-integrity)、
[Server validation](https://developer.apple.com/documentation/devicecheck/validating-apps-that-connect-to-your-server)。

### jetsam 實機驗收程序

以合成影像建立 100 筆本機紀錄；連續快速捲動與開關詳情 50 次、拍攝／圈選／取消 20 次，
再做前背景切換。以 Instruments Allocations／VM Tracker 記錄初始、峰值、靜置後 resident memory，
確認沒有持續累積或 JetsamEvent，並核對修邊前後像素座標與量測結果一致。
需歸檔裝置型號、iOS、build SHA、測試資料量、峰值及診斷 log；模擬器單元測試不替代此驗收。

### 2026-10-01 本機驗證結果

- 醫療版與民眾版 Release Simulator build 均成功；bundle 分別 com.woundai.app / com.woundai.lite。
- Release XCTest 51/51（4 支新增影像測試）；測試獨立開 ENABLE_TESTABILITY，ad-hoc 模擬器簽章。
- 首次測試因 Release 未開 @testable、未簽章 Keychain 與 Documents Finder metadata 失敗；改以 /private/tmp 的測試產物完成，正式設定未降低。
- 成品核對：兩份 manifest 與來源一致、AppIcon 名稱與 1024 無 alpha PNG 正確、無指定 DEBUG 診斷與 localhost 預設字串、ATS 禁止任意明文。
- 修正 CaseAndConsentViews 的兩個 actor 呼叫缺 await；本輪未再出現 Swift 6 isolation 警告。
- 未宣告 parity 落差 0；Mac owner_guard 通過。這些結果不等於雲端 IAM、簽名 Archive、實機 jetsam 或 Beta Review 已通過。
- build 仍是 22，這是本機驗證候選；下一次上傳需由正式發布流程分配更高 build number。

### nurse 送審流程補驗證（2026-10-01）

原 `25b5b7f` 的五項 CI 檢查已成功，遠端 XCTest 51/51。其後準備 nurse 審查步驟時，
另發現快速量測與紀錄複核將任意修邊直接設成 doctorVerified=true。
本輪補上伺服器回傳的 gt.verify／annotation.submit 權限判斷；nurse 仍可修邊與保存，
但不會標成醫師確認，重修舊輪廓也會清除原先確認狀態。訓練／深度送件按鈕依權限停用，
送件前重新登入查身分，伺服器仍是權限最終判定端。離線本機修邊不額外等待網路。
新增 5 支權限測試（完整 XCTest 56 項），新 head 需重新取得 CI，不能沿用舊 head 綠燈。

### 2026-10-02 demo 與實機記憶體進度

- PR #15 已合併為 `4005f392a5e77159ee44a140b86ca80af8fe0cdc`；正式服務未變更。
- demo revision：`woundai-backend-demo-demo-4005f392-pw2-10020537`，100% 流量。
  demo 密碼固定引用 Secret Manager 第 2 版，未讀取或記錄密碼內容。
- Jack 回報登入成功；另以瀏覽器唯讀驗證 `default:demo01` 顯示護理師、Dashboard 載入、0 筆資料，
  store 為 `local:/app/flywheel`。這不等於 iOS 登入或反覆冷啟動驗收完成。
- iOS `d8eb0a0` 的 CI 5/5 已通過；本輪新增 `ImageMemoryStressTests` 後，
  iPhone 16 Pro Max / iOS 27.0.1 的 Release XCTest **57/57** 通過。
- 新測試：100 張加密 4032×3024 合成影像、500 次縮圖、50 次完整解碼。
  physical footprint 初始 58.3 MiB、抽樣峰值 160.6 MiB、最後一輪 112.8 MiB；
  暖機後增量 7.7 MiB（五輪約 105.1、109.5、113.3、112.5、112.8 MiB）。
  這是元件層測試，並非持續採樣峰值；未涵蓋相機、LiDAR、SwiftUI 列表或前背景操作，jetsam 不結案。
- 新測試只刪除自己建立的 UUID 影像；不清空既有病例、照片或帳號。

### iPhone 示範環境驗收（未完成）

1. 開啟設定，在「後端連線」填入
   `https://woundai-backend-demo-z4kgfkob4a-de.a.run.app`；使用 `demo01`，
   密碼由 Jack 在裝置上直接輸入，按「儲存並測試連線」。記錄登入成功／nurse 身分。
   不可把一般 Release 的正式預設 URL 當成 demo；送審說明必須包含此設定步驟。
2. 僅使用新建虛構個案、合成影像或印刷模擬圖，完成量測 → nurse 修邊 → 本機保存 → 重開紀錄。
   不提交既有真實病例，不啟用研究送件。nurse 修邊不得標為醫師確認。
3. 依上方 jetsam 程序完成畫面／相機／LiDAR 壓力測試，歸檔 Instruments 與診斷結果。
4. 完成審查聯絡電話、測試者名單、隱私政策責任人及保存期限確認，再進行外部送審。
   2026-10-02 瀏覽器所見外部群組為 0 位測試者／0 個建置版本；切換建置列表時 Apple 要求重新登入。

### 接續順序

1. 新增實機壓力測試與進度文件更新送回 PR #16；以新 head 的 CI 為準。
2. demo 已完成部署、固定 URL、IAM 與瀏覽器 nurse 登入；接續 iPhone 示範環境验收與簽署 Archive。
3. 確認隱私政策責任人及保存期限，核可後發布現有 GitHub Pages、同步 ASC 問卷。
4. 實機 jetsam 回歸及測試者名單／Apple 登入完成後，再送醫療版 Beta Review。
5. 民眾版對外雲端測試等待 App Attest、撤回流程與研究／法規前提完成。

---

以下為歷史規劃與紀錄，不代表上述項目已通過現行版本驗收。


兩條路線分開走，**不互相等待**：

| | 醫療版 WoundMeasurementApp | 民眾版 WoundLite |
|---|---|---|
| 目標 | TestFlight 外部測試（臨床收案） | App Store 公開上架 |
| 審查 | Beta App Review（較寬鬆、1–2 天） | App Review（嚴格） |
| 阻擋 | App Icon、示範帳號 | 全部 4 項（見下） |
| 可動工時間 | **本週** | App Attest 完成後 |

---

## A0. 進度（2026-08-21 更新）

| 項目 | 狀態 |
|---|---|
| App Icon ×2 | ✅ `tools/make_app_icons.py`（SSOT，可重生） |
| `PrivacyInfo.xcprivacy` ×2 | ✅ 已進兩個 target 的 Resources |
| 隱私權政策 URL | ✅ 已上線並驗證可公開存取 |
| TestFlight demo 帳號 | ✅ `demo01`（密碼由 Jack 保管，Claude 不經手） |
| Release 設定體檢 | ✅ 後端網址正確、DEBUG 診斷不外洩 |
| jetsam 記憶體修正 | ✅ 待實機回歸 |
| App Attest（僅民眾版） | ⬜ 未開始——**民眾版上架的硬阻擋** |
| 醫材法規分類諮詢 | ⬜ 未開始——**最大變數** |

**隱私權政策網址**（填進 App Store Connect）：

- 民眾版 WoundLite：`https://jackh0001.github.io/WoundAI_Proj/woundlite.html`
- 醫療版 WoundAI：`https://jackh0001.github.io/WoundAI_Proj/woundai.html`

站台原始碼在 `site/privacy/`（main 分支），發佈於 `gh-pages` 分支。
**改政策時改 `site/privacy/` 再重推 gh-pages**，不要直接編 gh-pages
——那會讓兩邊分岔，而 main 上的版本才是有版本控管的那份。

⚠ 政策內容已逐條對照實作核對（EXIF 剝除、AES-256-GCM、ThisDeviceOnly、
結案後 90 天清除、IP 加鹽雜湊），未寫程式做不到的事。
**但這是準確的技術描述，不是法律意見**——正式送審前請法務或熟悉個資法者過目。

## A. 硬阻擋（沒有這些連上傳都不行）

### A1. App Icon 完全沒有圖 🔴 兩版都擋

`Assets.xcassets/AppIcon.appiconset/` 只有 `Contents.json`，**沒有任何 PNG**。
Xcode Archive 會失敗或 App Store Connect 直接退。

需要：1024×1024 無圓角、無 alpha 的 PNG（Xcode 15+ 單張即可自動衍生）。
兩個 App **必須是不同圖示**——同圖示會讓使用者混淆，也可能被審查質疑
「兩個 App 是否重複」（Guideline 4.3 Spam）。建議：醫療版偏臨床（深藍＋
量測十字），民眾版偏親和（綠＋傷口輪廓）。

### A2. `PrivacyInfo.xcprivacy` 缺席 🔴 兩版都擋

2024-05 起 App Store 強制。**本專案一定要宣告的 Required Reason API**：

- `NSPrivacyAccessedAPICategoryUserDefaults` → 理由碼 `CA92.1`
  （AppSettings／LitePrefs 都用 UserDefaults）
- `NSPrivacyAccessedAPICategoryFileTimestamp` → `C617.1`（若有讀檔案時間）
- `NSPrivacyAccessedAPICategoryDiskSpace` → `E174.1`（若有查容量；
  醫療版 `LocalImageStore.totalBytes` 要確認是否觸發）

以及資料蒐集宣告（`NSPrivacyCollectedDataTypes`）：

| App | 蒐集項目 | 連結身分 | 追蹤 |
|---|---|---|---|
| WoundLite（同意研究時） | 照片/影片、其他診斷資料 | **否** | **否** |
| WoundLite（未同意） | 無蒐集 | — | — |
| 醫療版 | 健康與健身、照片、其他使用者內容 | 是（帳號） | 否 |

⚠ 民眾版的「否連結身分」是我們的設計優勢（anon_id 不連結個人），
要在 App Privacy 問卷據實勾選——它同時是行銷賣點。

### A3. 隱私權政策網址 🔴 兩版都擋

App Store Connect 必填。內容至少涵蓋：蒐集什麼、為何蒐集（研究）、
保存多久、如何撤回（民眾版 `DELETE lite/data/<anon_id>`、醫療版同意撤回）、
聯絡方式。需要一個可公開存取的網址（GitHub Pages 或後端 `/privacy` 靜態頁皆可）。

### A4. App Attest（僅民眾版）🔴

後端契約已白紙黑字寫著：`anon_id` 是客戶端自產字串，**擋得住誤觸、
擋不住任何有意的濫用**；正式對外開放流量前必須換成裝置證明。
匿名端點沒有這層 = 公開一個誰都能按的計費按鈕。

工作量：iOS `DCAppAttestService`（產 keyId → attest → 每次請求帶 assertion）
＋後端驗證 Apple 憑證鏈。iOS 端約 1 天，後端約 1–2 天。

---

## B. 最大風險（不是技術，是分類）🟠

**傷口面積量測是否構成醫療器材軟體（SaMD）？**

事實面：
- 台灣 TFDA 對「醫用軟體」有分類指引；**用於診斷、治療決策**的量測軟體
  通常落入醫材管理；**純紀錄、衛教、生活型態參考**通常不落入。
- Apple Guideline 1.4.1：醫療 App 若提供不準確的資料可能造成傷害，
  須有依據；宣稱診斷功能者，審查會要求法規許可證明。
- 我們目前的定位語句（App 內）：「健康參考工具，非醫療診斷。傷口惡化、
  發燒或大量滲液請就醫。」——這是**正確的框架**，但要一致貫穿到
  App Store 描述、截圖文案、隱私政策、官網。

必要動作：
1. **文案紅線**：全平台不得出現「診斷」「判讀」「醫療級」「取代就醫」
   「精準測量傷口以評估癒合」等字樣；改用「記錄」「追蹤」「參考」。
2. **法規諮詢**（建議在提交前完成）：把 App 定位、功能清單、誤差數據
   （phantom ±5%／斜拍校正 ±3%）交給熟悉 TFDA 醫材分類的顧問或
   法務確認是否需要查驗登記。我不是法規顧問，這一條必須由專業人士拍板。
3. 醫療版走 TestFlight 內／外測，**不上架**，可暫時迴避此問題
   （TestFlight 屬測試用途，但仍不得對外宣稱醫療效能）。

---

## C. 送審素材清單

### C1. 兩版共同

- [ ] App Icon 1024×1024（各一）
- [ ] `PrivacyInfo.xcprivacy`（各一，內容不同）
- [ ] 隱私權政策 URL、支援 URL
- [ ] 出口合規：`ITSAppUsesNonExemptEncryption=false` 已設。
      理由：只用 Apple 平台加密（CryptoKit AES-GCM 保護本機資料）
      與標準 HTTPS，屬豁免範圍。**把這句寫進送審備註**，被問就有答案。
- [ ] 截圖：6.9"（iPhone 16 Pro Max）＋ 6.5"，各 3–5 張。
      ⚠ 必須用 **Release build** 截圖——Debug 的 🔧 診斷行不能出現在商店頁。

### C2. 醫療版 TestFlight 專屬

- [ ] **示範帳號**（Beta App Review 需要能登入）：建一個 `demo01`
      角色 nurse、綁示範組織，只用合成／模擬資料。⚠ 密碼由你設定並填入
      App Store Connect，我不經手。
- [ ] 測試資訊：「本 App 為臨床研究用傷口量測工具，僅供受邀醫護人員測試」
- [ ] 外部測試組：臨床測試者 email 清單
- [ ] 90 天到期提醒：TestFlight build 90 天失效，收案期要排定重新上傳

### C3. 民眾版 App Store 專屬

- [ ] **審查備註必須寫**（否則極可能被誤判為「App 無法使用」）：
      ```
      This app requires a LiDAR-equipped iPhone (iPhone 12 Pro or later
      Pro/Pro Max). On other devices it shows a compatibility notice by design.
      Please test on: iPhone 15 Pro / 16 Pro / 17 Pro.
      Measurement works on any object — no wound required for testing.
      Tap 拍攝傷口 → outline any small object → area is computed from LiDAR depth.
      No account or login is required.
      ```
- [ ] App 描述含裝置需求（第一段就要講）
- [ ] 年齡分級問卷：醫療/治療資訊 → 通常 12+
- [ ] 類別：醫療（Medical）或健康與健身（Health & Fitness）。
      **建議 Health & Fitness**——Medical 類審查對法規證明的要求更高，
      而我們的定位就是健康參考工具。
- [ ] 若同意研究上傳：App Privacy 問卷據實填「照片/影片、其他診斷資料，
      不連結身分、不用於追蹤」

---

## D. 建議時程

| 週 | 醫療版 | 民眾版 |
|---|---|---|
| W1 | Icon＋隱私宣告＋demo01＋Archive → TestFlight 內部測試 | Icon＋隱私宣告＋隱私政策頁 |
| W2 | Beta App Review → 外部測試上線、臨床收案續行 | App Attest（iOS＋後端）＋法規諮詢送件 |
| W3 | 收案中；90 天到期排程 | Release 截圖＋描述＋App Privacy 問卷 |
| W4 | — | 提交審查（預留 1–2 輪退件往返） |

## E. 我這邊接下來可立即動工的

1. 產生兩份 `PrivacyInfo.xcprivacy` 並掛進 project.yml（半天）
2. Release build 設定體檢（`#if DEBUG` 診斷行確認不外洩、Release 後端網址正確）
3. App Attest 客戶端實作（等你決定要不要現在做）
4. 隱私權政策草稿（中英，依實際資料流撰寫，非樣板）
5. jetsam 記憶體問題（task #40）——**上架前必修**，閃退是最常見的退件原因

需要你決定或提供的：App Icon 設計、隱私政策上架位置、法規諮詢對象、
demo01 密碼（你設定，不告訴我）、TestFlight 測試者名單。
