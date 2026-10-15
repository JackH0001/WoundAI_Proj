# 私有示範環境與操作簡報階段驗證 — 2026-10-15

## 結果與範圍

獨立示範後端已完成私有部署與合成資料驗收；尚未開放 App／Apple 審查對外入口。現行 MMH 服務與資料沒有因本階段而切換。

| 驗證 | 結果 | 參照（本機 evidence） |
|---|---|---|
| 新示範媒體桶權限修復 | 完成；原 projectOwner 綁定恢復，專用 runtime 保留，15 分鐘臨時綁定立即移除並讀回確認 | demo-private/repair-result.json |
| 執行身分隔離 | 核對 44 份 IAM 政策與 15 項有效權限 | demo-private/isolation-policy-graph.json、isolation.json |
| 私有後端資料流程 | 39/39 通過 | demo-private/acceptance-corrected/result.json |
| 重啟後保存與隔離 | 6/6 通過 | demo-private/restart-verification.json |
| 示範登入狀態補修（本機） | medical session 41、management session 7、review plan 7，合計 55 通過 | local-fixes/ 三份 review 測試日誌 |
| Android 原圖生命週期及修邊介面 | 8/8 模擬器測試通過；另以實際介面開啟修邊及組織工具確認 | local-fixes/android-bitmap-tests.log |

資料流程驗收涵蓋：登入、跨機構拒絕、影像辨識、傷口及組織遮罩保存收據、相同內容去重、兩個不可覆寫修訂、原始 f32LE 深度讀回及雜湊、方向／拍攝時間／內參、撤回後拒絕影像讀取與新上傳、稽核驗證。合成測試紀錄已撤回。深度 fixture 為 80×64、20,480 bytes；這是傳輸／保存完整性驗收，並非實機感測精度驗收。負向幾何案例同時含零深度，不能單憑它宣稱幾何檢查已獨立驗證。

初次 care receipt 檢查失敗原因為新示範 secret 使用單一字串而非 JSON keyring；已修正該新資源設定，通過 4 項格式檢查後重跑上述 39 項。舊版 secret 保留，未輸出內容。

## Android 修邊閃退修正

製作簡報時，對整張不可變 Bitmap 做裁切與縮小，Bitmap.createBitmap 可能直接回傳呼叫者原圖。TissueSeg 原本回收這個暫存參照，造成修邊 Canvas 再畫原圖時閃退。現在只回收自己擁有且不是原圖的暫存 Bitmap。

新增四種實際 Android 測試：整張縮小、整張不縮放、局部縮小、局部不縮放；每種重複分類三次並確認原圖仍可畫、像素未變。另重跑四項 ReviewControlsTest。只更新模擬器，不代表 Alldocube 實機或 Play 商店已更新；既有 Android 27 發布產物必須重新建置納入這項修正。

## 尚待完成

- 示範後端現行映像的 auth/session 仍回覆 session_reuse_not_configured；已完成限定完整示範 profile 的補修與本機測試，但尚未建置／部署。MMH 規則不變，管理 session 不對示範開放。
- 示範服務保留 Cloud Run 私有 IAM，匿名請求拒絕；「免 Google」產品入口尚未開放外部。需後續具體建置與入口方案，不可將本階段視為公開審查就緒。
- review01 僅在驗收程序中使用臨時產生密碼，未交付可用審查密碼；後續仍需使用者安全設定。
- Android 實機、商店新版本、示範 App 完整流程仍待驗。WoundLite 保存排程保持暫停。

單次示範 API 觀察約：登入 0.9 秒、辨識 2.0 秒、首筆標註 6.5 秒、重送去重 2.8 秒、深度 2 秒。這些不是手機端端到端數值，也不是有控制條件的 MMH 前後比較，不能據此宣稱取消 Google 可節省固定秒數。

## 使用者簡報

已完成 iOS／Android 各 13 頁可編輯 PowerPoint，使用實際模擬器合成資料畫面。逐頁檢視最終輸出並通過檔案結構／版面驗證。說明包含雙帳號、個案、輪廓／組織修邊、等待與保存、兩張影像輸出、自訂資料夾、修訂與深度差異。截圖未冒充已登入、已簽同意或已雲端保存；真機登入成功及最終保存畫面日後可補入。

本機完整證據根目錄：woundai-institution-evidence-20261007/mmh-iap-update-20261011/release-continuation-20261015。
簡報根目錄：woundai-user-guides-20261015；Android final v2、iOS final v3。
