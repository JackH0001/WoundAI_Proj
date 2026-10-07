# Lite 本機資料備份排除與儲存驗證（2026-10-05）

## 修復範圍

候選版新增 HealthDataFiles：Lite JSON 量測索引所在目錄，以及 LocalImageStore 的影像／深度目錄設定 isExcludedFromBackup，並由新 URL 讀回核對。原路徑保留，既有檔案在載入時補標記；不遷移、不刪除使用者紀錄。

寫入先在已排除目錄中建立暫存檔，設定檔案保護與備份排除後，再以同目錄 rename 原子替換。任何前置步驟失敗都不發布新索引；不可用父路徑、非一般檔案及 symlink 被拒絕。影像與深度仍 AES-GCM 加密，Lite JSON 不因此變成 AES 加密。

Lite 設定補上換機、移除 App 或裝置遺失可能無法復原的提醒，研究上傳不是個人備份。隱私政策僅更新 repo 內草稿，未發布。

## 驗證

| 環境／範圍 | 結果 |
| --- | --- |
| Lite Release 模擬器全套 | 150 個案例：149 通過、1 跳過、0 失敗 |
| 醫療 Release 共用儲存回歸 | 79/79 通過 |
| iPhone 16 Pro Max 獨立合成資料測試 | 7/7 通過、0 跳過 |

模擬器跳過的是檔案保護等級讀回：本機 simulator 回傳 protectionKey=nil。該案例在實機實際執行，兩次原子替換後皆讀回 NSFileProtectionCompleteUntilFirstUserAuthentication。其他備份旗標、舊加密位元組／解密讀回、JPEG／depth 新檔、symlink、無效父目錄與不可覆寫目錄測試也在實機通過。Lite 舊 JSON 升級／重新載入／修改，以及 symlink 索引拒絕，由真實模擬器檔案系統驗證。

實機使用獨立 bundle com.woundai.storageqa，包含來源中的 HealthDataFiles、LocalImageStore 與 PhiCrypto，以合成位元組和獨立 Keychain scope 執行；沒有安裝取代 com.woundai.app 或 com.woundai.lite，也沒有讀取其沙盒。測試結束已移除該診斷 App，留下測試日誌及 xcresult。

第一輪失敗紀錄保留：讀取保護屬性在模擬器得 nil；舊的失敗寫入測試用缺少目錄當反例，但新功能正是建立受保護目錄。後者改為父路徑被一般檔案占用的確定失敗案例，仍要求原內容完整不變。沒有將真正的資料寫入失敗改成可接受。

證據目錄：`/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-competitive-review-20261003/lite-backup-protection-20261005/`。包含 lite-final、medical-final、device-tests 的 log／xcresult、隔離 harness、移除回執及 validation.json 來源雜湊。

## 不可外推的結論

- Apple 說明備份排除旗標是給系統的指示，不能保證資料永遠不出現在任何備份或還原裝置；部分寫檔操作也可能重設屬性。此次驗證的是旗標與檔案保護實際讀回，不是完整 iCloud/Finder 備份還原測試。[Apple：Optimizing Your App’s Data for iCloud Backup](https://developer.apple.com/documentation/foundation/optimizing-your-app-s-data-for-icloud-backup)
- 不清除舊備份，不證明 Mac vault／Time Machine、人工匯出、研究雲端所有副本皆已被管制。
- 此次涵蓋 Lite 主索引及兩 App 共用影像目錄。醫療版 SQLite/WAL、depth_index.json 與其他待盤點檔案仍需另行完成，不能把 R5 整項標記完成。
- 不等同 App Attest／GCS 全鏈路或 IRB 已完成，也沒有修改研究同意與雲端角色。
- 尚未提交、封存或上傳含此修復的新 App；已上線醫療 Build 27 不包含本輪備份修復。
