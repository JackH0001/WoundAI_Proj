# iOS 實作完成計劃 — 對齊 Android 前台與後台

**日期**：2026-08-08（下午，量測存檔／時間軸入口／90 天清除三項接線完成後）
**基準**：Android `main`（11,468 行 Kotlin，UI 與 pipeline 層本輪逐檔重讀）↔ iOS build 內 19 檔＋今日接線
**目標**：iOS 在**版本功能上完全對齊** Android 前台，並連上同一套後台 API
**前次盤點**：`docs/ios_android_parity.md`（功能層級）。本文件深到元件與 API 欄位層級，並給出分階段實作計劃。

---

## 一、後台 API 對齊現況（先講結論：client 幾乎是完整的，缺的是兩個欄位與呼叫端）

`iOS/Core/BackendClient.swift` 與 Android `pipeline/BackendClient.kt` 逐 API 比對：

| API | Android | iOS client | iOS 有無 UI 呼叫端 |
|---|---|---|---|
| `POST /auth/login` + `LoginIdentity`（roleZh・perms・`can()`） | ✅ | ✅ | 量測前登入有；**identity 從未存進 `AppState`**，RBAC 顯示與閘門全缺 |
| `GET /health`（degraded 原因） | ✅ | ✅ | ✅ 啟動預熱＋設定頁 |
| `POST /api/v1/classify` | ✅ 含 `wound_polygons` **多輪廓** | ⚠ **只解析單輪廓 `wound_polygon`** | ✅ |
| `POST /api/v1/annotation` | ✅ 含 `gt_polygons`（>1 才送）、`area_cm2` | ⚠ **缺這兩個欄位**（其餘含 tissue_mask_png／tissue_raster／tissue_edited 皆齊） | ❌ 按鈕 body 是空的 |
| `POST /api/v1/auth/onetime_code` → `/console#c=…` | ✅ 設定頁「開啟主控台」 | ✅ `oneTimeCode()`＋`consoleURL()` 都寫好了 | ❌ 零呼叫端 |
| `GET /api/v1/flywheel/stats`（by_source 拆分） | ✅ | ✅ | ✅ 設定頁 |
| `POST /api/v1/consent/withdraw`／`restore` | ✅ 含重試佇列 | ✅ `ConsentSync` 含重試佇列 | ✅ 已接（本區兩端最對齊） |
| 冷啟動追蹤 `BackendWarmup.sinceLastOkMs()` | ✅ >15 分鐘顯示喚醒提示 | ❌ 只 ping、不記時間戳 | — |

**多輪廓是唯一的資料鏈級落差，而且會擴散。** Android 在 2026-08-07 修過一整輪「多處傷口只送最大那個」的 bug（第二個傷口被標成背景＝教模型「那不是傷口」），修法貫穿 classify 解析 → `PolygonJson` 多輪廓 JSON → 修邊畫布 `initialPolygons` → `submitAnnotation(allPolygons:)` → `gtPolygon` 欄位格式。iOS 目前整條鏈都是單輪廓——**修邊畫布動工之前必須先把這條鏈補齊**，否則畫布蓋在錯的地基上，之後要回頭改四層。

---

## 二、畫面層逐一比對（元件層級）

### 2.1 主選單
Android 5 入口：個案／**最近就診**／快速量測／設定／**使用說明書**（OutlinedButton 較輕樣式＋說明文字）。
iOS 現況 4 入口：個案／快速量測／**快速量測紀錄**（Android 沒有此入口在主選單，功能對應快速量測頁內的按鈕）／設定。
**缺**：最近就診、使用說明書。

### 2.2 個案管理（`CaseSelectScreen.kt` 360 行 ↔ `CaseSelectView`）

