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

示範身分**只**授予這四把密文的 `secretAccessor`。不要對它跑
`provision_runtime_identity.ps1`——那會授予它正式密文，部署腳本會因此拒絕部署。

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
| 用正式執行身分 | 部署前讀正式 service 的 SA 比對，相同就拒絕 |
| 用正式金鑰 | 四個密文參數必須是 `woundai-demo-*` 且彼此不同；部署前讀每把正式密文的 IAM 政策，示範身分在任何綁定裡就拒絕；部署後回讀 revision 的 `secretKeyRef` |
| 帶管理者憑證 | 不掛 `ADMIN_PASSWORD`，回讀時發現就失敗 |
| 多實例 | `--max-instances 1` 寫死，且部署後回讀 revision 的 `maxScale` 確認 |
| 種出醫師角色 | 角色白名單只有 `nurse`／`assistant`，後端再驗一次 |

最後一項的單實例是**正確性需求**，不是省錢：LocalStore 的鏈完整性鎖在行程內，
兩個實例就是兩份互不相干的資料，審查員會在連續兩次請求之間看到不同的紀錄列表。

腳本部署後會自己回讀 revision 設定（不是相信 `--set-env-vars` 傳了什麼）、
打 `/api/health` 確認 `store` 是 `local:` 且 care receipt 金鑰已就緒、
並讀啟動日誌確認種子真的跑過。

### 確認種子真的跑了

種子只要沒執行就會把原因印在啟動日誌——**拒絕是看得見的**，
安靜地什麼都不做會變成審查員回報登不進去的那天才發現。

```powershell
gcloud run services logs read woundai-backend --region asia-east1 --limit 50 --project $proj |
    Select-String 'demo 種子|已重建送審測試帳號'
```

成功長這樣：`已重建送審測試帳號 default:demo01（角色 nurse）`

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
