# Handoff 2026-08-19（Mac → Windows）：lite/segment 對難例全空——缺升級鏈

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
