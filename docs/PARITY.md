# Android ↔ iOS 平台一致性

`tools/parity_check.py` 會讀這份文件。**兩端的差異只有兩種合法狀態：已消除，或在這裡宣告。**
沒宣告的差異一律報錯。

執行：`python tools/parity_check.py`（離開碼 0＝一致）

## 為什麼要有這份文件

漂移不會有徵兆。這個專案實際發生過四次：

| # | 發生了什麼 | 症狀 |
|---|---|---|
| 1 | iOS 的預設後端網址抄自已廢棄的 service | 「登入失敗」，而訊息叫人去查帳密 |
| 2 | `Pipeline/` 8 個檔沒被加進 Xcode 專案 | 躺三個月，git diff 看起來一切正常 |
| 3 | Android 加了 `wound_polygons`，iOS 沒接 | 第二個傷口沒有初始輪廓 → 在訓練集裡被標成背景 |
| 4 | Android `versionCode` 前進，iOS 沒跟 | 「你裝的是哪一版」答不出來 |

第 3 類最危險：**沒有錯誤、沒有警告，只有訓練資料悄悄變錯**。

## 已宣告的差異

改動這一節時，請一併寫下**為什麼**與**打算什麼時候消除**。
**過期的宣告比沒有宣告更危險**——它會讓一個真正的新落差被當成「早就允許了」而放行。
所以補完一項就要從這裡刪掉，不要留著當歷史紀錄（歷史請看 git log）。

```yaml
# 格式：<檢查項>: <識別子>  # 理由
allow:
  annotation_field_missing_android: [depth_map_png, depth_conf_png, depth_format, depth_scale, camera_intrinsics]
  endpoint_missing_android: [/api/v1/depth, /api/v1/lite/segment, /api/v1/lite/annotation]
```

### iOS-only 端點（2026-08-21 宣告）

三個 iOS 有、Android 無的端點。**兩者的「無」是不同性質的**，分開說：

| 端點 | Android 為何沒有 | 消除計畫 |
|---|---|---|
| `/api/v1/depth` | 原始 LiDAR 深度補傳；Android 機型無 LiDAR | 隨上方深度欄位的 ARCore 決策一起定 |
| `/api/v1/lite/segment` | WoundLite 是 **iOS 專屬產品**，Android 沒有民眾版 | **不打算消除**——這是產品邊界，不是實作落後 |
| `/api/v1/lite/annotation` | 同上 | 同上 |

⚠ 後兩者屬「永久宣告」。過期的宣告比沒有宣告危險，但**產品邊界造成的差異
若不宣告，每次跑 parity 都會紅**，紅燈久了就沒人看——那才是真正的風險。
若日後推出 Android 民眾版，這兩行要立刻刪掉。

補充（來自 `codex/windows-preclinical-readiness-20260821` 的宣告，合併時刻意保留）：

- `/api/v1/depth`：在 ARCore 決策定案之前，**不可只為了讓 parity 變綠而加一個沒有資料來源的空呼叫**。
- `/api/v1/lite/*`：若日後建立 Android Lite variant，要做的不只是刪掉這兩行宣告，還必須接上
  完整的身分、同意與資料隔離流程——**不可把 Lite 權限混入醫療端 App**。

### annotation 深度欄位（iOS 有、Android 無，2026-08-18 宣告）

iOS 隨標註上傳 LiDAR 深度圖（`depth_map_png` png16_mm）＋置信度＋內參＋尺度，
供 WoundAI3D 精度研究與訓練（wire format：`docs/depth_capture_contract.md`；
②訓練同意閘門；後端 `/api/v1/annotation` 已驗證與儲存）。

Android 硬體無 LiDAR，可走 ARCore Depth（`depth_source: arcore_depth`），但精度與
LiDAR 差一級。**消除計畫**：iOS phantom 誤差表已完成（2026-08-18：正拍投影 ±5%、
斜拍校正 ÷cosθ ±3%），供 Windows 端評估 ARCore 路線是否值得做；決策後
要嘛 Android 補上（宣告刪除），要嘛在此改註「Android 不做，永久宣告」。

### ⚠ 更正（2026-08-19）：`quality` 的方向先前寫反了

原本宣告「`quality`：iOS 有、Android 無」。**實際是 iOS 讀了但從來沒送出。**

