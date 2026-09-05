# ⚠ 這個檔案必須以 **UTF-8 with BOM** 儲存。
#
# Windows PowerShell 5.1 在沒有 BOM 時會以系統 ANSI 字碼頁（繁中系統是 Big5）解讀 .ps1，
# 中文註解不只變成亂碼，某些位元組序列還會吃掉引號與大括號，導致整個檔案語法解析失敗
# （症狀是一連串「陳述式區塊中缺少 '}'」，而真正的原因與大括號無關）。
# 用編輯器另存時請確認選的是「UTF-8 with BOM / UTF-8-BOM」。
# 部署 WoundAI 後端到 GCP Cloud Run（彰化 asia-east1）
#
# 用法：
#   .\deploy_cloudrun.ps1 -ProjectId my-proj -Bucket woundai-flywheel-abc `
#       -AuditBucket woundai-flywheel-abc-audit-epoch-20260905 `
#       -RuntimeServiceAccount woundai-runtime@my-proj.iam.gserviceaccount.com
#
# -Setup 已退役：正式 P0-4 發布必須先分別執行 harden_bucket.ps1 與
# provision_runtime_identity.ps1，讓不可逆儲存與 IAM 變更有各自可覆核的證據。
#
# 詳細說明與排錯見 docs/deploy_cloudrun.md。

param(
    [Parameter(Mandatory = $true)][string]$ProjectId,
    [Parameter(Mandatory = $true)][string]$Bucket,
    [Parameter(Mandatory = $true)][string]$AuditBucket,
    [Parameter(Mandatory = $true)][string]$RuntimeServiceAccount,
    [string]$Region = "asia-east1",
    [string]$Service = "woundai-backend",
    [string]$CareReceiptSecret = "woundai-care-receipt-secret",
    [string]$RuntimeMainRoleId = "woundaiRuntimeMainObjects",
    [string]$RuntimeAuditRoleId = "woundaiRuntimeAuditAppend",
    # 記憶體上限。2Gi 足夠一般路由（只載 student），但**難例升級路由會再載入
    # a_unet 與 unetpp**，三個 ONNX session 同時在記憶體裡加上 OpenCV 與影像緩衝
    # 就可能超過 2Gi。Cloud Run 的 OOM 會直接殺掉容器 → 客戶端看到 503，
    # 而且**不會留下任何 Python traceback**，是最難自行歸因的一種失敗。
    [string]$Memory = "4Gi",
    [switch]$Setup,
    # 只跑部署後驗證，跳過建置與部署。
    # 存在的理由很實際：驗證段本身出錯時（例如用了某個 PowerShell 版本沒有的參數），
    # 若要重跑就得再等一次五分鐘的映像重建 —— 那個成本會讓人乾脆不驗。
    [switch]$VerifyOnly
)

# ⚠ 刻意**不設** $ErrorActionPreference = "Stop"。
#
# gcloud 在 Windows 上的進入點是 gcloud.ps1，它會繼承呼叫端的 ErrorActionPreference。
# 而那支包裝一開始會 `Test-Path` 探測內建 Python，在某些安裝方式下該路徑的 ACL 不給讀，
# 於是丟出「Access is denied」——**功能完全正常**（它會退回系統 Python），
# 但在 Stop 模式下這個原本無害的非終止錯誤會變成終止錯誤，整個部署會在第一個
# gcloud 呼叫就中止，而畫面上只有一句看起來與部署無關的 Test-Path 錯誤。
# 改成明確檢查 $LASTEXITCODE，該中止的地方自己 throw。
$ErrorActionPreference = "Continue"

# 直接叫 gcloud.cmd 繞過 .ps1 包裝，連那行噪音都不會出現。
# 找不到就退回 gcloud（PATH 上有就好）。
$script:GCLOUD = if (Get-Command gcloud.cmd -ErrorAction SilentlyContinue) { "gcloud.cmd" } else { "gcloud" }

function Say($msg) { Write-Host "`n▶ $msg" -ForegroundColor Cyan }
function Warn($msg) { Write-Host "⚠ $msg" -ForegroundColor Yellow }
# ⚠ 函式名稱**不可**用兩三個字母的縮寫。
# PowerShell 的命令解析順序是「別名 > 函式 > Cmdlet > 執行檔」——**別名贏過函式**。
# 這支腳本原本把包裝函式取名 `Gc`，而那正是內建的 `Get-Content` 別名，
# 於是自訂函式永遠沒被呼叫過：每一行 `Gc config set ...` 實際執行的是
# `Get-Content config set ...`，錯誤訊息指向 Get-Content，完全看不出是命名衝突，
# 而且帳單檢查因此拿到 null 值，誤報成「尚未連結帳單帳戶」。
# 用動詞-名詞的完整命名（PowerShell 慣例）就不會撞到任何內建別名。
function Invoke-GCloud {
    & $script:GCLOUD @args
}
# ⚠ **不可**用 `Invoke-WebRequest -SkipHttpErrorCheck`。
# 那是 PowerShell 7+ 才有的參數；Windows PowerShell 5.1（多數人手上的版本）會丟
# 「找不到符合參數名稱 'SkipHttpErrorCheck' 的參數」。
#
# 這件事之所以嚴重，不是因為檢查跑不動，而是因為**它會假裝跑過了**：
# 原本的「舊預設密碼」檢查把呼叫包在 try/catch 裡，catch 分支直接印
# 「✓ 舊預設密碼已失效」——參數錯誤被 catch 吃掉，於是不論後端是什麼狀態
# 都會印出綠勾。一個永遠會通過的安全檢查比沒有檢查更糟：它讓人**停止懷疑**。
#
# 這支 helper 把三種結果分開回報，5.1 與 7+ 都適用：
#   Code > 0  伺服器有回應（不論 2xx/4xx/5xx）
#   Code = 0  根本沒連上（DNS / 逾時 / TLS）—— 絕不可當成「被拒絕」
function Get-HttpResult {
    param([string]$Uri, [string]$Method = "GET", [string]$Body,
          [string]$ContentType = "application/json", [int]$TimeoutSec = 60)
    $p = @{ Uri = $Uri; Method = $Method; TimeoutSec = $TimeoutSec
            UseBasicParsing = $true; ErrorAction = "Stop" }
    if ($Body) { $p.Body = $Body; $p.ContentType = $ContentType }
    try {
        $r = Invoke-WebRequest @p
        return [pscustomobject]@{ Code = [int]$r.StatusCode; Content = [string]$r.Content; Reached = $true }
    } catch {
        $resp = $_.Exception.Response
        if ($null -ne $resp) {
            $code = 0
            try { $code = [int]$resp.StatusCode } catch { }
            if ($code -eq 0) { try { $code = [int]$resp.StatusCode.value__ } catch { } }
            $body2 = ""
            if ($_.ErrorDetails -and $_.ErrorDetails.Message) { $body2 = [string]$_.ErrorDetails.Message }
            return [pscustomobject]@{ Code = $code; Content = $body2; Reached = ($code -gt 0) }
        }
        return [pscustomobject]@{ Code = 0; Content = $_.Exception.Message; Reached = $false }
    }
}

