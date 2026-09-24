# 送審示範服務的部署腳本。**與 deploy_cloudrun.ps1 完全分離，這是刻意的。**
#
# ## 為什麼是「另一個 service」而不是同一個 service 的 no-traffic revision
#
# 示範版必須跑 WOUNDAI_STORE=local。如果它和正式版住在同一個 Cloud Run service，
# 就存在一條災難路徑：任何人對那個 service 下 `update-traffic`，都可能把正式流量
# 導到一個 local-store 的 revision 上——服務看起來完全正常，但資料不再落 GCS、
# 稽核鏈隨實例分叉，而且**沒有任何錯誤訊息**。等到發現時，中間那段資料已經沒了。
#
# 分成兩個 service 之後，這件事不是「我們很小心所以不會發生」，而是
# **做不到**：Cloud Run 的流量路由是 service 內部的操作，跨不了 service 邊界。
#
# 同理，deploy_cloudrun.ps1 的 Assert-CloudRunRevisionConfiguration 把
# `WOUNDAI_STORE = 'gcs'` 當不變量檢查；讓一個 local-store revision 出現在
# 那個 service 的 revision 清單裡，等於在正式路徑上埋一顆地雷。
#
# ## 這支腳本做不到的事（機械上，不是靠自律）
#
#   * 不能切正式流量：沒有任何一行**程式碼**呼叫 update-traffic
#     （註解裡提到它是在解釋為何不存在；靜態測試會先剔掉註解再檢查），
#     且拒絕以正式 service 為目標
#   * 不能寫 GCS：沒有 -Bucket／-AuditBucket 參數，WOUNDAI_STORE 寫死 local
#   * 不能用正式執行身分：部署前讀正式 service 目前的 SA，**讀不到就停**
#     （不知道正式身分是誰，就不能宣稱兩者不同），相同就拒絕；部署後再回讀
#     示範 revision 實際跑的身分，兩邊再比一次
#   * 不能多實例：--max-instances 1 寫死
#   * 不能用正式金鑰：JWT、Flask、care receipt 各用自己的 woundai-demo-* 密文，
#     也不掛正式管理者密碼（見下方「金鑰」一節）
#   * 不能在驗收沒過時說「完成」：部署後每一項檢查都是 throw，沒有一項是 Warn
#     （見下方「驗收」一節）
#
# 多實例那一項是**正確性需求**，不是省錢。LocalStore 的鏈完整性鎖在行程內，
# 兩個實例就是兩份互不相干的資料——審查員會在連續兩次請求之間看到不同的紀錄列表。
#
# ## 建置上下文：與正式部署同一份 vendor/
#
# Docker 的建置上下文只有 Backend/Flask/。classify 需要的 engineering 模組
# 在映像裡不存在，必須在建置前複製到 vendor/——正式腳本一直這樣做，這支的
# 第一版漏了。漏掉時服務照常啟動、登入 200，只有量測 503 或安靜退回
# gray-world 白平衡；而第一版的驗收只看 store 與 care receipt，照樣印「完成」。
# 兩份清單由靜態測試逐項比對：正式那邊多一個模組而這裡沒跟上，測試就失敗。
#
# ## 驗收：每一項都會中止
#
# 部署後依序確認，任何一項不成立都 throw，「完成」只在全部通過後才印：
#   1. service：最新建立的 revision 已就緒、就是這次建的那一版、100% 流量在它身上
#   2. revision 回讀：執行身分、Ready、單一容器、環境變數（含 GIT_COMMIT）、
#      掛的密文恰好是四把示範密文、單實例
#   3. /api/health：healthy，量測模組（分割、classify、色準、端點、canonical
#      golden）全部就位，build.git_commit 等於本機完整 SHA，build.revision
#      等於上面那一版，store 是 local，care receipt 金鑰已設定
#   4. 這個 revision 自己的日誌裡有 [demo-seed:ok]，且沒有 refused／error
# 第一版在第 3 項讀錯欄位（health.git_commit，實際在 build 底下），版本不符與
# 找不到種子紀錄都只是一行黃字，最後照樣印「完成」。
#
# ## 金鑰：每一把都必須是示範服務自己的
#
# 正式服務驗 access token 只看簽章，角色直接取自 token 自己的 claim，**不回查
# 帳號是否存在**，效期 24 小時（app.py 的 JWT_ACCESS_TOKEN_EXPIRES、
# api_flywheel.py 的 _who）。所以兩個服務只要共用 JWT 簽章金鑰，審查員在示範
# 服務登入 demo01 拿到的 nurse token，送到正式服務也會被當成 nurse 接受——
# 而 nurse 在正式服務上可以對任意 WD 代碼撤回或恢復同意。care receipt 的
# HMAC 金鑰同理：共用的話，示範服務簽出的 receipt 在正式服務也驗得過。
#
# 2026-09-23 之前的版本就掛著正式的三把密文。那一版從未部署，但它通過了全部
# 測試與 CI 閘門，因為沒有任何一支測試在看「示範服務掛了哪幾把密文」。
# 現在有三層，缺一不可：
#   1. 名稱：四個密文參數都必須是 woundai-demo-*，彼此不同，且不得是正式密文
#   2. 身分：部署前讀每一把正式密文的 IAM 政策，示範身分出現在任何綁定裡就拒絕
#   3. 回讀：部署後讀 revision 的 secretKeyRef，任何一個指向正式密文就失敗
# 不掛 ADMIN_PASSWORD：示範服務不需要管理者，送審帳號由開機種子建立。
#
# ## 這支腳本解決不了的事（請一併讀 docs/admin_operations.md §6）
#
# 它讓審查員**登得進去**，不保證**資料還在**。影像、receipt、稽核鏈都在容器
# 檔案系統裡，實例回收就消失。送審流程必須能在單一連續工作階段內走完，
# 且 App Review Notes 要寫明這是示範環境、資料不留存。

