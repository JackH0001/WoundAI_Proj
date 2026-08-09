# iOS ↔ Android 功能落差盤點

**日期**：2026-08-08
**iOS 狀態**：首次編譯成功並安裝上實機（commit `6baeb23` + 預檢修正 + Info.plist 修正）
**盤點方法**：逐檔閱讀 Android 60 個 `.kt` 與 iOS 在 build 內的 19 個 `.swift`，並對每一條「有實作」的宣告反查呼叫端

---

## 結論

iOS 不是「做到一半的 Android」，而是**只做了前半段的垂直切片**。

病患／同意書／個案管理這一段是完整的，classify 呼叫也是通的。但從「醫師看到 AI 結果」之後的**整條後半段完全不存在**：不能修邊、不能存檔、看不到時間軸、送不出訓練標註。

而最關鍵的一條是——

## 一、量測結果永遠不會被存下來

> **2026-08-08 下午更新：這一節描述的問題已修復。**修法見文末〈補記〉。
> 原文保留，因為它解釋了「為什麼一個編得起來、跑得動、畫面看起來正常的 App
> 可以整整不是病歷系統」——那個失效模式沒有任何執行期徵兆，值得記著。

`CaseRepository.insertMeasurement` 存在、能編譯、schema 有 30 個欄位對齊 Android Room v6，但**在整個 build 裡沒有任何一個呼叫端**。

後果是連鎖的，不是單點的：

- `measurements` 資料表從 App 安裝到現在一直是空的
- `TimelineView` 就算能進去也必然是空白 —— 而它目前根本進不去（沒有任何一行 `app.screen = .timeline`）
- `LocalImageStore` 被 `AppState` 建立了實例，然後**從來沒被呼叫過**。傷口照片一張都沒有落地
- 分析結果只活在 RAM 裡。使用者選下一張照片的瞬間，上一次的量測就沒了

也就是說：**目前的 iOS App 是一個示範工具，不是病歷系統。**它能告訴你這張照片的面積和 PUSH 分數，然後把答案丟掉。

第二關鍵的一條：`送出訓練標註` 這顆按鈕的 body 是空的（`MeasureFlowView.swift:161` 寫的是 `Button("送出訓練標註") { }`）。就算 `doctorVerified` 有辦法變成 true，按下去也什麼都不會發生。`BackendClient.submitAnnotation`（19 個參數的那支）同樣零呼叫端。（**此條尚未修復**，它依賴修邊畫布。）

---

## 二、導覽層對照

Android 用 `MainActivity` 的 `currentScreen` 字串狀態機，**10 個可達狀態 + 5 個巢狀畫面**。
iOS 用 `Screen` enum，宣告 6 個 case，**實際可達 5 個**。

| Android 狀態 | iOS 對應 | 狀態 |
|---|---|---|
| `main` 主選單（5 顆按鈕） | `.main`（**4 顆**按鈕） | 部分（缺「最近活動」「使用手冊」）|
| `cases` 個案選擇 | `.cases` | ✅ 對齊 |
| `measure` 臨床量測 | `.measure` | 部分（見下） |
| `quick` 快速量測 | `.quick` | 部分 |
| `settings` 後端設定 | `.settings` | ✅ 對齊 |
| `timeline` 時間軸 | `.timeline` | ✅ 可達（個案列的圖示・存檔後・主選單）|
| `review` 量測複核 | — | ❌ 無 |
| `recent` 最近活動 | — | ❌ 無 |
| `quickHistory` 快速量測歷史 | `.timeline`（無 case 時）| ✅ 併入時間軸 |
| `manual` 使用手冊 | — | ❌ 無 |
| （巢狀）`ConsentSignatureScreen` | 同意書簽名 sheet | ✅ 對齊 |
| （巢狀）`SamplePickerScreen` | PhotosPicker | 部分（無相機） |
| （巢狀）`MeasureScreen` 結果卡 | `ResultCard` | ✅ 對齊 |
| （巢狀）`AnalysisPreview` 疊圖 | `AnalysisPreview` | ✅ 對齊 |
| （巢狀）`WoundEditScreen` **921 行修邊器** | — | ❌ **無** |

---

## 三、功能對照（依工作流程順序）

