# 整合 CI 補修驗證 — 2026-10-14

PR #27 在固定來源整合後暴露的問題，本次保留原失敗日誌並修正，沒有降低資料或登入檢查。

## 修正

- 雲端 allowlist 打包遺漏 annotation_masks、tissue_mask_contract、medical_depth_geometry、review_service_profile 四個實際匯入的後端模組。補齊清單，打包前檢查 Python 靜態匯入閉包；不自動加入未知檔案。此檢查不涵蓋動態 import，不能取代容器執行驗收。
- iOS 遮罩原先透過 CGContext 重畫解碼，色彩轉換會改寫低值類別碼。改用 ImageIO 編碼與原始 sample bytes 解碼，保留 0–5 類別與二值傷口；拒絕透明、錯誤尺寸及不支援的 sample layout。新增線性 gamma 固定 PNG 驗證，避免同一編解碼器互相掩蓋問題。
- 工作階段失效檢查原先只編入機構版；改為一般醫療版也檢查，URLSession 回應後再次核对世代，防止登出／切換帳號後晚到的回應恢復舊身分。

## 本輪 Mac 實測

| 項目 | 結果 |
|---|---|
| 打包回歸 | 8/8 通過 |
| 工程完整執行 | 111/111 檔通過 |
| tools | 358 項，357 通過、1 略過（沒有 PowerShell） |
| iOS 三組針對性 XCTest | 35/35 通過 |
| 一般醫療版 XCTest | 191 項，190 通過、1 略過 |
| MMH XCTest | 132 項，131 通過、1 略過 |
| MMH XCUITest | 4/4 通過 |
| iOS 真實 encoder → Python 後端解碼 | 64 個傷口與64個組織像素逐點相同；孔洞、孤島、unknown tissue 保存 |

兩個 XCTest 略過的是需先取得系統資料夾選擇授權的跨程序測試；不當成通過。模擬器 iOS 26.5。本輪沒有更新實機、商店、雲端映像或服務；GitHub CI 的另一個 iOS 環境仍需跑完才可宣告來源檢查完成。

證據位於本機 `woundai-institution-evidence-20261007/mmh-iap-update-20261011/integrated-release-validation-20261014`；原始110檔交接 manifest 不變。Mac 為合格提交場域不等於 Windows／PowerShell 已完成驗證。

## CI 的系統資料夾前置條件

一般單元測試不先操作系統資料夾選擇器；跨程序案例因此會明確 skip。CI 只允許 `InstitutionPrivateExportsTests/testPersistedSystemFolderDeliversAfterProcessRestart()` 在 iOS Simulator、且原因完全等於缺少指定 UI 授權時略過，輸出 `system_folder_acceptance_pending: true`。其他 skip、真機 skip、實際失敗、錯誤原因或重複案例一律拒絕。這不是資料夾驗收通過；仍需單獨進行系統選擇器／重新啟動驗收。

驗證工具10/10回歸通過，並對本輪MMH xcresult讀回135 passed（131單元＋4介面）、1 skipped、0 failed，確認真實結果格式也可正確分類。