| 元件／行為 | Android | iOS |
|---|---|---|
| 病患清單（遮蔽 MRN）＋再點一次取消選取 | ✅ | ⚠ 有清單，無取消選取（用「換一位」按鈕，可接受） |
| 選定病患後**收起**新增表單 | ✅ | ✅ |
| MRN 查重導回既有病患 | ✅ | ✅ |
| 同意簽署／撤回／雲端還原／重試佇列 | ✅ | ✅ |
| 撤回未同步的**常駐橫幅＋「立即重試」按鈕** | ✅ | ⚠ 有橫幅，無重試按鈕 |
| **傷口部位／類型輸入欄**（薦骨・壓瘡…） | ✅ 兩個 TextField | ❌ **寫死「未指定」**——WD-code 是隨機碼，沒有部位／類型，兩個個案在清單上無法分辨 |
| 個案**摘要列**：上次面積・較首次 ±%・N 天前・共 N 次（±10% 變色） | ✅ `caseSummary` | ❌（`summary(caseId:)` 已寫好，零呼叫端） |
| **兩段式選取**：點傷口＝選取 → 展開「開始量測」「查看時間軸（N 次）」 | ✅ | ⚠ 今日接了並列的量測＋時間軸圖示鈕，等價但無摘要 |
| 返回鍵逐層退出（簽名→傷口→病患→離開） | ✅ BackHandler | ⚠ iOS 用 sheet＋toolbar 返回，行為近似 |

### 2.3 量測流程（`MeasureValidationEntry` 450 行＋`SamplePickerScreen` 135 行＋`MeasureScreen` 90 行 ↔ `MeasureFlowView`）

已對齊：classify 呼叫、結果卡、`AnalysisPreview` 校正框疊圖、滲液選擇器、六條靜默失敗提示（imageId 缺／imageReused／無貼紙／gray-world／phantomHint／低信心）、快速量測不綁個案的隔離。

**缺口（依嚴重度排）**：

1. **影像入口只有相簿**。Android 三入口：**拍照**（臨床主路徑——事後補件有壓縮／裁切破壞尺度的風險）／相簿／**檔案**（Files app，相簿看不到的圖）。iOS 缺相機與 fileImporter。
2. **樣本來源選擇器**（快速量測模式）：Android 強制先選 sample／phantom 才能載圖（phantom → `seg=color` 走色彩分割），附三行說明文字與未選警告。iOS 只有一個 phantom Toggle——語意對不上：Android 的「範例」與「模擬圖」是兩種不同的樣本來源，會寫進 `source` 欄位並決定計不計入收案統計。
3. **RBAC 身分列與閘門**：Android 常駐顯示「👤 醫師（王…）」；`!can("gt.verify")` 顯示「修邊不產生醫師已驗證」說明；`!can("record.save")` 直接擋存檔並給角色名。iOS `identity` 從未賦值，整組不存在。
4. **醫師修邊確認狀態列**：Android 結果出現後常駐顯示「✓ 已完成修邊確認」或「尚未確認：可存病歷、不得送標註（按取消不算）」。iOS 無。
5. **重要狀態彈窗**：Android 把 ✅／ℹ️／⚠️ 開頭的送出狀態彈 AlertDialog 強制確認（實測：只印一行字會被錯過）。iOS 無。
6. **phantom 路由檢查**：選了模擬圖但 route ≠ `phantom_color` → 幾乎必然是後端沒重啟，明講。iOS 無。
7. **冷啟動提示**：>15 分鐘沒成功呼叫過後端 → 「第一次量測約需 10–30 秒喚醒」。iOS 無。
8. **修邊放棄確認**：Android 修邊中按返回彈「放棄修邊？」。iOS 無（依賴修邊畫布）。
9. **滲液鎖定不一致（今日引入的偏差）**：Android 未輸入滲液前**「修邊」與「存入時間軸」都鎖定**（`needExudate`）；我今日做的 iOS 存檔鈕不要求滲液。需改回對齊——Android 的道理是 PUSH 總分缺滲液就算不出來，存進去的 notes 會永遠是「滲液 未填」。
10. 面積未校正時的**原因說明**：Android 給三條常見原因（貼紙太遠／反光摺痕／角度過斜）＋佔畫面 1/6 的具體指引。iOS 只有一句「請確認貼紙完整入鏡」。