param(
    [Parameter(Mandatory = $true)][string]$ProjectId,
    [Parameter(Mandatory = $true)][string]$RuntimeServiceAccount,
    # 授權字句必須由專案負責人親手寫，且要指名本次的 revision。
    # 下面會機械拒絕佔位符形狀——這個欄位的意義在於「有人想過才按下去」，
    # 貼一句樣板等於沒有授權。
    [Parameter(Mandatory = $true)][string]$DemoAuthorisationRef,
    [string]$Region = "asia-east1",
    # 示範 service。名稱必須以 -demo 結尾，且不得等於正式 service。
    [string]$Service = "woundai-backend-demo",
    [string]$ProductionService = "woundai-backend",
    [string]$DemoSeedUser = "demo01",
    [string]$DemoSeedRole = "nurse",
    [string]$DemoSeedSecret = "woundai-demo-password",
    # 示範服務自己的簽章與 HMAC 金鑰。預設值就是正確值；參數存在只是為了測試，
    # 任何指向正式密文的值都會被下面的檢查拒絕。
    [string]$DemoJwtSecret = "woundai-demo-jwt-secret",
    [string]$DemoFlaskSecret = "woundai-demo-flask-secret",
    [string]$DemoCareReceiptSecret = "woundai-demo-care-receipt-secret",
    [string]$Memory = "4Gi",
    # 只跑部署後驗證，不重建映像。
    [switch]$VerifyOnly
)

# 與 deploy_cloudrun.ps1 同樣的理由：gcloud.ps1 會繼承 ErrorActionPreference，
# 而它啟動時的 Test-Path 探測在某些安裝上會丟無害的 Access denied。
# 改成明確檢查 $LASTEXITCODE。
$ErrorActionPreference = "Continue"

function Say($msg)  { Write-Host "`n▶ $msg" -ForegroundColor Cyan }
function Warn($msg) { Write-Host "⚠ $msg" -ForegroundColor Yellow }
function Ok($msg)   { Write-Host "  ✓ $msg" -ForegroundColor Green }

function Invoke-GCloud {
    & gcloud @args
}

function Assert-GCloudOk($what) {
    if ($LASTEXITCODE -ne 0) { throw "$what 失敗（gcloud exit $LASTEXITCODE）" }
}

