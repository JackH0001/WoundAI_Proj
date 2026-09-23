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
#   * 不能用正式執行身分：部署前會去讀正式 service 目前的 SA 並比對，相同就拒絕
#   * 不能多實例：--max-instances 1 寫死
#   * 不能用正式金鑰：JWT、Flask、care receipt 各用自己的 woundai-demo-* 密文，
#     也不掛正式管理者密碼（見下方「金鑰」一節）
#
# 最後一項是**正確性需求**，不是省錢。LocalStore 的鏈完整性鎖在行程內，
# 兩個實例就是兩份互不相干的資料——審查員會在連續兩次請求之間看到不同的紀錄列表。
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
    $prodSa = (Invoke-GCloud run services describe $ProductionService `
        --project $ProjectId --region $Region `
        --format "value(spec.template.spec.serviceAccountName)" 2>$null)
    if ($LASTEXITCODE -ne 0) {
        Warn "讀不到正式 service 的執行身分（可能尚未部署）；跳過比對"
        return
    }
    $prodSa = ([string]$prodSa).Trim()
    if (-not [string]::IsNullOrWhiteSpace($prodSa) -and $prodSa -ceq $RuntimeServiceAccount) {
        throw "demo service must not run as the production runtime identity [$prodSa]"
    }
    Ok "執行身分與正式服務不同"
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
        # stdout 與 stderr 必須分開：gcloud 會在 stderr 印「有新版可用」之類的警告，
        # 混進去 JSON 就解析不了。JSON 只從 stdout 讀，NOT_FOUND 只從 stderr 判斷。
        $out = @(Invoke-GCloud secrets get-iam-policy $name --project $ProjectId --format json 2>&1)
        $exit = $LASTEXITCODE
        $stdout = ($out | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] } |
                   ForEach-Object { [string]$_ }) -join "`n"
        $stderr = ($out | Where-Object { $_ -is [System.Management.Automation.ErrorRecord] } |
                   ForEach-Object { [string]$_ }) -join "`n"
        if ($exit -ne 0) {
            if ($stderr -match 'NOT_FOUND') {
                Ok "正式密文 [$name] 不存在，無人可讀"
                continue
            }
            throw "cannot read the IAM policy of production secret [$name]; refusing to deploy without knowing who can read it"
        }
        $policy = $stdout | ConvertFrom-Json
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

