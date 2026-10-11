# MMH Play AAB 拒絕原因與修正（2026-10-11）

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