### 2.4 修邊畫布（`WoundEditScreen.kt` 921 行 ↔ **無**）——最大缺口

完整元件清單（實作驗收就照這張表）：

- **工具列**：邊界＋／邊界－／移動／組織🖌（FilterChip 四選一）＋「按住看原圖」peek 鈕（按住隱藏組織填色、**邊界線保留**、期間禁止作畫；版面高度恆定不跳動）
- **組織色盤**：肉芽綠／腐肉琥珀／壞死深紫／上皮粉／**其他灰**，α 一致 115；按鈕本身就是該疊色；非組織工具時停用但**不消失**（版面恆定）
- **筆刷**：螢幕半徑 10–90 slider；游標圈顏色隨工具（擦除紅／組織色／畫綠）
- **手勢**：單指依工具塗抹或平移（同一迴圈分流）；**雙指縮放＋平移**（進入雙指時還原第一指誤畫的那一筆）；補間塗抹（段落 > 筆刷直徑 3 倍時只點終點——掉影格防過標）
- **柵格**：ROI 初始＝AI 遮罩外框＋60% 邊距，工作解析度 ≤1024；**筆刷近框緣自動擴張**（每次 grow=max(64, 半幅)，內容整格搬移無損，上限 2200²，`cm2PerPx` 不變）；擴張時只有 B_PAINT 重算 `auto` 底稿
- **auto 底稿**：`TissueSeg.classify`——512 網格上 HSV 逐像素分類（**wbGains 色卡增益優先**，退灰世界只用遮罩內像素）＋ 3×3 多數決去椒鹽 → 最近鄰放大；新畫進遮罩的像素帶 auto 分類，不是 defaultClass
- **undo/redo**：24 MB 位元組預算（深度 2–8 隨遮罩大小），擴張後尺寸不合的快照丟棄；redo 在新筆畫時清空
- **即時數值列**：面積（像素數 × cm2PerPx）・PUSH partial・五類組織 %・尺度來源標示（ArUco✓／AI後備⚠）
- **提示**：其他 >10% 提示醫師確認；`tissue == auto` 全等時提示「未修正組織，不進組織訓練集」；遮罩空時提示從零塗抹
- **縮放列**：－／＋／ROI／全圖／↺／↩
- **完成修邊**：`traceAllBoundaries`（flood-fill 標記 → **所有**連通元件 Moore 邊界、minPx=64 濾雜點、由大到小）→ RDP(1.5) → 回傳 `(poly, allPolys, iou, areaOut, liveFrac, EditRaster)`；**IoU ≥ 0.9999 時面積回傳原值不重算**（往返有損，未改就不動數字）；`tissueEditedPx`＝醫師實際改過的像素數（決定遮罩能否進訓練集）
- **EditRaster** 帶 `canvasW/H`＋`wbGains` 一起持久化（iOS `EditRasterCodec` 已完整實作且過測試，直接可用）

iOS 已備好的地基：`EditRaster`＋`EditRasterCodec`（含測試）、`TissueCode`／`TissueSeg.grid`／`rasterizePolygon`、`TissueClassifierV2`（`classifyPixel`／`wbGains`／`applyGain` 已過 49 項金標）、`WoundPipeline.push`。**缺**：`TissueSeg.classify`（網格分類＋多數決，約 70 行）、畫布 UI 本體、`traceAllBoundaries`／`traceOne`／`rdp`／`scanlineFill`（約 120 行純演算法）。

### 2.5 時間軸（`WoundTimelineScreen.kt` 373 行 ↔ 今日的 `TimelineView`）

