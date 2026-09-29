# 管理者操作手冊：人員管理與系統管控

管理入口是 **`<後端網址>/console`**，側欄四頁籤，登入後依角色自動展開。
管理者不需要另一個網址、不需要 App、不需要 GCP 帳號。

> 目前後端：`https://woundai-backend-421209514056.asia-east1.run.app/console`

App 內的入口：主畫面 →「設定」→ 連線測試成功後，
「目前身分」下方會出現**開啟管理主控台**按鈕（只有管理者看得到）。

---

## 1. 誰看得到什麼

主控台是**側欄四頁籤**，登入後依後端回傳的 `perms` 決定看得到哪幾個。
**權限矩陣在後端**（`auth_users.PERMS`），前端不自己推導——兩邊各算一次遲早會不一致，
而不一致的那一刻通常是前端顯示得比後端寬鬆。

| 頁籤 | 需要權限 | 醫師 | 護理師 | 助理 | 工程師 | 管理者 |
|---|---|:-:|:-:|:-:|:-:|:-:|
| 飛輪 Dashboard | `flywheel.stats` | ✓ | ✓ | | ✓ | ✓ |
| 系統狀態 | `audit.read` | | | | ✓ | ✓ |
| 稽核軌跡 | `audit.read` | | | | ✓ | ✓ |
| **帳號管理** | `user.manage` | | | | | **✓** |

各頁籤**進到那一頁才去要資料**。第一版四支請求在登入當下全部發出，
其中稽核最慢，於是「只想看收案數」也得等它讀完。
網址帶 `#audit`、`#users` 之類的錨點可直接開到該頁（可加書籤）。

⚠ **前端隱藏不是存取控制。** 隱藏頁籤是為了讓人看得懂自己能做什麼；
真正的拒絕在每個端點的伺服器端檢查。把瀏覽器的 JS 改掉照樣送得出請求，
但後端會回 403 **並留下稽核紀錄**——`test_admin_console.py` §1c 鎖住這條。

**工程師刻意沒有 `user.manage`。** 若有部署權的人也能開帳號，
他就能給自己開一個醫師帳號去產生 `doctor_verified` 的 GT，
飛輪「GT 來自有資格者的判斷」這個前提就沒了。這兩個權限必須分屬不同人。

---

## 2. 人員管理

### 開新帳號

在「帳號管理」區填三格 →「新增（自動產生密碼）」：

| 欄位 | 填什麼 |
|---|---|
| 編號 | `ns05`、`dr04`、`as03`、`eng02`（小寫英數起始，2–31 字） |
| 角色 | 下拉選單 |
| 顯示名稱 | 「護理師 05」這類編號式名稱 |

⚠ **顯示名稱不要填真實姓名。** 識別碼會永久留在稽核軌跡——append-only，
進 WORM 桶後保留 7 年且無法覆寫。編號讓後端從頭到尾不知道任何人的姓名，
**帳號 ↔ 真人的對照表留在院方**，與 `WD-code ↔ 病患` 的對照留在手機是同一個模式。
稽核因此同時滿足「可歸屬」與「不含個資」。

密碼由伺服器產生（14 字，排除 `l1IO0` 等同形字）並**只顯示這一次**——
後端只存加鹽 PBKDF2 雜湊（200,000 次迭代），之後任何人都取不回明文。

配發時：**個別傳給本人**，不要用同一封訊息發給所有人（一次外洩就是全部）。

### 重設密碼

該列的「重設密碼」→ 確認 → 新密碼顯示一次。**舊密碼當場失效。**

沒有「使用者自助重設」是刻意的：自助重設需要另一套身分驗證（信箱所有權），
現在做一個半殘的流程只會多開一條繞過認證的路。等 S3 接院內 SSO 時一併處理。

### 離職／人員異動 → **停用，不刪除**

該列的「停用」→ 立即失效（不是等 token 過期）。

**系統不提供刪除帳號**。稽核軌跡引用著那些識別碼，
刪掉會讓歷史紀錄指向一個不存在的人——那正是稽核最不能發生的事。
停用後帳號仍留在清單（灰色），識別碼**不再配發給別人**。

---

## 3. 系統管控

### 系統狀態

| 看到什麼 | 代表 |
|---|---|
| 整體 `ok` | 正常 |
| 整體 `degraded` | **量測數值不可作臨床用途** |
| ONNX Runtime ✗ | 退回 HSV 色彩分割，面積會錯但仍回 HTTP 200 |
| 分割模型 ✗ | 無法產生遮罩 |
| classify 模組 ✗ | `/api/v1/classify` 直接 503 |

ONNX 那一列是最危險的失敗模式：**它不會報錯**。2026-07 曾因 `requirements.txt`
漏掉 `onnxruntime` 而靜默降級，前端顯示一個看起來完全正常的面積數字。
把降級狀態拉到檯面上就是為了這件事。看到 degraded → 通知工程師重新部署，
在修好之前**不要收案**。

### 稽核軌跡

每筆記錄「誰、以什麼身分、對哪個 WD-代碼、做了什麼、結果如何」。
**沒有任何病患姓名或影像。**

**篩選**：動作、操作者、角色、日期區間（起訖含當日）。
選單的可選值一律從**全量**算，不隨目前篩選縮小——否則選了某個動作之後就再也切不回去。

