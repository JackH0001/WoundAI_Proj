# MMH 與合成示範資料隔離驗證

2026-10-13 的本機驗證已確認，新增的儲存範圍可將 MMH 與合成示範的資料庫、影像及 iOS 深度索引分開。iOS 待傳佇列也完成同一紀錄 ID 的隔離驗證。現有 MMH 預設路徑不變；示範入口與雲端環境仍未啟用，因此這是環境切換的儲存基礎，不能當成完整示範環境驗收。

本次獨立分支 `codex/mmh-demo-storage-isolation-20261013` 以 `4fc41f164d766689d7caf89be2a9ef904bd0e891` 為基底。既有待授權推送的手機與後端 SHA 不變；本次未推送、部署或安裝至實機。

## 修正內容

| 範圍 | 修正及驗證意義 |
|---|---|
| iOS 深度 | 原本不同 LocalImageStore 共用全域 depth_index.json；現在索引明確綁定影像 store，所有讀取、保存、已上傳旗標與清理均傳入該 store。 |
| iOS 寫入失敗 | 索引讀取毀損時停止 attach、markUploaded 與 purge；新索引寫入失敗會清掉本次新 sidecar 並回報失敗。清理先成功保存索引，再刪除對應 sidecar。 |
| iOS 併發 | 以 process 內鎖保護索引 read-modify-write，避免同時保存丟失另一筆索引。跨 process 寫入尚未設計。 |
| iOS 儲存組合 | 新增固定 MedicalDataScope 與不可變 MedicalLocalStores，組合獨立 SQLite、影像、深度及加密 outbox。預設 AppState 尚未切到動態環境。 |
| Android | Room cache 依資料庫絕對路徑區分，固定 scope 決定資料庫名稱與影像位置；原 MMH 名稱、schema 與 migrations 不變。 |
| 檔名 | 兩端讀取／存在判斷／刪除拒絕含路徑分隔、前導點或 NUL 的影像參照，避免相對路徑越過儲存位置。此檢查不是獨立 OS 沙箱。 |

兩範圍仍位於同一 App 沙箱並使用既有加密機制；不宣稱擁有不同 OS 身分或獨立加密金鑰。iOS export／settings 的隔離名稱已定義，但尚未接入環境切換；Android 其餘偏好、憑證、同意與背景工作仍需整合。不得僅增加入口按鈕後宣稱完全隔離。

## 測試結果

| 驗證 | 結果 |
|---|---|
| iOS WoundAIInstitutionTests 完整回歸 | 102/102 通過，包含 Google 恢復、session、保存收據、疊圖、outbox 與新增 8 項隔離測試 |
| Android MMH validation 單元測試 | 97/97 通過，0 skipped |
| Android Pixel_7 模擬器 instrumentation | 11/11 通過，0 skipped；MedicalDataScopeTest、RasterProvenanceTest、ReviewControlsTest |
| Android validation APK、instrumentation APK、internal Kotlin | 建置成功 |
| WoundLite iOS Simulator | 共用程式編譯成功 |
| git diff whitespace | 通過 |

新增測試實際開啟 SQLite／Room 與加密檔案，以相同紀錄 ID、同名影像和同名深度查詢驗證不同內容各自保存；修改 MMH 上傳旗標、刪除 MMH 檔案不影響示範副本。iOS 另驗證 outbox 重開後仍隔離、毀損索引不被覆寫、索引寫入失敗不殘留新增 sidecar，以及兩端路徑穿越被拒絕。所有資料為合成 fixture。

第一次 Android 指令從 repo 根目錄呼叫不存在的 gradlew，未執行測試；改由 Android 目錄執行後上述建置與測試通過。iOS 保留既有條件編譯警告，Android 保留 Gradle 9 相容性警告，均非本輪新增編譯錯誤。

證據目錄：`/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-institution-evidence-20261007/mmh-iap-update-20261011/mobile-parity-20261013/storage/`。保存四份建置／測試 log、Android XML 與 summary.json。

## 尚待的發布與實機驗收

手機 UI／登入對齊見 `mmh_ui_auth_alignment_20261013.md`，iOS 恢復身分追加驗證見 `mmh_restore_boundary_validation_20261013.md`。本輪 102 項完整 iOS 回歸已包含後者最後追加的 11 項測試，無須把先前各輪數目相加。

手機的 MedicalSessionPool 依賴尚未部署的後端 `GET /api/v1/auth/session`。必須先完成成對來源覆核、CI 與後端候選驗證，才可更新實機驗收；不能把本機測試成功等同新版已上線。Google 自動恢復仍需真機確認授權到期、離線恢復、撤權、換帳號及明確登出。量測延遲需再分冷暖啟動、排隊、身分驗證、推論、稽核與深度保存實測，尚無證據承諾已把一分鐘縮短到特定秒數。

示範環境接線前尚須綁定 origin、audience、service、org、local scope，處理離開環境時的背景工作及憑證、同意資料隔離。新增雲端資源仍須具體方案授權。TestFlight 外測与 Play 商店安裝驗收尚未完成。
