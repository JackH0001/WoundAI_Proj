# Handoff 2026-08-19（Mac → Windows）：lite/segment 三連發

## 追加 ③（18:29）：404 已修，現在是**未接住的 HTML 500**

### 現象與時間線

- revision 00029（b749f49，18:13 部署）：health 顯示 `blueprint_failures: []`、
  `endpoints_registered: true` ——註冊修好了 ✅
- 18:29 App 實測：`🔧 http(status: 500, "<!doctype html>…Internal Server Error…")`
  ——**Flask 預設 HTML 500**，不是端點自己包的 JSON 500 → 例外發生在
  handler 內所有 try 範圍之外。

### Mac 端逐段稽核結果（b749f49 的 api_lite.lite_segment）

有防護：`_SEGMENT` 呼叫（含 escalate 鏈）、`_has_face`、`_store_depth_png`、
`_safe_json`。**無防護且環境相依**的只剩：

1. **`rate_check` → `_fw.read_jsonl(lite_rate_<今日>.jsonl)`**：
   走 `_store().read_lines(_key(path))`。若 GcsStore.read_lines 對
   **不存在的 blob** 拋例外而非回空——「每天第一個請求必 500」這一族。
   （請求都到不了 handler 深處，與「每次都 500」的實測相符。）
2. **本批 `store.py` 改動的副作用**：你們回報過 `_is_audit`/`AUDIT_KEYS`
   搬基底類別的修正——若 GcsStore 的 `append_line`／`put_blob` 在
   `lite/` 前綴鍵上走到新守衛的另一條路，同樣是 handler 外的爆點。

### 請求與建議

- **拉 revision 00029 在 18:29±5min 的 traceback**（一行 gcloud/console），
  直接定案——上面兩個假說一看堆疊就分曉。
- 修 bug 之外，建議 `lite_segment` 最外層加 catch-all：
  `logger.exception` ＋ JSON 500（`{"error":"internal"}`）。匿名民眾端點
  不該把 Flask HTML 洩給客戶端，而且 traceback 從此穩定進 log，
  這次「只能猜」的處境不會再出現。
- 驗收不變：診斷行 `route=cloud_escalated(AU)`、後端輪廓 ≥1。

（App 端診斷儀器這輪立功：404→500 的轉變與確切狀態碼都是畫面直讀，
零猜測。）

---

# 前兩發

## 追加（同日稍晚）：升級鏈修正後端點整支 404——註冊順序 NameError

### 現象

revision 00028（commit 4144ad9）上 `/api/v1/lite/segment` 回 **404**
（App 端 🔧 診斷：`http(status: 404, "<!doctype html>…Not Found…")`）。
健康檢查、主控台、醫療端點全部正常。

### 根因（app.py@4144ad9）

- 註冊區塊在 **~161–169 行**：`init_lite(segment_for_lite)`
- `def segment_for_lite` 在 **1219 行**

Python 頂層逐行執行 → 執行註冊時名稱還不存在 → `NameError` →
被 `except Exception as _le: print(...)` 吞掉 → blueprint 從未註冊 →
**每次部署必然 404**。Cloud Run 啟動 log 裡現在就有那行
「民眾版端點未載入: name 'segment_for_lite' is not defined」。

契約測試抓不到：`test_lite_segment.py` 直接 import api_lite 注入 mock，
不執行 app.py 的頂層順序——與主控台 JS 事件同族：檢查全過、跑起來才炸。

### 建議修法（最小 diff 二選一）

1. `init_lite(lambda img: segment_for_lite(img))` —— lambda 延遲解名，
   呼叫時 def 早已存在；註冊區塊與那 60 行註解都不用搬。
2. 或把整個 try 註冊區塊搬到 `segment_for_lite` 定義之後。

### 一起補的防線

- except 那行改 `logger.error`（print 在 Cloud Run 也進 log，但 error 級
  才會被人看到）。
- `deploy_cloudrun.ps1` 部署後驗證加一條：**GET `/api/v1/lite/segment`
  應回 405**（在=405、沒掛上=404）——「try/except 吞掉註冊」這一族病
  從此在部署當下現形。

### 驗收

同一張印刷樣例：App 診斷行應顯示 `route=cloud_escalated(AU)`、
後端輪廓 ≥1，自動圈選成功。

---

# 原始交辦（同日稍早）：lite/segment 對難例全空——缺升級鏈

## 現象（實機重現）

WoundLite 對多張**印刷傷口樣例**自動辨識全部回空（App 正確退手動圈選）。
端點有回 200、`wound_polygons: []`——不是連線問題，是模型沒抓到。

## 根因（讀碼定位，行號以 2386d87 為準）

- `app.py:162`：`init_lite(segment_wound_ai)` —— lite 端點拿到的是
  **student 基礎模型單獨一顆**。
- `app.py:1608-1632`：醫療版 classify 的雙軌升級——student 之後跑
  A∪U 集成第二意見（`_load_cloud_au`／`_au_infer`，thr 0.40），
  `au_ratio > 1.5 或 IoU < 0.5` 就改採集成遮罩（route=cloud_escalated(AU)）。
- `app.py:1631` 的既有註解自己就寫著：「**student 回空遮罩…但緊接著
  A∪U 集成常常救得回來**（實測 route=cloud_escalated(AU)）」。
- 佐證：2026-08-18/19 醫療版對**同一批印刷樣例**辨識全部成功，
  路由清一色 `cloud_escalated(AU)` —— 即 student 對這批樣例本來就是空，
  一直是集成在救。Lite 沒接集成 → 必然空手而回。

## 建議修法

把 classify 的 escalate 區塊抽成共用函式（例 `segment_with_escalate(rgb)`：
student → A∪U 第二意見，**同一組閾值**），classify 與 `init_lite` 都吃它——
與你們在 `_store_depth_png` 寫過的原則相同：重用不是省程式碼，
是讓兩條路徑的判準不分岔（「同一張圖醫療版認得、民眾版認不得」就是這次的樣子）。

## 成本考量（匿名端點跑集成）

- 集成**只在 student 空/低估時觸發**（觸發條件本來就是難例判定），
  一般消費者實拍多數不會走到；印刷翻拍是最難的 domain shift。
- 既有配額（30/日/裝置＋IP 200/日）已對總量封頂；若仍要保守，
  可對 lite 加獨立的每日集成次數上限（環境變數）。

## 驗收建議

1. 同一張印刷樣例打 lite/segment 應回非空 polygons（consent=false 測試不落地）。
2. `test_lite_segment.py` 加一條注入測試：「student 回空、ensemble 有」→
   端點應回 ensemble 結果；突變（拿掉升級）應紅。

## 附帶（iOS 端已自行修掉）

空輪廓＋已同意時，後端照樣落地影像與深度（對研究反而是重要的難例樣本）——
App 先前在空輪廓分支漏講「已上傳」，已補：據實顯示「未辨識到傷口，
去識別資料已上傳供研究（正是改進辨識所需的難例）」。