| 元件 | Android | iOS 今日版 |
|---|---|---|
| 累計 N 次（無條件顯示） | ✅ | ❌ |
| 面積趨勢摘要「首次→最新 ±%」（±10% 變色） | ✅ | ❌ |
| **面積折線圖**（Y 軸 0／半／滿刻度＋水平參考線＋首末日期軌） | ✅ | ❌ |
| **組織堆疊柱狀圖**（好→壞排序：上皮→肉芽→其他→腐肉→壞死；**無資料畫灰底「無資料」，不畫 0%**） | ✅ | ❌ |
| 單筆卡片：面積＋**較上次 ±%**＋notes＋woundType＋「已送訓練／可補送標註」徽章 | ✅ | ⚠ 有面積／notes／醫師✓／影像已清除 |
| **縮圖**（IO 執行緒解密降採樣；三態：載入中「…」／**解不開「無法解密」**（金鑰換新）／「已清除」） | ✅ | ❌（我只做了「已清除」圖示，未區分無法解密） |
| **點卡片 → 複核畫面**（回頭修邊／補送，不必重測） | ✅ | ❌（依賴 2.6） |
| 快速量測（unassignedOnly）模式：隱藏趨勢圖＋說明文字 | ✅ | ✅ 合併進同一 View，做法一致 |

### 2.6 量測複核（`MeasurementReviewScreen.kt` 464 行 ↔ **無**）

元件清單：載回加密影像＋`gtPolygon` 多輪廓＋柵格 resume（組織分區零損失、面積不漂移）；**座標空間檢核**（影像尺寸 ≠ imageW/H → 停用修邊並明講後果）；「重新修邊」→ 完成後更新 DB（doctorVerified=true、`annotationSubmitted=false` 重置、**correctionIou 刻意不覆寫**、notes 追加修訂行含組織%、柵格成對更新、先寫 DB 再刪舊柵格檔、記憶體 resumeRaster 同步換新）；「補送訓練標註」→ **送出前確認彈窗**逐欄列出將離開手機的資料（代碼／imageId／N 處傷口共 N 點／面積／滲液／來源／**組織遮罩三態**：不送／送but未修正不進訓練集／含醫師修正 N 像素）＋「個資不在其中」聲明＋clinical 來源警告；送出當下**重讀**訓練同意；成功後 `markAnnotationSubmitted`（本機標記失敗不可假裝成功）；結果彈 AlertDialog。

### 2.7 最近就診（`RecentActivityScreen.kt` 112 行 ↔ **無**）

`repo.recentRows(10)`：未結案個案依最後量測時間排序，每列＝病患提示（遮蔽 MRN）＋部位・類型＋wdCode＋摘要（上次面積・較首次 ±%・N 天前・共 N 次）＋**≥14 天未追蹤警示**＋「查看時間軸」；`canMeasure`（照護同意）閘門在此**同樣生效**（第二量測入口不得成為繞道）。iOS `CaseRepository` 缺 `recentRows` 對應查詢。

### 2.8 設定（`BackendSettingsScreen.kt` 293 行 ↔ `BackendSettingsView` 55 行）

iOS 已有：位址／帳密／儲存加密／health＋login 分開回報／flywheelStats／同意重試。
**缺**：App 版本號顯示（排錯第一問）；密碼欄「留空＝沿用已存」語意（iOS 現在留空重測會用舊密碼但 UI 沒說）；**身分區塊**（label＋identity）；**主控台按鈕**（依 `can()` 分流：flywheel.stats→「我的送件」、user.manage/audit.read→管理主控台、gcp.console→GCP；`oneTimeCode()` 放 URL fragment 開 Safari）；清除已存帳密；「臨床欄才是 n=20 分母」說明；誠實邊界聲明。

### 2.9 使用手冊（`ManualScreen.kt` 62 行 ↔ **無**）

`assets/manual.html`（32.5 KB，含角色分頁 JS）→ iOS 用 `WKWebView` 載 bundle 同一份檔案。**手冊內容兩端共用同一份 HTML**——iOS 按鈕文字對齊 Android 後才不會與手冊矛盾。`project.yml` 需把 manual.html 加進 resources。

### 2.10 相簿匯出（`GalleryExport.kt` 89 行 ↔ **無**）

快速量測存檔時把原圖另存共用相簿；**fail-closed：`source ∈ {sample, phantom}` 才寫**，clinical 一律拒絕（共用相簿不受加密／保存期限／撤回約束）。iOS 用 `PHPhotoLibrary`；`NSPhotoLibraryAddUsageDescription` 已在 Info.plist（先前預留，正是為這個）。