function Get-HttpResult([string]$Uri, [string]$Method = "GET", [int]$TimeoutSec = 60) {
    try {
        $r = Invoke-WebRequest -Uri $Uri -Method $Method -TimeoutSec $TimeoutSec `
            -UseBasicParsing -ErrorAction Stop
        return @{ Ok = $true; Status = [int]$r.StatusCode; Body = [string]$r.Content }
    } catch {
        $status = 0
        if ($_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode }
        return @{ Ok = $false; Status = $status; Body = [string]$_.Exception.Message }
    }
}

function Invoke-GCloudCaptured {
    # stdout 與 stderr 分開收：gcloud 會在 stderr 印「有新版可用」之類的警告，
    # 混進去 JSON 就解析不了；而失敗原因只在 stderr。
    $out = @(Invoke-GCloud @args 2>&1)
    $exit = $LASTEXITCODE
    $stdout = @($out | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] } |
                ForEach-Object { [string]$_ }) -join "`n"
    $stderr = @($out | Where-Object { $_ -is [System.Management.Automation.ErrorRecord] } |
                ForEach-Object { [string]$_ }) -join "`n"
    return @{ Exit = $exit; Stdout = $stdout; Stderr = $stderr }
}

function ConvertFrom-GCloudJson([string]$What, $Result) {
    # 讀不到、不是 JSON、是空的，一律停下來。三種情況都代表「不知道雲端現在
    # 是什麼狀態」，而不知道的時候任何比對都不成立。
    if ($Result.Exit -ne 0) {
        throw "cannot read $What (gcloud exit $($Result.Exit)): $($Result.Stderr)"
    }
    try { $obj = $Result.Stdout | ConvertFrom-Json -ErrorAction Stop }
    catch { throw "$What did not come back as JSON: $($_.Exception.Message)" }
    if ($null -eq $obj) { throw "$What came back empty" }
    return $obj
}

function Get-EngineeringVendorFiles {
    # 與 deploy_cloudrun.ps1 的 $needed 逐項相同，靜態測試會比對兩份清單。
    # 每一項漏掉的後果都寫在正式腳本那一段；最危險的是 color_calib.py——
    # 缺它 classify 不會壞，而是安靜退回 gray-world，紅色被壓到 ×0.78。
    return @(
        "phase2\wound_classifier.py",
        "phase1\clinical_rules.py",
        "phase2\aruco_calibrate.py",
        "phase2\verify_area_sheet.py",
        "phase2\color_calib.py",
        "phase0\preprocessing.json"
    )
}

function Copy-EngineeringVendor([string]$FlaskDir) {
    # vendor/ 不進版控（.gitignore），所以複製不會弄髒工作樹、也不影響
    # Get-DemoGitCommit 的乾淨檢查；它也不在 .gcloudignore 裡，所以會進建置上下文。
    # 每次部署重建，來源永遠是 engineering/ 那一份。
    $vendor = Join-Path $FlaskDir 'vendor'
    $engRoot = Join-Path $FlaskDir (Join-Path '..' (Join-Path '..' 'engineering'))
    $needed = @(Get-EngineeringVendorFiles)
    # 先確認來源齊全再動 vendor/：缺檔時不該先把現有的 vendor/ 刪掉。
    $missing = @($needed | Where-Object {
        -not (Test-Path -LiteralPath (Join-Path $engRoot $_) -PathType Leaf) })
    if ($missing.Count -gt 0) {
        throw "engineering is missing modules the demo image needs; classify would answer 503: " +
            ($missing -join ', ')
    }
    if (Test-Path -LiteralPath $vendor) {
        Remove-Item -LiteralPath $vendor -Recurse -Force -ErrorAction Stop
    }
    New-Item -ItemType Directory -Path $vendor -ErrorAction Stop | Out-Null
    foreach ($rel in $needed) {
        $src = Join-Path $engRoot $rel
        $dst = Join-Path $vendor (Split-Path $rel -Leaf)
        Copy-Item -LiteralPath $src -Destination $dst -Force -ErrorAction Stop
        # 複製完再比一次內容。$ErrorActionPreference 是 Continue，
        # 沒有這一步的話，一個半途失敗的複製只會留下一行紅字。
        $a = (Get-FileHash -LiteralPath $src -Algorithm SHA256).Hash
        $b = (Get-FileHash -LiteralPath $dst -Algorithm SHA256).Hash
        if ($a -cne $b) { throw "vendor copy of [$rel] does not match its engineering source" }
        Ok (Split-Path $rel -Leaf)
    }
    $present = @(Get-ChildItem -LiteralPath $vendor -Force | ForEach-Object { $_.Name } | Sort-Object)
    $expected = @($needed | ForEach-Object { Split-Path $_ -Leaf } | Sort-Object)
    if (($present -join '|') -cne ($expected -join '|')) {
        throw "vendor/ must hold exactly the reviewed engineering modules; found: " + ($present -join ', ')
    }
}

function Get-ProductionSecretNames {
    # 正式服務掛的密文。示範服務不得引用其中任何一個，示範身分也不得讀得到。
    # 測試會拿這份清單去比對 deploy_cloudrun.ps1 的 --set-secrets，
    # 正式那邊多了一把密文而這裡沒跟上，測試就會失敗。
    return @('woundai-admin-password', 'woundai-jwt-secret',
             'woundai-flask-secret', 'woundai-care-receipt-secret')
}

function Get-DemoSecretMap {
    # 環境變數 -> 示範密文。部署旗標與部署後回讀用同一份，兩邊不會各寫各的。
    return [ordered]@{
        'JWT_SECRET_KEY'             = $DemoJwtSecret
        'FLASK_SECRET_KEY'           = $DemoFlaskSecret
        'CARE_RECEIPT_SECRET'        = $DemoCareReceiptSecret
        'WOUNDAI_DEMO_SEED_PASSWORD' = $DemoSeedSecret
    }
}

function Assert-DemoInputs {
    # ── service 邊界。這是整份腳本最重要的一段 ──
    if ($Service -ceq $ProductionService) {
        throw "refuse to deploy the demo revision into the production service [$Service]"
    }
    if ($Service -cnotmatch '^[a-z][a-z0-9-]{2,44}-demo$') {
        throw "demo service name must end in -demo: [$Service]"
    }
    if ($ProductionService -cnotmatch '^[a-z][a-z0-9-]{2,48}$') {
        throw "invalid production service name to compare against: [$ProductionService]"
    }

    # ── 授權字句：與正式升版同樣的規格 ──
    if ([string]::IsNullOrWhiteSpace($DemoAuthorisationRef) `
            -or $DemoAuthorisationRef -match "[`r`n<>（）]" `
            -or $DemoAuthorisationRef -match '(?i)(placeholder|範本|填入|輸入|example|todo|xxx)') {
        throw "-DemoAuthorisationRef must be operator-supplied single-line text"
    }
    if ($DemoAuthorisationRef.Length -lt 12) {
        throw "-DemoAuthorisationRef is too short to be a considered authorization"
    }

    # ── 種子參數。角色白名單與後端 auth_users.DEMO_SEED_ROLES 對齊；
    #    後端仍會自己再驗一次（含權限層），這裡擋的是打錯字就部署出去 ──
    if ($DemoSeedUser -cnotmatch '^demo[0-9]{2}$') {
        throw "-DemoSeedUser must match demoNN: [$DemoSeedUser]"
    }
    if ($DemoSeedRole -cnotin @('nurse', 'assistant')) {
        throw "-DemoSeedRole must be nurse or assistant, never a role holding doctor endorsement: [$DemoSeedRole]"
    }

    # ── 金鑰：每一把都必須是示範服務自己的 ──
    $production = @(Get-ProductionSecretNames)
    $map = Get-DemoSecretMap
    foreach ($envName in $map.Keys) {
        $name = [string]$map[$envName]
        if ($production -ccontains $name) {
            throw "$envName would use production secret [$name]; the demo service must never share a key with production"
        }
        if ($name -cnotmatch '^woundai-demo-[a-z0-9-]{2,50}$') {
            throw "$envName must use a woundai-demo-* secret: [$name]"
        }
    }
    if (@($map.Values | Sort-Object -Unique).Count -ne $map.Count) {
        throw "demo secrets must be distinct; one key must not serve two purposes"
    }

    # ── 執行身分 ──
    $expectedSuffix = "@$ProjectId.iam.gserviceaccount.com"
    if ($RuntimeServiceAccount -notmatch '^[a-z][a-z0-9-]{4,28}[a-z0-9]@[a-z][a-z0-9-]{4,28}[a-z0-9]\.iam\.gserviceaccount\.com$' `
            -or -not $RuntimeServiceAccount.EndsWith($expectedSuffix, [StringComparison]::Ordinal)) {
        throw "RuntimeServiceAccount must be a dedicated service account in project [$ProjectId]"
    }
    if ($RuntimeServiceAccount -match '^\d+-compute@developer\.gserviceaccount\.com$' `
            -or $RuntimeServiceAccount -match '@appspot\.gserviceaccount\.com$') {
        throw "default Compute/App Engine service accounts are forbidden"
    }
}

function Assert-NotTheProductionIdentity {
    # 示範版跑 local store，程式碼根本不碰 GCS，所以這一條是縱深防禦：
    # 就算哪天有人把 WOUNDAI_STORE 改掉，這個身分也不該有正式桶的權限。
    #
    # **讀不到就停。** 第一版讀失敗時印一行警告就跳過比對——權限不足、登入
    # 過期、網路中斷、region 打錯，都會讓「沒有比對」跟「比對過、不同」
    # 一樣走到下一步。正式 service 確實存在（它就是正在服務的那一個），
    # 讀不到它只代表這台機器此刻看不清雲端，那正是不該部署的時候。
    #
    # 回傳正式身分：部署後回讀示範 revision 時要再比一次。
    $r = Invoke-GCloudCaptured run services describe $ProductionService `
        "--project=$ProjectId" "--region=$Region" '--format=json'
    $svc = ConvertFrom-GCloudJson "production service [$ProductionService]" $r
    $prodSa = ([string]$svc.spec.template.spec.serviceAccountName).Trim()
    if ($prodSa -notmatch '^[^@\s]+@[^@\s]+$') {
        throw "production service [$ProductionService] reports no runtime identity; refusing to compare against nothing"
    }
    if ($prodSa -ieq $RuntimeServiceAccount) {
        throw "demo service must not run as the production runtime identity [$prodSa]"
    }
    Ok "執行身分與正式服務不同（正式：$prodSa）"
    return $prodSa
}

function Assert-DemoSecretReady {
    $map = Get-DemoSecretMap
    foreach ($envName in $map.Keys) {
        $name = [string]$map[$envName]
        $versions = (Invoke-GCloud secrets versions list $name `
            --project $ProjectId --filter "state=ENABLED" --format "value(name)" 2>$null)
        if ($LASTEXITCODE -ne 0) {
            throw "secret [$name] not found; create it first (docs/admin_operations.md §6)"
        }
        $count = @($versions | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) }).Count
        if ($count -lt 1) { throw "secret [$name] has no ENABLED version" }
        Ok "$envName <- [$name]（$count 個啟用版本）"
    }
}

