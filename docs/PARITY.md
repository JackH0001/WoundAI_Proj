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
allow:
  classify_key_missing_android: [quality]
  annotation_field_missing_android: [quality]
  endpoint_missing_android: [/api/health]
```

### `quality`（iOS 有、Android 無）

iOS 讀取 classify 的 `quality`（對焦、過曝、marker 大小與傾斜）並隨標註送回後端。
Android 目前不讀。

**後果**：後端 `/api/v1/dataset/manifest` 的品質門檻（`min_focus` / `max_clipped` /
`max_skew` / `min_marker_frac`）**只篩得掉 iOS 收的樣本**——Android 收的樣本缺這些欄位，
會被當成「沒有品質資訊」而通過或漏掉，取決於門檻實作。

**打算**：Android 補上。這是**應該消除**的差異，不是設計選擇。

### `/api/health`（iOS 有、Android 無）

iOS 啟動時打 `/api/health`，在服務降級（缺分割模型／缺色準模組）時警示使用者
「面積與組織判讀不具臨床參考價值」。Android 用 `BackendWarmup` 只做冷啟動預熱，
不檢查降級狀態。

**打算**：Android 補上降級警示。同上，應該消除。

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
