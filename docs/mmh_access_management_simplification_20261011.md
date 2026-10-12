# MMH 門禁與應用帳號管理整合方案

日期：2026-10-11。這是可實作方案，本輪尚未新增 IAM 管理授權或修改服務。

## 建議介面

在 WoundAI 管理主控台「機構成員」同一頁顯示：Google Email、IAP 入口狀態、對應 WoundAI 帳號、醫師／護理角色、啟用狀態與最近一次操作结果。Google 帳號是私人服務的入口，WoundAI 帳號是應用內操作權限；同頁管理不等於合併成同一個權限。

第一階段可加入現有管理入口及操作清單，不需要給量測後台 IAM 管理能力：

- [MMH 所在專案的 IAP](https://console.cloud.google.com/security/iap?project=woundai-jackh001)：選定 asia-east1 `woundai-backend-mmhps20261007` 的服務資源，只管理 `roles/iap.httpsResourceAccessor` 的具名使用者。
- [OAuth Audience](https://console.cloud.google.com/auth/audience?project=woundai-jackh001)：若目前 OAuth 測試設定要求加入測試使用者，這是額外的名單，不等於 IAP 授權。
- WoundAI「帳號管理」：建立／停用 dr01 等帳號及應用角色。不得把 WoundAI admin 當成 GCP 管理員。

## 同頁直接新增／停用的下一階段

可利用 IAP IAM API，但這是新增管理能力，需要另行覆核具體實作與有限授權。

1. 使用獨立管理服務身分；量測／研究上傳執行身分不取得 setIamPolicy。
2. 固定 project／location／MMH service，拒絕從瀏覽器傳任意資源或任意角色。只允許 `user:` email 與入口角色，不允許 public、群組或跨服務變更。
3. GET 最新政策，保留 etag、条件與其他 bindings；僅改指定成員，再以 etag 條件更新並讀回。衝突時停止，不覆蓋他人更新。
4. 限已核定機構管理者、驗證 IAP 身分及 App 管理身分、CSRF 防護，逐次記錄操作者／對象／資源／結果；保護緊急管理者不被最後一人撤除。
5. 加入成員分兩個階段，撤權也分兩個階段，顯示部分成功／失敗；不得以綠勾假裝跨系統原子交易。撤權後驗證 Google 入口与應用 token/帳號的實際拒絕，明示傳播延遲及既有會話限制。
6. 先對假 IAM adapter 測試越權、政策併發、讀回不符及部分失敗，再用明確核定的合成測試者進行端到端驗收。

Google 群組也能簡化 IAP 名單維護，但會引入群組管理與成員傳播的依賴；目前 MMH 使用具名帳號且沒有 Organization，不為簡化介面先變更這個已驗證模型。

參照：[Google IAP 管理存取](https://docs.cloud.google.com/iap/docs/managing-access)。既有實測與授權範圍見 `mmh_google_entry_contract_20261010.md`。此方案不自動新增 OAuth、IAM、測試者或公開入口。

## 已開始實作：獨立權限操作核心（2026-10-11）

`Backend/AccessManager/iap_policy.py` 與 `iap_adapter.py` 是供後續獨立管理服務使用的實作，尚未掛入量測後台、沒有 HTTP 路由，也沒有自動取得 ADC 或命令列執行入口。正式服務及現有 Google／App 權限均未變更。本輪授權依使用者持續目標「建立帳號權限管理完整介面」及必要對齊程序，完成該介面必要的後端操作核心；**不是**將目標縮減為工具或管理入口連結。

### 已有行為

- REST adapter 固定 `projects/421209514056/iap_web/cloud_run-asia-east1/services/woundai-backend-mmhps20261007`，拒絕其他資源；讀取要求 policy version 3，以保留條件式 bindings。操作核心只新增／移除 `roles/iap.httpsResourceAccessor` 的單一具名使用者，保留其他 bindings、條件與政策欄位。
- 確認畫面將綁定整份政策及 etag 的 SHA-256；執行前重新讀取，過期時拒絕。寫入保留 etag，逾時／409／讀回不符不自動重送或回滾，避免覆蓋他人變更。政策雜湊僅供版本一致性，不是身分驗證或使用者簽章。
- 固定保護 Jack 的緊急管理入口，不接受 public、群組或 service-account 目標。既有其他 principal 原樣保留，不將它們當成本輪授權。
- 先持久化 audit intent，失敗即不寫 IAM；寫入後重新 GET 核對完整政策。完成稽核失敗、回覆不完整及回讀差異均以未確認狀態回報，不顯示成功。稽核介面目前為 Protocol，正式持久化 sink 尚待接入。
- 結果明確區分直接 binding 與有效存取；移除直接授權不會宣稱已移除上層／群組／條件授權或立即使既有工作階段失效。
- adapter 由呼叫者提供專用管理身分的 HTTP session，不會把量測 runtime 提升為 IAM 管理者；HTTP 重新導向被拒絕、逾時有上限、回覆錯誤不原樣洩漏。正式 session 必須停用寫入自動重試。

### 本輪實測

`python3 -B -m unittest Backend.AccessManager.test_iap_policy -v`：**31/31 通過**。涵蓋保留其他政策、條件版本、重複／無效 principal、救援帳號、過期快照、並行 etag 衝突、稽核前後故障、寫入後逾時、讀回不符、相同操作無寫入、政策排序／Google email 大小寫、HTTP 302／401／403／409／429／500 及傳輸錯誤遮蔽。皆為假 adapter／session 故障注入，沒有連線 Google 或變更 IAM；不可稱作雲端端到端驗收。

本輪是在 Mac 新增獨立 Python 元件，屬上述使用者要求的本次 Backend 跨平台實作例外；不修改 `owner_guard.py` 的 Windows 所有權規則、不冒稱 owner_guard 綠燈，不改 PowerShell 部署腳本。提交前檢查工作區，僅保全本輪新檔及文件，未覆蓋既有後端工作。

### 完整介面尚待

1. 同一「機構成員」頁的 Google／WoundAI 帳號對應資料模型、讀取及新增／停用／角色變更流程，並呈現分階段結果。
2. 雙重管理者驗證及 CSRF：必須驗證 Google IAP signed assertion、指定資源 audience／有效期，以及有效的 App admin 身分與組織。`actor` 參數不是認證；不能將本模組直接暴露給瀏覽器。尚需定案跨服務 App admin 驗證協定，不能只信任 client 自報角色或未驗簽 email 標頭。
3. 專用管理 runtime、最小資源 IAM、正式 audit sink、重送／結果不確定的人工核對頁及前端等待／重複點擊保護；新增雲端管理權限前須完成具體方案覆核。
4. 選定合成測試者後，實際加入、登入、變更角色、停用、IAP 撤權、既有 token 行為與雙系統部分失敗驗收。

官方依據：[IAP 資源層 get/set IAM 權限](https://docs.cloud.google.com/iap/docs/managing-access)、[IAM etag、政策版本與權限傳播](https://docs.cloud.google.com/iam/docs/allow-policies)、[IAP signed header 驗證](https://docs.cloud.google.com/iap/docs/signed-headers-howto)。Google 建議對衝突重做讀改寫；本工具採更保守的重新顯示確認畫面，不沿用已過期的人員操作確認。


## 網頁與 HTTP 邊界實作進度（2026-10-11）

新增 `web.py` 工廠及 `static/members.*`，同頁顯示 Google 直接／條件入口與 WoundAI 帳號角色。確認對話框綁定政策快照；寫入期間鎖定操作，讀回確認後要求刷新，結果不明時提示核對而不自動重送。前端使用 textContent；不保存 token，採同源請求。保留固定救援入口，未將移除直接 binding 說成所有有效入口或既有 session 均已撤销。

HTTP 層預設關閉（503），必須注入已驗證管理者、IAM adapter、稽核與帳號來源；不信任瀏覽器自報角色或未驗簽 Google 標頭。管理者需同時符合具名 allowlist、MMH org 與 App admin。POST 核對 Origin、JSON、綁定兩端 session 的 CSRF；帳號回覆僅白名單欄位，跨組織資料拒絕整份讀取。CSP、no-store、禁止跨域與嵌入。`VerifiedAdmin` 是 adapter 結果型別，不是 IAP 驗簽實作；正式登入仍未接線。

本輪 `python -B -W error::ResourceWarning -m unittest discover -s Backend/AccessManager -t . -v` **50/50 通過**（核心31、HTTP19），涵蓋未配置、假冒／錯誤角色／org、錯誤或非 ASCII CSRF、工作階段變更、任意 resource/role 注入、過期快照、寫後不明與稽核失敗；已修正檔案回覆測試未關閉問題。本機 `demo.py` 僅綁 loopback，使用記憶體 adapter；瀏覽器完成合成成員開通、刷新讀回、撤除、再讀回，救援按鈕 disabled，console error 0。本機測試不構成 Google IAM 端到端驗收。

仍需完整同頁帳號新增／角色變更／停用與人員對應、正式雙重登入、持久稽核、最小權限 runtime 及實際加入／撤除端到端驗收。沒有部署此管理介面；量測 runtime 未獲 IAM 管理權。延用上節本次 Mac Backend 實作例外，未修改 owner_guard 規則。

## 雙重管理者驗證增量（2026-10-11）

新增 `authentication.py`：Google assertion 只接受 ES256，以固定 Google 公開金鑰端點驗簽，核對 issuer、伺服器配置的 IAP resource audience、有效期與必要 claims。不使用 OAuth client ID 當 signed assertion audience，不跟隨 token 內的金鑰 URL。金鑰 HTTP 失敗、重新導向、未知 kid 均拒絕；目前沒有快取，每次管理操作會讀取公開金鑰，正式 runtime 可再加入有到期限制的快取。

App 管理 session 必須由伺服器端 resolver 取得，與已驗證 Google subject 綁定。每次操作透過固定 MMH `/api/v1/users/session` 路徑即時核對；該路徑使用已驗簽 App JWT，再查最新帳號狀態，MMH org、admin、未停用、角色一致才回覆。拒絕 OTC、跨 org、降權、停用及查詢失敗。只回 identity、org、role、jti 與 expiry，不回密碼、雜湊或 bearer；no-store。即使 blueprint 未掛主程式的 live-token callback，端點仍自行執行相同核對；舊非 MMH 服務回 503。

本輪本機驗證：

| 測試 | 結果 | 範圍 |
| --- | --- | --- |
| AccessManager unittest discovery | 60/60 | 原核心31、HTTP19、新增真實合成 ES256 驗簽／live adapter10 |
| `tools.test_mmh_management_session` | 7/7 | 真實 App JWT、暫存帳號庫、停用／降權／非 MMH 拒絕 |
| `tools.test_mmh_institution` | 13/13 | 專用機構隔離回歸 |
| `engineering/phase2/test_rbac.py` | 37/37 檢查 | 既有醫師／護理／助理／工程師／管理者權限回歸 |

共 80 個 unittest 與 37 個 RBAC 檢查。本輪初次 ES256 測試 fixture 的公開金鑰 enum 名稱錯誤已修正，修正後全數通過。加入既有 p0-4-audit workflow 的路徑觸發與測試命令；**未推送，沒有本輪遠端 CI 結果**。本機 PyJWT 為 2.14.0，後端 lock 為 2.13.0；部署前仍需 locked 環境 CI 驗證。

這是驗證元件及 read-only API 的完成，不等於正式登入工作階段已接線。仍欠安全 cookie／伺服器 session store、登入／登出 UI、獨立管理 runtime 的 IAP audience／後端 transport 憑證接線、正式持久稽核及帳號異動併發協定。完整帳號新增／停用／角色變更頁尚未完成；不將已測的假 transport 當成雲端驗收。未變更 IAM、未部署後端，也未新增管理者。

## 同頁帳號異動增量（2026-10-11，接續 b093cb0）

新增 `accounts.py` 與 `/api/accounts`，前端已有新增、顯示名稱、角色變更、停用／恢復表單及逐次確認。固定 MMH URL／org，拒絕額外欄位、任意密碼、非醫療角色及跨機構資料。保護 `admin` 救援帳號與目前管理者本人的異動。帳號變更與 Google 門禁分开回報，不冒稱兩者一次完成。

新增帳號由既有後端產生密碼；更新沿用原密碼。回覆要符合 status、身分、org、user、目標角色／狀態，再 GET 全部帳號核對保存結果，才顯示成功。POST 後逾時、HTTP 錯誤、保存收據錯配或讀回失敗一律標示「可能已保存、結果未確認」，不重送或回滾。密碼僅在新帳號成功時單次回傳，前端不用 storage／clipboard；明確隱藏或刷新會清除 DOM。前端在送出期間停用操作、清除過期名單，避免舊狀態與成功訊息並列。

本輪 AccessManager **74/74 unittest 通過**（含新增14），涵蓋新增、更新、停用、恢复、相同狀態不寫入、過期快照、既有帳號重建拒絕、救援／本人保護、HTTP 故障、密碼不出現在名單、收據錯配及 CSRF。已用本機浏览器完成合成 `qa-doctor` 新增→角色醫師改護理師→停用→刷新讀回；密碼隱藏後未再顯示，console error 0。全部使用記憶體 transport，沒有建立真實帳號。畫面證據：`/private/tmp/mmh-members-account-management.jpg`。

**正式接線阻擋仍存在：** 名單 SHA-256 只用來拒絕過期表單，不能阻止預讀與後端寫入之間另一位管理者更新。現有 `/api/v1/users` 是 append-only 全紀錄更新，沒有原子 CAS；故目前 adapter 不得正式接線，下一階段須把預期版本條件下推至帳號儲存層並對所有帳號寫入入口一致生效。已沿用既有後端的 audit intent/完成事件，但仍需正式 transport/session、管理 runtime 及持久 audit sink 的整合驗證。人員對應功能尚未完成。此段是前端與契約實作驗收，**不是完整管理系統部署完成**。

## 儲存層版本條件完成本機驗證（2026-10-11，接續 a22b2ca）

上一節的併發缺口已有程式修正，尚未部署。新增 `account_versions.py`：專用機構帳號保留舊紀錄，每筆新修訂帶前版雜湊與隨機 nonce；由前版連結重播，不依 GCS 物件列出順序猜最新版本。新 revision 是完整紀錄的 SHA-256，API 不回傳原始 password hash 或內部鏈資料。狀態改回原值仍有新 revision，拒絕 ABA 過期更新。

- LocalStore 在跨程序檔案鎖內核對前版、追加、flush/fsync；讀取共用鎖，避免讀到正在追加的半筆。
- GCS 使用 `users.jsonl/cas/<org>/<user>/<parent>.json`，兩個基於同版的更新爭用同一物件，透過既有 `if_generation_match=0` 原子建立。保留 security bucket 路由；沒有修改任何雲端物件或 IAM。
- 專用機構所有 `auth_users.upsert_user` 使用同一版本寫入機制。API 既有帳號更新必須帶讀到的 `expected_revision`；缺少、過期、或實際寫入時輸掉競爭均回 409，沒有自動重試。建立新帳號使用全零 root。
- 既有主控台改名、改角色、停用及重設密碼均附帶 revision；重設後重新整理避免保留過期版本。稽核 intent 帶 expected revision，完成事件帶新 revision。
- 新管理 adapter 要求後端 `account_versioning=cas-v1`、有效 revision，並核對 POST 收據與 GET 讀回是同一 revision。不將旧後端 HTTP 200 當成具備併發保護。
- 醫療版與 Lite 共用 staging allowlist 已加入新模組，避免 image 缺檔導致啟動失敗。

### 本輪實測

| 範圍 | 結果 |
| --- | --- |
| AccessManager（含版本能力與 409 處理） | 76/76 |
| 帳號版本、併發及真實後端路由整合 | 9/9 |
| MMH 機構與即時 session 回歸 | 20/20 |
| Lite build context | 5/5 |
| Medical image build | 24 通過、1 跳過（本機無 PowerShell） |
| 既有 RBAC | 37/37 檢查通過 |
| 既有 admin console、store abstraction 腳本 | 均 exit 0；console fixture 的短測試 HMAC key 有既有警告 |

併發案例：Mac 本機8個執行緒只有1個保存、7個衝突；模擬 GCS 強制8個寫入者都先通過預讀，再於 create-if-zero 競爭，仍只有1個保存、7個衝突。停用後的過期角色修改不能重新啟用帳號。整合案例使用真實 Flask JWT、帳號路由、LocalStore，透過管理 adapter 新增→保存收據→以產生密碼登入→停用→拒絕登入，全程合成資料、無雲端連線。

### 部署／回復限制

此格式是帳號儲存契約變更，**不可與舊版寫入者並行或在產生 CAS 紀錄後直接回滾舊 image**。舊版讀取順序不理解新鏈；舊版追加到已轉換帳號也會使新版遇到 orphan root 並拒絕讀取，防止默默採用衝突權限。正式部署前需先唯讀盤點帳號歷史、備份並核對雜湊、暫停所有帳號異動入口、確認所有服務／工具寫入者已更新，再以私有候選驗收後恢復。此方案尚未執行。Windows 跨程序鎖與真實 GCS 寫入仍待驗證，不以本機／假 GCS 結果替代。

完整目標仍欠正式 session/cookie、雙身分綁定與人員對應、持久管理稽核、最小權限 runtime、雲端部署及真實管理端到端驗收；商店發布簽章事項另見 `mmh_store_release_checkpoint_20261011.md`。

## 管理登入／登出接線（2026-10-11，接續 39e2cb1）

新增 `sessions.py` 與 `/api/session`、`/api/session/login`、`/api/session/logout`。工廠可注入 SessionController，沿用 Google signed assertion allowlist 與 MMH 即時 admin 核對；帳號操作 adapter 使用該登入 session 的 App bearer，並由既有 transport 提供獨立 IAP 門禁憑證。transport 並未在本輪取得雲端權限或自動搜尋憑證。

前端加入兩層登入狀態、一次「登入並驗證管理權限」、驗證期間鎖定及登入後刷新名單；僅已登入時顯示登出。HTTP 401 會撤下已登入標示。密碼送出後清空欄位，不進 localStorage/sessionStorage。WoundAI token 只在伺服器記憶體，不在 cookie/JSON/HTML；cookie 僅隨機 session ID，使用 `__Host-`、Secure、HttpOnly、SameSite=Strict、Path=/、無 Domain。登入／登出另驗 Origin 與綁 Google subject／session ID 的 CSRF；成功登入輪替 ID，舊 CSRF 失效。

Session 最長15分鐘、不因 GET 狀態查詢延長伺服器期限。Google subject 切換、live admin 降權、session 到期或重啟均要求重新登入。登出僅撤銷這個管理介面的伺服器 session，不宣稱登出整個 Google 瀏覽器帳號或撤銷其他 App JWT。每個 Google subject 每分鐘最多5次登入嘗試，跨 cookie 仍計數。單一 session 同時只能有一個登入請求；登出／撤銷後才收到的成功回覆不得重建 session。

**本輪驗證：AccessManager 92/92 unittest 通過**（原76＋新增16），JS `node --check` 通過。新增測試涵蓋安全 cookie、無 token 回傳、成功輪替、重新讀取狀態、登出、過期、服務重啟、降權、缺 Google header、Origin/CSRF 拒絕、舊 CSRF、錯密碼重試、非 admin 拒絕、subject 切換、延遲登入撤銷競態、重複登入、跨 cookie 限流與容量回收。使用合成 IAP verifier／假後端 transport；前一階段已有真實 ES256 與 MMH API 分項驗證。**沒有以此宣稱真實雙登入瀏覽器端到端通過**，本輪僅完成前端程式及 API/session 測試。

部署限制：目前 store 在單程序記憶體，正式 runtime 必須明確採單 instance／單 worker，或先提供同樣具備原子輪替／撤銷語意的共用 store；scale-out／重啟會失去登入而非偷偷恢復。這個限制只影響管理 session，不改研究影像或手機待上傳紀錄。正式持久稽核、Google／WoundAI 人員對應、runtime 建置／最小 IAM、帳號 CAS 格式切換與真實登入／授權／撤權驗收仍未完成。本輪未部署、未新增 IAM 或真實帳號。

## 管理稽核儲存介面（2026-10-11，接續 3e667fd）

新增 `Backend/AccessManager/audit.py`：不自行取得憑證，必須注入獨立管理身分的 HTTP transport；固定既有 MMH audit bucket 的 `access-management/v1/` 前綴，不觸碰臨床稽核鏈或研究影像。此節是本機實作證據，**沒有接線或寫入真實 GCS**。

- 每次操作以隨機 UUID 分成 `intent.json` 與唯一的 `outcome.json`。兩者均用 `ifGenerationMatch=0` 建立，核對回覆的 bucket/name/size/generation，再讀回該 generation 的全部內容，逐位元組一致才成功。
- IAM 變更前的 intent 若寫入逾時（包含實際可能已保存）、回覆不符或讀回損壞，禁止呼叫 IAM 寫入。完成稽核失敗則保留「結果未確認」，不重送 IAM、不自動回復。
- outcome 必須讀到同一操作、操作者、目標帳號、資源、角色、動作與前後政策雜湊的 intent，記下其 generation 與 SHA-256。verified/unconfirmed 共用一個 create-only outcome key，不能互相覆寫。
- 只接受明列欄位；拒絕密碼/token 額外欄位、跨機構操作者、其他資源或角色、假 effective-access 成功宣告。稽核保留必要的管理者與目標帳號，不寫入登入密碼、App JWT 或 Google token。
- 這是**未鎖定驗證環境**的應用程式防覆寫機制，不是 WORM、Bucket Lock 或完整防竄改保證。持有其他刪除／覆寫權限的人仍可能破壞證據；不能以本節宣稱高合規稽核完成。

本輪 `python -B -W error::ResourceWarning -m unittest discover -s Backend/AccessManager -t . -q`：**105/105 通過**，其中新增13項。涵蓋真實 policy 函式與假 HTTP GCS 的串接、正常／無異動、寫入前後逾時、intent 讀回損壞、IAM 寫入結果不明、完成稽核失敗、相同物件拒絕重建、缺少／錯配 intent、竄改 envelope、限定路徑／大小／generation、錯桶回覆、UTC 時鐘與敏感欄位拒絕。`git diff --check` 通過。沒有真實 GCS／IAP 端到端或遠端 CI 結果。

### 待部署方案的權限邊界（尚非可執行授權單）

管理 runtime 將需要固定 MMH IAP 服務的 `iap.webServices.getIamPolicy`／`iap.webServices.setIamPolicy`，以及上述 audit 物件前綴的 `storage.objects.create`／`storage.objects.get`。不應給研究／臨床物件讀寫、audit 刪除、桶管理或專案 Editor。後續仍須核對既有 IAM、條件支援與有效權限，建立精確 resource binding 計畫及失敗處理，再取得新增權限授權。`setIamPolicy` 本身能改整個該資源政策；僅准改具名 user 的控制目前在應用程式，不能宣稱 IAM 已把它限制到單一角色或單一成員。

官方依據：[IAP 資源政策權限](https://docs.cloud.google.com/iap/docs/managing-access)、[GCS generation 前置條件](https://docs.cloud.google.com/storage/docs/request-preconditions)、[GCS 物件建立介面](https://docs.cloud.google.com/storage/docs/json_api/v1/objects/insert)。管理 runtime、HTTP transport 無重試設定、真實 GCS 驗收、人員對應、帳號 CAS 遷移及完整端到端驗證仍待完成。商店簽章的使用者待辦仍以發布檢查點為準。

## 獨立管理服務接線（2026-10-11，接續 befb579）

新增 `runtime.py` 組合正式 IAP 驗簽、SessionController、帳號 CAS adapter、政策 adapter 與 GCS 稽核。修正工廠的設定守門：已提供 per-session 帳號 adapter 時，不再錯誤要求另一個 `list_accounts` 函式。沒有用假帳號清單繞過正式接線。

預定服務 `woundai-mmh-access-manager`、執行身分 `mmh-access-manager@woundai-jackh001.iam.gserviceaccount.com`；**均為待授權新增資源，尚未建立**。啟動要求精確 K_SERVICE 與明確 private-synthetic-validation 設定，僅從 Cloud Run metadata 取得該指定身分的短效憑證，不搜尋 ADC、個人登入或本機私鑰。現階段管理者 allowlist 僅既有 Jack 帳號。

後端 transport 只允許固定 MMH 的 login、live admin session 與 users 路由。App bearer 留在 Authorization，服務 Google token 放在 Proxy-Authorization；不互相覆蓋。管理用 cloud bearer 只用於指定 IAP 政策與 audit 前綴。公開 IAP 驗簽金鑰讀取不帶上述憑證。停用環境 proxy/netrc、自動 HTTP 重試與重新登入後重播 POST；拒絕任意 URL、redirect、verify/auth 參數覆寫。metadata ID token 的未驗簽 claims 僅用於已信任 metadata 回應的身分／快取期限檢查，不當成驗證外部使用者的依據；後端 IAP 仍需驗證 Google 簽章與權限。

新增 Dockerfile，沿用後端已固定 digest 的 Python base，僅 COPY 管理模組與靜態頁、使用非 root 使用者，固定一個 sync worker、一個 thread、不記 HTTP access log。部署仍須 max-instances=1、concurrency=1；重啟要求重新登入，不能混用多 worker。依賴 lock 是既有後端 lock 中18項必要套件與全部原始雜湊的子集，不裝影像／模型依賴。

本機 **117/117 unittest 通過**（新增12項）。新增一條使用真實合成 ES256 簽章、Flask cookie/CSRF 與正式 adapter 組合的整合案例：Google assertion→App admin 登入→讀取名單→具名門禁變更→intent/outcome 保存讀回→新增醫師帳號→登出→拒絕讀取。HTTP 後端與 GCS 仍是記憶體 fixture，**不是部署後端到端驗收**。其他測試核對兩種 bearer、401 不重播、錯目的地／header／TLS 覆寫拒絕、錯執行身分、短效 token 快取及無設定時完全不取得憑證。

官方接線依據：[Cloud Run signed-header audience](https://docs.cloud.google.com/iap/docs/signed-headers-howto)、[IAP programmatic authentication](https://docs.cloud.google.com/iap/docs/authentication-howto)、[metadata ID token](https://docs.cloud.google.com/docs/authentication/get-id-token)。仍需鎖定依賴安裝驗證、容器建置、部署設定檢查、實際服務的 IAP/OAuth 設定與最小權限方案，取得新增資源授權後才能部署。本機沒有 Docker 可用，尚未建置此容器；本轮沒有增加雲端管理權限。

補充實測：隔離 venv `/private/tmp/mmh-access-manager-locked-venv` 已以 `pip install --require-hashes` 安裝18項固定依賴，含 PyJWT 2.13.0（不再沿用 review venv 的2.14.0）；相同管理測試 **117/117 通過**，`pip check` 無缺損依賴。這補足 macOS locked-dependency 測試，仍不等於 Linux 容器建置完成。安裝紀錄：`/private/tmp/mmh-access-manager-locked-install.log`。

## 登入狀態介面回歸與瀏覽器驗收（2026-10-11 19:30）

檢查發現重新驗證失敗時，畫面可能留下先前管理者名稱、帳號編輯表單或確認視窗；後端仍會拒絕未授權請求，但使用者可能誤判登入狀態。修正 `members.js`：重新驗證開始即清除舊操作快照、待確認操作、表格、一次性密碼及編輯視窗，顯示正在確認；失敗不保留已登入外觀。讀取名單遇到401時同步清除狀態，要求重新整理取得新 session CSRF；缺失或矛盾的 session 回覆也不啟用管理按鈕。

- **新增介面狀態測試8/8通過**：執行實際 `members.js`、合成 DOM 與 fetch，涵蓋重整恢復、等待期間清空、App 過期、網路失敗、錯誤成功回覆、members 401、單次登入及登出。測試檔 `Backend/AccessManager/test_members_ui.cjs` 已加入既有 audit CI 命令，本輪未推送，沒有遠端 CI 結果。
- **管理服務117/117通過**：固定依賴 venv 重跑。JavaScript 語法與 `git diff --check` 通過。
- **瀏覽器實際驗收**：在只綁定127.0.0.1的 localhost fixture，使用正式 SessionController／Flask及同一份靜態頁，合成 IAP 身分與記憶體後端。未登入不顯示登出；一次登入後讀取名單；整頁 reload 恢復已登入；開啟編輯表單後登出，表單收起、名單清空、按鈕停用、登入表單恢復。瀏覽器 error log 0筆。測試服務已停止。
- 此 fixture 僅在 loopback 模擬 HTTPS 反向代理與 Google header，使用已知合成測試帳密；沒有變更正式 HTTPS／cookie／IAP 守門，沒有實際 Google OAuth、雲端權限變更或真實帳號建立。不能以本輪結果宣稱部署後端到端通過。

可重現命令：`node --test Backend/AccessManager/test_members_ui.cjs`；後端命令沿用前節。畫面及本機 fixture 留存於 `woundai-institution-evidence-20261007/mmh-iap-update-20261011/store-and-members/session-ui/`：`mmh-management-session-restored.jpg`、`mmh-management-session-signedout.jpg`、`mmh-session-ui-fixture.py`。該 fixture 不是部署程式，Dockerfile 不包含它。

目標一仍未完成：管理容器／最小 IAM／帳號 CAS 切換與真實雲端驗收尚待執行；iOS 外部審查登入路徑、Android 發布簽署及 Play 正式簽章 OAuth／內測啟用仍以商店檢查點為準。

## 真實雲端唯讀盤點及帳號切換檢查（2026-10-11 19:34）

已使用既有 gcloud 工作階段核對實際 Cloud Run、IAP 及安全桶物件中繼資料。CLI 的 IAP 指令不支援 cloud-run resource type，改用官方 IAP `getIamPolicy` REST、版本3讀取；沒有以失敗的 CLI 結果推定政策缺失。短效 token 僅在程序記憶體，用完即結束，不輸出或寫入證據。

| 現況 | 本輪證據 |
| --- | --- |
| MMH IAP | 仍啟用；目前直接入口政策只有既有 Jack Google 帳號 |
| 預設入口 | 100% 指向 `00001-hqd`，image digest `814d266e84f4a50b36363923e8a262aa97f4eaa18e6a5588ac27331231ab996b` |
| candidate | 指向 `cb7f9dc2b28bc`，image digest `3726392b344450fa680e4150e8f76510f232b1970e82848a6d7a7c9c666cf5a0` |
| 帳號儲存 | 兩個 revision 共用 MMH security bucket、`flywheel/users.jsonl`；10個物件、3,668 bytes、0個 CAS 路徑物件；最後更新2026-10-10T16:18:17.298Z |
| 專用管理服務／身分 | 指定名稱查詢均為空，尚未建立 |
| 稽核桶直接政策 | 現有 runtime 的 audit append 自訂角色與 projectOwner legacy bindings；沒有管理服務 grant。此處未把直接政策當成完整有效權限證明 |

只列舉帳號物件 name/generation/size/updated 來統計，**未下載帳號內容或密碼雜湊，未保存物件名稱清單**。本輪未驗證帳號歷史可重播、未製作帳號備份。完整安全摘要 `woundai-institution-evidence-20261007/mmh-iap-update-20261011/store-and-members/management-cloud-baseline.json`。

新增 `tools/check_mmh_account_cutover.py`：接收新鮮快照與另行覆核的 immutable image digest，核對所有對外可達 traffic／tag revision（包含0%舊tag）、100%預設流量、candidate、IAP、身分及共用儲存邊界。任一可達舊 image、遺漏／重複 revision 或過期快照均拒絕。檢查即使通過，也明確回報 `deployment_or_write_authorized=false`；不能取代 CAS source/build provenance、外部寫入工具盤點、歷史重播備份及實際停用帳號驗收。

**測試8/8通過**，已加入 CI 命令與路徑觸發。將真實雲端快照輸入、以現有 candidate digest 作負向對照，正確 exit1：`exposed_old_or_unreviewed_image`。此對照只證明預設入口與candidate仍不同 image，沒有把現有candidate宣稱為CAS-capable。尚未推送／遠端CI。

### 依盤點修正的部署次序

1. 準備管理 image 與 CAS-capable 醫療後端的可重現建置；管理服務維持獨立身分、單 instance／worker，影像模型套件不加入管理 image。
2. 將新增資源與最小IAM列為獨立授權範圍：管理服務、專用SA、其自身IAP/OAuth、對既有MMH入口的服務身分存取、固定MMH IAP政策get/set，以及audit指定前綴的create/get。不能承襲研究／媒體／帳號桶讀取、Secret Manager、Editor、刪除或代用身分權限。IAP setIamPolicy仍能改整個固定資源政策，應用程式限制不等於IAM條件限制。
3. 帳號切換前須協調暫停所有帳號異動工具，取得並驗證受控備份與完整歷史重播；列出所有讀寫端。**只更新candidate不夠**：預設入口的舊程式也可能讀到過時角色／啟用狀態。先前部署授權未包含改預設100%流量，實際切換必須把兩入口一致更新／舊tag處理列入具體授權。
4. 切換後重新取得雲端快照通過上述gate，再對每個入口驗證帳號新增、角色變更、停用、舊JWT拒絕、衝突409與收據。全部通過才恢復管理寫入。
5. 一旦產生CAS紀錄，不直接退回舊讀寫image；失敗時停止帳號異動、保留證據，再部署理解同一資料格式的修正版。未證明可安全回復前，不承諾一鍵回滾。

以上尚未執行資源建立、建置、流量切換或IAM變更；不是新增授權單，也沒有降低既有MMH私有入口保護。