### 2.11 不補的（兩端皆 dead code 或已除役）

端上 ONNX／OpenCV 分割（模型不在 repo，架構決定走後端）；`AdvancedCameraModule`＋`ImageQualityAssessor`（Android 也無入口）；`CaptureScreen`／`HistoryScreen`／舊 `SettingsScreen`（已標 Deprecated）；`AnnotationActivity`／`DoctorAuthActivity`（密碼已清為 `REMOVED_USE_BACKEND_AUTH`，無入口）；模擬器 loopback 警告（iOS 無此問題）。端上／後端切換開關也不做——iOS 刻意後端單軌，`WoundSegmenter` 無實作者。

---

## 三、實作計劃

原則：**資料鏈先於畫布，畫布先於一切依賴它的畫面**。每項附 Android 參考與驗收條件。

### 階段 1｜多輪廓資料鏈＋修邊畫布（解鎖 doctorVerified 與整個飛輪）

> **2026-08-08 傍晚：1.1–1.8 程式碼全部完成**（+4 檔：`PolygonJson.swift`／`MaskTrace.swift`／
> `WoundEditView.swift`／`EditPipelineTests.swift`；另修改 BackendClient・TissueCodes・Entities・
> MeasureFlowView）。靜態稽核 0 重複宣告／0 括號失衡；調色盤與 Android `T_COLORS` 逐值比對一致；
> `verify_logic.py` 49/49。另兩處與計劃的偏差：① 線上欄位名是 `gt_polygons` 非 `all_polygons`
> （依 Android 實際 wire format 實作）② `analyze()` 比照 Android 改成 ≤2048 縮圖後上傳並以縮圖
> 為畫布／存檔影像（座標空間一致性前提，原本送原圖是潛在的 3.8× 面積 bug 溫床）。
>
> **同日模擬器驗證（iPhone 17／iOS 26.4 模擬器，一次編譯通過）**：
> - 單元測試 **41/41 全過**（既有 26＋新增 15：PolygonJson 6／MaskTrace 5／TissueSeg.classify 3／
>   多輪廓解析 2 併入其他套件計數），`Test Suite 'All tests' passed`，0 failures。
> - App 冒煙：啟動路徑（同意重試＋90 天清除＋預熱）無崩潰；建病患（PHI 加密＋遮蔽 MRN）→
>   簽同意（簽名拖曳未被 ScrollView 吃掉、①＋簽名的雙重 disabled 閘門正確）→ 建個案（WD-code）→
>   個案列雙按鈕（量測＋時間軸圖示）→ 時間軸個案標題＋空狀態 → 返回鏈 → 快速量測選照片 →
>   縮圖管線執行 → **無憑證時 fail-closed**「尚未設定後端帳號密碼」。
> - **留待實機＋後端**：classify→修邊畫布互動（筆刷／雙指縮放／擴張／即時數值）→ 存檔 →
>   送訓練標註 E2E（需後端帳密，不在模擬器輸入）。
>
> **同日稍晚：模擬器 ↔ 正式 Cloud Run E2E 全數通過**（帳密由 Jack 現場輸入；佇列基線 28 筆）：
> ① health＋「連得上/帳密錯」分流 ② classify 雙軌路由都走到（花圖 `cloud_escalated(AU)` 難例上雲、
> 瀑布圖 `student`）③ 無貼紙→未校正＋gray-world ×0.78 警告 ④ 畫布互動：邊界＋筆刷即時 11%→29%、
> undo 復原、組織🖌 改標壞死 0%→60%、完成修邊回寫結果卡、**只改組織時 IoU=1.00 且面積不動**
> ⑤ 滲液未填鎖修邊/存檔 ⑥ 送標註 ✅ enqueued（WD-C1E04E2E）＋強制確認彈窗 ⑦ 原樣重送 → ℹ️
> duplicate_skipped（影像/輪廓/遮罩三同去重）⑧ 臨床模式同影像 → ⚠ image_reused 警告 ⑨ **同意死局
> 回歸（Android 8/7 test001）**：簽②→restore 無殘留、撤回→「已撤回，雲端同步完成」、重簽②→
> restore 解除封鎖，全程無重試佇列 ⑩ 佇列對帳 29 筆/範例 3（+1）、**臨床 12 不變（零統計污染）**
> ⑪ 存入時間軸→快速量測紀錄列＋「更新這一筆」去重 UPDATE 就位。
> 過程中修掉一個真 bug：**iOS Release 預設後端網址指向舊部署**（wound-ai-867037876992 →
> 對齊 woundai-backend-421209514056）。環境備忘（非 App 問題）：模擬器打字會被 Mac 注音 IME
> 攔走（改剪貼簿）；通知中心面板會隱形攔截點擊；iOS 26 模擬器的 Toggle 要有按壓時長的合成事件。
> **留實機**：ArUco 真實貼紙照（模擬器無樣本）、雙指縮放（滑鼠無多點觸控）、phantom 模擬圖、效能。