function Assert-DemoIdentityCannotReadProductionSecrets {
    # 金鑰分開，防的是「示範服務掛錯密文」。這一條防的是另一件事：示範身分本身
    # 有權讀正式密文——那樣只要示範容器被攻破，正式金鑰就跟著外流。
    # 最可能的成因是拿正式的佈建腳本對示範身分跑了一次。
    #
    # 只看得到各密文自己的 IAM 政策。專案層級授予的 secretAccessor 不在這裡，
    # 那一層靠佈建步驟保證（docs/admin_operations.md §6：示範身分只授予四把示範密文）。
    $member = "serviceAccount:$RuntimeServiceAccount"
    foreach ($name in @(Get-ProductionSecretNames)) {
        # JSON 只從 stdout 讀，NOT_FOUND 只從 stderr 判斷（見 Invoke-GCloudCaptured）。
        $r = Invoke-GCloudCaptured secrets get-iam-policy $name "--project=$ProjectId" '--format=json'
        if ($r.Exit -ne 0) {
            if ($r.Stderr -match 'NOT_FOUND') {
                Ok "正式密文 [$name] 不存在，無人可讀"
                continue
            }
            throw "cannot read the IAM policy of production secret [$name]; refusing to deploy without knowing who can read it"
        }
        $policy = ConvertFrom-GCloudJson "IAM policy of production secret [$name]" $r
        foreach ($binding in @($policy.bindings)) {
            if ($null -ne $binding -and @($binding.members) -ccontains $member) {
                throw "demo identity [$RuntimeServiceAccount] holds [$($binding.role)] on production secret [$name]"
            }
        }
    }
    Ok "示範身分不在任何一把正式密文的 IAM 綁定裡"
}

