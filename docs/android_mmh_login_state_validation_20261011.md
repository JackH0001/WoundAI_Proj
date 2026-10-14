# Android MMH 登入狀態與操作修正（2026-10-11）

版本：1.0-mmh-validation-r4（26）。已在 Alldocube iPlay 60 mini Pro 保留資料安裝及啟動。

## 修正

- Google 門禁驗證有效時顯示「切換 Google 帳號」與登出按鈕，未登入不顯示登出；畫面開著時每秒核對本機 session 有效期限，到期改為登入提示。這不是每秒向 Google 或後端發出網路請求。
- WoundAI 明示尚未登入／帳密已儲存待驗證／驗證中／已登入與角色。只有目前驗證成功才顯示「登出 WoundAI 帳號」。
- 合併「儲存」與「連線測試」為「儲存並登入」，一次完成安全保存、帳號登入與後端健康檢查。進行中停用帳密輸入、Google 切換、重複送出及畫面返回按鈕。
- 重返設定頁自動使用既有加密帳密核對登入，驗證中明確顯示等待；不以存在儲存帳密就推定已登入。成功後恢復角色／主控台入口。
- 修改帳號、密碼或後端位址立即清除畫面的舊驗證結果；改帳號或位址且未輸入密碼時，不會將舊帳號密碼送到新帳號／新伺服器。
- 未登入但有儲存帳密時，提供「清除已儲存的帳密」而非誤稱登出。登出不刪本機量測紀錄。
- 將設定頁中 APK／後端技術歷史說明改為使用者需要的登入、角色及帳密保存說明。

## 保留的安全政策

Google token 只在 App 記憶體中；更新、重啟、授權到期仍需重新登入 Google。依既有 MMH 帳號隔離政策，重新登入、切換或登出 Google 會清除本機 WoundAI 帳密，介面已明示需再次輸入。此輪沒有新增永久 token、擴大 OAuth/IAM 或變動雲端流量。

## 驗證

- JVM 52/52：InstitutionSession 20、AnnotationReceipt 23、ReviewTrainingConsent 9。
- 平板 Compose 11/11：登入按鈕／狀態 3、設定頁 2、紀錄預覽與收合 4、送件等待與保存狀態 2。
- APK 建置、保留資料安裝、版本核對及啟動通過。owner_guard/mac 與 git diff --check 通過。
- UI 測試使用隔離狀態，沒有輸入真實帳密或送出研究資料。使用者於 2026-10-11（台北）回報「成功，但會需要等一點重登入時間」，確認真實 Google+WoundAI 登入及返回設定後恢復登入狀態、不需重填密碼。此結果為使用者實機回報，與自動化 UI 測試分開記錄。

## 尚未在本輪修正

研究資料時間軸補送的模型／品質溯源缺漏，以及未改遮罩時修改像素計數漂移，見 [r3 雲端驗證](android_mmh_review_ui_validation_20261011.md)。該次已確認原件未覆寫、修訂保存成功，但非完全相同 payload 去重驗收。此輪只更動登入介面，沒有更改研究資料或部署後端。

## 證據

- [測試與 APK 摘要](/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-institution-evidence-20261007/mmh-iap-update-20261011/android-login-r4/summary.json)
- [平板 11 項測試結果](/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-institution-evidence-20261007/mmh-iap-update-20261011/android-login-r4/device-tests.log)
- [建置紀錄](/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-institution-evidence-20261007/mmh-iap-update-20261011/android-login-r4/build.log)

## 實機回饋：返回設定仍有等待

目前每次進入設定會依序重新登入、讀取健康狀態及佇列統計，整段完成前均使用登入等待狀態。使用者確認成功，但感受到等待；本輪未量測秒數，不能斷言延遲來自冷啟動。

後续改善：帳號驗證成功即呈現已登入及角色，服務健康／佇列讀取使用各自進度，不讓非登入請求延長「登入中」提示。若採短期 session 重用，必須绑定 Google session、帳號與伺服器；到期、登出、切換身分及 401/403 立即失效，不以儲存帳密取代驗證。本輪僅記錄驗收與改善項目，沒有再次改版或要求使用者重登入。

後續本機候選已區分「登入驗證」與「已登入、讀取雲端狀態」，驗證及未部署限制見 [候選修訂溯源報告](android_mmh_provenance_validation_20261011.md)。平板仍為本頁已驗收 r4。
