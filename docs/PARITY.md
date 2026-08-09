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

```yaml
# 格式：<檢查項>: <識別子>  # 理由
allow: {}
```

**目前沒有任何宣告的不對稱。** 空的 allow 清單是好事，但它有個前提：
**過期的宣告比沒有宣告更危險**——它會讓一個真正的新落差被當成「早就允許了」而放行。
所以補完一項就要從這裡刪掉，不要留著當歷史紀錄（歷史請看 git log）。

### 已消除：`quality`（2026-08-10）

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

契約一致 ≠ 功能等價。以下要靠實機測試與這張表：

| 功能 | Android | iOS | 備註 |
|---|---|---|---|
| 個案結案 | ✅ | ⚠ repo 有方法，UI 無入口 | iOS 待補按鈕 |
| 個案刪除（限空個案） | ✅ | ❌ | iOS 待補 |
| 時間軸單筆刪除 | ❌ | ❌ | 兩端都待補（規則已在 repo 層） |
| ArUco 貼紙誤認排除 | ✅ | ❌ | iOS 待補；Android 的過濾規則見 `analyzeViaBackend` |
| 多處傷口：讀 `wound_polygons` | ✅ | ✅ | |
| 多處傷口：修邊畫面初始輪廓 | ✅ | ✅ | iOS 走 `RasterOps.buildRaster(polygons:)` |
| 多處傷口：送 `gt_polygons` | ✅ | ✅ | iOS 走 `RasterOps.rasterToPolygons` |
| 修邊：組織按鈕呈現實際疊色 | ✅ | ❓ | Android `fda4c8a` 修的三項，iOS 是從修正前版本移植 |
| 修邊：工具切換不讓影像跳動 | ✅ | ❓ | 同上，需實機確認 |
| 修邊：擴張不造成過標 | ✅ | ❓ | 同上；iOS 目前沒有柵格擴張功能 |
| App 內使用說明書 | ✅ | ❌ | |
| 我的送件清單 | ✅ | ❌ | |
| 時間軸趨勢圖 | ✅ | ❌ | |
| 端上分割 / 端上 ArUco | ❌ | ❌ | 兩端都缺模型與 framework，一律走後端 |

## 加新功能時的流程

1. 一端做完後，**先跑 `parity_check.py`**——契約層面的漏接會當場被抓到
2. 另一端跟上；跟不上的話，在上面的表裡寫一列，註明原因與預計時程
3. 兩端版號一起遞增（`Android/version.properties` 與 `iOS/project.yml`）
