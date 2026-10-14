# MMH 具名 Google 入口：兩端契約與分階段設定

2026-10-10，Jack 已選擇測試者使用具名 Google 帳號，再登入 WoundAI 医師／護理角色。此選擇未被解讀為立即改動雲端 IAM 或公開服務。

## 目前實作與邊界

新增 iOS InstitutionIAPTransport：同一實例綁定一個 Google subject、audience 與 HTTPS origin；Google SDK adapter 必須先更新 token。傳輸層保留 WoundAI Authorization，將 Google ID token 放 Proxy-Authorization。缺設定、錯站點、錯 subject/audience、過期或控制字元 token 在 HTTP 前拒絕。移除 Cookie 與呼叫端自帶 X-Serverless-Authorization；拒絕所有重導向，不自動重送寫入。

登出永久失效該 transport；刷新期間登出不得再發送，已在途回應不得恢復登入狀態。已送到後端的請求不能由本機登出撤銷，需依服務端請求結果／撤回機制處理。離線 token 欄位檢查不是密碼學驗章；真正驗章與入口白名單由 IAP 執行。

BackendClient 可注入 transport，包含登入與 health 在內的醫療 API 都經同一 send 路徑；MMH build 若未注入則拒絕網路，不退回未保護入口。一般醫療版與 Lite 未注入時維持原 transport。Google SDK、登入 UI、實際 OAuth client 與 token 更新 adapter 已在下述後續階段接上；服務 IAP／入口白名單與實機登入尚未驗收，因此不是可發行版。

## iOS／Android 共用契約

| 項目 | 規則 |
| --- | --- |
| Google 身分 | 每位測試者獨立；入口權限不等於醫師資格 |
| Google 標頭 | Proxy-Authorization: Bearer ID_TOKEN；audience 綁定 IAP 資源 client |
| 應用標頭 | Authorization: Bearer APP_JWT；仍由後端驗 org／role／帳號有效狀態 |
| 登入順序 | Google → health 入口確認 → WoundAI 帳密 → 角色與機構核對 |
| 帳號切換 | 先清除應用 session、取消／隔離在途回應，再建立新 Google subject 的 client |
| 目的地 | 固定 HTTPS origin，API 路徑；不將兩層憑證送往重新導向位置 |
| 錯誤與重試 | 不把應用 403 當 Google token 過期；不盲目重播標註／撤回／深度寫入 |
| 密碼與憑證 | 不記錄、不寫版本庫；App 不含服務帳號私鑰或 OAuth web client secret |
| Web console | 瀏覽器有自己的 IAP 登入 session；App token 不代替瀏覽器入口認證 |

Android internalTest 應按此契約實作 Google 身分 adapter 與 OkHttp interceptor；本輪未修改 Android、未聲稱兩端已完成對齊。Android OAuth client 需實際 package ID 與該特殊版簽署憑證 SHA-1，不能拿一般版或 debug 值替代。

## Google 官方條件與實際查核

- 2026-10-10 唯讀 enabled services 查詢：run.googleapis.com 已啟用，iap.googleapis.com 未列於已啟用服務。
- IAP 直接整合 Cloud Run 可用於無 organization 的專案，但需要外部／自訂 OAuth 設定；初次設定可能需 Console。IAP 是服務層設定，影響同一服務所有 revisions，不能冒稱只變更 candidate tag。
- 原生 App 程式化登入需 OAuth client 及允許的 client 設定；預設 Google-managed client 不足。Proxy-Authorization 可讓應用保留自己的 Authorization，仍需真實入口驗收。

