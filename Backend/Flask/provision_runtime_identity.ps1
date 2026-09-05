# P0-4 dedicated Cloud Run runtime identity. Default invocation is read-only.
# Windows PowerShell 5.1 compatible; keep UTF-8 BOM + CRLF on disk.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ProjectId,
    [Parameter(Mandatory = $true)][string]$Bucket,
    [Parameter(Mandatory = $true)][string]$AuditBucket,
    [Parameter(Mandatory = $true)][string]$RuntimeServiceAccount,
    [string]$Region = 'asia-east1',
    [string]$CareReceiptSecret = 'woundai-care-receipt-secret',
    [string]$RuntimeMainRoleId = 'woundaiRuntimeMainObjects',
    [string]$RuntimeAuditRoleId = 'woundaiRuntimeAuditAppend',
    [switch]$Apply
)

$ErrorActionPreference = 'Stop'
$script:GCLOUD = if (Get-Command gcloud.cmd -ErrorAction SilentlyContinue) { 'gcloud.cmd' } else { 'gcloud' }
$script:AUDIT_RETENTION_SECONDS = 220903200L
$script:MAIN_PERMISSIONS = @(
    'storage.objects.create','storage.objects.delete','storage.objects.get','storage.objects.list')
$script:AUDIT_PERMISSIONS = @(
    'storage.buckets.get','storage.objects.create','storage.objects.get','storage.objects.list')