證據：主控台送件清單裡每一筆的品質欄都是 `—`，含 2026-08-09 那四筆 iOS 紀錄。
讀碼確認 `BackendClient.swift:617` 有 `obj["quality"] = v`，但兩個呼叫端
（`MeasureFlowView.swift:500`、`ReviewView.swift:271`）都沒有傳。

`parity_check` 報不出來——它檢查「送出程式碼裡有沒有出現這個欄位名」，
而那行確實在。缺的是呼叫端。**這是「宣告在、值不在」的第三次**
（前兩次：`resume = null`、`initialPolygons = emptyList()`）。

已交辦 iOS 端補呼叫端，見 `docs/handoff_2026-08-19_windows_to_mac.md` 第 1 項。

### 已消除：`quality`（Android，2026-08-10）

iOS 讀取 classify 的 `quality`（對焦、過曝、marker 大小與傾斜）並隨標註送回後端，
Android 先前不讀。後果是後端 `/api/v1/dataset/manifest` 的品質門檻
（`min_focus` / `max_clipped` / `max_skew` / `min_marker_frac`）**只篩得掉 iOS 收的樣本**——
門檻對缺欄位的紀錄一律放行（舊紀錄本來就沒有，擋掉會把早期樣本整批丟掉），
於是 Android 收的模糊、過曝、角度過斜樣本會照樣進訓練集，而報表上看不出來。

Android 已於 `ClassifyResult.quality` 讀取、`submitAnnotation(quality=)` 回送。
端上**不挑鍵、整包收**：後端加了新指標而端上硬編一份清單去挑，那個指標就永遠不會落盤，
且沒有任何地方會報錯。

### 已消除：`/api/health` 降級警示（2026-08-10）

Android 已在 `BackendClient.health()`、`BackendWarmup.degradedBanner()` 補上，
顯示於個案管理頁常駐橫幅與連線測試結果。

⚠ 健康度**在登入之前問**（`/api/health` 免認證）。放在登入成功之後的話，
帳密還沒設對時就永遠看不到降級——而這兩件事的處置完全不同：
一個叫人改帳密，一個叫人找管理者。

### 兩端共有的限制：補送路徑不帶 `quality`

`quality` 只在**當次量測**送得出去。從時間軸補送標註時，那些指標已經不在手上——
兩端的 measurement 紀錄都只存 `quality: String`（值是 `"backend"`，是資料來源標記，
不是品質指標），沒有欄位放這幾個數字。

後果：補送的那一筆在訓練集匯出時**篩不掉**。這是兩端一致的行為，所以不是 parity 落差，
但仍是缺陷。要消除得加一個 DB 欄位（`qualityJson`）與 migration，兩端同步做。

## 不在自動檢查範圍內的（需人工確認）

契約一致 ≠ 功能等價。以下要靠實機測試與這張表。

⚠ **這張表自 2026-08-18 起有 49 天沒有更新**，期間兩端各自又改了十幾輪 UI。
2026-10-06 的盤點一次補進九列新差異，並更正兩列已經過期的狀態——而這兩列的
更正是查程式碼查出來的，不是有人記得。這正是本文件開頭那句「過期的宣告比沒有
宣告危險」的實例：表上寫 ❌ 的東西其實早就做完了，於是沒有人再去看它，
真正的新差異也就一起躺在表外。

**維護規則**：任一端動到使用者看得到的行為（畫面、手勢、提示、顏色、等待時間），
就在這一輪把對應的列改掉或新增一列。不確定另一端有沒有，就去 grep——
查證的成本遠低於一列過期宣告的成本。