參照：[Cloud Run IAP](https://docs.cloud.google.com/run/docs/securing/identity-aware-proxy-cloud-run)、[IAP 手機驗證與雙標頭](https://docs.cloud.google.com/iap/docs/authentication-howto)、[iOS Google Sign-In 設定](https://developers.google.com/identity/sign-in/ios/start-integrating)。

## 下一個可獨立授權的設定階段

先只準備 OAuth，不把 IAP 開到 MMH：

1. 固定 project woundai-jackh001／421209514056，啟用 iap.googleapis.com。
2. 先讀既有 OAuth branding／audience，不覆寫共用設定；若可用則沿用，若會影響既有 App 則停止回報。
3. 建立 MMH 專用 web（IAP）及 iOS OAuth client，iOS bundle 固定 com.woundai.app.mmhps20261007。Web redirect 使用 Google 指定的 iap.googleapis.com OAuth handleRedirect。
4. 使用 External/Testing，初期測試者名單擬僅 Jack（jack.hou@gmail.com），仍需本階段授權確認。不得發布 OAuth 至正式、擅加其他測試者或要求不必要的 Google 資料 scope。
5. iOS 只保存公開 client ID／reverse scheme／audience。Web secret 不寫入 repo、對話、App 或測試證據；如需服務端保存另列限定資源操作。

此階段不部署映像、不改 IAM、不啟用任何服務的 IAP、不改 MMH 流量，也不影響舊正式或 demo。OAuth 配置讀回後完成 SDK／UI 與 refresh/logout 測試。

之後啟用 MMH IAP 必須另有可覆核的變更：專案 IAP service agent 僅取得該服務 run.invoker；具名人員僅取得該 IAP 資源入口權。需要先更新原本拒絕所有 service-level binding 的部署守門，精確允許該 agent，拒絕其他擴權。核對 service etag、流量、image digest、API token 與瀏覽器登入／回復方案，才能啟用。不得用放行 allUsers 解決手機連線。

## 驗收範圍

離線 XCTest 驗證標頭、目的地、生命週期、實際 BackendClient health／login 與深度上傳既有行為。尚不證明真實 Google 登入、IAP 驗章、白名單撤權、雙標頭真實轉送、機構帳號與 Google 身分的稽核關聯已完成。現階段保持合成資料與未驗證配準隔離，IRB 尚未送件。

## 本輪實測（2026-10-10）

- XCTest 17/17：新增傳輸／登入生命週期 12 項，既有深度沿革／multipart 5 項；零跳過。
- MMH 與 Lite Release iPhoneOS 編譯皆成功；未簽署、未安裝、未發布。
- owner_guard --platform mac 通過，4 檔均在本次範圍；git diff --check 通過。
- 成功登入回應晚於 logout 的競態由 authGeneration 拒絕；測試用確定性的延遲回應控制，不依賴睡眠時間。
- 證據：woundai-institution-evidence-20261007/mmh-iap-client-20261010，包含 verified.xcresult、verified-tests.log、兩個 Release log。
- 本輪未執行真實 Google OAuth／IAP 連線，也沒有變更雲端設定；不把離線假 token 測試計為雲端驗章成功。

## OAuth 準備階段完成（2026-10-10）

Jack 已另外明確同意 Google API 服務使用者資料政策，於 Console 完成初始化。初始化前總覽及 client 頁皆顯示尚未設定；未覆寫既有 OAuth 品牌。IAP API 已啟用，未啟用服務 IAP、未改 IAM 或流量。

- 品牌：WoundAI MMHPS20261007；External／Testing。
- 唯一測試者：jack.hou@gmail.com（重新載入 audience 頁確認 1 位）。
- web client：`421209514056-c62hg9vv5p7g2l96n2efig269kcsads6.apps.googleusercontent.com`。
- web redirect：`https://iap.googleapis.com/v1/oauth/clientIds/421209514056-c62hg9vv5p7g2l96n2efig269kcsads6.apps.googleusercontent.com:handleRedirect`；重新開啟 client 頁核對。
- iOS client：`421209514056-j72bu49ou8kh31s61na1v4t817lecsmq.apps.googleusercontent.com`。
- iOS bundle：`com.woundai.app.mmhps20261007`；Apple Team：`LY2F24ZM68`。
- iOS URL scheme：`com.googleusercontent.apps.421209514056-j72bu49ou8kh31s61na1v4t817lecsmq`。
- OAuth 資料存取權三類 scope 清單均為空，未新增資料存取 scope；Firebase App Check 未啟用。
- web secret 未讀取、下載或保存。之後服務端接線需另行安排安全輸入／保存；不可將它加入 App 或版本庫。

本輪只有設定讀回驗證，沒有重跑 XCTest、建置或宣稱真實 Google 登入成功。MMH 原 revision 00001-hqd 仍有 100% 流量，c9a4b9e49f414 仍只有 candidate tag（預設 0%）；服務 operation-id 未變。

證據：`woundai-institution-evidence-20261007/mmh-oauth-preparation-20261010/validation.json`。後續先完成 SDK／UI 與 token 更新／登出驗證，再提出服務層 IAP、精確入口 IAM 及 programmatic client allowlist 的可覆核變更。

## Google SDK 與 App 整合（2026-10-10，OAuth 準備後）

只在 WoundAIInstitution 連結官方 GoogleSignIn／GoogleSignInSwift 9.0.0。一般醫療版與 Lite 不連結這兩個產品。公開 client ID、server audience 與回呼 scheme 僅放 InstitutionInfo.plist；未放 web secret。Google Sign-In 的基本帳號身分仍需在後續 App Privacy／隱私說明中核對，不能把 OAuth client 建立成功當作上架審核已完成。

設定頁提供官方 Google 登入按鈕、目前 Google 帳號與登出。順序為 Google 身分 → WoundAI 帳號。每次明確登入先清除先前 App 帳密與舊傳輸；不在 App 啟動時自動還原並重送前一位使用者帳密。SDK refreshTokensIfNeeded 取得的 ID token 必須核對實際 sub、aud、issuer、exp，不以配置值代替 token 內容；這些本機檢查不取代 IAP 密碼學驗章。

後端客戶端建立時擷取當時 transport，之後不得切換到另一個 Google subject。Google 登出清除共用入口、永久撤銷 provider／transport；更新途中才回來的 token 不得恢復登入。一般 App JWT 與 Google token 仍使用分開的 Authorization／Proxy-Authorization 標頭。

InstitutionInfo.plist 的 WoundAIInstitutionBackendURL **仍留空**。Google 登入可以進行，但登入成功也不會啟用後端傳輸；UI 顯示入口準備中並禁止送出 WoundAI 帳密。只有完成後續服務 IAP、programmatic client allowlist 與機構 IAM 驗收，才把核准 origin 寫入特殊版。不得填公開舊正式／demo 位址來迴避入口。

### 可重現建置與測試

- 執行 `iOS/generate_institution_project.sh`；此腳本用 XcodeGen 產生專案並放入 `iOS/Dependencies/Institution.Package.resolved`，固定本輪驗證的 8 個套件版本／revision。
- xcodebuild 加 `-onlyUsePackageVersionsFromResolvedFile`，拒絕自動升級依賴。
- MMH 單元測試 target：WoundAIInstitutionTests；UI target：WoundAIInstitutionUITests。可分開 `-only-testing:` 執行並加 `-parallel-testing-enabled NO`，避免本輪 Xcode 多 target 排程收尾異常。
- UI 測試只驗證設定畫面，不提交 Google 登入，不輸入或讀取使用者密碼。

本輪通過項目與結果保存在 `woundai-institution-evidence-20261007/mmh-google-sdk-20261010`。未宣稱真實 Google OAuth、IAP 驗章、具名使用者入口撤權、裝置深度配準或 TestFlight 發布完成；也沒有更動雲端 IAM／IAP／流量。Android 仍需依同一雙憑證契約另行整合，不能使用 iOS client 作為 Android client。

## 實機 Google 登入與入口變更準備（2026-10-10，SDK 整合後）

本輪來源 `c30497313ccfaa10ade8707c4d66c17babd601b6`。Debug 真機簽署建置成功，codesign deep/strict 驗證通過，Team LY2F24ZM68，provisioning profile 包含 J-iP16PM，效期至 2027-08-11。成功安裝並啟動獨立 bundle `com.woundai.app.mmhps20261007`，顯示名稱 WoundAI MMHPS、1.0 (27)。這是 Xcode 安裝的開發測試版，不是 TestFlight 更新。

Jack 已在本輪 Google 登入提示後回覆「已登入成功」。這是使用者回報的實機驗收；未讀取或保存手機 token／密碼。App 後端 origin 仍空白，所以 Google 登入成功不等於 IAP、機構帳密或上傳驗收成功。

新增 `tools/plan_mmh_iap_entry.py`：只產生離線方案與檢查讀回，不包含雲端寫入或執行授權。固定 MMH resource/uid、已驗收映像、runtime、要求與實際流量、公開 OAuth IDs、唯一入口成員；未知設定、條件式／額外 IAM binding、公開主體及錯誤資源均拒絕。OAuth secret 只在未來安全接線步驟輸入，讀回核對 `clientSecretSha256`，不能把明文交給此檢查器。雜湊只是方案完整性標記，不是授權簽章。

本輪：入口方案測試 13/13；既有私有 revision 更新測試 18/18；create-only 部署測試 29/29。既有部署工具沒有放寬：IAP 啟用後它们仍會拒絕下一次映像部署，必須另外加入完整入口證據再更新，不能只放行一個 invoker binding。這不阻止本次僅設定既有服務入口，且避免未來重建時悄悄取消保護。

### 已讀回的變更基線

- Cloud Run v2：服務 uid `ed1bb40f-1c67-4e1a-844f-39a61146769c`，generation 2；IAP 未啟用；服務 IAM 無直接 binding。
- 映像 `sha256:72e86414797c03aac3eecc3011cc6ce9216c32aab6c88f276c406c4ce8ad5467`。
- 原 revision `00001-hqd` 100%；`c9a4b9e49f414` candidate tag、預設 0%。本次 IAP 變更不切流。
- IAP 資源 `projects/421209514056/iap_web/cloud_run-asia-east1/services/woundai-backend-mmhps20261007` GET 成功；settings 只有 name，直接入口政策只有 etag。
- 本機 gcloud 534.0.0 不支援 `iap settings --resource-type=cloud-run`；beta 元件未安裝且沒有自動安裝。改用 Google 官方資源路徑唯讀 REST，未擴大到 project-level 設定。
- 上層 IAP `iap_web`、`cloud_run-asia-east1` 政策另行讀回。專案 IAM 必須經 Cloud Resource Manager 查詢，不能把 IAP 的 `projects/NUMBER:getIamPolicy` HTTP 400 當成空政策。各既有角色的 web accessViaIAP 權限查核結果保存在本輪證據。

### 下一階段的具體變更範圍（尚未執行）

僅 `woundai-jackh001` 的 MMH 服務：

1. 執行前重新讀回 service etag/uid、要求與實際流量、template、Run IAM、IAP settings、IAP 各層政策及專案角色。任何非預期變動停止，不自動重製方案後繼續。採維護時段單一操作者；IAP settings 沒有可依賴的 etag，需緊接讀／改／讀並保留並行變動風險。
2. 僅對 MMH IAP 資源設定上述 web OAuth client 與 iOS programmatic allowlist（只含上述 iOS client）。既有 web secret 未保存且無法重新讀取；若需新增一把，只能在這個 MMH web client 下新增。由操作者在 Google 安全表單完成 secret 接線；不經對話、App、命令列參數、版本庫或測試紀錄。讀回摘要只含雜湊，確認後停用未使用的舊 secret 須明確納入此次授權。
3. MMH Cloud Run 服務層僅新增 `roles/run.invoker` 給 `service-421209514056@gcp-sa-iap.iam.gserviceaccount.com`；MMH IAP 資源層僅新增 `roles/iap.httpsResourceAccessor` 給 `jack.hou@gmail.com`。不新增專案層角色，不放行 allUsers，不給 runtime 額外權限。
4. 僅 PATCH Cloud Run `iapEnabled=true`，保留 template、映像、流量與資源限制。IAP 是整個服務的入口，會同時保護原 revision 與 candidate；不是 candidate-only 變更。
5. 讀回 exact binding、custom OAuth/secret digest/programmatic allowlist、服務 protected fields 及實際流量。先驗證無 Google 憑證被拒；Jack 瀏覽器與 App 通過 IAP；正確 Google 身分配無效 App 帳密仍被應用拒絕；兩層登入成功後僅以合成資料驗證 RGB、遮罩、深度封包與撤回。不得以 health 200 代替完整驗收。
6. App 接到已保護的 candidate origin 後重新簽署安裝，測試雙標頭、登出／重登、IAP 入口撤權及恢復。裝置端先前的純 Google 登入不能取代這一步。Android OAuth／adapter 尚未完成，本輪不宣稱 Android 入口通過。

### 回復與停止條件

未明確授權前，不執行上述新增 secret、IAM、IAP 寫入。啟用後若驗證失敗，在確認只有本次變更且無其他操作者更新的條件下，以最新 service/policy etag 回復 `iapEnabled=false` 並移除本次精確新增的兩個 binding，恢復 MMH 資源層 IAP settings 至原本未設定狀態。保留原有 private invoker 檢查，不開公開入口，也不刪除任何媒體／稽核資料。

若 outcome 不明，先讀回；不盲目重送 PATCH／新增 secret。若發現並行變更，停止自動回復並回報，不能用舊完整 policy 覆蓋。雲端入口可回復不代表已送出的資料寫入可撤銷，後者走現有撤回機制。

官方參照：[服務層 IAP](https://docs.cloud.google.com/run/docs/securing/identity-aware-proxy-cloud-run)、[IAP 資源與繼承權限](https://docs.cloud.google.com/iap/docs/managing-access)、[自訂 OAuth](https://docs.cloud.google.com/iap/docs/custom-oauth-configuration)、[IapSettings 欄位契約](https://docs.cloud.google.com/iap/docs/reference/rest/v1/IapSettings)。官方自訂 OAuth 範例的 update mask 命名與欄位參照不一致，實際接線須以 API schema 的 clientId/clientSecret 欄位或 Console 支援表單核對，不能直接複製示例後宣稱成功。

本輪證據：`woundai-institution-evidence-20261007/mmh-device-entry-20261010`；包含建置、安裝／啟動、基線讀回、離線方案與測試結果。服務仍未啟用 IAP，沒有新的雲端 IAM／secret 寫入。

## 已授權執行後的停止點（2026-10-10：無 Organization 的 mobile allowlist）

Jack 已授權方案 `5d8373c3ec9aae21662a590100cd244d4e610987593c2b069d644b97df185d0a`，並親手在既有 MMH web client 新增密鑰、填入 MMH IAP 自訂 OAuth 表單儲存。GET 已讀到正確 web client 與 `clientSecretSha256`；未取得密鑰明文。這證明設定有保存，尚不是 OAuth 登入交換成功的證據。舊密鑰尚未停用，待實際接線驗收後再由操作者處理。

Console 同時保存 `accessSettings.allowedDomainsSettings.enable=false`、`applicationSettings.attributePropagationSettings.enable=false`。檢查器只接受這兩個精確布林 false 預設，不忽略 true、0、字串 false、額外子欄位或未知控制。已加入回歸測試，且只有 web client／密鑰、缺 mobile allowlist 時仍拒絕驗收。

Google IAP service agent 原先不存在；依官方 `services.generateServiceIdentity` 建立 `service-421209514056@gcp-sa-iap.iam.gserviceaccount.com`，操作前後專案 IAM 完整讀回相同，沒有新增專案角色。服務層 run.invoker 及 IAP 使用者入口授權尚未套用。

向本文件指定 MMH IAP 資源 PATCH 唯一 iOS client 的 `programmaticClients` 時，Google 回 HTTP 400：

> Programmatic Oauth Client … does not belong to an organization.

原先未保留錯誤細節，曾核對 update mask 前綴；採官方範例 `iapSettings.accessSettings.oauthSettings.programmaticClients` 後仍得到上面的 Organization 拒絕。不能將此歸因於密鑰、改 URL 或 IAM 權限不足。失敗後 GET 確認設定與操作者儲存版本完全相同，沒有部分更新。

停止點：IAP=false、Run/IAP 直接政策均空、service generation 2、映像/template/要求流量/實際流量與授權基線一致。沒有執行啟用、切流、手機 URL 修改、重建安裝或 E2E 上傳。保留 custom OAuth 接線與無新授權的 IAP agent；沒有刪除或撤銷操作者剛保存的憑證。

原方案在此專案不能按原樣完成；離線檢查通過不代表 Google 接受實際設定。下一步須區分：

- 官方文件支援無 Organization 的專案使用 custom web OAuth；本次拒絕只直接證明 iOS `programmaticClients` 白名單設定失敗。
- 現行 SDK 以 web client 為 token audience，但「同專案 client、相同 web audience 是否無須 programmatic allowlist」仍未取得本專案端到端證據。不得先移除守門要求就標示成功。
- 先釐清 Google 支援的無 Organization 手機路徑，準備獨立可覆核驗證方案。若需要 Organization 遷移、其他入口服務、token broker 或擴大 IAM，均屬新範圍；不能為了繞過此錯誤直接執行。

本輪本機測試：入口方案 16/16、revision 更新 18/18、create-only 部署 29/29，共 63/63。專案 9 個角色無 web accessViaIAP 權限，IAP 兩層祖先政策均空。這些不等於手機／瀏覽器登入與雲端資料驗收。

證據：`../woundai-institution-evidence-20261007/mmh-iap-enable-20261010/` 的 `handoff-status.json`、`settings-operator-saved.json`、`iap-agent-creation.json`、`inherited-access-before.json`、四份 `*-blocked.json`。沒有研究影像、帳密或 token。

參照：[IAP agent 建立](https://cloud.google.com/service-usage/docs/reference/rest/v1beta1/services/generateServiceIdentity)、[mobile programmatic authentication](https://docs.cloud.google.com/iap/docs/authentication-howto)、[OAuth client 分享與白名單範圍](https://docs.cloud.google.com/iap/docs/sharing-oauth-clients)。

## 無 Organization 的自訂 web audience 實測（2026-10-10，進行中）

Jack 在上一停止點後明確要求「確認無 Organization 專案可用的手機登入方案並進行修正驗證」，承接既有 MMH IAP／兩個限定 binding／手機重建授權。本輪沒有新增 OAuth client、Token Creator、token broker、公開入口或 Organization 遷移。

依 Google 自訂 OAuth 文件：未隸屬 Organization 的專案可使用 custom OAuth，且 custom OAuth 支援 programmatic access。手機文件要求取得資源 web client audience 的 ID token；但這些敘述不能單獨證明本專案的 native 流程成功。因此建立獨立 `mmh.iap-native-probe/1` 方案 `79ba6d72d9a5db252e8a14fd376eefb36c2267452e491a3052f84e24aeeed12c`，保留 v1 必須具備 mobile allowlist 的測試，沒有把 v1 改成遇錯自動放行。

實測只使用已儲存的 MMH web OAuth，沒有額外 programmaticClients；iOS SDK 指定原 web client 作 `serverClientID`／token audience、使用原 iOS client 進行 Google 登入。Google/IAP 必須實際驗簽與核對使用者 IAM。新檢查器只回報「probe configuration passed」，永遠不把設定檢查等同手機登入或上傳驗收。

已完成：

- Run 服務只新增 `roles/run.invoker` 給 IAP service agent；IAP 資源只新增 `roles/iap.httpsResourceAccessor` 給 Jack。Google 將同一 email 正規化為 `user:Jack.Hou@gmail.com`；檢查器只將此已知 user email 的大小寫正規化，不接受其他 principal 類型、地址或額外 binding。
- 只 PATCH `iapEnabled=true`，服務 generation 3；完整 template、映像 digest、要求/實際流量與 protected controls 與基線一致。原 revision 100%、candidate tag 0% 不變。
- 三個 service/candidate URL 分別測無 Google 憑證、無效 Google token、只有無效 App token，共 9/9 拒絕或導向 Google 登入；未保存 cookies、tokens 或影像。
- 瀏覽器以既有 Jack Google 帳號完成選擇後回候選 URL，但 Comet 顯示 `ERR_BLOCKED_BY_CLIENT`。同期候選 revision 有 health 200 伺服器紀錄；這是到達後端的時間關聯證據，尚不把瀏覽器畫面驗收列為通過。
- 獨立 MMH App 設為 1.0(28)，一般醫療版/Lite 版號不變。設定固定候選 URL，新增 Google 登入後只讀 health 探測，必須 HTTP 200 且 build.service 正確、revision 非空，才允許下一層 WoundAI 帳密。沒有 Google/IAP 成功不傳 App 密碼，HTTP 錯誤不會被顯示為成功。
- Debug 真機 BUILD SUCCEEDED、codesign deep/strict 通過、J-iP16PM 安裝成功。啟動因手機鎖定失敗，已請 Jack 解鎖並操作登入；尚不能宣稱手機 token 已被 IAP 接受。
- XCTest：21/21 單元 + 1/1 UI 通過，涵蓋 token、雙標頭、登出、重新導向拒絕與新增 health 判定。Python：新 native probe 9/9、v1 entry 16/16；owner_guard mac 與 diff check 通過。

尚待：手機 native token 與 IAP 成功、正確 Google 身分配錯 App 帳密仍拒絕、正確雙層登入、合成影像/遮罩/RGB-D/修訂/撤回驗收、IAP 入口撤權與恢復、舊 OAuth secret 停用。Android 入口尚未驗收。

此實測若確認不支援或出現安全檢查失敗，以最新 etag 取消 IAP flag 並移除本輪兩個精確 binding；保留操作者已儲存的 custom OAuth（此實測的起點），不改其他服務、映像、流量或資料。若僅等待本人解鎖/登入，保持此私有入口並明確標示未完成，不當成技術失敗或驗收成功。回復前先讀回，遇並行變動停止而不覆蓋。

證據仍在 `mmh-iap-enable-20261010/`：`native-probe-proposal.json`、`native-*-after.json`、`native-config-result.json`、`native-negative-http.json`、`native-device-build.log`、`native-install.json`、`native-launch.json`、`native-ios-tests.xcresult`、`native-health-status-log.json`。方案雜湊只是完整性標記，非授權簽章。


## 手機入口實測確認（2026-10-11 整理）

來源為 iOS `992a828`、獨立 MMH 1.0(28)。Jack 回報顯示「Google 與機構入口驗證通過」。Cloud Run 唯讀查詢同時取得 `/api/health?mmh_native_probe=1` 的 HTTP 200：UTC 2026-10-10 15:56:46.921338（台北 23:56:46），revision `woundai-backend-mmhps20261007-c9a4b9e49f414`。App 只有在指定服務的 health JSON 與 revision 檢查通過後才顯示此訊息；未擷取手機 token。

這證明本專案的同專案 iOS client／custom web audience 路徑能通過 IAP，無須先設定遭 Organization 限制拒絕的 programmaticClients。這是本設定的實測結果，不能外推到任意 OAuth client、Android 或其他專案。

本輪重新 GET service、Run IAM、IAP settings、IAP IAM，由 `check_native_probe` 檢查通過：IAP 啟用、僅既有 IAP agent 的服務 invoker 與 Jack 的入口權；custom OAuth 與密鑰摘要未變，映像／template／流量未變。沒有新增 IAM 或憑證。配置檢查器仍不自行宣告手機或上傳通過；手機證據另存。

Comet 本輪開啟候選 `/console` 已正常顯示應用登入頁，沒有調整瀏覽器保護；先前 health 頁 ERR_BLOCKED_BY_CLIENT 保留為歷史紀錄。OAuth 設定儲存與瀏覽器頁面呈現是不同驗收項目，不需再建立密鑰。已交接 Jack 使用 `mmhps20261007:admin` 的 MMH 管理密碼登入；日常 physician／nurse 帳號尚未核發，不能沿用 demo 或舊正式帳號。

尚待：有效 Google 身分配無效 App 帳密的拒絕、正確機構帳密登入、手機合成影像／遮罩／RGB-D／修訂去重／撤回，以及 IAP 入口撤權恢復。未以 health 成功取代這些項目；舊 OAuth secret 仍待安全停用，Android 仍未接入。

新增證據（同一 `mmh-iap-enable-20261010/`）：`confirmed-phone-health-log.json`、`confirmed-phone-result.json`、`confirmed-phone-{service,run-policy,settings,iap-policy}.json`、`console-login-20261011.png`；只保存必要狀態、revision 與配置，不保存 token、密碼或研究影像。之前 21/21 XCTest 單元、1/1 UI、9/9 native probe 與 16/16 舊入口 Python 測試為前輪結果，本輪文件更新未重跑，不能算本輪新增測試。


### MMH 管理帳號登入確認與手機測試身分交接

2026-10-11 Jack 已自行取用既有 MMH 管理密碼並登入候選 console；未新增 Secret 版本或重設帳號。畫面顯示 `mmhps20261007:admin`／管理者，Dashboard 指向 MMH 媒體與稽核桶。Cloud Run 同 revision 日誌：台北 00:13:25 `/api/auth/login` POST 200、00:14:31 `/api/v1/users` GET 200。此為瀏覽器兩層登入與帳號讀取驗收，不能代替手機 App 帳密登入。

帳號清單讀回 5 筆：1 筆 bootstrap admin 啟用、4 筆先前合成驗收帳號停用；日常 `dr01` 尚未建立。已在 console 準備 `dr01`／physician／「MMH 合成驗收醫師 01」，由 Jack 親自按新增、保存一次性密碼、在 MMH 1.0(28) 登入；尚未送出建立請求，不將準備表單記為已核發。此帳號限定本人合成測試，不當臨床收案或 Apple 審查共用帳號。管理者不具備 clinical/annotation 權限，不能以管理帳號替代醫師流程。

證據：`console-auth-status-20261011.json`、`physician-create-handoff-20261011.png`（建立前，無密碼）。另觀察到較早登入 401，但沒有記錄其輸入條件，故不當成受控錯誤帳密反例測試。


### dr01 建立與手機登入回報

Jack 已回覆 dr01 建立且手機登入成功。MMH 候選日誌核對台北 2026-10-11 00:18:13 users POST 200、00:21:00 auth/login POST 200；HTTP 摘要不包含登入主體，因此 dr01 主體對應依本人回報，未擷取 App token。原始碼確認 `dr01` 自動使用 MMH DEFAULT_ORG，完整 `mmhps20261007:dr01` 也接受；單填機構代碼不等於 dr01。

主控台為清除一次性密碼重新載入後回到登入頁（目前 session 不跨重新載入）；手機登入不受此瀏覽器動作影響。已交接手機拍攝不對稱幾何合成圖、醫師修邊、存檔與送出標註；下一輪核對 RGB／遮罩／組織層／深度位元組、內參、方向沿革與雜湊，尚未視為上傳或 3D 配準驗收通過。證據：`physician-login-20261011.json`。
