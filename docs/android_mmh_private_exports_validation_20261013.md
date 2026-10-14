# Android MMH 私密行政副本 — 2026-10-13

## 本機修正範圍

基底 `dc7165c3e5f2b148cb7c8c9d05c763d5d3349c00`、分支 `codex/mmh-full-alignment-20261013`。接續六份固定 patch，本層只改 Android 與文件。未提交、推送、部署或更新實機／商店。

MMH validation 與 internal 的行政副本預設關閉，設定新增「建立行政影像與結果副本」。啟用狀態按 MMH／合成示範隔離並保留。副本使用 Keystore AES-GCM 加密，存入 `noBackupFilesDir/administrative_exports/<scope>/`，不寫 MediaStore、不提供 FileProvider URI，不建立明文暫存檔。MMH 遇失敗不退回共用相簿。一般非 MMH 版本不顯示此設定，原有相簿政策未在本輪更動。

- 相機擷取副本保存收到的 bitmap 尺寸，以 JPEG 品質 100 編碼；不宣稱等於感測器原始檔或無損影像。
- 結果副本 ZIP 包含 measurement.jpg、overlay.jpg、result.json；有修邊 raster 時另含 wound.png、tissue.png。保留面積／PUSH／滲液／組織比例、醫師確認、image_id（可為 null）、輪廓與 raster 定位。
- 副本建立時間明標 export_created_at_ms，不冒充拍攝或曝光時間。每份 ZIP 附 sha256.json；解密、scope 及內容雜湊全驗過才寫明文。
- 使用者從設定選單選一份副本，再用系統「建立文件」選擇 ZIP 目的地。取消不匯出；失敗不刪本機副本；外部目的地可能同步，UI 會說明。
- 關閉開關保留舊副本，不影響量測時間軸與研究上傳。舊版相簿檔不自動搬移／刪除。

## 操作說明（尚未發布的新本機版本）

1. 在目前環境的「後端連線設定」下方找到「行政影像匯出（本機）」。
2. 若需要行政副本，開啟「建立行政影像與結果副本」。
3. 後續拍攝保存擷取副本；按量測頁存檔時生成結果副本。請分別檢查時間軸存檔訊息與行政副本訊息，副本存在不證明時間軸或雲端保存成功。
4. 回設定按「選擇副本並匯出…」，按建立時間選一份，再自行選擇儲存位置。

## 驗證

- JVM 105/105，MMH validation APK／測試 APK 建置、MMH internal 與 Release Kotlin 編譯通過。
- Pixel_7 模擬器 25/25：8 項私密副本測試、2 項設定 UI、15 項原環境／門禁回歸。
- 工程隔離 runner 111/111 檔；邏輯與 parity 另存 log。不冒稱 Windows 鎖版／實機或 SAF 外部提供者完整驗收。
- 測試覆蓋預設關閉、偏好設定保留、scope 隔離、密文落地、noBackup 位置、關閉保留舊檔、路徑穿越、損毀拒絕且無明文輸出、ZIP 位元組／雜湊、成對附件與 metadata 防覆寫、MMH 不退回相簿。

追加建置曾因原 app/build 的 `ic_launcher_foreground 2.xml` 重複生成檔失敗；未據此修改產品，改以獨立 /private/tmp buildDirectory 及 project cache 建置成功。不能僅憑檔名推定是哪個同步工具造成。失敗 log 保留。

## 未完成界線

這是行政副本，並非 WoundAI3D 完整 RGB-D 匯出包；Android 仍無 LiDAR 採集。相機副本與結果副本分開列出，尚未建立跨副本的唯一採集識別綁定。時間軸重修後自動生成同版本副本、與資料庫成功交易綁定及副本保留／撤回清理策略仍待補齊；目前不宣稱與 iOS 行政副本全流程完全一致。

系統檔案選擇器的真實外部目的地寫入／取消、實機效能及 Play 安裝版仍待驗收。本輪只在模擬器驗 UI 與記憶體輸出串流，沒有匯出使用者資料。持久上傳 outbox、雲端新格式部署、商店發布等原目標仍未完成。

證據：`woundai-institution-evidence-20261007/mmh-iap-update-20261011/android-private-exports-validation-20261013/`。