| 工作流程 | Android | iOS | 缺口性質 |
|---|---|---|---|
| 病患建檔（PHI 加密、MRN 雜湊去重） | ✅ | ✅ | — |
| 二階同意書（照護／訓練分離）+ 手寫簽名 | ✅ | ✅ | — |
| 同意撤回・雲端還原・重試佇列 | ✅ | ✅ | — |
| 傷口個案 + WD-code | ✅ | ✅ 但 `bodySite`／`woundType` 寫死「未指定」 | 缺輸入 UI |
| **相機拍攝** | ✅ CameraX（`CameraCaptureScreen` 但無入口） | ❌ 只有相簿選圖 | 隔離區有 `CameraCaptureView`（AVFoundation，真實實作） |
| 影像品質評估（銳利度／亮度／曝光） | 有程式碼但無入口 | ❌ | 兩邊都是 dead code |
| `POST /classify` 雲端分析 | ✅ | ✅ | — |
| 校正框目視複核疊圖 | ✅ | ✅ | — |
| 結果卡（面積・PUSH・組織・信心） | ✅ | ✅ | — |
| 靜默失敗提示（image_reused 等 6 條） | ✅ | ✅ | — |
| 滲液輸入 | ✅ | ✅ | — |
| **醫師修邊（筆刷／組織重繪／undo-redo／多連通域）** | ✅ 921 行 | ❌ | 底層 `EditRaster` codec **已完整實作且有測試**，只缺畫布 UI |
| **存入時間軸** | ✅ | ✅ **已接線** | `MeasureViewModel.saveToTimeline` |
| **影像落地（加密）** | ✅ | ✅ **已接線** | 同上，含同圖去重與孤兒檔回收 |
| **時間軸趨勢圖 + 縮圖列表** | ✅ 373 行 | ⚠ 清單可達且有資料，**但無趨勢圖、無縮圖** | 入口已補；圖表待做 |
| **量測複核・補送標註** | ✅ 464 行 | ❌ | 未實作 |
| **送出訓練標註** | ✅ | ❌ 按鈕 body 是空的 | `submitAnnotation` 已實作，零呼叫端 |
| 最近活動（近 10 筆・追蹤逾期提醒） | ✅ | ❌ | 未實作 |
| 使用手冊（WebView + 32KB HTML） | ✅ | ❌ | 未實作 |
| 相簿匯出（fail-closed，只放行 sample/phantom） | ✅ | ❌ | 未實作 |
| 快速量測歷史 | ✅ 獨立畫面 | ✅ **已接線** | 併入 `TimelineView`（無 case 時查 `unassignedMeasurements`）|
| 90 天影像清除 | ✅ | ✅ **已接線** | `RootView.task` 啟動時執行 |
| RBAC 角色顯示 | ✅ | ⚠ `AppState.identity` 從未被賦值 | 需要接線 |
| 一次性登入碼 → 網頁主控台 | ✅ | ⚠ `oneTimeCode()` 已寫，零呼叫端 | 需要接線 |
| 端上分割（ONNX / Core ML） | 程式碼有、模型缺、未接線 | 程式碼有、**無任何實作者**、模型缺 | 兩邊都不可用 |
| 端上 ArUco | 明確 stub（回 null） | 明確 stub（回 nil） | 兩邊都不可用，刻意 |

---

## 四、三種缺口要分開看

盤點下來，iOS 的缺口不是同一種東西，補起來的成本差一個數量級：

**(A) 寫了但沒接線** —— 成本最低，多半是幾十行的呼叫端
`insertMeasurement`、`LocalImageStore`、`submitAnnotation`、`purgeExpiredImages`、`oneTimeCode`、`identity` 賦值、`TimelineView` 的入口、`searchPatients`、`touchLastVisit`、`closeCase`、`summary(caseId:)`、`unassignedMeasurements`。

這一批之所以存在，是因為它們是「照著 Android 對等實作」寫出來的，但當時沒有編譯器可以跑，作者選擇先把資料層寫完整、UI 只做最小可驗證路徑。現在編得起來了，接線是純機械工作。

**(B) 真的沒寫** —— 成本最高
修邊畫布（Android 921 行，含筆刷、24MB 預算的 undo/redo 堆疊、ROI 自動擴張、多連通域邊界追蹤、即時 PUSH 重算）、量測複核畫面、最近活動、使用手冊。

**(C) 缺資產，兩端都不可用**
端上分割模型（`.mlmodel` / `.onnx` 都不在 repo）、`opencv2.xcframework`。這一類**不該現在處理**——Android 也不能用，架構上早就決定 ArUco 與分割都在後端做。

---

## 五、建議補齊順序

