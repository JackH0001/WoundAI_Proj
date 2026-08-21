# 上架計畫：醫療版 TestFlight ＋ 民眾版 App Store（2026-08-20 起草）

兩條路線分開走，**不互相等待**：

| | 醫療版 WoundMeasurementApp | 民眾版 WoundLite |
|---|---|---|
| 目標 | TestFlight 外部測試（臨床收案） | App Store 公開上架 |
| 審查 | Beta App Review（較寬鬆、1–2 天） | App Review（嚴格） |
| 阻擋 | App Icon、示範帳號 | 全部 4 項（見下） |
| 可動工時間 | **本週** | App Attest 完成後 |

---

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
      角色 physician、綁測試組織，資料與臨床區隔。⚠ 密碼由你設定並填入
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