| # | 項目 | 新增／修改 | Android 參考 | 驗收 |
|---|---|---|---|---|
| 1.1 | `PolygonJson`：多輪廓 `[[[x,y],…],…]` ↔ JSON，`parse` 兼容舊單輪廓格式、`largest()` | 新 `Core/PolygonJson.swift`（~80 行）；`Measurement.polygonPoints` 改走它 | `PolygonJson.kt` 全檔 | 單元測試：單↔多格式互解、退化輸入回空 |
| 1.2 | `ClassifyResult.woundPolygons` 解析 `wound_polygons`，空時退回單輪廓 | `BackendClient.parseClassify` | `BackendClient.kt:222-237` | 對後端實測一張多傷口圖 |
| 1.3 | `submitAnnotation` 補 `allPolygons`＋`areaCm2` 參數（body：`gt_polygons` >1 才送／`area_cm2`） | `BackendClient.swift:522` | `BackendClient.kt:279-348` | 後端收到欄位（佇列 jsonl 目視） |
| 1.4 | `TissueSeg.classify`：網格 HSV 分類（wbGains 優先／灰世界退路只算遮罩內）＋3×3 多數決 | `Core/TissueCodes.swift` 加 ~70 行（CGImage 取像素） | `TissueSeg.kt:52-101` | 新增金標測試：與 Android 同輸入同輸出 |
| 1.5 | **`WoundEditView`**：2.4 節全部元件 | 新 `UI/WoundEditView.swift`（預估 900–1100 行 Swift）＋純演算法放 `Core/MaskTrace.swift`（scanlineFill／traceAllBoundaries／traceOne／rdp，~150 行） | `WoundEditScreen.kt` 全檔 | ① phantom 圖：AI 空遮罩→從零塗抹→面積≈標稱值 ② 修組織後 liveFrac 即時變 ③ 雙指縮放不留點 ④ 擴張後繼續塗無錯位 ⑤ undo/redo ⑥ 完成後 ResultCard 數字更新 |
| 1.6 | 修邊接入 `MeasureFlowView`：「醫師確認・修邊」鈕（滲液未填鎖定）、`markDoctorVerified` 接 `applyEditedPolygon` 等價邏輯、修邊中返回彈「放棄修邊？」、狀態列（✓已確認／尚未確認說明） | `MeasureFlowView.swift` | `MeasureValidationEntry.kt:146-186, 254-270` | 取消不算確認；完成後 saveToTimeline 存的是修邊後面積／組織／柵格 |
| 1.7 | **送出訓練標註接線**：填上空按鈕——條件顯示（來源・個案・滲液・修邊✓・存檔✓摘要）、送出當下重讀 `trainEffective`、呼叫 `submitAnnotation(allPolygons:areaCm2:…)`、結果彈窗（✅／ℹ️ 去重／⚠️ 守門）、成功後 `markAnnotationSubmitted` | `MeasureFlowView.swift` | `MeasureValidationEntry.kt:387-450`＋`MeasureViewModel.kt:290-353` | 後端佇列收到；撤回同意後送出被擋且訊息正確 |
| 1.8 | 滲液鎖定對齊：未填滲液時「修邊」「存入時間軸」都 disabled＋原因 | 今日程式碼小改 | `MeasureScreen.kt:66-82` | — |