**分頁**：每頁 50 筆，`offset` 從**最新**往回數。稽核的閱讀習慣是從最近看起；
用「從頭數第幾筆」分頁會讓第 1 頁的內容隨著新紀錄寫入而不斷改變。

**匯出 CSV**：匯出目前篩選結果，含 `hash` 欄（少了它就無法離線驗鏈）。
檔案帶 UTF-8 BOM——沒有 BOM 的話 Excel 會用系統字碼頁開，中文全變亂碼，
而這份檔案是要交給法遵窗口的。**匯出動作本身也會進稽核**：
少了那一筆，一份外流的稽核 CSV 追不到是誰帶走的。

### 雜湊鏈驗證（手動觸發）

按「驗證完整鏈」。它**不會**跟著開頁自動跑——驗證要逐筆重算 SHA-256，是 O(n)
且沒有取巧空間（只驗最後一段等於沒驗）。若每次開頁都跑，紀錄累積後這一頁會慢到
沒人想開，而**不開的主控台等於沒有稽核**。

- **✓ 完整** → 頁面給出「錨定資訊」（head 雜湊 + 驗證時間 + 驗證者 + 筆數）。
  **把它抄進會議紀錄或法遵文件**：之後任何一筆被改、被刪或被調換，
  重新驗證都會對不上這個 head。這是把「請相信我們」變成「你可以自己驗」的關鍵動作。
  建議頻率：每月一次，以及每次法遵查核前。
- **✗ 異常** → 立即通知工程師，並**停止寫入新紀錄**。
  異常分三種：`hash_mismatch`（內容被改）、`broken_link`（前一筆被刪或順序被調換）、
  `fork`（兩筆指向同一前驅，多實例並行寫入或有人補塞）。

平時頁面上顯示的 `head` 只是最後一筆的雜湊（O(1)），**不代表驗證過**——
兩者刻意用不同欄位與不同措辭，避免有人把「有 head」誤讀成「鏈是好的」。

> 誠實邊界：雜湊鏈能**偵測**竄改，**擋不住整份重寫**。
> 有寫入權限的人仍可整條重算。所以稽核另寫一份到 WORM 桶（保留 7 年、無法覆寫）——
> 兩者互補而非替代。

---

## 4. 管理者**不能**做的事（刻意）

| 做不到 | 為什麼 |
|---|---|
| 看病患姓名／病歷號 | 雲端從來沒有。PII 以 Keystore 加密留在手機，只有 `WD-` 代碼上雲 |
| 看傷口影像 | 主控台不含影像檢視。加進來會讓這一頁從「無 PII」變成「有影像」，是完全不同的資安等級 |
| 刪除帳號 | 稽核軌跡引用著識別碼 |
| 刪除稽核紀錄 | append-only ＋ WORM 桶 |
| 產生 `doctor_verified` | 只有醫師角色可以。管理者不是臨床角色 |
| 存入病歷／送訓練標註 | 同上 |

**GCP Console 是另一套權限系統。** App 角色 ≠ GCP IAM。
有 IAM 的人能直接讀儲存桶裡的原始傷口影像、看 Secret、刪資源，
完全繞過本 App 所有閘門。**臨床角色一律不給 GCP IAM**，
管理者若無工程需求也不需要。

---

## 5. 用指令批次開通（替代路徑）

一次開 10 組帳號用腳本比較快：

```powershell
cd C:\dev\WoundAI_Proj\Backend\Flask
$u = "https://woundai-backend-421209514056.asia-east1.run.app"

.\provision_users.ps1 -BaseUrl $u -List                     # 列出現有帳號
.\provision_users.ps1 -BaseUrl $u                           # 開通預設 10 組（編號式）
.\provision_users.ps1 -BaseUrl $u -Disable dr.chen,ns.liu   # 停用
```

產出的 `accounts_*.csv` 含明文密碼：個別傳給本人後**立即刪除**，不要進版控
（`.gitignore` 已排除）。「配發給」欄由院方填寫，那份對照表留在院方。

### 顯示名稱變成「?? 01」怎麼辦

```powershell
.\provision_users.ps1 -BaseUrl $u -FixNames   # 只改名，不動密碼、不動啟用狀態
```

或直接在 `/console` 帳號列表按「改名」。

**成因**（2026-08-04 十組帳號全中）：PowerShell 5.1 的 `Invoke-RestMethod` 在
`-ContentType "application/json"`（沒帶 charset）時，會以 **ISO-8859-1** 編碼請求主體。
中文全部落在該編碼之外，每個字變成 `?` 送出去，而請求**照樣回 200**——
伺服器把 `?` 當合法字串存下來。沒有錯誤、沒有警告、資料已經壞了。

判別方法：帳號列表的 `role_zh` 欄（伺服器端常數）中文正常，
而 `display_name` 欄（客戶端送上去的）是問號 —— 同一個回應、同一個終端機，
所以不可能是傳輸或顯示編碼，只可能是**送出時就壞了**。

腳本已改為自行編成 UTF-8 位元組陣列送出。瀏覽器一律以 UTF-8 送出，
所以 `/console` 從來不受這個問題影響，是最不會出事的改名途徑。

---

## 6. 送審測試帳號（App Review 專用）

Apple 審查需要一組能登入的帳號。**不要用 /console 手動建**——候選版本跑
`WOUNDAI_STORE=local`，而 LocalStore 的根目錄在容器裡，Cloud Run 一回收執行個體
帳號就沒了。審查員通常隔幾天才登入，那時帳號已不存在，結果就是被拒。