function Say([string]$Message) { Write-Host "`n=== $Message ===" -ForegroundColor Cyan }
function Die([string]$Message) { throw "[DIE] $Message" }
function Plan([string]$Message) { Write-Host "  PLAN: $Message" -ForegroundColor Yellow }
function Invoke-GCloudScoped([string[]]$Arguments) { & $script:GCLOUD @Arguments "--project=$ProjectId" }
function Invoke-GCloudChecked([string]$What, [string[]]$Arguments) {
    Invoke-GCloudScoped $Arguments
    if ($LASTEXITCODE -ne 0) { Die "$What failed (gcloud exit $LASTEXITCODE)" }
}
function Try-GCloudJson([string[]]$Arguments) {
    $raw = Invoke-GCloudScoped $Arguments 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $raw) { return $null }
    try { return ($raw | ConvertFrom-Json) } catch { Die "gcloud returned invalid JSON: $_" }
}
function Get-GCloudJson([string]$What, [string[]]$Arguments) {
    $value = Try-GCloudJson $Arguments
    if ($null -eq $value) { Die "$What failed or does not exist" }
    return $value
}
function Get-Field($Object, [string[]]$Paths) {
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
function Assert-Inputs {
    if ($AuditBucket -ceq "$Bucket-audit" -or
            $AuditBucket -match '(?i)(smoke|test|tmp|temp|dev|sandbox)') {
        Die "AuditBucket must be an explicit non-legacy, non-disposable locked epoch"
    }
    if ($AuditBucket -eq $Bucket) { Die 'AuditBucket must differ from Bucket' }
    $suffix = "@$ProjectId.iam.gserviceaccount.com"
    if (-not $RuntimeServiceAccount.EndsWith($suffix, [StringComparison]::Ordinal) -or
            $RuntimeServiceAccount -match '^\d+-compute@developer\.gserviceaccount\.com$' -or
            $RuntimeServiceAccount -match '@appspot\.gserviceaccount\.com$') {
        Die 'RuntimeServiceAccount must be a dedicated account in ProjectId'
    }
    if ($RuntimeServiceAccount -notmatch '^[a-z][a-z0-9-]{4,28}[a-z0-9]@[a-z][a-z0-9-]{4,28}[a-z0-9]\.iam\.gserviceaccount\.com$') {
        Die 'RuntimeServiceAccount has an invalid service-account email shape'
    }
    foreach ($name in @($RuntimeMainRoleId,$RuntimeAuditRoleId)) {
        if ($name -notmatch '^[A-Za-z][A-Za-z0-9_.]{2,63}$') { Die "invalid custom role id: [$name]" }
    }
}
function Assert-Bucket([string]$Name, [string]$ProjectNumber, [bool]$RequireLocked) {
    $bucket = Get-GCloudJson "bucket gs://$Name" @(
        'storage','buckets','describe',"gs://$Name",'--raw','--format=json')
    if ([string](Get-Field $bucket @('projectNumber')) -cne $ProjectNumber) {
        Die "gs://$Name belongs to another project"
    }
    if ([string](Get-Field $bucket @('location')) -cne $Region.ToUpperInvariant()) {
        Die "gs://$Name location does not match [$Region]"
    }
    if ((Get-Field $bucket @('iamConfiguration.publicAccessPrevention','public_access_prevention')) -cne 'enforced' -or
            (Get-Field $bucket @('iamConfiguration.uniformBucketLevelAccess.enabled','uniform_bucket_level_access')) -ne $true) {
        Die "gs://$Name is not PAP+UBLA hardened"
    }
    if ($RequireLocked) {
        $retention = [int64](Get-Field $bucket @('retentionPolicy.retentionPeriod','retention_policy.retentionPeriod'))
        $locked = (Get-Field $bucket @('retentionPolicy.isLocked','retention_policy.isLocked')) -eq $true
        if ($retention -ne $script:AUDIT_RETENTION_SECONDS -or -not $locked) {
            Die "gs://$Name is not the locked 7-year audit epoch"
        }
    }
}
function Ensure-ServiceAccount {
    $sa = Try-GCloudJson @('iam','service-accounts','describe',$RuntimeServiceAccount,'--format=json')
    if ($null -eq $sa) {
        if (-not $Apply) { Plan "create dedicated service account $RuntimeServiceAccount"; return }
        $accountId = $RuntimeServiceAccount.Split('@')[0]
        Invoke-GCloudChecked 'create runtime service account' @(
            'iam','service-accounts','create',$accountId,'--display-name=WoundAI clinical runtime','--quiet')
        $sa = Get-GCloudJson 'created runtime service account' @(
            'iam','service-accounts','describe',$RuntimeServiceAccount,'--format=json')
    }
    if ($null -ne $sa -and ($sa.disabled -eq $true -or [string]$sa.projectId -cne $ProjectId)) {
        Die 'runtime service account is disabled or belongs to another project'
    }
}
function Ensure-CustomRole([string]$RoleId, [string]$Title, [string[]]$Permissions) {
    $role = Try-GCloudJson @('iam','roles','describe',$RoleId,'--format=json')
    $permissionCsv = (@($Permissions | Sort-Object) -join ',')
    if ($null -eq $role) {
        if (-not $Apply) { Plan "create custom role $RoleId with [$permissionCsv]"; return }
        Invoke-GCloudChecked "create custom role $RoleId" @(
            'iam','roles','create',$RoleId,"--title=$Title",
            '--description=WoundAI reviewed runtime least-privilege role',"--permissions=$permissionCsv",'--stage=GA','--quiet')
        $role = Get-GCloudJson "created custom role $RoleId" @('iam','roles','describe',$RoleId,'--format=json')
    } elseif (-not (Test-ExactSet @($role.includedPermissions) $Permissions)) {
        if (-not $Apply) { Plan "update custom role $RoleId to exact permissions [$permissionCsv]"; return }
        Invoke-GCloudChecked "update custom role $RoleId" @(
            'iam','roles','update',$RoleId,"--title=$Title",
            '--description=WoundAI reviewed runtime least-privilege role',"--permissions=$permissionCsv",'--stage=GA','--quiet')
        $role = Get-GCloudJson "updated custom role $RoleId" @('iam','roles','describe',$RoleId,'--format=json')
    }
    if ($null -ne $role -and (-not (Test-ExactSet @($role.includedPermissions) $Permissions) -or
            $role.deleted -eq $true -or $role.stage -eq 'DISABLED')) {
        Die "custom role $RoleId failed exact readback"
    }
}
function Ensure-CareReceiptSecret {
    $secret = Try-GCloudJson @('secrets','describe',$CareReceiptSecret,'--format=json')
    if ($null -ne $secret) {
        Assert-SecretHasEnabledVersion $CareReceiptSecret
        return
    }
    if (-not $Apply) { Plan "create care receipt keyring secret $CareReceiptSecret"; return }
    $key = New-Object byte[] 32
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($key) } finally { $rng.Dispose() }
    $encoded = [Convert]::ToBase64String($key).TrimEnd('=').Replace('+','-').Replace('/','_')
    $kid = 'phase-a-' + (Get-Date).ToUniversalTime().ToString('yyyyMMdd')
    $payload = @{active_kid=$kid;keys=@{$kid=@{secret_b64=$encoded}}} | ConvertTo-Json -Depth 6 -Compress
    $temp = [IO.Path]::GetTempFileName()
    try {
        [IO.File]::WriteAllText($temp,$payload,(New-Object Text.UTF8Encoding $false))
        Invoke-GCloudChecked "create care receipt secret $CareReceiptSecret" @(
            'secrets','create',$CareReceiptSecret,"--data-file=$temp",'--replication-policy=automatic','--quiet')
    } finally {
        [Array]::Clear($key,0,$key.Length)
        Remove-Item -LiteralPath $temp -Force -ErrorAction SilentlyContinue
    }
    [void](Get-GCloudJson "created secret $CareReceiptSecret" @('secrets','describe',$CareReceiptSecret,'--format=json'))
}
function Assert-SecretHasEnabledVersion([string]$Name) {
    $versions = @(Invoke-GCloudScoped @(
        'secrets','versions','list',$Name,'--filter=state:ENABLED','--limit=1','--format=value(name)') 2>$null)
    if ($LASTEXITCODE -ne 0 -or $versions.Count -ne 1 -or
            [string]::IsNullOrWhiteSpace([string]$versions[0])) {
        Die "secret $Name has no readable ENABLED version"
    }
}
function Get-MemberRoles($Policy, [string]$Member) {
    return @($Policy.bindings | Where-Object { @($_.members) -contains $Member } |
        ForEach-Object { [string]$_.role } | Sort-Object -Unique)
}
function Ensure-Binding([string]$Kind, [string]$Resource, [string]$Role) {
    $member = "serviceAccount:$RuntimeServiceAccount"
    if ($Kind -eq 'bucket') {
        $policyArgs = @('storage','buckets','get-iam-policy',"gs://$Resource",'--format=json')
        $addArgs = @('storage','buckets','add-iam-policy-binding',"gs://$Resource","--member=$member","--role=$Role",'--quiet')
    } elseif ($Kind -eq 'secret') {
        $policyArgs = @('secrets','get-iam-policy',$Resource,'--format=json')
        $addArgs = @('secrets','add-iam-policy-binding',$Resource,"--member=$member","--role=$Role",'--quiet')
    } else { Die "unsupported IAM resource kind: $Kind" }
    $policy = Try-GCloudJson $policyArgs
    if ($null -eq $policy) {
        if (-not $Apply -and $Kind -eq 'secret') {
            Plan "bind $member to $Role on secret $Resource after the planned secret creation"
            return
        }
        Die "$Kind IAM $Resource cannot be read"
    }
    $roles = @(Get-MemberRoles $policy $member)
    $unexpected = @($roles | Where-Object { $_ -cne $Role })
    if ($unexpected.Count -ne 0) {
        Die "$Kind $Resource already grants the runtime identity unreviewed roles: [$($unexpected -join '; ')]"
    }
    if ($roles -notcontains $Role) {
        if (-not $Apply) { Plan "bind $member to $Role on $Kind $Resource"; return }
        Invoke-GCloudChecked "bind $Role on $Resource" $addArgs
    }
}
function Assert-ExactBinding([string]$Kind, [string]$Resource, [string]$ExpectedRole) {
    $member = "serviceAccount:$RuntimeServiceAccount"
    $policyArgs = if ($Kind -eq 'bucket') {
        @('storage','buckets','get-iam-policy',"gs://$Resource",'--format=json')
    } else { @('secrets','get-iam-policy',$Resource,'--format=json') }
    $policy = Get-GCloudJson "$Kind IAM $Resource" $policyArgs
    $roles = @(Get-MemberRoles $policy $member)
    if (-not (Test-ExactSet $roles @($ExpectedRole))) {
        Die "$Kind $Resource roles for runtime identity mismatch: [$($roles -join '; ')]"
    }
}

