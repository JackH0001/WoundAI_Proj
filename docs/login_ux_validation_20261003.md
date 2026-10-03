# 登入與登出操作驗證（2026-10-03）

## App 改動

回應 TestFlight 使用者在「設定 → 儲存並測試連線」遇到的操作問題：

- 鍵盤增加「完成」，帳號可切到密碼、密碼可直接送出；送出時收起鍵盤。
- 立即顯示進度及首次連線可能較久的提示。連線時停用帳號、密碼、網址、登入、主控台及登出操作，避免重複請求或中途換帳號。
- 可取消連線檢查；離開畫面亦取消。非同步階段檢查取消狀態後才更新身分。
- 「登出雲端帳號」清除 App 儲存的帳密、當前身分與此連線物件的 token，保留本機紀錄與網址。瀏覽器需獨立登出。
- 換帳號時必須重填密碼；網路例外不再被吞掉後顯示為帳密錯誤。

## 驗證

- Xcode 27、Release、iPhone 17 Pro／iOS 26.0 獨立模擬器：XCTest **57 通過、0 失敗、0 跳過**。簽章使用模擬器 ad-hoc 身分。
- 使用僅綁定 localhost 的合成 HTTP fixture，沒有使用真實帳密、病患資料或正式後端。
- 實際 UI 已確認：完成鍵收鍵盤；等候提示與停用控制項；登入成功；登出後身分與帳密清除，重進設定仍為空白；取消後延遲回應不恢復身分；401 與無法連線各有對應提示。
- 取消情境先於最後追加的「讀取身分後再查取消」防護驗證；最終版本重新完成 57 項測試及 401、連線失敗、登出 UI 檢查。
- `git diff --check` 與 `owner_guard --platform mac` 通過。
- DerivedData 放在 `/private/tmp`，避免 Documents 中的 Finder metadata 造成簽章失敗。

以上不取代新版 TestFlight 的實機驗收。已安裝的 build 23 不會自動取得原始碼修正；需重新封存、上傳新 build，處理完成後再分發。

## 瀏覽器主控台（獨立 patch）

對部署基準 `4005f392a5e77159ee44a140b86ca80af8fe0cdc` 重現：`applyPerms()` 對所有 `nav a` 做權限篩選，誤隱藏沒有 `data-tab` 的登出連結；手機 CSS 另將整個 `.who` 隱藏。

已準備單檔 patch：登出改為 button，頁籤篩選限定 `nav a[data-tab]`，手機顯示帳號區並允許換行。390px 寬度下，密碼登入與一次性碼登入後皆可登出並回到登入頁。既有 `test_console_js.py` 對候選檔實際解析通過，patch 可乾淨套入基準。

此 patch 尚未進後端分支或部署，保留 Windows 擁有的 `Backend/` 提交界線。App 和網頁的本機登出皆不宣稱已撤銷伺服器發出的 JWT；伺服器 token 撤銷是獨立工作。

## 後續：build 25 候選的環境與版號顯示

使用者回報缺 image_id 後，已確認 App 仍連到舊服務。歷史 revision 505ff2e 的 classify 回應沒有 persisted 欄位；新版 App 刻意不採信缺少保存確認的 image_id。唯讀線上 OPTIONS 檢查亦確認舊服務 care/attest 回 404，demo 回 200；demo health 顯示 LocalStore 與 care receipt 已設定。這些檢查不等同於使用者個別影像已成功上傳或保存。

- 設定頁從 Bundle 讀取版本與建置號；模擬器已實際顯示 1.0（25）。
- 醫療版新安裝預設 demo；Lite 既有路徑維持原設定。既存網址優先，只有帳號卻沒有網址的舊狀態亦保留原服務，避免升級把帳密送往另一平台。
- 缺 image_id 的提示改為保存未確認，依後端原因補充提示，不再直接推測已撤回訓練同意。醫師修邊成功不再單獨宣稱可送標註。
- Release XCTest 61/61，包含新安裝、保留既存網址、帳密無網址時不跨平台，以及明確選擇 demo 的四個回歸情境。既有缺 persisted／persisted=false 禁止採信 image_id 的測試仍通過。

build 24 已提交 Beta Review；上述後續修正需另以 build 25 分發。已儲存舊網址的使用者仍需自行登出、更改網址並使用 demo 帳號重新登入。

### 預設環境 CI 修正

82d17eb 的 p0-4-audit 偵測到原先「所有平台只能有一個網址」的規則衝突。共用 guard 改為嚴格解析 DEBUG 模擬器／WOUND_LITE／醫療版三個分支，逐項對照操作手冊的發布設定表；Lite 與 Android 仍須相同，demo 必須獨立。未知條件、分支外提前返回、localhost Release、缺少或重複文件設定、CI 未涵蓋檔案等都會失敗。

本機 guard 7/7 通過，13/13 個記憶體內變異均被抓到，沒有改寫被驗證的原始碼。Mac 的 owner_guard 明確回報 1 個跨所有權檔案：engineering/phase2/test_backend_url_parity.py；不是所有權檢查通過。本次依 Jack 在此協作中「授權由 Mac 這邊進行檢查並提交」的指示，限定此共用網址 guard 例外提交；沒有更動 owner_guard 或後端程式，後端部署授權範圍亦未擴大。
