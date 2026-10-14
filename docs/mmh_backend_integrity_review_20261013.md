# 2026-10-13 生態系覆核核實與 W0–W3 本機修正

## 基準與範圍

收到 `review_mmh_ecosystem_20261013.md` 及 `codex_worklist_backend_integrity_20261013.md`，以本機整合分支 `codex/mmh-full-alignment-20261013` 的 `dc7165c3e5f2b148cb7c8c9d05c763d5d3349c00` 為基底。工作清單是參考資料，未據此擴大雲端或公開來源授權。

依使用者決定，本輪沒有新增 MMH synthetic-only／臨床資料來源分級限制。共用格式與幾何檢查仍適用 MMH；既有權限、同意、撤回與不可覆寫修訂邏輯不取消。

W0 patch 和 W1–W3 後端 patch 分開交付。後者另在乾淨 main `85775e8613b8a4680f8df2d0eca2e292dd383fd9` 套用與測試，不必合入 PR #24。本輪未推送、未合併、未建置雲端映像、未部署、未改帳號／IAP，也未安裝新的手機版。

## 完成內容

| 工作 | 實際變更 |
|---|---|
| W0 | Android 紀錄預覽補 `resumeRaster?.wbGains`。靜態檢查辨識只供比較的 copy，實際寫入仍受保護；支援新版原圖切換。原三項失敗已修正。gitleaks 整合衝突先前已解，保留兩組精確路徑 AND 值的 allowlist。 |
| W1 | 遮罩入庫前驗 base64、4MB、IHDR、2–4096 尺寸、完整解碼、8-bit L/RGB/RGBA、0–5 類別、RGBA 不透明及 R 通道編碼、必要定位、至少 8×8 有效交集與修訂欄位型別／範圍。允許 ROI 部分超出照片。失敗 400 tissue_mask_invalid、稽核 tissue_mask_rejected、無資產／入列 intent。讀回與匯出重新檢查；不猜缺少的 m_scale。 |
| W2 | f32 的參考／深度／RGB 比例與宣告尺寸檢查；PNG 內參有限正值／主點範圍、灰階深度與置信度尺寸；保存 depth_png/reference 格點、歷史輸出 unknown。無效 PNG 深度保持 2D，不記為深度完成。 |
| W3 | manifest 補輪廓、面積、醫師確認、滲液、App／裝置／模型來源及深度來源；缺值 null，interrater 也保留 gt_polygons。離線匯出所有輪廓聯集，記 n_polygons，資料卡說明不保證孔洞。 |

新增測試已接入 `.github/workflows/p0-4-audit.yml` 兩處 paths 與執行步驟。W3b PowerShell 深度下載及 IAP 取件工具未在本輪更動。

## 本輪驗證

- 乾淨 main 85775e8 加 W1–W3 patch：108/108 檔通過，不依賴 PR #24。
- 整合分支工程測試 109/109 檔通過；使用 repo 隔離 runner，每檔獨立執行。
- tools 29 檔全部通過，包含 344 個 unittest cases。
- 管理服務 Python 121/121、JavaScript 9/9 通過。
- Android MMH validation APK 與 internal Kotlin 編譯成功，單元測試 97/97 通過；本轮未做手機實拍／Play 安裝驗收。
- 14/14 變異測試攔截成功：尺寸、IHDR、類別、交集、修訂比例、f32 參考／RGB 比例、PNG 比例、置信度尺寸、格點標記、manifest 輪廓、多輪廓匯出、真正 raster 寫入與白平衡漏傳。
- gitleaks 8.28.0 變更範圍零命中；12/12 allowlist 實際掃描邊界案例通過。
- verify_logic、parity_check、git diff --check 通過。
- owner_guard --platform mac 預期拒絕 Backend/engineering 的跨平台提交，本輪未修改規則，交付 patch，不宣告該檢查通過。

環境：macOS Python 3.12.14、NumPy 2.4.6、OpenCV 5.0.0、FastAPI 0.141.1。不是 Windows／PowerShell 實跑；Windows lock 的 NumPy 項為 2.5.2，故不聲稱完全重現 Windows lock 環境。

首次全套 105/109：新版 guard 揭露舊 fixture 用正方形 RGB 搭 4:3 深度、非現行 ROI sx/sy、像素數超過遮罩容量，以及舊測試仍預期落盤階段 409。修正 fixture 為實際 App 契約與新的寫入前 400，保留拒絕測試；沒有放寬產品驗證。最初變異測試另揭露 PNG/RGB 比例缺反例，已補上。所有初始失敗紀錄保留。

## 覆核報告與較新整合版的差異

- PR #24／main 的舊快照不包含本機 `dc7165c`：已有採集回呼時固定的 capture_id／時間來源、Lite rgbd/2、完整深度 SHA／payload／annotation receipt 核對。但此時間仍是回呼時的 wall clock，不是感測器曝光時間；尚未部署。
- gitleaks 與管理模組已在本機整合，公開 PR 尚未更新，不把本機成功當成商店與雲端已對齊。
- 「Lite 收到 0 筆」及「標註 71 秒」屬報告引用時點；本輪未重新盤點雲端或測延遲，不能當成目前實測結果。

## 仍未完成、不能以格式測試取代的工作

1. 原生最終傷口二值遮罩上傳與不可覆寫保存：多輪廓聯集仍無法還原孔洞，也不能用組織碼 0 猜傷口外／未知組織。必須獨立封包與雜湊。
2. WoundAI3D 含 IAP 的完整取件、逐物件驗 SHA、重新檢查撤回、重建與實體非對稱標靶配準；畸變、曝光時間、重力／多視角資料不能虛構。
3. Android 持久 outbox、原確認醫師與修訂收據綁定，以及商店版實機驗收。
4. 稽核增量驗證需先定義 generation／鏈頭的可靠錨點与完整檢查策略；不以關閉驗證或移除稽核換速度。手機 session 重用已有本機修正，仍待成對部署驗收。
5. 穩定服務入口與 candidate 分離；Lite 專用服務、App Attest 實機驗收；最新獨立示範環境仍待完整資源方案與授權。
6. 歷史不完整資料按實際用途隔離；不是直接刪除或推定整個服務都無用。

完整日誌、兩份 patch、SHA-256 及乾淨 main 驗證結果見同日 `backend-integrity-review-20261013` 證據資料夾。本輪無新的 backend commit，patch manifest 明列基底與檔案。
