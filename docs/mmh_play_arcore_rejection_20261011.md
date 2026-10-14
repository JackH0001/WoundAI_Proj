# MMH Play AAB 拒絕原因與修正（2026-10-11）

## 內測已實際啟用（2026-10-12 00:11 台北）

依使用者已授權的 MMH Play 內測發布目標，核對僅有 MMH Synthetic QA（Jack 本人）被選取後，在版本 26 的預覽頁完成「儲存並發布」與確認。Play 返回群組頁，明確顯示 **有效／最新版本 26（1.0-mmh-internal）／提供給內部測試人員／發布日期 10月12日 上午12:11**。因此此版本已不是草稿。

發布前檢查為 0 錯誤、2 警告：缺少去混淆檔（未啟用 R8）及原生 debug symbols。測試者警告已消失。群組的參加連結按鈕已啟用：[MMH Play 內測](https://play.google.com/apps/internaltest/4700876842238129583)。Play 提示設定與審查尚未完成，因此顯示暫名 `com.woundai.app.mmhps20261007 (unreviewed)`；不宣稱已通過正式商店審查。

**功能驗收仍未完成**：Play 專用 OAuth 用戶端已填好但尚待新增授權。商店簽章版的 Google／WoundAI 雙登入及合成標註收據必須另行驗證，不能用原 validation App 的結果替代。本輪沒有要求使用者刪除既有 validation App 或本機紀錄。

TestFlight 測試資訊仍缺審查用帳號／密碼；「需要登入」維持勾選，儲存 disabled，未提交 Beta Review。MMH 隱私說明草稿已交負責人核閱並請求發布授權，未發布。

證據：[內測啟用畫面](/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-institution-evidence-20261007/mmh-iap-update-20261011/android-local-signing/arcore-fix/play-internal-active.png)、[發布與套件摘要](/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-institution-evidence-20261007/mmh-iap-update-20261011/android-local-signing/arcore-fix/summary.json)。

以下為較早檢查點。


## 最新狀態（2026-10-12）：Play 已接受，尚未啟用內測

ARCore 修正版已由使用者重新簽署並上傳，Play 接收版本 26（1.0-mmh-internal）。獨立簽章驗證 1024 個 payload 全部通過，內容與指定 unsigned AAB 完全一致。新版 signed AAB SHA-256 為 `35b352432d3a4ae06cea3eb02ec54a0824df7dc6ac05199566240114437308aa`。

Play 審查預覽當時為 0 錯誤、3 警告（尚無測試者、缺少混淆對照檔、缺少原生 debug symbols）。之後已儲存僅含 Jack 本人的 MMH Synthetic QA 名單並選為此內測群組；尚未重讀版本頁確認警告數是否下降。R8 未啟用；不填造混淆對照檔。尚未按「儲存並發布」，也尚未驗證 Play 安裝版。

使用者提供 Play App Signing SHA-1：`DC:66:73:BD:75:F6:E2:0B:0E:3C:6E:22:7F:78:FB:F8:1E:C1:19:B9`，不是 upload key 指紋。Cloud 現有清單僅有 Android Validation、iOS、IAP Web 三個 MMH OAuth client。已準備新的 Android Play Internal 表單，套件 `com.woundai.app.mmhps20261007`，綁定此 SHA-1；尚未建立，等待新增登入用戶端授權。未修改 IAM、IAP 或其他 OAuth client。

參照：[Play／簽署驗證摘要](/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-institution-evidence-20261007/mmh-iap-update-20261011/android-local-signing/arcore-fix/summary.json)、[待建立 OAuth 表單](/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-institution-evidence-20261007/mmh-iap-update-20261011/android-local-signing/arcore-fix/play-oauth-prepared.png)、[已儲存測試名單](/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-institution-evidence-20261007/mmh-iap-update-20261011/android-local-signing/arcore-fix/play-testers-saved.png)。

以下較早段落保留當時狀態；目前待辦為 OAuth 建立、內測啟用，以及 Play 安裝後雙登入／上傳驗收。


## 結論

使用者已以既有 upload key 完成簽署。獨立驗證全部 **1024 個 payload 簽章**通過，且每個 payload 都與指定的 unsigned AAB 完全一致。簽署正確不代表套件符合 Play 的發布要求。

已將該 AAB 上傳至 WoundAI MMHPS 的內部測試草稿。Play 拒絕原因：已指定 `com.google.ar.core`，但缺少 `com.google.ar.core.min_apk_version`。版本名稱與繁中合成測試說明已儲存，草稿沒有可发布的有效 AAB；未啟用內測。

## 修正

目前 Android 有效相機程式使用 CameraX／Camera2；ARCore Gradle dependency 已註解，深度模組檔案為 `.kt.disabled`。因此移除 main manifest 的 ARCore feature 與 required metadata，以及 mmhInternal 的 optional override。不填造 minimum APK version、不加入未實作的 ARCore SDK；相機權限、原有 CameraX 流程及 MMH IAP 設定維持。

Google 官方對 AR Required／Optional 的說明要求配套 SDK 與執行期檢查；`optional` 並不表示可以留下沒有 SDK 的 AR 宣告。參照 [Enable AR in your Android app](https://developers.google.com/ar/develop/java/enable-arcore)。先前本機檢查把 optional 視為通過，未涵蓋這個商店檢查，已在發布進度文件補正。

## 驗證與產物

- `:app:testMmhValidationUnitTest :app:bundleMmhInternal`：BUILD SUCCESSFUL，21 秒。單元測試 task 為 UP-TO-DATE，沒有冒稱本輪重跑 64 項測試。
- 直接解析最終 AAB 的 protobuf manifest，核對 package `com.woundai.app.mmhps20261007`、versionCode 26、versionName `1.0-mmh-internal`、關閉備份及明文 HTTP、未啟用 debug、無 ARCore 宣告。
- 同一檢查器對 Play 拒絕的舊 AAB 確實拒絕，對重建新 AAB 通過。
- 原已簽署 AAB SHA-256：`599e0ab35dcb800ad14c54ef927b4e91b49732e69c6613e569409f725ca13642`。
- 新 unsigned AAB SHA-256：`14034bca98869e411649f20759839adce8d3a186cade16405b044e6928d14105`。

新產物和專用簽署 launcher 放在獨立 `arcore-fix/` 目錄，保留旧產物與金鑰。因內容已變更，需使用者於本機重新輸入既有密碼簽署；代理未取得或保存密碼。新版尚未簽署／上傳，不能宣稱已通過 Play。版本碼 26 尚未有有效套件入庫，先保留此值；若 Play 後續表示已占用，再依實際回覆升版。

## 發布仍待完成

新 AAB 簽署與 Play 接收、Play App Signing SHA-1／對應 release OAuth client、指定測試者與內測啟用、商店安裝後雙登入及同筆標註收據驗收。已有 validation App 的成功不取代以上驗收。

證據：[簽署獨立核對](/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-institution-evidence-20261007/mmh-iap-update-20261011/android-local-signing/independent-signature-check.json)、[Play 拒絕文字記錄](/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-institution-evidence-20261007/mmh-iap-update-20261011/android-local-signing/play-upload-rejection.json)、[新 AAB 與 manifest 檢查摘要](/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-institution-evidence-20261007/mmh-iap-update-20261011/android-local-signing/arcore-fix/summary.json)。
