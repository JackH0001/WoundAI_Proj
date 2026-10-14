# Android MMH 時間軸與行政副本交易對齊 — 2026-10-13

## 範圍

基底 dc7165c3e5f2b148cb7c8c9d05c763d5d3349c00，分支 codex/mmh-full-alignment-20261013。接續七份已凍結 patch，本層只修改 Android 與文件，未提交、推送、部署或更新實機／商店。這份報告更新前份 android_mmh_private_exports_validation_20261013.md 中「與資料庫保存交易及時間軸重修尚未綁定」的缺口；舊測試證據保持不變。

## 行為

- 新量測先成功存入 Room 時間軸，再從該筆已存影像、raster 與數值建立行政副本；資料庫失敗不啟動副本產生。
- 重修、無變動的醫師確認走相同產生器。讀取前核對已存版本與當前環境；臨床個案仍需有效照護同意，不新增 MMH 合成／臨床來源分級。
- 副本含 measurement.jpg（本機已存 JPEG 原位元組）、overlay.jpg、result.json；有 raster 時包含原生 wound.png、tissue.png。PUSH 依保存的面積、組織比例與滲液重算，缺資料保持未知，不抄舊備註數值。
- 使用紀錄 ID 與各檔案雜湊導出的版本鍵去重；內容相同不重建，內容不同保留新副本。既有同名副本必須解密、逐檔比對相符，否則拒絕而不覆寫。
- annotationSubmitted 狀態變動不產生新的行政版本。副本仍為 noBackupFilesDir 中 AES-GCM 密文，明確匯出才寫至使用者選擇的目的地。
- 保存期間顯示進度，阻止重複存檔、重新進修邊、補送與返回。副本失敗明確顯示時間軸已保存與副本未完成，兩者不混為同一成功狀態。

## 本輪實測

- 最終 APK：105/105 JVM 測試；MMH validation APK／測試 APK 建置、MMH internal 及 Release Kotlin 編譯成功。
- Pixel_7 模擬器：36/36。新增 11 項涵蓋實際 Room 保存後回呼、重複保存、資料庫失敗不回呼、副本失敗不誤報存檔失败，以及相同版本去重、修訂保留舊位元組、過期紀錄／關閉環境／缺同意／損毀遮罩與尺寸錯誤拒絕。
- 工程隔離 runner 111/111 檔，30.077 秒。
- verify_logic：50 項無失敗；parity：未宣告落差 0、已宣告差異 10，不能据此宣稱兩平台所有功能相同；git diff --check 通過。
- 以 /private/tmp 獨立 Gradle buildDirectory 與 cache 建置，不處理先前工作目錄內重複生成檔，不接觸使用者實機資料。

這是 macOS／Android 模擬器測試，不是 Windows 鎖版、平板相機、Play 安裝版或雲端完整資料驗收。既有工程測試是回歸證據；新的行為證據来自實際 Room 與 Android Keystore 測試。

## 尚待完成

1. 相機高清副本與量測副本仍缺共同採集識別綁定；measurement.jpg 是時間軸的已存影像，不宣稱原始感測器或最高解析度。
2. 既有資料庫沒有保存 calibration marker quad，結果包明列 null；captured_at 亦為 null，record_created_at_ms 不冒充曝光時間。
3. 私密副本保留／撤回清理策略、實機 SAF 外部提供者與效能仍待驗收；關閉選項保留既有副本。
4. 此包不含原始深度與 WoundAI3D 完整研究封包；Android 持久上傳 outbox、雲端成對格式部署、iOS／Android 發布及獨立示範環境仍未完成。

證據目錄：woundai-institution-evidence-20261007/mmh-iap-update-20261011/android-saved-exports-validation-20261013。包含最終建置、單元／模擬器測試、工程 runner、邏輯與 parity 日誌、可重建的 patch 及 SHA-256 manifest。
