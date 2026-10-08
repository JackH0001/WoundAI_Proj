# 醫療版拍攝導引與修邊手勢驗證（2026-10-05）

## 本次修改

- 個案列增加相機、「臨床拍攝」與右箭頭，整列加底色及點擊範圍；原有照護同意限制與獨立時間軸入口維持。
- 臨床量測已有影像時，拍照按鈕顯示「重新拍照」。開啟相機仍沿用原流程，取消不主動清除舊結果。
- 共用修邊器在第二指加入時還原第一筆暫存，包括筆刷造成 ROI 擴張的情形；取消手勢不提交筆跡，未收到有效起始事件的移動不畫點。
- 還原快照納入遮罩、組織、原始及自動底稿、ROI 大小與原點。ROI 擴張不再清除尚待還原的筆畫快照與既有 undo；每個歷史堆疊依實際四層資料量限制快照數。
- 修正同時縮放／平移的中心錨點計算；固定動態標頭預留行數，減少內容改變使畫布高度與座標變動。只處理畫布自己的觸控。

## 驗證結果

| 驗證 | 結果 |
| --- | --- |
| WoundMeasurementApp Release，全套（含六個新手勢測試） | 78/78，0 失敗 |
| WoundLite Release，共用修邊器回歸 | 141/141，0 失敗 |
| 增加畫面測試後，EditorGestureTests 專項重跑 | 7/7，0 失敗 |
| 合成圖片修邊器實際渲染 | 畫布高度 >200 pt、寬度 >250 pt；截圖核對工具列無遮擋 |
| 指定修改檔案 git diff --check | 通過 |

專項七項與全套測試有重疊，不加總成獨立案例。六項手勢測試涵蓋：先產生筆點且擴張 ROI 後加入第二指、跨 ROI 大小 undo/redo、取消還原、被鎖定時起始的移動、縮放加平移的錨點、取消筆畫保留既有 undo。第一項同時驗證 Lite 與醫療模式。

執行環境：iOS 26 模擬器，arm64，Release、ENABLE_TESTABILITY=YES，deployment target 17.0。首次執行遇到測試模組未開 enable-testing，第二次在 Documents DerivedData 遇 codesign resource fork；移至 /private/tmp 並修正測試建置參數後成功。這兩次失敗保留在證據目錄。最後只整理縮排與註解，未再改執行邏輯。

## 證據與限制

證據目錄：`/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-competitive-review-20261003/medical-ui-gestures-20261005/`，包含 medical-verified.log/.xcresult、lite-verified.log/.xcresult、editor-visual.log/.xcresult、attachments 及 validation.json（來源 SHA-256）。

手勢狀態機及畫面測試已通過，尚未在實機重演多指起落時序，不能保證所有硬體操作情境都已排除抖動。拍攝入口與重新拍照標題已編譯，尚未單独做實機視覺驗收。此輪未提交／推送、未建立或上傳新版 TestFlight；目前已上傳的 Build 26 不含本輪修復。下一個安裝版本需重驗：首次雙指縮放無新增圓點、放大 ROI 連續描邊穩定、undo/redo、取消拍照保留舊結果、兩個入口不混淆。