| 順序 | 項目 | 類型 | 狀態 | 理由 |
|---|---|---|---|---|
| 1 | 量測存檔：接 `insertMeasurement` + `LocalImageStore` | A | ✅ 已完成 | 沒有這個，App 不是病歷系統。而且它是後面每一項的前提 |
| 2 | 時間軸入口 + 主選單補按鈕 | A | ✅ 已完成 | 存了就要看得到。`TimelineView` 已經寫好了 |
| 3 | 90 天清除排程 | A | ✅ 已完成 | PHI 保存期限是法遵要求，不是功能 |
| 4 | 修邊畫布 | B | 待做 | 最大的一塊。它同時解鎖 `doctorVerified` 與訓練標註 |
| 5 | 接 `submitAnnotation` 到那顆空按鈕 | A | 待做 | 依賴 4 |
| 6 | 相機拍攝 | B | 待做 | 隔離區有可用的 AVFoundation 實作，可以救回來 |
| 7 | 量測複核・最近活動・手冊 | B | 待做 | 補完導覽對等 |

第 1–3 項合起來大約是一個工作階段的量，而且風險低（都是既有 API 的呼叫端）。第 4 項要單獨規劃。

---

## 補記：第 1–3 項的實作（2026-08-08）

改了 4 個檔、+349 行，全部是既有 API 的呼叫端，沒有新增任何演算法。

**`CaseRepository.updateMeasurement`（新增）**
iOS 原本只有 `insertMeasurement`、`markAnnotationSubmitted` 與清除用的 `UPDATE … SET imagePath = ''`，**沒有一支通用的更新**。Android 的同圖去重需要它：醫師常常存一次、補完滲液再存一次，兩次都 INSERT 的話時間軸會多出一個**面積相同、時間戳不同**的點——在癒合曲線上那看起來像「這段期間毫無進展」。它不是壞掉的資料，它是**看起來很合理的壞資料**。

**`MeasureViewModel.saveToTimeline`（新增）**
從 Android `MeasureViewModel.kt:385–519` 逐條移植不可調換的順序：

1. 影像一律從目前畫面重存，**絕不沿用舊檔**。`gtPolygon`／`imageW`／`imageH` 的座標空間綁的是「這一份影像」，沿用舊檔會讓座標對到另一張圖，而面積算出來仍是一個合理的數字（Android 實測差過 3.8 倍）。
2. **先寫資料庫、再刪舊檔**（與 `purgeExpiredImages` 同一個理由）。
3. 修邊過就以柵格為準（面積、組織比例、IoU），否則醫師以為自己的修正被記下來了。
4. `doctorVerified` **只增不減**；`timestamp` 更新時保留原值；組織比例在 insert 與 update **兩條分支都要寫**（Android 的 update 分支曾經漏掉，而影像 90 天後被清除，組織比例是唯一還原得回來的東西）。
5. 換影像時 `lastSavedId` 必須一起清空，否則下一張照片會**覆蓋掉上一張的病歷列**。

**存檔與送標註的門檻刻意不同。** 存病歷只要有結果就該存得下去；送訓練標註才需要 `image_id`＋滲液＋醫師修邊三者齊備。綁在同一顆按鈕上的話，「拿不到 image_id」的量測會連病歷都留不下來。

**快速量測的隔離**：主選單進入快速量測時清空 `chosenCase`，存檔時再以 `clinicalMode ? app.chosenCase : nil` 二次把關。快速量測畫面不顯示個案標題，殘留的個案是**看不見的**，只靠一層判斷不夠。

**驗證**：`swift_audit.py` 對 build 內 19 檔 0 重複宣告／0 括號失衡；`verify_logic.py` 49 項全過；`updateMeasurement` 的 SQL 用真 SQLite 建表驗過——31 佔位 ⇄ 31 綁定、30 欄全覆蓋、欄位順序與綁定順序逐一對齊。型別檢查仍只有 `xcodebuild` 給得起。

---

## 附註：不要用檔案數量判斷落差

iOS 磁碟上有約 108 個 `.swift`，但**只有 19 個在 build 裡**。其餘 89 個是上一代 ImageJ 架構的遺留，含 58 組頂層符號重複宣告，從來沒有編譯成功過（見 `docs/ios_legacy_quarantine.md`）。

隔離區裡確實有一些真實實作值得日後救回（`CameraCaptureView`、`AnnotationView`、`SegmentationEngineCoreML`、`PDFReportGenerator`），但也混著 5 個明確的 Mock／Simulator 模組與約 21 個含亂數模擬邏輯的檔案。逐檔判斷，不要整批搬。