所以送審帳號由後端在**每次冷啟動時重建**（`auth_users.seed_demo_from_env`）。

### 這個出口有多窄

| 關卡 | 規則 |
|---|---|
| 儲存層 | 必須是 LocalStore。**問物件不問環境變數**。正式服務是 gcs，種子在那裡永遠不動 |
| 帳號名 | 必須是 `demoNN` 形狀。種不出 `admin2`，而且稽核裡一眼認得出 |
| 角色（名單） | 只有 `nurse`／`assistant` |
| 角色（權限） | 該角色若持有 `gt.verify`／`annotation.submit`／`user.manage`／`audit.read`／`gcp.console`／`backend.config` 任一項就**拒絕** |
| 密碼 | 只從環境變數來。程式碼裡沒有，後端也不隨機產生 |
| 既有帳號 | 已存在就完全不動。不覆蓋密碼，**不把停用的帳號重新啟用** |

第四道是為了未來：若哪天有人給 `nurse` 加上 `gt.verify`，種子會拒絕，
而不是安靜地把醫師背書交給外部審查員。

### 為什麼是 `nurse`

`physician` 帶 `gt.verify` 與 `annotation.submit`——那是 `doctor_verified`
的唯一來源。交給外部審查員，等於讓陌生人有辦法把資料以醫師身分送進訓練集
（`auth_users.py` 的 `lite` 角色註解記錄了同一個錯誤已經發生過一次）。

`assistant` 則沒有 `flywheel.stats`，審查員一開紀錄頁就 403，看起來像 App 壞了。

`nurse` 是能走完整套流程、又不帶醫師背書與任何管理權的最小角色。

### 為什麼示範服務的每一把金鑰都必須是自己的

正式服務驗 access token 只看簽章，**角色直接取自 token 自己的 claim，不回查帳號
是否存在**，效期 24 小時（`app.py` 的 `JWT_ACCESS_TOKEN_EXPIRES`、`api_flywheel.py`
的 `_who`）。所以兩個服務只要共用 JWT 簽章金鑰，審查員在示範服務登入 `demo01`
拿到的 nurse token，送到正式服務也會被當成 nurse 接受——而 nurse 在正式服務上
可以對任意 WD 代碼撤回或恢復同意。care receipt 的 HMAC 金鑰同理。

2026-09-23 之前的示範腳本就掛著正式的三把密文（JWT、Flask、管理者密碼），而且
通過了全部測試與 CI 閘門，因為沒有任何測試在看示範服務掛了哪些密文。那一版從未
部署。現在示範服務用自己的四把 `woundai-demo-*` 密文，**不掛管理者密碼**
（示範服務不需要管理者，送審帳號由開機種子建立）。

另一個相關但獨立的既有問題：`/console` 停用帳號只會讓**之後的密碼登入**失敗，
已簽發的 token 仍有效到 24 小時期滿。這是正式服務本身的行為，不在本節處理範圍。

### 建立示範身分與四把密文（密碼全程不經過對話，也不進版控）

PowerShell 5.1。`RNGCryptoServiceProvider` 是密碼學等級亂數。送審密碼的字元集沿用
`api_users._gen_password`，排除 `l/1/I/O/0` 這些同形字——密碼要靠人抄寫；
`-lt 224` 是拒絕取樣（224 = 56 × 4），少了它會有模數偏差。另外三把金鑰從頭到尾
**不顯示**，也沒有任何人需要知道它們的值。

示範身分**只**授予這四把密文的 `secretAccessor`，專案層級**不給任何角色**，
也不要把它加進任何群組。不要對它跑 `provision_runtime_identity.ps1`——那會授予它
正式密文，部署腳本會因此拒絕部署。

```powershell
$proj = 'woundai-jackh001'
$sa   = "woundai-demo-run@$proj.iam.gserviceaccount.com"
$rng  = New-Object System.Security.Cryptography.RNGCryptoServiceProvider
function New-B64Url([int]$Bytes) {
    $b = New-Object byte[] $Bytes
    $rng.GetBytes($b)
    [Convert]::ToBase64String($b).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

# 1. 示範服務自己的執行身分
gcloud iam service-accounts create woundai-demo-run --project $proj `
    --display-name "WoundAI demo service (App Review)"

# 2. 送審密碼：人要抄寫，所以用無同形字的字元集
$chars = 'abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789'
$pw = ''
while ($pw.Length -lt 24) {
    $b = New-Object byte[] 1
    $rng.GetBytes($b)
    if ($b[0] -lt 224) { $pw += $chars[$b[0] % 56] }
}