### 階段 2｜時間軸完整版＋複核（病歷閉環）

> **2026-08-09 上午（實機測試回饋後）**：2.1＋2.2 ✅（新 `TimelineCharts.swift`：面積折線＋
> 組織堆疊＋縮圖三態；`TimelineView` 補累計/±%/徽章）、2.4 部位/類型輸入＋摘要列 ✅、
> 3.1 相機＋檔案入口 ✅（新 `CameraCaptureView.swift`；拍照/相簿/檔案三按鈕）。
> `CURRENT_PROJECT_VERSION` 19→21 對齊 Android。**待做**：2.3 複核畫面（時間軸點紀錄
> 進入重修/補送——目前時間軸列尚不可點）、2.5 最近就診、撤回橫幅重試鈕。

| # | 項目 | 落點 | Android 參考 |
|---|---|---|---|
| 2.1 | ✅ 面積折線圖＋組織堆疊圖（SwiftUI Canvas；無資料畫灰底不畫 0%；好→壞堆疊序） | 新 `UI/TimelineCharts.swift` | `WoundTimelineScreen.kt:234-373` |
| 2.2 | ✅ 時間軸卡片補齊：累計 N 次、趨勢摘要 ±%、較上次 ±%（±10% 變色）、已送訓練／可補送徽章、縮圖三態（背景解密、**區分無法解密與已清除**） | `TimelineView` 重構 | `WoundTimelineScreen.kt:73-228` |
| 2.3 | ✅ **複核畫面**（2026-08-09：`ReviewView.swift`＋時間軸列可點；含座標空間檢核、逐欄揭露、correctionIou 不覆寫、送出當下重讀同意）。同日並完成 **WoundAI3D 深度擷取 MVP**（`DepthCapture.swift`：LiDAR RGB-D＋內參加密 sidecar、`depth_source` 三值真值，見 `docs/woundai3d_depth_capture_plan.md`） | 新 `UI/ReviewView.swift`；`Screen.review`；`AppState.reviewRecord` | `MeasurementReviewScreen.kt` 全檔 |
| 2.4 | ✅ 個案畫面：部位／類型輸入欄（取代寫死「未指定」）、摘要列（`summary(caseId:)` 接線）；撤回橫幅「立即重試」待做 | `CaseAndConsentViews.swift` | `CaseSelectScreen.kt:273-354` |
| 2.5 | 最近就診：`CaseRepository.recentRows(limit:)`（JOIN 未結案個案＋最後量測時間＋病患提示遮蔽 MRN＋consentCare）＋畫面（≥14 天警示、同意閘門） | `CaseRepository.swift`＋新 `UI/RecentActivityView.swift`；`Screen` 加 `.recent` | `RecentActivityScreen.kt`＋repo `recentRows` |

### 階段 3｜相機・設定對齊・手冊・匯出（營運完備）