Assert-Inputs
$mode = if ($Apply) { 'APPLY' } else { 'DRY-RUN' }
Say "plan ($mode)"
Write-Host "  project: $ProjectId"
Write-Host "  runtime: $RuntimeServiceAccount"
Write-Host "  main: gs://$Bucket"
Write-Host "  audit: gs://$AuditBucket"

Say 'immutable target preflight'
$project = Get-GCloudJson "project $ProjectId" @('projects','describe',$ProjectId,'--format=json')
$projectNumber = [string]$project.projectNumber
if ([string]::IsNullOrWhiteSpace($projectNumber) -or [string]$project.projectId -cne $ProjectId) {
    Die 'project identity readback mismatch'
}
Assert-Bucket $Bucket $projectNumber $false
Assert-Bucket $AuditBucket $projectNumber $true
foreach ($requiredSecret in @('woundai-admin-password','woundai-jwt-secret')) {
    [void](Get-GCloudJson "required secret $requiredSecret" @('secrets','describe',$requiredSecret,'--format=json'))
    Assert-SecretHasEnabledVersion $requiredSecret
}

$member = "serviceAccount:$RuntimeServiceAccount"
$projectPolicy = Get-GCloudJson 'project IAM' @('projects','get-iam-policy',$ProjectId,'--format=json')
$projectRoles = @(Get-MemberRoles $projectPolicy $member)
if ($projectRoles.Count -ne 0) { Die "runtime identity has forbidden project-level roles: [$($projectRoles -join '; ')]" }
$legacyRuntime = "serviceAccount:$projectNumber-compute@developer.gserviceaccount.com"
$legacyRoles = @(Get-MemberRoles $projectPolicy $legacyRuntime)
if ($legacyRoles.Count -ne 0) {
    Write-Host "  POST-CUTOVER BLOCKER: default Compute SA project roles=[$($legacyRoles -join '; ')]" -ForegroundColor Yellow
}

