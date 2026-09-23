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
#
# 最後一項是**正確性需求**，不是省錢。LocalStore 的鏈完整性鎖在行程內，
# 兩個實例就是兩份互不相干的資料——審查員會在連續兩次請求之間看到不同的紀錄列表。
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
    if ($DemoSeedSecret -cnotmatch '^[a-z][a-z0-9-]{2,60}$') {
        throw "invalid demo secret name: [$DemoSeedSecret]"
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
    $versions = (Invoke-GCloud secrets versions list $DemoSeedSecret `
        --project $ProjectId --filter "state=ENABLED" --format "value(name)" 2>$null)
    if ($LASTEXITCODE -ne 0) {
        throw "secret [$DemoSeedSecret] not found; create it first (docs/admin_operations.md §6)"
    }
    $count = @($versions | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) }).Count
    if ($count -lt 1) { throw "secret [$DemoSeedSecret] has no ENABLED version" }
    Ok "示範密碼密文有 $count 個啟用版本"
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
    $limit = [string]$rev.metadata.annotations.'autoscaling.knative.dev/maxScale'
    if ($limit -cne '1') {
        throw "demo revision must be pinned to a single instance (maxScale=1), got [$limit]"
    }
    Ok "revision 設定正確：local store、無桶、單實例"
}

# ══════════════════════════════════════════════════════════════════
Say "示範服務部署（送審用）"
Assert-DemoInputs
Ok "輸入通過：service [$Service] 不是正式 service [$ProductionService]"

Invoke-GCloud config set project $ProjectId | Out-Null
Assert-GCloudOk "設定 gcloud project"

Assert-NotTheProductionIdentity
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
        --set-secrets "ADMIN_PASSWORD=woundai-admin-password:latest,JWT_SECRET_KEY=woundai-jwt-secret:latest,FLASK_SECRET_KEY=woundai-flask-secret:latest,WOUNDAI_DEMO_SEED_PASSWORD=${DemoSeedSecret}:latest"
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
