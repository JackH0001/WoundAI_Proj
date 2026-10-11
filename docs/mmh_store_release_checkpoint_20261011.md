# MMH 商店發布檢查點（2026-10-11）

## 最新狀態（2026-10-12）：Play 已接受，尚未啟用內測

ARCore 修正版已由使用者重新簽署並上傳，Play 接收版本 26（1.0-mmh-internal）。獨立簽章驗證 1024 個 payload 全部通過，內容與指定 unsigned AAB 完全一致。新版 signed AAB SHA-256 為 `35b352432d3a4ae06cea3eb02ec54a0824df7dc6ac05199566240114437308aa`。

Play 審查預覽當時為 0 錯誤、3 警告（尚無測試者、缺少混淆對照檔、缺少原生 debug symbols）。之後已儲存僅含 Jack 本人的 MMH Synthetic QA 名單並選為此內測群組；尚未重讀版本頁確認警告數是否下降。R8 未啟用；不填造混淆對照檔。尚未按「儲存並發布」，也尚未驗證 Play 安裝版。

使用者提供 Play App Signing SHA-1：`DC:66:73:BD:75:F6:E2:0B:0E:3C:6E:22:7F:78:FB:F8:1E:C1:19:B9`，不是 upload key 指紋。Cloud 現有清單僅有 Android Validation、iOS、IAP Web 三個 MMH OAuth client。已準備新的 Android Play Internal 表單，套件 `com.woundai.app.mmhps20261007`，綁定此 SHA-1；尚未建立，等待新增登入用戶端授權。未修改 IAM、IAP 或其他 OAuth client。

參照：[Play／簽署驗證摘要](/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-institution-evidence-20261007/mmh-iap-update-20261011/android-local-signing/arcore-fix/summary.json)、[待建立 OAuth 表單](/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-institution-evidence-20261007/mmh-iap-update-20261011/android-local-signing/arcore-fix/play-oauth-prepared.png)、[已儲存測試名單](/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-institution-evidence-20261007/mmh-iap-update-20261011/android-local-signing/arcore-fix/play-testers-saved.png)。

以下較早段落保留當時狀態；目前待辦為 OAuth 建立、內測啟用，以及 Play 安裝後雙登入／上傳驗收。


此文件依各階段追加證據，以下最新進度優先於先前登入頁檢查點；非商店上架完成證明。目標仍包含 iOS TestFlight 外部測試、Android Play 內測實際啟用及完整帳號權限管理介面。

## 更新：MMH iOS Build 33 已上傳（2026-10-11 19:08 台北時間）

使用者完成 Xcode 帳號登入後，隔離環境仍有 Keychain/profile 讀取失敗；使用獲准的 macOS 簽署服務重試後 `EXPORT SUCCEEDED`，未要求重填帳密、未修改憑證或描述檔。

- 產物 `/private/tmp/mmh-33-appstore-export/WoundAIInstitution.ipa`，6,440,547 bytes；匯出產物 SHA-256 `f05600d518bbae3bba5112476bcef60e0cfc71517a6f38f448271b8a03c56654`。
- 套件、版本1.0、build33、團隊LY2F24ZM68、application-identifier、get-task-allow=false、無裝置清單、非enterprise 共8項核對通過；`codesign --verify --deep --strict` 通過。
- 接續以同 archive 的 App Store Connect upload 匯出流程上傳；`mmh-33-appstore-upload.log` 回報 Upload succeeded / Uploaded package is processing。上傳流程可能重新打包，前述 SHA-256 僅代表本機 export 產物，非宣稱 Apple 伺服器保存位元雜湊。
- ASC 專屬 App 6821572870 已顯示 **1.0(33)、處理中、Oct 11 2026 7:08 PM**。沒有把上傳成功當成 Beta Review 通過。
- 已建立手動分發的內部群組 `MMH Synthetic QA`（a0ccdf15-2dd3-4153-839c-86dab9f9a894），加入使用者先前指定的 jack.hou@gmail.com；當時0個建置，尚無可安裝版本。未新增其他測試者或雲端入口權限。
- Android 使用者指定現有 `woundai-upload.jks`，已確認檔案存在；尚待對應簽署設定／本機輸入既有密碼。未複製或輸出金鑰內容。Play SHA-1 複製按鈕回報已複製，但工具剪貼簿仍空，已提供按鈕位置請使用者回覆公開指紋，不以 SHA-256 猜算 SHA-1。

紀錄：`/private/tmp/mmh-33-export-retry-authorized.log`、`/private/tmp/mmh-33-appstore-upload.log`；群組畫面 `/private/tmp/mmh-testflight-qa-created.jpg`。後續仍需處理完成、加入建置、內部驗收、外測必要資料／可審查登入路徑及 Beta Review；Android 簽署、Play release OAuth 與內測發布仍未完成。