# 3. 另外三把：值從不顯示
$values = [ordered]@{
    'woundai-demo-password'            = $pw
    'woundai-demo-jwt-secret'          = (New-B64Url 48)
    'woundai-demo-flask-secret'        = (New-B64Url 48)
    'woundai-demo-care-receipt-secret' = (@{ active_kid = 'demo1'; keys = @{
                                             demo1 = @{ secret_b64 = (New-B64Url 32) } } } |
                                          ConvertTo-Json -Compress -Depth 5)
}
foreach ($name in $values.Keys) {
    gcloud secrets create $name --replication-policy=automatic --project $proj
    $values[$name] | gcloud secrets versions add $name --data-file=- --project $proj
    gcloud secrets add-iam-policy-binding $name --project $proj `
        --member "serviceAccount:$sa" --role roles/secretmanager.secretAccessor
}

$pw            # ← 讀這一次，直接填進 App Store Connect「登入資訊」，不要貼到任何對話
Remove-Variable pw, values, rng, b
```

**關於結尾換行。** PowerShell 把字串管線給原生程式時會補一個換行（5.1 補 CRLF），
所以上面存進去的每一把密文結尾都帶著它。這是預期中的，由程式端處理：
`resolve_secret` 會 strip JWT／Flask 金鑰，`json.loads` 容許 care receipt JSON 結尾的
空白，種子會去掉密碼結尾的 CR/LF（並拒絕任何其他空白或控制字元）。
**不要把種子裡那個 `rstrip` 拿掉**——少了它，帳號密碼會以 CRLF 結尾，
審查員照著輸入永遠登不進去。`test_demo_seed_guardrails.py` 有測試釘住這一點。

### 驗證示範身分的「有效」權限

上面只保證「我們授予了什麼」，不保證「它實際能做什麼」。專案、資料夾、組織層級的
角色會繼承到每一把密文，群組成員資格與拒絕政策也會改變結果，而這些都不會出現在
單一密文自己的政策裡——**某把密文上沒有直接授權，不足以證明示範身分讀不到它**
（[IAM 繼承說明](https://cloud.google.com/iam/docs/resource-hierarchy-access-control)）。

**「現在讀不到」也不等於「拿不到」。** 2026-09-27 覆核指出：示範身分若經群組取得
`roles/resourcemanager.projectIamAdmin`，四把正式密文的有效權限都會是
`CANNOT_ACCESS`，但它可以改專案 IAM，把 `secretAccessor` 授給自己
（[設定 IAM 政策本身就能授予其他權限](https://cloud.google.com/resource-manager/docs/access-control-proj)）。
冒用別的服務帳號也是同一類路徑：冒用正式執行身分就讀得到正式密文。

所以佈建完要查下面三件事，部署腳本每次部署前也會重查前兩件：

1. **專案與上層的每一個綁定**（`get-ancestors-iam-policy`）：示範身分不得出現；
   群組、網域、`allUsers`／`allAuthenticatedUsers`、`principal://`／`principalSet://`
   出現在任何綁定裡也拒絕，不論角色。群組成員資格在這裡**證明不了**——Cloud Identity
   的成員檢查只在 Workspace／Cloud Identity 的特定方案可用，而且只看得到你有權檢視的
   成員關係，查不到不代表不是成員
   （[checkTransitiveMembership](https://cloud.google.com/identity/docs/reference/rest/v1/groups.memberships/checkTransitiveMembership)）。
   證明不了就不放行：專案或上層若有這類綁定，要先移除，或改授予在真正需要的資源上、
   不放在專案層級，示範服務才部署得了。四把正式密文自己的政策也一樣：示範身分不得出現，
   群組、網域與公開主體也不得出現——讀得到正式密文的，必須是點得出名字的帳號，第 2 項
   才能逐一檢查。
2. **Policy Troubleshooter 的有效權限**（會計算上層繼承、看得到的群組與拒絕政策；
   看不清的回 `UNKNOWN_*`，一律不算數）。下列每一項都必須是 `CANNOT_ACCESS`：
   - 四把正式密文：`secretmanager.versions.access`、`secretmanager.secrets.setIamPolicy`
   - 專案：`resourcemanager.projects.setIamPolicy`
   - 專案裡的每一個服務帳號、正式執行身分，以及專案、上層與正式密文政策裡點名的每一個
     服務帳號——包括別的專案的：`iam.serviceAccounts.actAs`、`getAccessToken`、
     `getOpenIdToken`、`signBlob`、`signJwt`、`implicitDelegation`、
     `iam.serviceAccountKeys.create`、`iam.serviceAccounts.setIamPolicy`。
     不問的只有示範身分自己，和本專案的 Google 帳號——整個 email 逐字比對，不看名字樣式：
     `<專案編號>@cloudservices.gserviceaccount.com`（Google API 服務代理）、
     `<專案編號>@cloudbuild.gserviceaccount.com`（舊版 Cloud Build 帳號），以及
     `service-<專案編號>@` 加上下表其中一個網域。這些帳號不建在任何客戶專案裡，客戶也無法
     直接存取它們（[服務帳號類型](https://cloud.google.com/iam/docs/service-account-types#service-agents)）；
     舊版 Cloud Build 帳號上加不了 IAM 綁定，沒有主體能被授權替它產生 token
     （[Cloud Build 服務帳號](https://cloud.google.com/build/docs/cloud-build-service-account)）。
     沒有人能把冒用它們的權限授予示範身分；它們的政策在 Google 的專案裡，這裡讀不到，而
     Troubleshooter 對讀不到的政策只會回 Unknown
     （[Policy Troubleshooter](https://cloud.google.com/policy-intelligence/docs/troubleshoot-access)）。

     | 網域 | 服務代理 | 查證 |
     |---|---|---|
     | `containerregistry.iam.gserviceaccount.com` | Container Registry | [文件](https://cloud.google.com/functions/docs/concepts/iam) |
     | `gcp-sa-artifactregistry.iam.gserviceaccount.com` | Artifact Registry | [文件](https://cloud.google.com/artifact-registry/docs/ar-service-account) |
     | `gcp-sa-cloudbuild.iam.gserviceaccount.com` | Cloud Build | [文件](https://cloud.google.com/build/docs/securing-builds/configure-access-for-cloud-build-service-account) |
     | `gcp-sa-cloudscheduler.iam.gserviceaccount.com` | Cloud Scheduler | [文件](https://cloud.google.com/scheduler/docs/http-target-auth) |
     | `gcp-sa-pubsub.iam.gserviceaccount.com` | Pub/Sub | [文件](https://cloud.google.com/pubsub/docs/authenticate-push-subscriptions) |
     | `serverless-robot-prod.iam.gserviceaccount.com` | Cloud Run | [文件](https://cloud.google.com/run/docs/configuring/services/service-identity) |

     名單是 2026-09-27 盤點本專案政策時實際出現的六個，不預先放其他服務的。不用名字樣式
     比對，是因為任何專案都能建一個叫 `service-<我們的編號>@它的專案.iam.gserviceaccount.com`
     的帳號，而那個帳號能被誰冒用由它的專案決定（同日自我覆核）。名單以外的帳號——別的
     專案的服務代理、本專案之後啟用的服務的代理——都照樣問，問不出結果就拒絕部署；在文件
     確認是 Google 服務代理後，經審查加進 `Get-GoogleServiceAgentDomains` 與下面的驗證區塊
     （兩份由測試比對）。
   - 密文的資源全名必須用**專案編號**：`//secretmanager.googleapis.com/projects/<專案編號>/secrets/<名稱>`。
     2026-09-29 對本專案實測，用專案 ID 時 Troubleshooter 與 `gcloud iam list-testable-permissions`
     不論問哪個權限都回 `INVALID_ARGUMENT`——那是請求被拒，不是存取判定——換成專案編號才有答案。
     專案（`//cloudresourcemanager.googleapis.com/projects/<專案 ID>`）與服務帳號
     （`//iam.googleapis.com/projects/-/serviceAccounts/<email>`）同日實測都接受。
   - 不存在的正式密文不問：沒有內容可讀、沒有政策可改，Troubleshooter 也無從評估不存在的
     資源（2026-09-27 盤點時 `woundai-care-receipt-secret` 尚未建立）。建立之後自動回到
     檢查範圍。示範身分也建立不了它：建立密文要專案層級的權限，而第 1 項確認示範身分在
     專案與上層沒有任何角色。
3. 四把示範密文的 `secretmanager.versions.access` 是 `CAN_ACCESS`（否則示範服務讀不到自己的金鑰）。

範圍：第 2 項是**一跳**——示範身分能不能直接冒用上面那些帳號。2026-09-27 第二次覆核
指出，前一版漏了「政策裡點名的外部帳號」：外部帳號 B 被授予讀正式密文、示範身分能替 B
簽發 token，三道檢查卻都通過，因為從沒問到 B。經過其他專案、又沒被上述政策點名的帳號
轉手的多跳冒用鏈，這裡不分析；示範身分只在本專案使用，不要在其他專案授予它任何權限。

```powershell
& {
    $proj = 'woundai-jackh001'
    $sa   = "woundai-demo-run@$proj.iam.gserviceaccount.com"
    $prodSecrets = 'woundai-admin-password', 'woundai-jwt-secret', 'woundai-flask-secret',
                   'woundai-care-receipt-secret'
    # 與 deploy_demo_candidate.ps1 的 Get-GoogleServiceAgentDomains 相同（測試比對）
    $agentDomains = 'containerregistry.iam.gserviceaccount.com', 'gcp-sa-artifactregistry.iam.gserviceaccount.com',
                    'gcp-sa-cloudbuild.iam.gserviceaccount.com', 'gcp-sa-cloudscheduler.iam.gserviceaccount.com',
                    'gcp-sa-pubsub.iam.gserviceaccount.com', 'serverless-robot-prod.iam.gserviceaccount.com'

    # Policy Troubleshooter API：一次性的專案設定，部署腳本不會替你打開它
    gcloud services enable policytroubleshooter.googleapis.com --project $proj

    # 1. 專案、上層與正式密文的政策：示範身分不得出現，證明不了不含它的主體也不得出現
    $policies = @()
    $ancestors = gcloud projects get-ancestors-iam-policy $proj --format=json | ConvertFrom-Json
    foreach ($e in @($ancestors)) { $policies += [pscustomobject]@{ Where = "$($e.type)/$($e.id)"; Policy = $e.policy } }
    $present = @()
    foreach ($n in $prodSecrets) {
        $out = @(gcloud secrets get-iam-policy $n --project $proj --format=json 2>&1)
        if ($LASTEXITCODE -eq 0) {
            $pol = @($out | Where-Object { $_ -isnot [Management.Automation.ErrorRecord] }) -join "`n" | ConvertFrom-Json
            $policies += [pscustomobject]@{ Where = "secret/$n"; Policy = $pol }
            $present += $n
        } elseif ("$out" -match 'NOT_FOUND') {
            "正式密文 $n 不存在：沒有內容可讀，第 2 項不問它"
        } else {
            throw "讀不到正式密文 $n 的政策：$out"
        }
    }
    $bad = @()
    $named = @()
    foreach ($x in $policies) {
        foreach ($b in @($x.Policy.bindings)) {
            foreach ($m in @($b.members)) {
                if (-not $m) { continue }
                $isDemo = ($m -ieq "serviceAccount:$sa") -or ($m -ieq "user:$sa")
                $known  = $m.StartsWith('user:') -or $m.StartsWith('serviceAccount:') -or $m.StartsWith('deleted:')
                if ($isDemo -or -not $known) { $bad += "$($x.Where) $($b.role) <- $m" }
                if ($m.StartsWith('serviceAccount:')) { $named += $m.Substring(15).ToLower() }
            }
        }
    }
    "專案、上層與正式密文：$($bad.Count) 個不該出現的綁定（應為 0）"
    $bad

    # 2. 每一項都應為 CANNOT_ACCESS；3. 示範密文應為 CAN_ACCESS
    # 密文要用專案編號命名：用專案 ID，Troubleshooter 會回 INVALID_ARGUMENT（2026-09-29 實測）
    $number = gcloud projects describe $proj --format='value(projectNumber)'
    if ($number -notmatch '^[0-9]{6,20}$') { throw "讀不到專案編號：$number" }
    $checks = @()
    foreach ($n in $present) {
        foreach ($p in 'secretmanager.versions.access', 'secretmanager.secrets.setIamPolicy') {
            $checks += [pscustomobject]@{ R = "//secretmanager.googleapis.com/projects/$number/secrets/$n"
                                          P = $p; Want = 'CANNOT_ACCESS' }
        }
    }
    $checks += [pscustomobject]@{ R = "//cloudresourcemanager.googleapis.com/projects/$proj"
                                  P = 'resourcemanager.projects.setIamPolicy'; Want = 'CANNOT_ACCESS' }
    $prodSa = gcloud run services describe woundai-backend --project $proj --region asia-east1 `
        --format='value(spec.template.spec.serviceAccountName)'
    $listed = gcloud iam service-accounts list --project $proj --format=json | ConvertFrom-Json
    $listedEmails = @(@($listed) | ForEach-Object { ([string]$_.email).ToLower() })
    $agentEmails = @($agentDomains | ForEach-Object { "service-$number@$_" }) +
                   @("$number@cloudservices.gserviceaccount.com", "$number@cloudbuild.gserviceaccount.com")
    $agents = @($named | Where-Object { $listedEmails -notcontains $_ -and $agentEmails -ccontains $_ } |
        Sort-Object -Unique)
    $others = @(@($listedEmails) + ([string]$prodSa).ToLower() + @($named) |
        Where-Object { $_ -and $_ -ne $sa -and $agents -notcontains $_ } | Sort-Object -Unique)
    foreach ($o in $others) {
        foreach ($p in 'iam.serviceAccounts.actAs', 'iam.serviceAccounts.getAccessToken',
                       'iam.serviceAccounts.getOpenIdToken', 'iam.serviceAccounts.signBlob',
                       'iam.serviceAccounts.signJwt', 'iam.serviceAccounts.implicitDelegation',
                       'iam.serviceAccountKeys.create', 'iam.serviceAccounts.setIamPolicy') {
            $checks += [pscustomobject]@{ R = "//iam.googleapis.com/projects/-/serviceAccounts/$o"
                                          P = $p; Want = 'CANNOT_ACCESS' }
        }
    }
    foreach ($n in 'woundai-demo-password', 'woundai-demo-jwt-secret', 'woundai-demo-flask-secret',
                   'woundai-demo-care-receipt-secret') {
        $checks += [pscustomobject]@{ R = "//secretmanager.googleapis.com/projects/$number/secrets/$n"
                                      P = 'secretmanager.versions.access'; Want = 'CAN_ACCESS' }
    }
    $wrong = 0
    foreach ($c in $checks) {
        $got = (gcloud policy-intelligence troubleshoot-policy iam $c.R "--principal-email=$sa" `
            "--permission=$($c.P)" --project $proj --quiet --format=json | ConvertFrom-Json).overallAccessState
        if ($got -ne $c.Want) { $wrong++; "不符 $($c.R) $($c.P)：$got（應為 $($c.Want)）" }
    }
    "檢查的服務帳號：$($others -join ', ')"
    "未詢問的本專案 Google 帳號：$($agents -join ', ')"
    "有效權限：$($checks.Count) 項，不符 $wrong 項（應為 0）"
}
```

第 1 項不是 0，或有效權限有任何不符，就先停下來處理，不要部署。

### 部署示範服務

用 `deploy_demo_candidate.ps1`，**不要**用 `deploy_cloudrun.ps1`。

兩支腳本刻意分離。示範版跑 `WOUNDAI_STORE=local`；若它和正式版住在同一個
Cloud Run service，任何人對那個 service 下 `update-traffic` 都可能把正式流量
導到 local-store revision 上——服務看起來正常，但資料不再落 GCS、稽核鏈隨實例
分叉，而且沒有任何錯誤訊息。分成兩個 service 之後這件事不是「很小心所以不會
發生」，而是**做不到**：流量路由跨不了 service 邊界。

（`deploy_cloudrun.ps1` 的 `Assert-CloudRunRevisionConfiguration` 也把
`WOUNDAI_STORE = 'gcs'` 當不變量檢查，所以 local-store revision 出現在那個
service 的清單裡，對正式路徑同樣是地雷。）

```powershell
cd C:\dev\WoundAI_Proj\Backend\Flask

.\deploy_demo_candidate.ps1 `
    -ProjectId woundai-jackh001 `
    -RuntimeServiceAccount woundai-demo-run@woundai-jackh001.iam.gserviceaccount.com `
    -DemoAuthorisationRef "<你親手寫的授權字句，要指名用途與日期>"
```

`-DemoAuthorisationRef` 會機械拒絕佔位符形狀（空白、換行、`placeholder`、
`範本`、`填入`、`TODO`、`xxx`、太短）。這個欄位的意義是「有人想過才按下去」，
貼樣板等於沒有授權。

腳本做不到的事（機械上）：

| 做不到 | 怎麼擋的 |
|---|---|
| 切正式流量 | 沒有任何一行程式碼呼叫 `update-traffic`，且拒絕以正式 service 為目標 |
| 寫 GCS | 沒有 `-Bucket`／`-AuditBucket` 參數，`WOUNDAI_STORE` 寫死 local |
| 用正式執行身分 | 部署前讀正式 service 的 SA，**讀不到就停**（權限不足、登入過期、網路中斷都算），相同就拒絕；部署後回讀示範 revision 實際跑的身分再比一次 |
| 用正式金鑰，或讓示範身分自己拿到 | 四個密文參數必須是 `woundai-demo-*` 且彼此不同；部署前讀每把正式密文的 IAM 政策，示範身分、群組、網域或公開主體在任何綁定裡就拒絕；示範身分在專案或上層有任何角色就拒絕——直接、經群組、網域或公開主體都算，證明不了就拒絕；以 Policy Troubleshooter 查有效權限：讀正式密文、把它授權給自己、改專案 IAM、冒用專案裡的服務帳號、正式執行身分或這些政策點名的任何服務帳號（含外部的），任何一項不是 `CANNOT_ACCESS` 就拒絕；部署後回讀 revision 的 `secretKeyRef` |
| 帶管理者憑證 | 不掛 `ADMIN_PASSWORD`，回讀時發現就失敗 |
| 種出醫師角色 | 角色白名單只有 `nurse`／`assistant`，後端再驗一次 |
| 建出缺模組的映像 | 建置前把 engineering 模組複製到 `vendor/`，清單與 `deploy_cloudrun.ps1` 逐項相同（測試比對）；缺任何一個就停 |
| 驗收沒過還說「完成」 | 部署後每一項檢查都是 `throw`，沒有一項只是警告（見下） |

**實例上限是 1，但這是上限，不是保證。** `--max-instances 1` 寫死，部署後也回讀
revision 的 `maxScale`。目的是讓第二個實例盡量不要出現，這是正確性考量，不是省錢：
LocalStore 的鏈完整性鎖在行程內，兩個實例就是兩份互不相干的資料。但 Cloud Run
在流量突增時可能短暫超過上限，而且上限按 revision 各自計算，部署交接期間新舊
revision 可能同時有實例（[Cloud Run 說明](https://cloud.google.com/run/docs/configuring/max-instances-limits)）。
所以腳本能保證的只有「設定是 1」。登入不受影響——每個實例開機都用同一份密文重建
同一個帳號、用同一把示範 JWT 金鑰；受影響的是資料連續性，而那本來就不承諾
（見下方「種子解決的是登得進去」）。前一版把「多實例」列在上表的「做不到」裡，
2026-09-25 覆核指出那是超出事實的保證。

部署後的驗收依序如下，**任何一項不成立都中止，「完成」只在全部通過後才印**：

1. service：最新建立的 revision 已就緒、就是這次建的那一版、100% 流量在它身上
2. revision 回讀（不是相信 `--set-env-vars` 傳了什麼）：執行身分、Ready、
   環境變數含 `GIT_COMMIT`、掛的密文恰好是四把示範密文、實例上限 1
3. `/api/health`：`healthy`，分割模型、classify、色準、端點、canonical golden
   全部就位；`build.git_commit` 等於本機完整 SHA、`build.revision` 等於上一步
   那一版；`store` 是 `local:`；care receipt 金鑰已設定
4. **這個 revision 自己的**日誌裡有 `[demo-seed:ok]`，且沒有 `refused`／`error`

2026-09-23 覆核 `b24a47c` 時發現前一版在這裡的缺口：建置沒有 `vendor/`
（服務能登入、量測 503）；commit 讀成不存在的 `health.git_commit`（實際在
`build` 底下），版本不符與找不到種子紀錄都只是黃字；正式身分讀不到時跳過比對。
第 3 項只剩 A∪U 模型檔缺席是提醒而不中止——它刻意不在 `degraded` 裡（產品決策，
見 `app.py`）。

### 確認種子真的跑了

部署腳本的第 4 項驗收已經做了這件事；這裡是手動重查的方法。

種子每一種結果都會在啟動日誌印一行，開頭是 ASCII 標記——**拒絕是看得見的**，
安靜地什麼都不做會變成審查員回報登不進去的那天才發現：

| 標記 | 意思 |
|---|---|
| `[demo-seed:ok]` | 帳號已建立 |
| `[demo-seed:exists]` | 帳號已存在、不覆蓋（同一容器裡 worker 重啟時會出現，正常） |
| `[demo-seed:refused]` | 設定被拒絕，後面接原因 |
| `[demo-seed:error]` | 例外，後面接原因 |

標記用 ASCII 是因為 Windows PowerShell 5.1 會用主控台字碼頁解讀原生程式的
輸出，後面的中文可能變成亂碼；標記本身不受影響。那一行也一定會 flush——
Cloud Run 的 stdout 是管線，不 flush 的話它可能一直躺在緩衝區裡。

**只看示範 service、只看目前這個 revision**，否則舊 revision 的成功紀錄會冒充新版：

```powershell
& {
    $proj = 'woundai-jackh001'
    $rev = gcloud run services describe woundai-backend-demo --region asia-east1 `
        --project $proj --format "value(status.latestReadyRevisionName)"
    gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=woundai-backend-demo AND resource.labels.revision_name=$rev" `
        --project $proj --order=asc --limit=1000 --format "value(textPayload)" |
        Select-String -CaseSensitive '\[demo-seed:'
}
```

成功長這樣：`[demo-seed:ok] 已重建送審測試帳號 default:demo01（角色 nurse）`

### ⚠ 種子解決的是「登得進去」，**不是「資料還在」**

這一點務必講清楚，否則會在送審時踩到：

冷啟動重建的只有**帳號**。影像、receipt、稽核鏈全部躺在同一個容器檔案系統裡，
實例一回收就消失（[Cloud Run 檔案系統說明](https://cloud.google.com/run/docs/container-contract#file_system)）。
所以審查員若在實例 A 完成量測，之後被導到實例 B，他的紀錄不在那裡——
**他會看到一個空的紀錄列表，然後合理地回報「App 存不了東西」**。

實務上要求：

- 送審流程必須能在**單一連續工作階段**內走完，不依賴跨階段留存
- 審查說明（App Review Notes）要明說這是示範環境、資料不留存
- 不要把 gcsfuse ＋ 單實例當成解法：FUSE **沒有同檔多寫者鎖定**，
  而容器本身仍是多執行緒的，多寫者會直接產生鏈分叉
  （[官方限制](https://cloud.google.com/run/docs/configuring/services/cloud-storage-volume-mounts)）
- 真正的資料留存要等新紀元桶，那是另一條路線

### 停用不是持久的（跨冷啟動會被重建）

**先前這份文件寫「種子不會把停用的帳號重新啟用」，那句話只在同一個實例上成立。**

「已存在就不動」比對的是**目前這個實例的帳號檔**。實例回收後檔案是空的，
帳號不存在，於是種子照常建立一個**啟用中**的 `demo01`。
在 `/console` 停用只會擋住當前實例，下一次冷啟動就失效。

唯一可靠的關閉方式：**把三個環境變數與密文拿掉，重新部署。**

（`test_demo_seed_guardrails.py` 兩支測試分別釘住這兩種情境，
`..._on_the_same_instance` 與 `..._cold_start_on_an_empty_store_recreates_...`。）

### 送審結束後

把 `WOUNDAI_DEMO_SEED_USER`／`ROLE`／`PASSWORD` 三個變數與密文拿掉，重新部署——
這個出口就跟著消失。

### 角色涵蓋範圍要反映在審查說明裡

`nurse` 走得完量測與紀錄，但**沒有** `gt.verify` 與 `annotation.submit`。
審查說明與 UI 不可宣稱「完整醫師流程均可操作」——
審查員點得到卻做不到的按鈕，本身就是被退件的理由。

---

## 7. 驗證

```bash
python engineering/phase2/test_admin_console.py   # 56 項，全通過
python engineering/phase2/test_rbac.py            # 37 項
```

鎖住的線：前端隱藏不是存取控制、權限分層真的分開、停用立即生效、
帳號異動可歸屬、鏈驗證預設不跑但按了要真的驗、
分頁 offset 從最新往回數、篩選選單不隨篩選縮小、CSV 帶 BOM 且含 hash、
匯出與驗證本身都留痕。

---

## 8. 效能邊界（誠實說明）

稽核查詢仍是 **O(n)**：每次請求都要讀完整份紀錄才能篩選與計數。
目前的緩解有兩層，但都不是根治：

1. **鏈驗證改成手動**——把最貴的部分（逐筆 SHA-256）從每次開頁移走。
2. **append-only 增量快取**（`store.GcsStore`）——GCS 是一筆一物件，
   讀一萬筆稽核＝一萬次 GET。快取讓同一個 Cloud Run 實例只下載沒讀過的物件。
   列舉仍每次都做，所以**別的實例寫入的新紀錄一定看得到**，正確性不依賴快取。

真正的根治是把小物件定期合併成大檔案，但**稽核桶是 WORM，物件依設計刪不掉**，
所以合併不適用於稽核（可用於佇列）。到幾十萬筆量級時的正解是外部索引
（例如 BigQuery 匯出），列為後續。

以目前 n=20 收案的量級（每天數十筆稽核），這一頁是即時的。

## 9. 尚未做的（S2／S3）

- **多機構隔離**。識別碼格式 `<org>:<user>` 已經帶著 org，但目前所有人都在 `default`，
  且**沒有跨機構的資料隔離**。多院區上線前必須補（見 `rbac_design.md` §6）。
- **院內 SSO**。目前 App 仍會碰到密碼。正解是後端接 SSO 發短效 token。
- **標註審核與模型晉升介面**。需要影像檢視，見 `cloud_console_ui_spec.md` C1 之後。
- `rbac_design.md` §9 有五個需要院方回答的問題（org 粒度、住院醫師可否確認 GT、
  流程中是否有助理、誰核發與覆核帳號、離職處理時限）——這些不是技術決定。