| # | 項目 | 落點 | 備註 |
|---|---|---|---|
| 3.1 | 相機拍攝（AVFoundation 全解析、修正方向；隔離區 `CameraCaptureView` 逐檔評估後救回或重寫）＋「檔案」入口（fileImporter） | 新 `UI/CameraCaptureView.swift` | `NSCameraUsageDescription` 已備 |
| 3.2 | 快速量測來源選擇器：sample／phantom 二選一（取代 Toggle）＋說明文字＋phantom 路由檢查提示 | `MeasureFlowView.swift` | `source` 欄位語意對齊後端統計 |
| 3.3 | RBAC 全域接線：登入後 `app.identity = client.identity`；量測頁身分列＋`record.save`／`gt.verify` 閘門與說明 | `MeasureFlowView`＋`WoundAIApp` | UI 閘門給理由，真正拒絕在後端 |
| 3.4 | 設定頁對齊：版本號、留空沿用語意、身分區塊、主控台按鈕（oneTimeCode→fragment→Safari `openURL`）、清除帳密、n=20 說明 | `BackendSettingsView` | `MARKETING_VERSION`/`CURRENT_PROJECT_VERSION` 從 Bundle 讀 |
| 3.5 | 冷啟動提示：`BackendWarmup`（UserDefaults 記最後成功時刻，>15 分鐘顯示提示） | 新 `Core/BackendWarmup.swift`（~30 行） | `MeasureValidationEntry.kt:246-252` |
| 3.6 | 使用手冊：WKWebView 載 bundle `manual.html`（JS on、檔案/網路 off）；project.yml 加 resource | 新 `UI/ManualView.swift`；`Screen` 加 `.manual` | 與 Android 共用同一份 HTML |
| 3.7 | 相簿匯出 fail-closed（PHPhotoLibrary；只放行 sample/phantom；成功顯示相對路徑） | 新 `Core/GalleryExport.swift` | `GalleryExport.kt` |
| 3.8 | 重要狀態彈窗機制（✅／ℹ️／⚠️ 強制確認、與頁面訊息分離）——做成共用 modifier 供 1.7／2.3 使用 | `UI/` 共用 | `MeasureValidationEntry.kt:129-145` |

### 每階段收尾（固定動作）

`xcodegen generate`（有新檔）→ Xcode 實機建置 → 對照 2.x 節元件表逐項點過 → `swift_audit.py`＋`verify_logic.py` → 契約測試補該階段的新規則（多輪廓 JSON、TissueSeg 金標、liveFrac 含 other）→ 更新 `docs/ios_android_parity.md` 狀態欄。

### 規模與依賴

- 階段 1 ≈ 1,400–1,700 行（畫布佔七成）。1.1–1.4 可先行合併，是純資料層，風險低；1.5 是唯一的高複雜度項。
- 階段 2 ≈ 900 行。2.3 依賴 1.5（重新修邊）與 1.1（多輪廓解析）。
- 階段 3 ≈ 600 行，項目彼此獨立、可穿插。
- 全程不動 DB schema（v6 已對齊 Android Room v6），不動後端。

### 風險備忘

1. **畫布效能**：Android 用可變 Bitmap 局部 `setPixel`；SwiftUI `Canvas` 每幀重繪整圖。對策：overlay 維持 `CGContext`＋`vImage` 或直接持有 `CGMutableImage` 等價物，筆畫只改髒矩形，`version` 驅動重繪——與 Android 同構。若 SwiftUI 手勢分流（單指畫／雙指縮放同迴圈）做不乾淨，退 `UIViewRepresentable`＋`UIGestureRecognizer`，其餘不變。
2. **peek 按住手勢**：SwiftUI 無 `InteractionSource`，用 `LongPressGesture(minimumDuration: 0)`＋`onPressingChanged` 等價實作；期間禁畫的旗標要進畫布手勢迴圈（Android 註解裡那個「另一手誤畫進 GT」的坑）。
3. **縮圖記憶體**：清單捲動時背景解密，沿用 `loadThumbnail(maxPixel:)` 已有的降採樣；不要快取全圖（Android 註解：捲幾列就 OOM）。
4. **manual.html 的角色分頁 JS** 在 WKWebView 的行為要實測一次（`allowFileAccess` 等 Android 設定在 WKWebView 對應 `WKWebViewConfiguration` 預設即可）。

---

## 四、對齊後仍刻意保留的平台差異

快速量測紀錄入口同時放主選單（iOS 今日加的）與快速量測頁內（對齊 Android 時保留兩處皆可達）；iOS 無實體返回鍵，Android 的 BackHandler 層層退出改以 toolbar 返回＋sheet dismiss 表達；端上分割雙軌開關不實作（單軌後端，`WoundAnalyzer` 保留為日後端上路徑的接口）。