同輪後續：Apple 已處理完成；Build 33 從「準備測試」加入上述群組後，讀回 **1位測試人員、1個建置版本、1.0(33)「正在測試」**。已請使用者由 TestFlight 安裝並核對Google／WoundAI登入；尚未取得商店分發版實機驗收回覆。**內測已啟用，外測尚未提交／啟用**。持久證據目錄 `woundai-institution-evidence-20261007/mmh-iap-update-20261011/store-and-members/` 內新增 `build33-upload-verification.json`、`mmh-testflight-build33-testing.jpg` 及匯出／上傳 log。

## 外部測試送審缺口：可實際操作的審查登入路徑

2026-10-11 接續核對 ASC「測試資訊」：Beta 描述、聯絡資訊、隱私政策及審查登入資訊尚未填妥，未提交 Beta Review。目前內部群組正在測試不代表外部審查已通過。

程式核對 `iOS/WoundMeasurementApp/Core/AppSettings.swift:57`：機構 target 固定使用 Info.plist 的 MMH 入口，拒絕沿用 demo／舊正式位址。現有 `dr01` 是本人合成驗收帳號，不是已授權提供給 Apple 的共用審查帳號。只在備註填 WoundAI 帳密，不能使尚無 MMH Google 門禁授權的 Apple 審查者完成登入。

送審前須完成以下可重現驗收，不能勾選「不需登入」來迴避：

1. 指定使用者持有、專供合成審查且可撤回的身分／測試環境方案；不得提供個人 Google 或管理者帳密。新增門禁成員及 OAuth 測試者時列明實際範圍，另確認授權。
2. 從無既有登入狀態的裝置，完成 Google 門禁、最低必要 WoundAI 角色、合成量測、醫師確認、保存／重送與收據核對；檢查 Google 額外驗證是否會阻止審查者使用。未完成此驗證前，不宣稱專用 Google 帳號一定能解決審查。
3. 核對只能使用核准的合成資料範圍，並記錄審查後撤權／停用方式；既有機構私有服務不改為公開。
4. 審查資料需如實解釋兩層登入、拍攝標靶及硬體條件。若無法提供可用審查帳號，依 Apple 規則先取得完整示範模式方案的同意，再建置及驗證；目前 build 33 沒有這項模式，不能以文字備註假裝已有。