function Get-DemoGitCommit {
    # 與正式部署的差別：**不要求在 main 上**。示範版存在的目的就是讓尚未合併的
    # 程式碼被實機驗證。但仍要求乾淨工作樹，而且該 commit 必須已經推上 origin
    # ——否則沒有人能覆核雲端正在跑的是什麼。
    $repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
    $safe = $repoRoot.Replace('\','/')
    $full = (& git -c "safe.directory=$safe" -C $repoRoot rev-parse HEAD 2>$null)
    if ($LASTEXITCODE -ne 0 -or ([string]$full).Trim() -notmatch '^[0-9a-f]{40}$') {
        throw "cannot establish a full git commit for the demo deployment"
    }
    $full = ([string]$full).Trim()
    $changes = @(& git -c "safe.directory=$safe" -C $repoRoot `
        status --porcelain --untracked-files=all 2>$null)
    if ($LASTEXITCODE -ne 0) { throw "cannot verify git worktree state" }
    if ($changes.Count -ne 0) {
        throw "refuse to deploy a dirty worktree; commit and push every source artifact first"
    }
    $origin = (& git -c "safe.directory=$safe" -C $repoRoot remote get-url origin 2>$null)
    if ($LASTEXITCODE -ne 0 -or ([string]$origin).Trim() -cne `
            'https://github.com/JackH0001/WoundAI_Proj.git') {
        throw "deployment origin is not the reviewed WoundAI_Proj repository"
    }
    $onRemote = @(& git -c "safe.directory=$safe" -C $repoRoot `
        branch -r --contains $full 2>$null | Where-Object {
            -not [string]::IsNullOrWhiteSpace([string]$_) })
    if ($LASTEXITCODE -ne 0 -or $onRemote.Count -eq 0) {
        throw "commit [$full] is not on any origin branch; push it so the deployed code is reviewable"
    }
    return $full
}

function Assert-DemoServiceState([string]$ExpectedRevision) {
    # 部署後先確認「後面要驗的，就是這次建出來、而且正在服務的那一版」。
    # 新 revision 起不來時 latestReadyRevisionName 仍指向上一版；沒有這一條，
    # 後面每一項驗收都會對著舊版跑，然後通過。
    $r = Invoke-GCloudCaptured run services describe $Service `
        "--project=$ProjectId" "--region=$Region" '--format=json'
    $svc = ConvertFrom-GCloudJson "demo service [$Service]" $r
    $created = [string]$svc.status.latestCreatedRevisionName
    $ready = [string]$svc.status.latestReadyRevisionName
    if ([string]::IsNullOrWhiteSpace($ready) -or $created -cne $ready) {
        throw "demo service's latest created revision [$created] is not its latest ready revision [$ready]"
    }
    if (-not [string]::IsNullOrWhiteSpace($ExpectedRevision) -and $ready -cne $ExpectedRevision) {
        throw "demo service is serving [$ready], not the revision this run deployed [$ExpectedRevision]"
    }
    if ([string]$svc.spec.template.spec.serviceAccountName -cne $RuntimeServiceAccount) {
        throw "demo service template does not run as [$RuntimeServiceAccount]"
    }
    $serving = @($svc.status.traffic | Where-Object {
        $null -ne $_ -and $null -ne $_.percent -and [int]$_.percent -gt 0 })
    if ($serving.Count -ne 1 -or [int]$serving[0].percent -ne 100 `
            -or [string]$serving[0].revisionName -cne $ready) {
        throw "demo traffic is not 100% on ready revision [$ready]"
    }
    $url = [string]$svc.status.url
    if ($url -cnotmatch '^https://') { throw "demo service has no https URL [$url]" }
    Ok "revision [$ready] 已就緒，承接 100% 流量"
    return @{ Url = $url; Revision = $ready }
}

function Assert-DemoRevisionConfiguration([string]$Revision, [string]$ExpectedGitCommit,
                                          [string]$ProductionIdentity) {
    if ($ExpectedGitCommit -cnotmatch '^[0-9a-f]{40}$') {
        throw "no full local commit to compare the demo revision against"
    }
    if ([string]::IsNullOrWhiteSpace($ProductionIdentity)) {
        throw "production runtime identity is unknown; cannot confirm the demo revision is separate from it"
    }
    $r = Invoke-GCloudCaptured run revisions describe $Revision `
        "--project=$ProjectId" "--region=$Region" '--format=json'
    $rev = ConvertFrom-GCloudJson "demo revision [$Revision]" $r

    # 身分：傳了 --service-account 不是證據，revision 實際跑的身分才是。
    $runsAs = [string]$rev.spec.serviceAccountName
    if ($runsAs -cne $RuntimeServiceAccount) {
        throw "demo revision runs as [$runsAs], expected [$RuntimeServiceAccount]"
    }
    if ($runsAs -ieq $ProductionIdentity) {
        throw "demo revision runs as the production runtime identity [$runsAs]"
    }
    $readyCondition = @($rev.status.conditions | Where-Object {
        $null -ne $_ -and [string]$_.type -ceq 'Ready' -and [string]$_.status -ceq 'True' })
    if ($readyCondition.Count -ne 1) { throw "demo revision [$Revision] is not Ready" }
    if (@($rev.spec.containers).Count -ne 1) {
        throw "demo revision must run exactly one container"
    }

    $envs = @{}
    $refs = @{}
    foreach ($e in @($rev.spec.containers[0].env)) {
        if ($null -eq $e -or $null -eq $e.name) { continue }
        $name = [string]$e.name
        if ($envs.ContainsKey($name) -or $refs.ContainsKey($name)) {
            throw "demo revision has duplicate environment key [$name]"
        }
        # 回讀掛上的密文。傳了什麼旗標不是證據，revision 實際引用什麼才是。
        if ($null -ne $e.valueFrom -and $null -ne $e.valueFrom.secretKeyRef) {
            $refs[$name] = [string]$e.valueFrom.secretKeyRef.name
        } else {
            $envs[$name] = [string]$e.value
        }
    }
    if ($envs['WOUNDAI_STORE'] -cne 'local') {
        throw "demo revision must run WOUNDAI_STORE=local, got [$($envs['WOUNDAI_STORE'])]"
    }
    foreach ($forbidden in @('WOUNDAI_GCS_BUCKET', 'WOUNDAI_AUDIT_BUCKET', 'WOUNDAI_GCS_PREFIX')) {
        if ($envs.ContainsKey($forbidden)) {
            throw "demo revision carries [$forbidden]; it must not be pointed at any bucket"
        }
    }
    $plain = [ordered]@{
        'WOUNDAI_ENABLE_LITE_API' = '0'
        'WOUNDAI_DEMO_SEED_USER'  = $DemoSeedUser
        'WOUNDAI_DEMO_SEED_ROLE'  = $DemoSeedRole
        'GIT_COMMIT'              = $ExpectedGitCommit
    }
    foreach ($k in $plain.Keys) {
        if ($envs[$k] -cne $plain[$k]) {
            throw "demo revision has [$k]=[$($envs[$k])], expected [$($plain[$k])]"
        }
    }

    $production = @(Get-ProductionSecretNames)
    foreach ($envName in $refs.Keys) {
        if ($production -ccontains $refs[$envName]) {
            throw "demo revision mounts production secret [$($refs[$envName])] as [$envName]"
        }
    }
    if ($refs.ContainsKey('ADMIN_PASSWORD')) {
        throw "demo revision mounts ADMIN_PASSWORD; the demo service must not carry an administrator credential"
    }
    $map = Get-DemoSecretMap
    foreach ($envName in $map.Keys) {
        if ($refs[$envName] -cne $map[$envName]) {
            throw "demo revision maps [$envName] to [$($refs[$envName])], expected [$($map[$envName])]"
        }
    }
    foreach ($envName in $refs.Keys) {
        if (-not $map.Contains($envName)) {
            throw "demo revision mounts an unexpected secret [$($refs[$envName])] as [$envName]"
        }
    }

    $limit = [string]$rev.metadata.annotations.'autoscaling.knative.dev/maxScale'
    if ($limit -cne '1') {
        throw "demo revision must be pinned to a single instance (maxScale=1), got [$limit]"
    }
    Ok "revision 設定正確：示範身分、local store、無桶、單實例、只掛四把示範密文、commit 相符"
}

function Assert-DemoHealth($Health, [string]$ExpectedGitCommit, [string]$ExpectedRevision) {
    # 降級模式是「會回答的錯誤」：登入 200、stats 200，只有量測 503 或退回
    # HSV／gray-world。示範服務存在的目的就是讓審查員量測，這種狀態下印
    # 「完成」比部署失敗更糟。所以每一項都收進失敗清單，最後一次 throw。
    if ($null -eq $Health) { throw "demo /api/health returned no JSON" }
    if ($ExpectedGitCommit -cnotmatch '^[0-9a-f]{40}$') {
        throw "no full local commit to compare the demo health against"
    }
    $failures = @()
    if ([string]$Health.status -cne 'healthy') {
        $failures += "status=[$($Health.status)] $($Health.degraded_reason)"
    }
    foreach ($name in @('segmentation_model', 'classify_modules', 'color_calibration',
                        'endpoints_registered', 'canonicalization_golden')) {
        $v = $Health.services.$name
        if (-not ($v -is [bool] -and $v)) { $failures += "services.$name is not true" }
    }
    $lite = $Health.services.lite_public_api_enabled
    if (-not ($lite -is [bool] -and -not $lite)) {
        $failures += "the public lite API must be off on the demo service"
    }
    if ([string]$Health.store -cnotmatch '^local:') {
        $failures += "store is [$($Health.store)], not local storage"
    }
    $care = $Health.care_receipt.configured
    if (-not ($care -is [bool] -and $care)) {
        $failures += "care receipt keyring is not configured; the consent flow would answer 503"
    }
    # 欄位在 build 底下。第一版讀的是 health.git_commit——一個不存在的欄位，
    # 所以「版本不符」永遠成立，而它只印黃字，於是從來沒有人看它。
    if ([string]$Health.build.git_commit -cne $ExpectedGitCommit) {
        $failures += "health build.git_commit [$($Health.build.git_commit)] is not the local commit [$ExpectedGitCommit]"
    }
    if ([string]$Health.build.revision -cne $ExpectedRevision) {
        $failures += "health build.revision [$($Health.build.revision)] is not the verified revision [$ExpectedRevision]"
    }
    if ([string]$Health.build.service -cne $Service) {
        $failures += "health build.service [$($Health.build.service)] is not [$Service]"
    }
    if ($failures.Count -gt 0) {
        throw "demo health gate failed: " + ($failures -join '; ')
    }
    Ok "healthy：分割、classify、色準、端點、canonical golden 全部就位"
    Ok "build：$($Health.build.revision) @ $($Health.build.git_commit)"
    Ok "store：$($Health.store)；care receipt 金鑰：$($Health.care_receipt.signing_kid)"
}

function Assert-DemoSeedLogged([string]$Revision, [int]$Attempts = 12, [int]$IntervalSec = 10) {
    # 不用登入來驗證——那需要密碼，而密碼不該經過這支腳本。改讀**這個 revision**
    # 自己的日誌。第一版讀整個 service 最近 200 行、比對中文訊息、找不到只警告：
    # 舊 revision 的成功紀錄可以冒充新版的，Windows 主控台字碼頁可以把中文
    # 變成亂碼讓比對落空，而落空之後照樣印「完成」。
    #
    # 標記由 app.py 以 ASCII、flush=True 印出（見那裡的註解）：
    #   [demo-seed:ok]       已建立            ← 至少要有一行
    #   [demo-seed:exists]   已存在、不覆蓋     ← 同容器 worker 重啟，正常
    #   [demo-seed:refused]  設定被拒絕         ← 任何一行都中止
    #   [demo-seed:error]    例外              ← 任何一行都中止
    # 日誌寫入有延遲，所以有限次重讀；讀取本身失敗則立刻停。
    #
    # 由舊到新讀（--order=asc）：種子在開機時印，是這個 revision 最早的幾行之一；
    # 由新到舊讀的話，請求日誌一多就會把它擠出 --limit。gcloud 的 --freshness
    # 只在由新到舊時生效，所以不加；revision 名稱含 commit 與部署時間，本身就
    # 只屬於這一次部署，不需要時間窗。篩選值刻意不加引號：Windows PowerShell 5.1
    # 傳給原生程式時會吃掉內嵌的雙引號。
    $filter = "resource.type=cloud_run_revision AND resource.labels.service_name=$Service " +
              "AND resource.labels.revision_name=$Revision"
    for ($i = 1; $i -le $Attempts; $i++) {
        $r = Invoke-GCloudCaptured logging read $filter "--project=$ProjectId" `
            '--order=asc' '--limit=1000' '--format=value(textPayload)'
        if ($r.Exit -ne 0) {
            throw "cannot read the logs of demo revision [$Revision] (gcloud exit $($r.Exit)); the review account cannot be confirmed: $($r.Stderr)"
        }
        $lines = @($r.Stdout -split "`r?`n")
        $bad = @($lines | Where-Object { $_ -cmatch '\[demo-seed:(refused|error)\]' })
        if ($bad.Count -gt 0) {
            foreach ($line in $bad) { Warn $line }
            throw "demo seed was refused or failed on revision [$Revision]; see the lines above"
        }
        if (@($lines | Where-Object { $_ -cmatch '\[demo-seed:ok\]' }).Count -gt 0) {
            Ok "送審帳號已由 revision [$Revision] 開機時建立"
            return
        }
        if ($i -lt $Attempts) { Start-Sleep -Seconds $IntervalSec }
    }
    throw "no [demo-seed:ok] line from revision [$Revision] after $Attempts reads; the review account may not exist"
}

# ══════════════════════════════════════════════════════════════════
Say "示範服務部署（送審用）"
Assert-DemoInputs
Ok "輸入通過：service [$Service] 不是正式 service [$ProductionService]"

Invoke-GCloud config set project $ProjectId | Out-Null
Assert-GCloudOk "設定 gcloud project"

$ProductionRuntimeIdentity = Assert-NotTheProductionIdentity
Assert-DemoIdentityCannotReadProductionSecrets
Assert-DemoSecretReady

$GitCommit = Get-DemoGitCommit
$DeployedAt = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$RevisionSuffix = 'demo-' + $GitCommit.Substring(0, 8) + '-' + `
    (Get-Date).ToUniversalTime().ToString('MMddHHmm')
Ok "部署來源 commit：$GitCommit"

if (-not $VerifyOnly) {
    Say "複製 engineering 模組到 vendor/（與正式部署同一份清單）"
    Copy-EngineeringVendor -FlaskDir $PSScriptRoot

    Say "建置並部署到示範 service（單實例、local store）"
    # --source 用腳本所在目錄，不用 `.`。從 repo 根目錄執行
    # `.\Backend\Flask\deploy_demo_candidate.ps1` 時，`.` 會是 repo 根目錄，
    # gcloud 就會把整個 repo（iOS、Android、engineering…）打包上傳。
    Invoke-GCloud run deploy $Service `
        --source $PSScriptRoot `
        --project $ProjectId `
        --region $Region `
        --revision-suffix $RevisionSuffix `
        --service-account $RuntimeServiceAccount `
        --allow-unauthenticated `
        --memory $Memory `
        --cpu 2 `
        --timeout 120 `
        --concurrency 4 `
        --min-instances 1 `
        --max-instances 1 `
        --set-env-vars "WOUNDAI_STORE=local,WOUNDAI_ENABLE_LITE_API=0,WOUNDAI_DEMO_SEED_USER=$DemoSeedUser,WOUNDAI_DEMO_SEED_ROLE=$DemoSeedRole,GIT_COMMIT=$GitCommit,DEPLOYED_AT=$DeployedAt" `
        --set-secrets (@((Get-DemoSecretMap).GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value):latest" }) -join ',')
    Assert-GCloudOk "示範服務部署"
    # 這次部署建出來的 revision 名稱是確定的；後面每一項驗收都綁在它身上。
    $ExpectedRevision = "$Service-$RevisionSuffix"
} else {
    Say "只跑驗證（-VerifyOnly）"
    $ExpectedRevision = ''
}

# ── 驗收。以下每一項失敗都 throw；「完成」只在全部通過後才印 ──
Say "回讀示範 service 與 revision"
$state = Assert-DemoServiceState -ExpectedRevision $ExpectedRevision
$url = [string]$state.Url
$liveRevision = [string]$state.Revision
Ok "示範服務 URL：$url"
Assert-DemoRevisionConfiguration -Revision $liveRevision -ExpectedGitCommit $GitCommit `
    -ProductionIdentity $ProductionRuntimeIdentity

Say "驗證 /api/health"
$h = Get-HttpResult -Uri "$url/api/health" -TimeoutSec 90
if (-not $h.Ok) { throw "health 探針失敗（HTTP $($h.Status)）：$($h.Body)" }
try { $health = $h.Body | ConvertFrom-Json -ErrorAction Stop }
catch { throw "demo /api/health did not return JSON: $($_.Exception.Message)" }
Assert-DemoHealth -Health $health -ExpectedGitCommit $GitCommit -ExpectedRevision $liveRevision
# A∪U 是升級路由，不進 degraded（產品決策，見 app.py），所以這裡維持提醒而不是中止。
if ($health.services.au_ensemble_files_present -ne $true) {
    Warn "映像裡沒有 A∪U 模型檔——難例升級路由不會啟用。建置機器的 models/ 目錄可能是空的。"
}

Say "確認送審帳號的開機種子有跑"
Assert-DemoSeedLogged -Revision $liveRevision

Say "完成"
Write-Host ""
Write-Host "  示範服務   : $Service" -ForegroundColor Green
Write-Host "  URL        : $url" -ForegroundColor Green
Write-Host "  revision   : $liveRevision" -ForegroundColor Green
Write-Host "  commit     : $GitCommit" -ForegroundColor Green
Write-Host "  執行身分    : $RuntimeServiceAccount（正式：$ProductionRuntimeIdentity）" -ForegroundColor Green
Write-Host "  授權        : $DemoAuthorisationRef" -ForegroundColor Green
Write-Host ""
Write-Host "  下一步：把這個 URL 填進 App 設定頁與 App Store Connect。" -ForegroundColor Yellow
Write-Host "  ⚠ 資料不留存：實例回收後影像與紀錄會消失，審查說明必須寫明。" -ForegroundColor Yellow