function Assert-DemoRevisionConfiguration([string]$Revision) {
    $rev = Invoke-GCloud run revisions describe $Revision `
        --project $ProjectId --region $Region --format json 2>$null | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or $null -eq $rev) { throw "cannot read demo revision [$Revision]" }

    $envs = @{}
    foreach ($e in @($rev.spec.containers[0].env)) {
        if ($null -ne $e.name) { $envs[[string]$e.name] = [string]$e.value }
    }
    if ($envs['WOUNDAI_STORE'] -cne 'local') {
        throw "demo revision must run WOUNDAI_STORE=local, got [$($envs['WOUNDAI_STORE'])]"
    }
    foreach ($forbidden in @('WOUNDAI_GCS_BUCKET', 'WOUNDAI_AUDIT_BUCKET', 'WOUNDAI_GCS_PREFIX')) {
        if ($envs.ContainsKey($forbidden)) {
            throw "demo revision carries [$forbidden]; it must not be pointed at any bucket"
        }
    }
    # 回讀掛上的密文。傳了什麼旗標不是證據，revision 實際引用什麼才是。
    $refs = @{}
    foreach ($e in @($rev.spec.containers[0].env)) {
        $ref = $e.valueFrom.secretKeyRef
        if ($null -ne $ref) { $refs[[string]$e.name] = [string]$ref.name }
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

    $limit = [string]$rev.metadata.annotations.'autoscaling.knative.dev/maxScale'
    if ($limit -cne '1') {
        throw "demo revision must be pinned to a single instance (maxScale=1), got [$limit]"
    }
    Ok "revision 設定正確：local store、無桶、單實例、只掛示範密文"
}

# ══════════════════════════════════════════════════════════════════
Say "示範服務部署（送審用）"
Assert-DemoInputs
Ok "輸入通過：service [$Service] 不是正式 service [$ProductionService]"

Invoke-GCloud config set project $ProjectId | Out-Null
Assert-GCloudOk "設定 gcloud project"

Assert-NotTheProductionIdentity
Assert-DemoIdentityCannotReadProductionSecrets
Assert-DemoSecretReady

$GitCommit = Get-DemoGitCommit
$DeployedAt = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$RevisionSuffix = 'demo-' + $GitCommit.Substring(0, 8) + '-' + `
    (Get-Date).ToUniversalTime().ToString('MMddHHmm')
Ok "部署來源 commit：$GitCommit"

if (-not $VerifyOnly) {
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
} else {
    Say "只跑驗證（-VerifyOnly）"
}

$svc = Invoke-GCloud run services describe $Service --project $ProjectId `
    --region $Region --format json | ConvertFrom-Json
Assert-GCloudOk "讀取示範 service"
$url = [string]$svc.status.url
$liveRevision = [string]$svc.status.latestReadyRevisionName
if ([string]::IsNullOrWhiteSpace($url)) { throw "demo service has no URL" }
Ok "示範服務 URL：$url"
Ok "目前 revision：$liveRevision"

Assert-DemoRevisionConfiguration -Revision $liveRevision

Say "驗證 /api/health"
$h = Get-HttpResult -Uri "$url/api/health"
if (-not $h.Ok) { throw "health 探針失敗（HTTP $($h.Status)）：$($h.Body)" }
$health = $h.Body | ConvertFrom-Json
$store = [string]$health.store
if ($store -cnotmatch '^local:') {
    throw "demo service is not on local storage; health says [$store]"
}
Ok "store：$store"
if (-not $health.care_receipt.configured) {
    throw "care receipt keyring is not configured on the demo service; the consent flow would answer 503"
}
Ok "care receipt 金鑰：$($health.care_receipt.signing_kid)"
Ok "A∪U 模型檔存在：$($health.services.au_ensemble_files_present)"
if (-not $health.services.au_ensemble_files_present) {
    Warn "映像裡沒有 A∪U 模型檔——難例升級路由不會啟用。建置機器的 models/ 目錄可能是空的。"
}
if ([string]$health.git_commit -cne $GitCommit.Substring(0, 7) `
        -and [string]$health.git_commit -cne $GitCommit) {
    Warn "health 回報的 commit [$($health.git_commit)] 與本機 [$GitCommit] 不一致"
}

Say "確認送審帳號的開機種子有跑"
# 不用登入來驗證——那需要密碼，而密碼不該經過這支腳本。改讀啟動日誌。
$logs = Invoke-GCloud run services logs read $Service --project $ProjectId `
    --region $Region --limit 200 2>$null
$seedOk = @($logs | Select-String -Pattern '已重建送審測試帳號').Count -gt 0
$seedRefused = @($logs | Select-String -Pattern 'demo 種子未執行|demo 種子失敗')
if ($seedRefused.Count -gt 0) {
    foreach ($line in $seedRefused) { Warn ([string]$line) }
    throw "demo seed refused to run; see the reasons above"
}
if (-not $seedOk) {
    Warn "日誌裡沒看到種子訊息（可能已被輪替掉）。若審查員登不進去，先查這裡。"
} else {
    Ok "送審帳號已於開機時重建"
}

Say "完成"
Write-Host ""
Write-Host "  示範服務   : $Service" -ForegroundColor Green
Write-Host "  URL        : $url" -ForegroundColor Green
Write-Host "  revision   : $liveRevision" -ForegroundColor Green
Write-Host "  commit     : $GitCommit" -ForegroundColor Green
Write-Host "  授權        : $DemoAuthorisationRef" -ForegroundColor Green
Write-Host ""
Write-Host "  下一步：把這個 URL 填進 App 設定頁與 App Store Connect。" -ForegroundColor Yellow
Write-Host "  ⚠ 資料不留存：實例回收後影像與紀錄會消失，審查說明必須寫明。" -ForegroundColor Yellow