以上是送審準備清單，未新增帳號、IAM 或審查模式，未向 Apple 傳送密碼。官方依據：[提供測試資訊](https://developer.apple.com/help/app-store-connect/test-a-beta-version/provide-test-information)、[App Review Guidelines（Before You Submit、2.1、2.2）](https://developer.apple.com/app-store/review/guidelines/)。

## 先前階段：商店 App 已建立，簽署與發布當時尚未完成

使用者已完成兩個商店登入，並自行完成 Play 建立表單的政策／出口聲明。本輪確認：

- Apple explicit Bundle ID `com.woundai.app.mmhps20261007` 已登記，團隊 LY2F24ZM68。App Store Connect **WoundAI MMHPS / 6821572870** 已建立，SKU `WOUNDAI-MMHPS20261007-IOS`、繁體中文、有限存取（頁面選中 Jack Hou）。TestFlight 顯示尚無建置；沒有誤用既有 WoundAI App。
- Play **WoundAI MMHPS / 4976242294543814439** 已建立，開發者帳戶 8894212153419089965，套件相同。內部測試已建立空白草稿 1，沒有上傳 AAB 或推出版本。
- Play 畫面提供 App Signing SHA-256 `97:12:29:80:03:C2:3B:4D:2B:A5:D6:98:0B:05:27:22:62:6A:C2:33:5B:17:52:52:26:3F:5E:F9:D6:2D:10:ED`；上傳金鑰指紋尚待首次 AAB。SHA-1 剪貼簿／憑證下載工具沒有取得可靠結果，已請使用者提供公開指紋，未猜值或建立 release OAuth。
- 已知 WoundAI checkout 沒有 release keystore 設定；已請使用者指定既有路徑或確認建立專用 upload key，沒有使用 debug 金鑰冒充發布金鑰。
- MMH iOS 1.0(33) Release `ARCHIVE SUCCEEDED`；自動匯出 App Store Connect IPA 失敗：`No Accounts` 及缺少 App Store provisioning profile。可用本機身份只有 Apple Development。已請使用者在 Xcode Apple Accounts 登入；瀏覽器登入不等於 Xcode 發布工作階段。尚未 IPA 上傳或 Beta Review。
- 封存來源比對找到暫存副本的手冊落後，已同步最新手冊後重建；隔離的 `_quarantine` 不在此 target 中。不因部分建置成功宣稱商店內測完成。
- 成員管理網頁與伺服器介面本機實作完成，核心＋HTTP **50/50** 測試通過。本機合成 adapter 完成開通／撤除／讀回、保護救援帳號、操作後需刷新、瀏覽器 error 0。尚缺正式雙重驗證 adapter、audit sink、帳號變更與專用 runtime，未改雲端 IAM。

參照：[本輪摘要與畫面證據](/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-institution-evidence-20261007/mmh-iap-update-20261011/store-and-members/summary.json)。

## 先前檢查點（下列登入待辦已由上述結果取代）

| 項目 | 當輪可確認狀態 | 下一個必要證據 |
| --- | --- | --- |
| iOS MMH | project.institution.yml：com.woundai.app.mmhps20261007、1.0(33)、Release archive scheme；使用者回報已登入 | Distribution archive/export、ASC 對應 App、build 處理及 Beta Review／外測啟用 |
| Android MMH | mmhValidation 是 debug 簽章的獨立 .mmhps20261007.validation 套件；r5 修正僅完成本機建置測試。新增 mmhInternal 已完成未簽署 APK／AAB 建置 | Play app identity／signing fingerprint、release OAuth client、簽署 AAB、簽署版實機驗收、實際內測啟用 |
| Android internalTest | 仍繼承一般 release 舊正式後台、ALLOW_CLINICAL_GALLERY_EXPORT=true | 不能直接將此 build 當 MMH Play 內測版提交；須另行固定 MMH 私有入口與輸出政策 |
| 帳號權限管理 | 新增獨立 MMH IAM 操作核心與固定資源 REST adapter，31/31 故障注入測試通過；未部署 | 實際介面、雙重身分／CSRF 接線、正式稽核與管理 runtime、限定 IAM 授權及授權／撤權端對端驗證 |

本輪取得既有 Play Console 分頁，但 extension/CDP 連線逾時，未讀到現在的應用程式清單。Codex 瀏覽器新開 App Store Connect 回到登入頁（authResult=FAILED），已請使用者在 Apple 頁面登入，不索取帳密。不依先前「已登入」訊息推定目前工作階段仍有效。

Android Google OAuth 現有授權及實機驗證限 validation package 與其已登記 SHA-1；不能推定涵蓋未來 Play App Signing 憑證。新增正式 OAuth client／入口權限前須有具體 package 和 Play 提供的 signing certificate，再依既有授權範圍處理。

沒有在此輪建立商店 App、上傳 AAB／IPA、提交審查或新增雲端 IAM。WoundLite 工作仍在目標一後續範圍，沒有因 MMH 局部通過而宣告完成。

## Android MMH 發布配置本機驗證

**後續更正（23:48 台北）：** 以下為當時的本機檢查，ARCore optional 的判準不完整。實際 Play 上傳仍被拒絕，因沒有 ARCore SDK 卻保留 `com.google.ar.core` metadata。已移除未實作的宣告並重建；新 AAB 尚待重新簽署及 Play 再驗證。詳見 [Play 拒絕原因與修正](mmh_play_arcore_rejection_20261011.md)。

新增 `mmhInternal`（1.0-mmh-internal／26），套件固定 `com.woundai.app.mmhps20261007`，與現有 validation 安裝分開。繼承 release、停用除錯，沿用 MMH IAP 與合成驗證限制，固定既有 candidate 私有網址；不自動開啟臨床影像共用相簿輸出。Manifest 停用備份及明文 HTTP。現有量測流程沒有使用 ARCore Session，故此 MMH 配置將 ARCore metadata 改為 optional，避免不必要的裝置篩選。

- `assembleMmhInternal` 與 `bundleMmhInternal`：BUILD SUCCESSFUL（41 秒），包含 release vital lint。
- 產物 Manifest／生成 BuildConfig：10 項檢查通過，涵蓋套件、版號、除錯、備份、明文連線、ARCore、IAP、共用相簿與固定網址。
- APK 六個 arm64-v8a 原生函式庫：ELF 及 ZIP 16 KB 對齊通過；AAB 同六個函式庫 ELF 16 KB 通過。AAB 不是最終 Play 分割 APK，未將這項檢查宣稱為 Play 端產物驗收。
- 本 checkout 沒有 release keystore.properties；APK 的 apksigner 回報缺少簽章 manifest，AAB 的 jarsigner 明確回報 unsigned。這是未簽署檢查產物，不能提交商店，沒有以 debug 金鑰冒充發布簽章。
- 本輪沒有將此新套件安裝到平板。現有使用者資料及已安裝 validation App 保留；不將既有 validation OAuth 成功當成新套件登入成功。

Play Console 在 Codex 瀏覽器也回到 Google 登入頁，已交接使用者登入；App 內 Google 登入與商店管理中心工作階段不同。尚未讀到 Play 套件／簽章，未重問未完成的登入要求。

參照：[本機建置與產物檢查摘要](/Users/Jack.Hou/Documents/Codex/2026-06-28/woundai-institution-evidence-20261007/mmh-iap-update-20261011/android-mmh-internal-profile/summary.json)。此證據不取代 release 簽章、實機登入／上傳或商店內測啟用驗收。