| 功能 | Android | iOS | 備註 |
|---|---|---|---|
| 個案結案 | ✅ | ✅ | iOS：個案清單長按 →「結案」（2026-08-21 核對，此列先前過時） |
| 個案刪除（限空個案） | ✅ | ✅ | **2026-10-06 更正**：iOS 早已實作（`CaseAndConsentViews.swift` 個案列長按 →「刪除（僅限沒有任何量測的個案）」），此列先前過時 |
| 時間軸單筆刪除 | ✅ | ✅ | **2026-10-06 更正**：Android `65fa275`（標題即「補齊與 iOS 的最後一個缺口」）、iOS `CaseAndConsentViews.swift`「刪除這筆量測（含加密影像與深度）」；此列先前過時 |
| ArUco 貼紙誤認排除 | ✅ | ✅ | iOS：≥70% 落在貼紙外擴 15% 框內的輪廓自動剔除＋提示 |
| 多處傷口：讀 `wound_polygons` | ✅ | ✅ | |
| 多處傷口：修邊畫面初始輪廓 | ✅ | ✅ | iOS 走 `RasterOps.buildRaster(polygons:)` |
| 多處傷口：送 `gt_polygons` | ✅ | ✅ | iOS 走 `RasterOps.rasterToPolygons` |
| 修邊：組織按鈕呈現實際疊色 | ✅ | ✅ | iOS 實色 chips（2026-08 實機修正） |
| 修邊：工具切換不讓影像跳動 | ✅ | ✅ | iOS tissueRow 恆定高度 |
| 修邊：擴張不造成過標 | ✅ | ✅ | iOS `expandIfNeeded`＋段落過長不補間；實機多輪驗證 |
| App 內使用說明書 | ✅ | ✅ | 共用 `manual.html` 單一來源 |
| 我的送件清單 | ✅ | ❌ | iOS 為單筆複核（ReviewView），清單視圖待補 |
| 時間軸趨勢圖 | ✅ | ✅ | iOS `TimelineCharts` 面積/PUSH 雙圖＋±% |
| 免貼紙深度量測（LiDAR） | ❌ | ✅ | iOS 專屬硬體；對照卡＋品質閘；Android 見上方 ARCore 宣告 |
| 端上分割 / 端上 ArUco | ❌ | ❌ | 兩端一律走後端；iOS 民眾版留有地端模型槽（`WoundLite/Models`） |

**以下十二列為 2026-10-06 盤點新增**（比對基準：Android `5c28b29`、iOS `origin/codex/ios-release-assets-20261001` d7e3617）