function Assert-GCloudOk($what) {
    if ($LASTEXITCODE -ne 0) { throw "$what 失敗（gcloud 退出碼 $LASTEXITCODE）。上面的訊息是原因。" }
}
function Assert-GCloudProjectTarget {
    # ProjectId must constrain the active gcloud context before any bucket,
    # secret, build, or Cloud Run mutation.  Do not merely print it in a plan.
    $active = (& $script:GCLOUD config get-value project 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $active -or $active.Trim() -cne $ProjectId) {
        throw "gcloud active project mismatch: expected [$ProjectId], got [$active]"
    }
    $confirmed = (& $script:GCLOUD projects describe $ProjectId --format='value(projectId)' 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $confirmed -or $confirmed.Trim() -cne $ProjectId) {
        throw "cannot verify gcloud target project [$ProjectId]"
    }
}
function Get-ObjectField($Object, [string[]]$Paths) {
    foreach ($path in $Paths) {
        $current = $Object
        foreach ($segment in $path.Split('.')) {
            if ($null -eq $current) { break }
            $property = $current.PSObject.Properties[$segment]
            if ($null -eq $property) { $current = $null; break }
            $current = $property.Value
        }
        if ($null -ne $current) { return $current }
    }
    return $null
}
function Test-ExactSet([string[]]$Actual, [string[]]$Expected) {
    $a = @($Actual | Sort-Object -Unique)
    $e = @($Expected | Sort-Object -Unique)
    return (($a -join "`n") -ceq ($e -join "`n"))
}
function Assert-DeploymentInputs {
    if ($Setup) {
        throw "-Setup is retired for P0-4; run reviewed hardening and identity provisioning separately"
    }
    if ($AuditBucket -ceq "$Bucket-audit") {
        throw "AuditBucket must be an explicit fresh locked epoch, not the legacy default [$AuditBucket]"
    }
    if ($AuditBucket -match '(?i)(smoke|test|tmp|temp|dev|sandbox)') {
        throw "AuditBucket name identifies a disposable environment and cannot be deployed: [$AuditBucket]"
    }
    if ($AuditBucket -eq $Bucket) { throw "AuditBucket must differ from the main Bucket" }
    foreach ($candidate in @($Bucket, $AuditBucket)) {
        if ($candidate -notmatch '^[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]$') {
            throw "invalid bucket name: [$candidate]"
        }
    }
    $expectedSuffix = "@$ProjectId.iam.gserviceaccount.com"
    if ($RuntimeServiceAccount -notmatch '^[a-z][a-z0-9-]{4,28}[a-z0-9]@[a-z][a-z0-9-]{4,28}[a-z0-9]\.iam\.gserviceaccount\.com$' `
            -or -not $RuntimeServiceAccount.EndsWith($expectedSuffix, [StringComparison]::Ordinal)) {
        throw "RuntimeServiceAccount must be a dedicated service account in project [$ProjectId]"
    }
    if ($RuntimeServiceAccount -match '^\d+-compute@developer\.gserviceaccount\.com$' `
            -or $RuntimeServiceAccount -match '@appspot\.gserviceaccount\.com$') {
        throw "default Compute/App Engine service accounts are forbidden for the clinical runtime"
    }
}
function Get-GCloudJson([string]$What, [string[]]$Arguments) {
    $raw = (& $script:GCLOUD @Arguments)
    if ($LASTEXITCODE -ne 0 -or -not $raw) { throw "$What failed" }
    try { return ($raw | ConvertFrom-Json) }
    catch { throw "$What returned invalid JSON: $_" }
}
function Assert-PolicyMemberExactRoles($Policy, [string]$Member, [string[]]$ExpectedRoles,
                                       [string]$What) {
    $roles = @($Policy.bindings | Where-Object { @($_.members) -contains $Member } |
        ForEach-Object { [string]$_.role })
    if (-not (Test-ExactSet $roles $ExpectedRoles)) {
        throw "$What roles for [$Member] mismatch; actual=[$($roles -join '; ')] expected=[$($ExpectedRoles -join '; ')]"
    }
}
function Assert-CustomRole([string]$RoleId, [string[]]$Permissions) {
    $role = Get-GCloudJson "custom role $RoleId" @(
        'iam','roles','describe',$RoleId,"--project=$ProjectId",'--format=json')
    if ($role.deleted -eq $true -or $role.stage -eq 'DISABLED') {
        throw "custom role $RoleId is deleted or disabled"
    }
    if (-not (Test-ExactSet @($role.includedPermissions) $Permissions)) {
        throw "custom role $RoleId permissions do not match the reviewed runtime contract"
    }
}
function Assert-SecretEnabledVersion([string]$Name) {
    $versions = @(& $script:GCLOUD secrets versions list $Name `
        "--project=$ProjectId" --filter='state:ENABLED' --limit=1 --format='value(name)' 2>$null)
    if ($LASTEXITCODE -ne 0 -or $versions.Count -ne 1 `
            -or [string]::IsNullOrWhiteSpace([string]$versions[0])) {
        throw "secret $Name has no readable ENABLED version"
    }
}
function Assert-DeploymentAuditBucket([string]$ExpectedProjectNumber) {
    # Do this before `run deploy`, not only through the post-deploy health
    # endpoint.  A deployment may otherwise replace a good revision with one
    # that is guaranteed to fail closed on its first audit write.
    $raw = (& $script:GCLOUD storage buckets describe "gs://$AuditBucket" `
        "--project=$ProjectId" --raw --format=json 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $raw) {
        throw "audit bucket gs://$AuditBucket cannot be described before deployment"
    }
    try { $audit = $raw | ConvertFrom-Json }
    catch { throw "audit bucket gs://$AuditBucket returned invalid JSON: $_" }
    $actualProjectNumber = [string](Get-ObjectField $audit @('projectNumber'))
    if ([string]::IsNullOrWhiteSpace($actualProjectNumber) -or $actualProjectNumber -cne $ExpectedProjectNumber) {
        throw "audit bucket gs://$AuditBucket belongs to project number [$actualProjectNumber], not $ProjectId"
    }
    $retention = [int64](Get-ObjectField $audit @('retention_policy.retentionPeriod','retentionPolicy.retentionPeriod'))
    $locked = (Get-ObjectField $audit @('retention_policy.isLocked','retentionPolicy.isLocked')) -eq $true
    if ($retention -ne 220903200 -or -not $locked) {
        throw "audit bucket gs://$AuditBucket must have an already-locked 7-year retention policy before deployment"
    }
    if ([string](Get-ObjectField $audit @('location')) -cne $Region.ToUpperInvariant()) {
        throw "audit bucket gs://$AuditBucket location mismatch"
    }
    if ((Get-ObjectField $audit @('iamConfiguration.publicAccessPrevention','public_access_prevention')) -cne 'enforced' `
            -or (Get-ObjectField $audit @('iamConfiguration.uniformBucketLevelAccess.enabled','uniform_bucket_level_access')) -ne $true) {
        throw "audit bucket gs://$AuditBucket is not PAP+UBLA hardened"
    }
}
function Assert-RuntimeProvisioning([string]$ExpectedProjectNumber) {
    $member = "serviceAccount:$RuntimeServiceAccount"
    $sa = Get-GCloudJson "runtime service account" @(
        'iam','service-accounts','describe',$RuntimeServiceAccount,"--project=$ProjectId",'--format=json')
    if ($sa.disabled -eq $true -or [string]$sa.projectId -cne $ProjectId) {
        throw "runtime service account is disabled or belongs to another project"
    }

    $mainPermissions = @('storage.objects.create','storage.objects.delete',
                         'storage.objects.get','storage.objects.list')
    $auditPermissions = @('storage.buckets.get','storage.objects.create',
                          'storage.objects.get','storage.objects.list')
    Assert-CustomRole $RuntimeMainRoleId $mainPermissions
    Assert-CustomRole $RuntimeAuditRoleId $auditPermissions

    $projectPolicy = Get-GCloudJson "project IAM" @(
        'projects','get-iam-policy',$ProjectId,'--format=json')
    Assert-PolicyMemberExactRoles $projectPolicy $member @() "project IAM"

    $mainPolicy = Get-GCloudJson "main bucket IAM" @(
        'storage','buckets','get-iam-policy',"gs://$Bucket","--project=$ProjectId",'--format=json')
    Assert-PolicyMemberExactRoles $mainPolicy $member @("projects/$ProjectId/roles/$RuntimeMainRoleId") "main bucket IAM"

    $auditPolicy = Get-GCloudJson "audit bucket IAM" @(
        'storage','buckets','get-iam-policy',"gs://$AuditBucket","--project=$ProjectId",'--format=json')
    Assert-PolicyMemberExactRoles $auditPolicy $member @("projects/$ProjectId/roles/$RuntimeAuditRoleId") "audit bucket IAM"

    foreach ($secret in @('woundai-admin-password','woundai-jwt-secret',$CareReceiptSecret)) {
        Assert-SecretEnabledVersion $secret
        $secretPolicy = Get-GCloudJson "secret IAM $secret" @(
            'secrets','get-iam-policy',$secret,"--project=$ProjectId",'--format=json')
        Assert-PolicyMemberExactRoles $secretPolicy $member @('roles/secretmanager.secretAccessor') "secret IAM $secret"
    }
    Write-Host "  ✓ dedicated runtime identity and least-privilege bindings verified"
}
function Invoke-DeploymentPreflight {
    $projectNumber = (& $script:GCLOUD projects describe $ProjectId `
        "--project=$ProjectId" --format='value(projectNumber)' 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $projectNumber) {
        throw "cannot resolve project number for deployment preflight"
    }
    $projectNumber = ([string]$projectNumber).Trim()
    # Reuse the bucket policy verifier in read-only mode.  This establishes the
    # main bucket's exact lifecycle/versioning/soft-delete controls and the
    # audit bucket's ownership, PAP, UBLA, retention and object-path policy.
    & (Join-Path $PSScriptRoot 'harden_bucket.ps1') -ProjectId $ProjectId `
        -Bucket $Bucket -AuditBucket $AuditBucket -Region $Region -Audit
    if (-not $?) { throw "read-only bucket policy preflight failed" }
    Assert-DeploymentAuditBucket $projectNumber
    Assert-RuntimeProvisioning $projectNumber
}
function Get-CleanGitCommit {
    $repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
    $safeRepoRoot = $repoRoot.Replace('\','/')
    # Trust only the repository that physically contains this reviewed script;
    # this also makes the gate work in isolated Windows test accounts whose SID
    # differs from the checkout owner.
    $full = (& git -c "safe.directory=$safeRepoRoot" -C $repoRoot rev-parse HEAD 2>$null)
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace([string]$full) `
            -or ([string]$full).Trim() -notmatch '^[0-9a-f]{40}$') {
        throw "cannot establish a full git commit for deployment"
    }
    $changes = @(& git -c "safe.directory=$safeRepoRoot" -C $repoRoot `
        status --porcelain --untracked-files=all 2>$null)
    if ($LASTEXITCODE -ne 0) { throw "cannot verify git worktree state" }
    if ($changes.Count -ne 0) {
        throw "refuse to deploy a dirty worktree; commit and review every source artifact first"
    }
    $branch = (& git -c "safe.directory=$safeRepoRoot" -C $repoRoot `
        symbolic-ref --quiet --short HEAD 2>$null)
    if ($LASTEXITCODE -ne 0 -or ([string]$branch).Trim() -cne 'main') {
        throw "deployment source must be the checked-out main branch"
    }
    $origin = (& git -c "safe.directory=$safeRepoRoot" -C $repoRoot `
        remote get-url origin 2>$null)
    if ($LASTEXITCODE -ne 0 -or ([string]$origin).Trim() -cne `
            'https://github.com/JackH0001/WoundAI_Proj.git') {
        throw "deployment origin is not the reviewed WoundAI_Proj repository"
    }
    $remote = @(& git -c "safe.directory=$safeRepoRoot" -C $repoRoot `
        ls-remote --heads origin refs/heads/main 2>$null)
    if ($LASTEXITCODE -ne 0 -or $remote.Count -ne 1 -or
            [string]$remote[0] -notmatch '^([0-9a-f]{40})\s+refs/heads/main$') {
        throw "cannot establish the current remote main commit"
    }
    if ($Matches[1] -cne ([string]$full).Trim()) {
        throw "local main is not the current reviewed origin/main commit"
    }
    return ([string]$full).Trim()
}
function Assert-CloudRunRevisionConfiguration([switch]$RequireExclusiveTraffic,
                                               [string]$CandidateTag) {
    $serviceState = Get-GCloudJson "Cloud Run service $Service" @(
        'run','services','describe',$Service,"--project=$ProjectId","--region=$Region",'--format=json')
    $created = [string]$serviceState.status.latestCreatedRevisionName
    $ready = [string]$serviceState.status.latestReadyRevisionName
    if ([string]::IsNullOrWhiteSpace($created) -or $created -cne $ready) {
        throw "Cloud Run latest created revision [$created] is not the latest ready revision [$ready]"
    }
    if ([string]$serviceState.spec.template.spec.serviceAccountName -cne $RuntimeServiceAccount) {
        throw "Cloud Run service template is not pinned to [$RuntimeServiceAccount]"
    }
    $traffic = @($serviceState.status.traffic)
    $taggedUrl = $null
    if ($RequireExclusiveTraffic) {
        $percentage = @($traffic | Where-Object { $null -ne $_.percent })
        if ($percentage.Count -ne 1 -or [int]$percentage[0].percent -ne 100 `
                -or [string]$percentage[0].revisionName -cne $ready) {
            throw "Cloud Run traffic is not exclusively pinned to ready revision [$ready]"
        }
    } else {
        if ([string]::IsNullOrWhiteSpace($CandidateTag)) {
            throw "candidate verification requires an explicit traffic tag"
        }
        $tagged = @($traffic | Where-Object { [string]$_.tag -ceq $CandidateTag })
        if ($tagged.Count -ne 1 -or [string]$tagged[0].revisionName -cne $ready `
                -or [string]::IsNullOrWhiteSpace([string]$tagged[0].url)) {
            throw "candidate tag [$CandidateTag] is not bound to ready revision [$ready]"
        }
        $taggedUrl = [string]$tagged[0].url
    }

    $revision = Get-GCloudJson "Cloud Run revision $ready" @(
        'run','revisions','describe',$ready,"--project=$ProjectId","--region=$Region",'--format=json')
    if ([string]$revision.spec.serviceAccountName -cne $RuntimeServiceAccount) {
        throw "ready revision [$ready] runs as an unexpected service account"
    }
    $readyCondition = @($revision.status.conditions | Where-Object {
        $_.type -eq 'Ready' -and [string]$_.status -eq 'True'
    })
    if ($readyCondition.Count -ne 1) { throw "ready revision [$ready] lacks one positive Ready condition" }
    if (@($revision.spec.containers).Count -ne 1) {
        throw "ready revision [$ready] must contain exactly one reviewed application container"
    }

    $envByName = @{}
    foreach ($entry in @($revision.spec.containers[0].env)) {
        if ($envByName.ContainsKey([string]$entry.name)) {
            throw "ready revision has duplicate environment key [$($entry.name)]"
        }
        $envByName[[string]$entry.name] = $entry
    }
    $plainExpected = @{
        WOUNDAI_STORE = 'gcs'; WOUNDAI_GCS_BUCKET = $Bucket;
        WOUNDAI_GCS_PREFIX = 'flywheel'; WOUNDAI_AUDIT_BUCKET = $AuditBucket;
        WOUNDAI_ENABLE_LITE_API = '0'; GIT_COMMIT = $GitCommit
    }
    foreach ($name in $plainExpected.Keys) {
        if (-not $envByName.ContainsKey($name) `
                -or [string]$envByName[$name].value -cne [string]$plainExpected[$name]) {
            throw "ready revision environment [$name] does not match the deployment contract"
        }
    }
    # These are Secret Manager resource names, never secret values.  Keep the
    # environment key and resource name in distinct fields so scanners (and
    # reviewers) cannot mistake this deployment contract for a credential.
    $secretExpected = @(
        [pscustomobject]@{ EnvironmentName = 'ADMIN_PASSWORD'; SecretName = 'woundai-admin-password' },
        [pscustomobject]@{ EnvironmentName = 'JWT_SECRET_KEY'; SecretName = 'woundai-jwt-secret' },
        [pscustomobject]@{ EnvironmentName = 'CARE_RECEIPT_SECRET'; SecretName = $CareReceiptSecret }
    )
    foreach ($expectedSecret in $secretExpected) {
        $name = [string]$expectedSecret.EnvironmentName
        $ref = $envByName[$name].valueFrom.secretKeyRef
        if ($null -eq $ref -or [string]$ref.name -cne [string]$expectedSecret.SecretName `
                -or [string]$ref.key -cne 'latest') {
            throw "ready revision secret reference [$name] does not match the deployment contract"
        }
    }
    $script:EXPECTED_REVISION = $ready
    $trafficMode = if ($RequireExclusiveTraffic) { '100% live' } else { "tag=$CandidateTag; no live traffic" }
    Write-Host "  ✓ revision ${ready}: identity, $trafficMode, environment and secret refs verified"
    return [pscustomobject]@{ revision=$ready; tagged_url=$taggedUrl; service=$serviceState }
}

Assert-DeploymentInputs
$GitCommit = Get-CleanGitCommit
$DeployedAt = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$CandidateTag = 'p0-4-candidate'
$RevisionSuffix = 'p04-' + $GitCommit.Substring(0, 8) + '-' + `
    (Get-Date).ToUniversalTime().ToString('yyyyMMddHHmmss')
$PreviousRevision = $null

# -VerifyOnly 時整段建置流程跳過。這裡不是「加速」——是讓驗證能獨立重跑，
# 因為一個要等五分鐘才能重試的檢查，實務上等於沒有檢查。
if (-not $VerifyOnly) {

    # ── 出發前檢查 ───────────────────────────────────────────────────────
    # 這兩個檔案是病人影像不進容器映像的唯一防線。缺了就中止——
    # 映像一旦推上登錄檔，之後刪 flywheel/ 也拿不回來，因為它已經在某個映像層裡。
    Say "檢查建置上下文"
    foreach ($f in @(".gcloudignore", ".dockerignore")) {
        if (-not (Test-Path $f)) { throw "缺 $f。沒有它，flywheel/ 的傷口影像會被上傳並烤進映像。中止。" }
    }
    if ((Get-Content .gcloudignore -Raw) -notmatch "flywheel") {
        throw ".gcloudignore 沒有排除 flywheel/。中止。"
    }
    Write-Host "  ✓ flywheel/ 與 *.db 已排除"

    # Git identity was established before entering this branch.  A dirty or
    # untracked source tree is a hard stop, never a suffix on a plausible SHA.
    Write-Host "  ✓ 部署身分 $GitCommit @ $DeployedAt"

    Say "確認專案與帳單"
    Invoke-GCloud config set project $ProjectId | Out-Null
    Assert-GCloudOk "設定 gcloud project $ProjectId"
    Assert-GCloudProjectTarget
    # 帳單沒綁的話 Cloud Run 會以權限錯誤失敗，而訊息完全不會提到「帳單」——
    # 那是這條流程最常卡住也最難自行歸因的一步，所以先檢查再說。
    $billing = Invoke-GCloud billing projects describe $ProjectId --format "value(billingEnabled)" 2>$null
    if ($LASTEXITCODE -ne 0) {
        # 查詢本身失敗（多半是 Cloud Billing API 未啟用或帳號無 billing.viewer 權限）。
        # 這與「確定沒綁」是兩件事，不可混為一談——後者要去綁，前者要去開 API 或要權限。
        Warn "無法查詢帳單狀態（可能是 Cloud Billing API 未啟用或權限不足）。跳過檢查，直接嘗試部署。"
        Warn "若部署以權限錯誤失敗，請先執行： gcloud.cmd billing projects link $ProjectId --billing-account=<ACCOUNT_ID>"
    } elseif ($billing -ne "True") {
        # ⚠ here-string 的結束標記 "@ **必須頂到行首**，不能有任何縮排。
        # 縮排的話 PowerShell 找不到結尾，會把後面整份腳本都當成字串內容，
        # 而錯誤訊息會是遙遠某處的「缺少 '}'」——完全指不到這裡。
        throw @"
專案 $ProjectId 尚未連結帳單帳戶，Cloud Run 無法部署。
（免費額度仍需綁定帳單帳戶；額度內不會扣款。）

    gcloud billing accounts list
    gcloud billing projects link $ProjectId --billing-account=<上面列出的 ACCOUNT_ID>

綁好之後重跑本腳本。
"@
    }
    Write-Host "  ✓ 帳單已連結"

    if ($Setup) {
        Say "開啟必要的 GCP 服務"
        Invoke-GCloud services enable run.googleapis.com artifactregistry.googleapis.com `
            storage.googleapis.com cloudbuild.googleapis.com secretmanager.googleapis.com

        Say "建立儲存桶 gs://$Bucket（$Region，統一存取控管）"
        # 統一存取控管：關掉物件層級 ACL，權限只由 IAM 決定。
        # 混用兩套權限模型是「以為關了其實還開著」最常見的來源。
        Invoke-GCloud storage buckets create "gs://$Bucket" --location=$Region --uniform-bucket-level-access 2>$null
        if ($LASTEXITCODE -ne 0) { Warn "儲存桶已存在或建立失敗，繼續" }

        Say "產生密碼與 JWT 金鑰並存入 Secret Manager"
        # ⚠ **已存在就不重新產生**。
        # 重跑 -Setup 是常態（第一次部署失敗、修完再跑一次），若每次都新增一個版本，
        # 密碼就會在使用者不知情的情況下換掉——他手上抄的那組突然失效，
        # 而症狀是「App 顯示帳密不正確」，完全看不出是重跑腳本造成的。
        $chars = (48..57) + (65..90) + (97..122)
        Invoke-GCloud secrets describe woundai-admin-password 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Warn "密碼 secret 已存在，沿用現有的（不重新產生）。"
            Write-Host "  取回： gcloud.cmd secrets versions access latest --secret=woundai-admin-password"
        } else {
            $pw = -join ($chars | Get-Random -Count 32 | ForEach-Object { [char]$_ })
            # ⚠ **不可**用 `$pw | gcloud ... --data-file=-`。
            # PowerShell 送進管線時會在字串尾端補 CRLF，於是 Secret Manager 存的其實是
            # `<密碼>\r\n`。後果有兩層：容器拿到的 ADMIN_PASSWORD 帶著換行；而使用者用
            # `gcloud secrets versions access` 取回時 PowerShell 會把它切成**多行陣列**，
            # `ConvertTo-Json` 再把陣列序列化成 JSON list，後端就收到 list 而不是字串 → 500。
            # 改用 WriteAllText 寫暫存檔（不補換行），用完立刻刪除。
            $tmp = [IO.Path]::GetTempFileName()
            try {
                [IO.File]::WriteAllText($tmp, $pw, (New-Object Text.UTF8Encoding $false))
                & $script:GCLOUD secrets create woundai-admin-password --data-file=$tmp --replication-policy=automatic
                Assert-GCloudOk "建立密碼 secret"
            } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
            Write-Host "`n" -NoNewline
            Write-Host "═══════════════════════════════════════════════" -ForegroundColor Green
            Write-Host " 後端密碼（App 設定頁要填，只顯示這一次）：" -ForegroundColor Green
            Write-Host "   帳號：admin"
            Write-Host "   密碼：$pw"
            Write-Host "═══════════════════════════════════════════════" -ForegroundColor Green
            Warn "現在就記下來。之後可用 gcloud.cmd secrets versions access latest --secret=woundai-admin-password 取回。"
        }
        Invoke-GCloud secrets describe woundai-jwt-secret 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) {
            $jwtKey = -join ($chars | Get-Random -Count 48 | ForEach-Object { [char]$_ })
            $tmp2 = [IO.Path]::GetTempFileName()
            try {
                [IO.File]::WriteAllText($tmp2, $jwtKey, (New-Object Text.UTF8Encoding $false))
                & $script:GCLOUD secrets create woundai-jwt-secret --data-file=$tmp2 --replication-policy=automatic
                Assert-GCloudOk "建立 JWT secret"
            } finally { Remove-Item $tmp2 -Force -ErrorAction SilentlyContinue }
        }

        Invoke-GCloud secrets describe $CareReceiptSecret 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) {
            $careKeyBytes = New-Object byte[] 32
            $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
            try { $rng.GetBytes($careKeyBytes) } finally { $rng.Dispose() }
            $careB64 = [Convert]::ToBase64String($careKeyBytes).TrimEnd('=').Replace('+','-').Replace('/','_')
            $careJson = @{ active_kid = 'phase-a-20260901'; keys = @{
                'phase-a-20260901' = @{ secret_b64 = $careB64 }
            }} | ConvertTo-Json -Depth 6 -Compress
            $tmp3 = [IO.Path]::GetTempFileName()
            try {
                [IO.File]::WriteAllText($tmp3, $careJson, (New-Object Text.UTF8Encoding $false))
                & $script:GCLOUD secrets create $CareReceiptSecret --data-file=$tmp3 --replication-policy=automatic
                Assert-GCloudOk "建立 care receipt secret"
            } finally {
                [Array]::Clear($careKeyBytes, 0, $careKeyBytes.Length)
                Remove-Item $tmp3 -Force -ErrorAction SilentlyContinue
            }
        }
    }

    Invoke-GCloud secrets describe $CareReceiptSecret 2>$null | Out-Null
    Assert-GCloudOk "care receipt secret $CareReceiptSecret 不存在"

    # IAM mutation is deliberately outside the deployment path.  A deployment
    # must consume a previously reviewed provisioning state, not silently grant
    # itself broader access while publishing code.
    Say "驗證正式稽核桶與專用執行身分"
    Invoke-DeploymentPreflight

    # ── 複製 engineering 模組到 vendor/ ───────────────────────────────────
    #
    # ⚠ Docker 的建置上下文只有 `Backend/Flask/`。app.py 從 `../../engineering` 載入
    # 組織分類與 PUSH 模組——那條路徑在**映像裡不存在**。本機開發完全看不出問題，
    # 直到部署上雲，classify 才以 `No module named 'wound_classifier'` 回 503，
    # 而登入與 stats 都是 200，健康檢查也全綠。
    #
    # vendor/ **不進版控**：同一份程式碼放兩個地方，遲早有人只改了其中一個。
    # 每次部署重新複製，來源永遠是 engineering/ 那一份。
    Say "複製 engineering 模組到 vendor/"
    $vendor = Join-Path $PSScriptRoot "vendor"
    $engRoot = Join-Path $PSScriptRoot "..\..\engineering"
    $needed = @(
        "phase2\wound_classifier.py",
        "phase1\clinical_rules.py",
        "phase2\aruco_calibrate.py",
        "phase2\verify_area_sheet.py",
        # 色準校正。漏了它 classify 不會壞——它被包在 try/except 裡——
        # 而是**安靜退回 gray-world 白平衡**，紅色被壓抑 ×0.78，肉芽被低估。
        # 這正是本專案最典型的失敗形狀：一切回 200，數字看起來合理。
        "phase2\color_calib.py",
        "phase0\preprocessing.json"
    )
    if (Test-Path $vendor) { Remove-Item $vendor -Recurse -Force }
    New-Item -ItemType Directory -Path $vendor | Out-Null
    $missing = @()
    foreach ($rel in $needed) {
        $src = Join-Path $engRoot $rel
        if (Test-Path $src) {
            Copy-Item $src (Join-Path $vendor (Split-Path $rel -Leaf)) -Force
            Write-Host "  ✓ $(Split-Path $rel -Leaf)"
        } else { $missing += $rel }
    }
    if ($missing) {
        # 缺檔就中止。讓它在這裡大聲失敗，而不是部署完才在手機上看到 503——
        # 那時候要從「分析失敗」一路追到「建置上下文不含 engineering/」，成本高得多。
        throw "engineering 缺少必要模組，classify 會在雲端 503：`n  " + ($missing -join "`n  ")
    }

    Say "部署到 Cloud Run（$Region）"
    # 參數理由見 docs/deploy_cloudrun.md §4：
    #   memory 2Gi  — 難例集成同時載三個 ONNX 模型；1Gi 會被 OOM kill 且不留例外
    #   cpu 2       — 實測難例集成 796 ms；1 vCPU 會翻倍到使用者會放棄的程度
    #   concurrency 4 — 推論是 CPU-bound，預設 80 會讓請求擠在兩顆核心上
    #   max-instances 3 — 成本上限，避免被掃描機器人打到無限擴張
    # Preserve the currently serving revision before creating a candidate.
    # The candidate receives a tag URL but zero percent of live traffic; every
    # runtime/security probe below targets that URL before cutover.
    $beforeService = Get-GCloudJson "pre-deploy Cloud Run service $Service" @(
        'run','services','describe',$Service,"--project=$ProjectId","--region=$Region",'--format=json')
    $beforeLive = @($beforeService.status.traffic | Where-Object {
        $null -ne $_.percent -and [int]$_.percent -eq 100
    })
    if ($beforeLive.Count -ne 1 -or
            [string]::IsNullOrWhiteSpace([string]$beforeLive[0].revisionName)) {
        throw "pre-deploy service must have exactly one 100% live revision"
    }
    $PreviousRevision = [string]$beforeLive[0].revisionName
    Write-Host "  ✓ previous live revision preserved for rollback: $PreviousRevision"

    Invoke-GCloud run deploy $Service `
        --source . `
        --project $ProjectId `
        --region $Region `
        --revision-suffix $RevisionSuffix `
        --tag $CandidateTag `
        --no-traffic `
        --service-account $RuntimeServiceAccount `
        --allow-unauthenticated `
        --memory $Memory `
        --cpu 2 `
        --timeout 120 `
        --concurrency 4 `
        --min-instances 0 `
        --max-instances 3 `
        --set-env-vars "WOUNDAI_STORE=gcs,WOUNDAI_GCS_BUCKET=$Bucket,WOUNDAI_GCS_PREFIX=flywheel,WOUNDAI_AUDIT_BUCKET=$AuditBucket,WOUNDAI_ENABLE_LITE_API=0,GIT_COMMIT=$GitCommit,DEPLOYED_AT=$DeployedAt" `
        --set-secrets "ADMIN_PASSWORD=woundai-admin-password:latest,JWT_SECRET_KEY=woundai-jwt-secret:latest,CARE_RECEIPT_SECRET=$CareReceiptSecret`:latest"
    Assert-GCloudOk "Cloud Run 部署"


} else {
    Say "只跑驗證（-VerifyOnly，跳過建置與部署）"
    Invoke-GCloud config set project $ProjectId | Out-Null
    Assert-GCloudOk "設定 gcloud project $ProjectId"
    Assert-GCloudProjectTarget
    Say "重驗正式稽核桶與專用執行身分"
    Invoke-DeploymentPreflight
}