Say 'identity, roles and care keyring'
Ensure-ServiceAccount
Ensure-CustomRole $RuntimeMainRoleId 'WoundAI Runtime Main Objects' $script:MAIN_PERMISSIONS
Ensure-CustomRole $RuntimeAuditRoleId 'WoundAI Runtime Audit Append' $script:AUDIT_PERMISSIONS
Ensure-CareReceiptSecret

Say 'resource-scoped bindings'
$mainRole = "projects/$ProjectId/roles/$RuntimeMainRoleId"
$auditRole = "projects/$ProjectId/roles/$RuntimeAuditRoleId"
Ensure-Binding bucket $Bucket $mainRole
Ensure-Binding bucket $AuditBucket $auditRole
foreach ($secret in @('woundai-admin-password','woundai-jwt-secret',$CareReceiptSecret)) {
    Ensure-Binding secret $secret 'roles/secretmanager.secretAccessor'
}

if ($Apply) {
    Say 'fail-closed readback'
    Ensure-ServiceAccount
    Ensure-CustomRole $RuntimeMainRoleId 'WoundAI Runtime Main Objects' $script:MAIN_PERMISSIONS
    Ensure-CustomRole $RuntimeAuditRoleId 'WoundAI Runtime Audit Append' $script:AUDIT_PERMISSIONS
    foreach ($secret in @('woundai-admin-password','woundai-jwt-secret',$CareReceiptSecret)) {
        Assert-SecretHasEnabledVersion $secret
    }
    Assert-ExactBinding bucket $Bucket $mainRole
    Assert-ExactBinding bucket $AuditBucket $auditRole
    foreach ($secret in @('woundai-admin-password','woundai-jwt-secret',$CareReceiptSecret)) {
        Assert-ExactBinding secret $secret 'roles/secretmanager.secretAccessor'
    }
    $projectPolicy = Get-GCloudJson 'project IAM readback' @('projects','get-iam-policy',$ProjectId,'--format=json')
    if (@(Get-MemberRoles $projectPolicy $member).Count -ne 0) {
        Die 'runtime identity acquired a project-level role during provisioning'
    }
    Write-Host 'PASS: dedicated runtime identity is provisioned with exact resource-scoped roles.' -ForegroundColor Green
} else {
    Write-Host 'DRY RUN ONLY: no service account, secret, role, or IAM mutation was performed.' -ForegroundColor Yellow
}

Write-Host 'POST-CUTOVER GATE: inventory other workloads, then remove default Compute SA Editor/objectAdmin/secretAccessor.' -ForegroundColor Yellow