| 功能 | Android | iOS | 備註 |
|---|---|---|---|
| 拍攝：對焦狀態引導 | ❌ | ✅ | iOS `25cbff7`：中央 AF 框（黃＝對焦中／綠＝完成）＋ POI 置中連續對焦；訊號源刻意雙重（`isAdjustingFocus` 在 LiDAR 相機不觸發 → 併用 `lensPosition` 變動＋0.45s 靜止）。Android `CameraCaptureScreen.kt` **未設定任何對焦**，亦無狀態顯示。<br>⚠ 嚴重度最高：近距特寫沒鎖焦就是糊的，而糊照的 ArUco 角點誤差會**直接變成面積誤差**，後端照回 200。<br>移植路徑：CameraX 無公開的 AF 狀態觀察點，需 `Camera2Interop.Extender.setSessionCaptureCallback` 讀 `CaptureResult.CONTROL_AF_STATE` |
| 拍攝：取景提示含「約佔畫面 1/6」與後果 | ❌ | ✅ | Android 只寫「與傷口同框、盡量平貼」。1/6 規則在 Android 其實存在——但寫在 ArUco **失敗之後**的錯誤訊息裡，而提示要在拍之前才有用 |
| 拍攝：提示文字有半透明底 | ❌ | ✅ | Android 是裸 `Text` 疊在即時預覽上，亮膚色／反光構圖下讀不到；iOS 用 `black.opacity(0.45)` 圓角底 |
| 拍攝：圓形快門鍵 | ❌ | ✅ | iOS 72pt 圓形（相機的標準操作語彙）；Android 是文字按鈕「拍攝」。純外觀，列入備查 |
| 修邊：組織按鈕色塊實心可辨 | ❌ | ✅ | `fda4c8a`（Android）先做，iOS 抄過去後**實機回報「α115 再乘 0.4 淡到像空心按鈕」**，改為不透明底色（選中 0.95／可用 0.65／停用 0.35）。Android 仍是 `Color(T_COLORS[c])` 半透明 α≈0.45、停用 α≈0.18——正是 iOS 回報的那個值。<br>這一列是**反向 backport**：差異的來源是 iOS 多做了一輪實機修正 |
| 修邊：底稿 `seedAuto` 不卡主執行緒 | ❌ | ✅ | iOS `25cbff7` 註明**實測 >10 秒白凍**，改背景 `runSeed` ＋鎖畫布提示。Android `WoundEditScreen.kt` 的 `seedAuto` 仍在 `remember { }`（＝組合／主執行緒）。<br>諷刺的是 Android 自己在 `stamp()` 裡已寫明 seedAuto「在 2200² 上是近千萬次寫入，跑在筆畫進行中的主執行緒上」並為此限定只在 B_PAINT 呼叫——**但開畫面那一次仍是同步的** |
| 結果頁：組織圖層 | ✅ | ❌ | Android 參照圖三層（輪廓／組織／校正框）且等比全寬；iOS `AnalysisPreview` 固定 `frame(height: 300)`、僅輪廓＋校正框兩個開關。**此列 Android 領先** |
| 結果頁：組織層取自醫師修邊柵格 | ✅ | ❌ | Android 2026-10-06 起優先用 `EditRaster`（修改前無條件重跑色彩啟發式 → 百分比更新了圖沒更新）。iOS 無組織層，不適用 |
| 結果頁：雙指縮放平移檢視細節 | ❌ | ❌ | **兩端都待補。** 兩端的 Magnification／雙指都只存在於修邊畫面。<br>⚠ 要做請**同一輪兩端一起做**——分開做就是下一列 PARITY 落差 |
| 結果頁：圖層開關的狀態編碼 | ❌ | ✅ | iOS 實心＝顯示／空心＝隱藏，底色對應圖上框色，再加眼睛圖示（雙重編碼，強光下仍可辨）。Android 為 Material `FilterChip`，只有深淺差 |
| 校正框顏色 | 綠 `0xFF39FF6A` | 黃 | ⚠ 同一個安全檢查，兩個 App 教使用者看**兩種顏色**（文案也各自寫「綠框」／「黃框」）。同院同時發兩個平台時，換機就會認錯。<br>**2026-10-06 決議：統一為綠**——黃在黃色膠帶、反光與膚色上辨識度較差，且綠已在 Android 實機驗過。**iOS 待改**（顏色＋「黃框」文案；`iOS/` 歸 mac 機器所有，由 Mac 端執行） |
| 匯出量測結果疊圖到共用相簿 | ✅ | ❌ | Android 2026-10-06 特殊內測版：原圖＋**醫師修邊的**組織層＋輪廓＋ArUco 框＋下方結果標註帶（面積／PUSH／組織％／尺度／驗證狀態／免責）。<br>綁 `ALLOW_CLINICAL_GALLERY_EXPORT`，只有 internalTest 建置會寫入臨床相簿。<br>iOS 是否要跟上，取決於特殊合作部門的流程需求——**尚未決定，不是實作落後** |


## 加新功能時的流程

1. 一端做完後，**先跑 `parity_check.py`**——契約層面的漏接會當場被抓到
2. 另一端跟上；跟不上的話，在上面的表裡寫一列，註明原因與預計時程
3. 兩端版號一起遞增（`Android/version.properties` 與 `iOS/project.yml`）

   ⚠ **2026-10-06：這條規則目前是失效狀態，而且 `parity_check.py` 正在因此 exit 1。**
   Android `versionCode=25`、iOS `CURRENT_PROJECT_VERSION=22`。中間的 23／24／25 全是
   Android 單邊的平台工作（16 KB 分頁對齊、啟動圖示修復、RBAC），iOS 沒有對應改動可跟。

   值得注意的是：守門程式**一直都有在叫**，紅的不是沒被偵測到，是沒有人處理。
   一個長期紅著的守門等同於沒有守門——下一個真正的落差出現時，它會被當成同一則舊噪音。

   規則要二選一，不要讓它繼續對不上：
   - **(a)** 改成「**功能版**對齊、平台修訂各自跳號」——兩端只在功能同步時對齊整數位
   - **(b)** 維持嚴格對齊，iOS 下次直接跳到 25

   在做出選擇之前，**不要再引用這條規則當作已生效的檢查**——一條沒人遵守的規則
   比沒有規則更糟，它會讓後來的人以為版號已經被誰看過了。

4. 使用者看得到的行為改動（畫面、手勢、提示、顏色、等待時間），在同一輪更新上面
   那張人工表。`parity_check.py` 是**契約層**（端點／JSON 鍵／網址／版號），
   UI 落差它本來就抓不到，也不打算抓——所以這一層沒有任何東西會自己叫。