Say "驗證 Cloud Run 不可變 revision 設定"
if ($VerifyOnly) {
    $revisionState = Assert-CloudRunRevisionConfiguration -RequireExclusiveTraffic
} else {
    $revisionState = Assert-CloudRunRevisionConfiguration -CandidateTag $CandidateTag
}

# ⚠ Cloud Run 一個服務有**兩個等價網址**：
#   舊式 https://<服務>-<雜湊>-<區碼>.a.run.app
#   新式 https://<服務>-<專案編號>.<區域>.run.app   ← gcloud deploy 印的是這個
# `value(status.url)` 回的是舊式，於是腳本最後印的網址與部署過程印的不同，
# 而使用者手上 App 設定的又是另一個 —— 三個都能用，但看起來像是「網址變了」，
# 會讓人跑去改一個根本不需要改的設定。統一取新式。
#
# ⚠ **不要相信欄位名，要相信打得通。**
# 第一版用 `--format "value(status.urls[])"` 挑新式網址，但那個欄位在部分 gcloud
# 版本根本不存在——查詢靜默回空字串，偏好邏輯無聲失效，於是又印回舊式網址。
# 這種「查一個不存在的欄位」不會報錯，只會安靜地什麼都不做，是最難察覺的一類 bug。
# 改成：依文件規則組出新式網址，**實際打一次 /api/health**，200 才採用。
$url = if ($VerifyOnly) {
    Invoke-GCloud run services describe $Service --region $Region --project $ProjectId --format "value(status.url)"
} else {
    [string]$revisionState.tagged_url
}
if ([string]::IsNullOrWhiteSpace([string]$url)) {
    throw "Cloud Run verification URL is empty"
}
$altUrl = $null
$projNumForUrl = Invoke-GCloud projects describe $ProjectId --project $ProjectId --format "value(projectNumber)"
if ($VerifyOnly -and $projNumForUrl) {
    $newStyle = "https://$Service-$projNumForUrl.$Region.run.app"
    if ($newStyle -ne $url) {
        $probe = Get-HttpResult -Uri "$newStyle/api/health" -TimeoutSec 30
        if ($probe.Code -eq 200) { $altUrl = $url; $url = $newStyle }
    }
}

$criticalHealthFailures = @()
Say "部署後驗證"
try {
    $h = Invoke-RestMethod "$url/api/health" -TimeoutSec 90
    Write-Host "  ✓ /api/health → $($h.status)"
    # 降級模式是「會回答的錯誤」——服務照常回 200 並給出面積，只是演算法完全不同。
    # 這種失敗不會有人注意到，所以部署當下就要攔。
    if ($h.status -ne "healthy") {
        $criticalHealthFailures += "status=$($h.status)"
        Warn "❌ 後端處於降級模式：$($h.degraded_reason)"
        Warn "   量測結果不具臨床參考價值。請確認 requirements.txt 含 onnxruntime 且 models/ 內有 .onnx。"
    } else { Write-Host "  ✓ 分割模型已載入（非降級模式）" }
    # ── 端點真的掛上了嗎 ──────────────────────────────────────────
    #
    # blueprint 註冊被包在 try/except 裡，失敗只印一行 stdout。
    # 2026-08-19：`/api/v1/lite/segment` 因為註冊時參照了尚未定義的函式而
    # NameError，服務照常啟動、健康檢查全綠、**那條路一直 404**，兩輪沒被發現。
    #
    # 用 GET 去打一個只收 POST 的端點：**掛上了會回 405，沒掛上才是 404**。
    # 這一條分得出「端點存在但方法不對」與「端點根本不存在」，
    # 而後者正是那次事故的形狀。
    if ($h.blueprint_failures -and $h.blueprint_failures.Count -gt 0) {
        $criticalHealthFailures += "blueprint failures"
        foreach ($bf in $h.blueprint_failures) {
            Warn "❌ 端點未註冊：$($bf.name) —— $($bf.error)"
        }
    }
    foreach ($ep in @("/api/v1/lite/segment", "/api/v1/annotation", "/api/v1/depth")) {
        $r = Get-HttpResult -Uri "$url$ep"
        if ($r.Code -eq 404) {
            $criticalHealthFailures += "$ep missing"
            Warn "❌ $ep 回 404 —— 這條路**沒有掛上**（blueprint 註冊失敗），不是權限問題。"
        } elseif ($r.Code -in 401, 403, 405) {
            Write-Host "  ✓ $ep 已註冊（GET → $($r.Code)）"
        } else {
            $criticalHealthFailures += "$ep unexpected HTTP $($r.Code)"
            Write-Host "  ? $ep → $($r.Code)（非預期）"
        }
    }
    # classify 模組單獨檢查：登入與 stats 會照常 200，只有量測會 503，
    # 光看 status 綠燈是抓不到的（實際發生過）。
    if ($h.services.classify_modules -ne $true) {
        $criticalHealthFailures += "classify modules unavailable"
        Warn "❌ classify 模組未載入 —— /api/v1/classify 會回 503（量測會失敗）"
    } else { Write-Host "  ✓ classify 模組已載入（組織分類 / PUSH / ArUco）" }
    # 儲存後端必須是 gcs，否則資料會隨 Cloud Run 實例回收而消失且無任何錯誤
    if ($h.store -notlike "gcs://*") {
        $criticalHealthFailures += "store is not GCS"
        Warn "❌ 儲存後端是 [$($h.store)]，不是 GCS。Cloud Run 的容器檔案系統是暫時的，"
        Warn "   佇列與影像會在實例回收時消失。請確認 WOUNDAI_STORE=gcs 已設定。"
    } else { Write-Host "  ✓ 儲存後端：$($h.store)" }
    if ($h.audit_retention.verified -ne $true -or $h.audit_retention.locked -ne $true `
            -or [int64]$h.audit_retention.retention_seconds -ne 220903200) {
        $criticalHealthFailures += "audit retention is not verified+locked for 7 years"
    } else { Write-Host "  ✓ 稽核桶 7 年 retention 已實讀且鎖定" }
    if ([string]$h.audit_retention.bucket -cne $AuditBucket) {
        $criticalHealthFailures += "runtime audit bucket does not match [$AuditBucket]"
    }
    if ($h.care_receipt.configured -ne $true) {
        $criticalHealthFailures += "care receipt keyring not configured"
    } else { Write-Host "  ✓ care receipt keyring 已設定" }
    if ($h.canonicalization_version -ne 'canon-v1;cv2==5.0.0;jpeg_q=95') {
        $criticalHealthFailures += "canonicalization version mismatch"
    }
    if ($h.canonicalization_golden_sha256 -ne 'ccb92d5c6df548442a59cd71f2a41971177b679216e571a1b3f6de811d7b6748') {
        $criticalHealthFailures += "canonicalization golden mismatch"
    }
    if ($h.canonicalization_golden_ok -ne $true) {
        $criticalHealthFailures += "canonicalization golden was not computed successfully at runtime"
    }
    if ([string]$h.build.revision -cne $script:EXPECTED_REVISION) {
        $criticalHealthFailures += "health revision does not match ready revision [$script:EXPECTED_REVISION]"
    }
    if ([string]$h.build.git_commit -cne $GitCommit) {
        $criticalHealthFailures += "health git commit does not match clean local HEAD [$GitCommit]"
    }
} catch {
    $criticalHealthFailures += "health readback failed: $_"
    Warn "健康檢查失敗（首次冷啟動可能較久，稍後重試）：$_"
}
if ($criticalHealthFailures.Count -gt 0) {
    throw "部署後關鍵驗證失敗：" + ($criticalHealthFailures -join '; ')
}

# 舊的公開預設密碼必須已經失效。這條檢查存在的理由：
# 密碼曾經硬編碼在公開 repo 裡，如果 secret 沒掛好而程式又留著預設值，
# 服務就會用一個全世界都知道的密碼對外開放。
$r = Get-HttpResult -Uri "$url/api/auth/login" -Method POST `
     -Body '{"username":"admin","password":"woundai-admin"}'
if (-not $r.Reached) {
    $criticalHealthFailures += "known-default password probe was unreachable"
    Warn "❌ 無法連線，舊預設密碼未經驗證：$($r.Content)"
} elseif ($r.Code -eq 200) {
    $criticalHealthFailures += "known default admin password accepted"
    Warn "❌ 舊的公開預設密碼仍可登入！請立即檢查 ADMIN_PASSWORD secret 是否正確掛載。"
} elseif ($r.Code -eq 401 -or $r.Code -eq 403) {
    Write-Host "  ✓ 舊預設密碼已失效（HTTP $($r.Code)）"
} else {
    $criticalHealthFailures += "known-default password probe returned unexpected HTTP $($r.Code)"
    Warn "❌ 舊預設密碼探針回非預期 HTTP $($r.Code)"
}

# 管理端點必須 fail-closed。
#
# 這條檢查的存在理由很具體：新增一個路由時忘了掛 @jwt_required 是最容易犯、
# 也最不容易被發現的錯——本機測試時瀏覽器帶著 token，一切正常；
# 上線後它就是一個**任何人都能列出所有帳號**的公開端點。
# 部署當下用「不帶 token」打一次，是唯一能在事故前抓到它的時機。
Say "管理端點存取控制"
foreach ($ep in @("/api/v1/users", "/api/v1/audit",
                  "/api/v1/flywheel/records",
                  "/api/v1/flywheel/record/0123456789abcdef/preview.svg")) {
    $r = Get-HttpResult -Uri "$url$ep"
    if (-not $r.Reached) {
        $criticalHealthFailures += "$ep authorization probe was unreachable"
        Warn "❌ $ep 連不上，無法建立拒絕未授權請求的證據：$($r.Content)"
    } elseif ($r.Code -eq 401 -or $r.Code -eq 403) {
        Write-Host "  ✓ $ep 未帶 token → HTTP $($r.Code)（拒絕）"
    } else {
        $criticalHealthFailures += "$ep unauthenticated probe returned unexpected HTTP $($r.Code)"
        Warn "❌ $ep 未帶 token → 非預期 HTTP $($r.Code)"
    }
}
$c = Get-HttpResult -Uri "$url/console"
if (-not $c.Reached) {
    Warn "⚠ /console 連不上，**未經驗證**：$($c.Content)"
} elseif ($c.Code -eq 200 -and $c.Content -match 'id="tab-users"' -and $c.Content -match 'id="tab-audit"') {
    Write-Host "  ✓ /console 含四個管理頁籤（Dashboard・系統狀態・稽核・帳號）"
} elseif ($c.Code -eq 200) {
    Warn "⚠ /console 可開，但沒有管理分區 —— 這版映像是舊的主控台。"
} else {
    Warn "⚠ /console → HTTP $($c.Code)"
}

# These probes run after the health block above, so their failures need their
# own terminal gate.  A warning is not an acceptable result for a known public
# admin credential or an unauthenticated administrative response.
if ($criticalHealthFailures.Count -gt 0) {
    throw "部署後關鍵驗證失敗：" + ($criticalHealthFailures -join '; ')
}

# ── 部署身分：雲端跑的到底是不是本機這一版 ──────────────────────────────
#
# 這是整份驗證裡最直接回答「部署有沒有生效」的一條。
# 先前兩次事故（漏裝 onnxruntime、URL 探測靜默失效）都不是功能報錯，
# 而是「部署完了，但跑的不是預期的東西」——那從功能表現上看不出來。
Say "部署身分"
$hb = Get-HttpResult -Uri "$url/api/health"
if (-not $hb.Reached) {
    throw "/api/health unreachable during deployment identity verification: $($hb.Content)"
} else {
    try { $hj = $hb.Content | ConvertFrom-Json }
    catch { throw "/api/health returned invalid JSON during deployment identity verification: $_" }
    $b = $hj.build
    if (-not $b) {
        throw "/api/health has no build identity"
    } else {
        Write-Host "  雲端 revision   $($b.revision)"
        Write-Host "  雲端 git commit $($b.git_commit)"
        Write-Host "  部署時間        $($b.deployed_at)"
        if ([string]$b.revision -cne $script:EXPECTED_REVISION) {
            throw "health revision [$($b.revision)] does not match ready revision [$script:EXPECTED_REVISION]"
        }
        if ([string]$b.git_commit -cne $GitCommit) {
            throw "cloud git commit [$($b.git_commit)] does not match clean local HEAD [$GitCommit]"
        }
        Write-Host "  ✓ 與本機完整 SHA $GitCommit 一致"
    }
}

if (-not $VerifyOnly) {
    Say "候選驗證全綠後切換 100% 流量"
    try {
        Invoke-GCloud run services update-traffic $Service `
            --project $ProjectId `
            --region $Region `
            "--to-revisions=$($script:EXPECTED_REVISION)=100" `
            "--remove-tags=$CandidateTag" `
            --quiet
        Assert-GCloudOk "Cloud Run candidate traffic cutover"
        [void](Assert-CloudRunRevisionConfiguration -RequireExclusiveTraffic)

        $liveUrl = Invoke-GCloud run services describe $Service --region $Region `
            --project $ProjectId --format "value(status.url)"
        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace([string]$liveUrl)) {
            throw "cannot resolve live service URL after cutover"
        }
        $liveHealth = Get-HttpResult -Uri "$liveUrl/api/health" -TimeoutSec 90
        if (-not $liveHealth.Reached -or $liveHealth.Code -ne 200) {
            throw "live health probe failed after cutover: HTTP $($liveHealth.Code) $($liveHealth.Content)"
        }
        try { $liveJson = $liveHealth.Content | ConvertFrom-Json }
        catch { throw "live health returned invalid JSON after cutover: $_" }
        if ($liveJson.status -cne 'healthy' `
                -or [string]$liveJson.build.revision -cne $script:EXPECTED_REVISION `
                -or [string]$liveJson.build.git_commit -cne $GitCommit `
                -or $liveJson.audit_retention.verified -ne $true `
                -or $liveJson.audit_retention.locked -ne $true) {
            throw "live health identity/security evidence mismatches the verified candidate"
        }
        $url = [string]$liveUrl
        Write-Host "  ✓ live traffic and health identify revision $($script:EXPECTED_REVISION)"
    } catch {
        $cutoverError = $_
        Warn "candidate cutover verification failed; rolling traffic back to $PreviousRevision"
        Invoke-GCloud run services update-traffic $Service `
            --project $ProjectId `
            --region $Region `
            "--to-revisions=$PreviousRevision=100" `
            --quiet
        if ($LASTEXITCODE -ne 0) {
            throw "CUTOVER FAILED AND AUTOMATIC ROLLBACK FAILED. original=$cutoverError"
        }
        throw "candidate cutover failed; traffic restored to $PreviousRevision. original=$cutoverError"
    }
}

if ($altUrl) {
    # 兩個都印出來。使用者手上 App 可能設的是任一個，看到「只有一個」會以為要改設定。
    Write-Host "`n（另一個等價網址：$altUrl —— 同一個服務，兩者皆可用，不必改 App 設定）"
}

Write-Host "`n服務網址　：$url" -ForegroundColor Green
Write-Host "管理主控台：$url/console" -ForegroundColor Green
Write-Host @"

下一步
  1. 管理者：瀏覽器開 $url/console → 以 admin 登入 → 展開「帳號管理／稽核軌跡／系統狀態」
     （操作手冊：docs/admin_operations.md）
  2. 臨床測試者：App 主畫面 →「設定」→ 後端位址填上面的網址（含 https://）
     → 填各自的帳號密碼 → 儲存 → 連線測試
  3. 批次開通帳號：.\provision_users.ps1 -BaseUrl $url
"@
