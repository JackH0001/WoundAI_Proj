# P0-4 Cloud Storage hardening. Default invocation is read-only.
# Windows PowerShell 5.1 compatible; keep UTF-8 BOM + CRLF on disk.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ProjectId,
    [Parameter(Mandatory = $true)][string]$Bucket,
    # 預設沿用 "<主桶>-audit"。指定別的桶,是為了讓「鎖定紀元」從一條乾淨的
    # 稽核鏈(GENESIS 起算)開始,而不必把開發期既有的鏈一起凍進 7 年保留。
    [string]$AuditBucket = "$Bucket-audit",
    [string]$Region = "asia-east1",
    [int]$QuarantineDays = 30,
    [int]$StagingDays = 30,
    [int]$StagingMetaExtraDays = 7,
    [int]$NoncurrentDays = 30,
    [int]$SoftDeleteDays = 7,
    [switch]$Audit,
    [switch]$Apply,
    [switch]$LockRetention,
    [string]$LockAuthorisationRef
)

$ErrorActionPreference = "Stop"
$script:GCLOUD = if (Get-Command gcloud.cmd -ErrorAction SilentlyContinue) { "gcloud.cmd" } else { "gcloud" }
$script:AUDIT_RETENTION_SECONDS = 220903200L

function Say([string]$Message) { Write-Host "`n=== $Message ===" -ForegroundColor Cyan }
function Die([string]$Message) { throw "[DIE] $Message" }
function Invoke-GCloudScoped([string[]]$Arguments) {
    # ProjectId is a safety boundary, not a label for the transcript.  Every
    # gcloud request in this script is explicitly scoped so an unrelated active
    # gcloud configuration cannot create, update, or lock a bucket elsewhere.
    & $script:GCLOUD @Arguments "--project=$ProjectId"
}
function Invoke-GCloudChecked([string]$What, [string[]]$Arguments) {
    Invoke-GCloudScoped $Arguments
    if ($LASTEXITCODE -ne 0) { Die "$What failed (gcloud exit $LASTEXITCODE)" }
}
function Get-GCloudJson([string]$What, [string[]]$Arguments) {
    $raw = Invoke-GCloudScoped $Arguments
    if ($LASTEXITCODE -ne 0 -or -not $raw) { Die "$What failed" }
    try { return ($raw | ConvertFrom-Json) } catch { Die "$What returned invalid JSON: $_" }
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
function Test-AuthorisationRef([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value) -or $Value -match '[\r\n<>（）]' -or
            $Value -match '輸入|填入|placeholder|範本') {
        Die "-LockAuthorisationRef must be operator-supplied text without placeholders or line breaks"
    }
}
function Assert-ProjectTarget {
    $project = Get-GCloudJson "describe project $ProjectId" @(
        'projects','describe',$ProjectId,'--format=json')
    if ([string](Get-Field $project @('projectId')) -cne $ProjectId) {
        Die "gcloud project identity mismatch: expected $ProjectId"
    }
    $number = [string](Get-Field $project @('projectNumber'))
    if ([string]::IsNullOrWhiteSpace($number)) {
        Die "project $ProjectId did not return a projectNumber"
    }
    $script:PROJECT_NUMBER = $number
}
function Assert-BucketProject($Configuration, [string]$Name) {
    $number = [string](Get-Field $Configuration @('projectNumber'))
    if ([string]::IsNullOrWhiteSpace($number) -or $number -cne $script:PROJECT_NUMBER) {
        Die "gs://$Name belongs to project number [$number], not $ProjectId"
    }
}
function Assert-BucketLocation($Configuration, [string]$Name) {
    if ($Configuration.location -ne $Region.ToUpperInvariant()) {
        Die "gs://$Name location [$($Configuration.location)] does not match $Region"
    }
}
function Get-Bucket([string]$Name) {
    # Normalized `gcloud storage buckets describe` output in SDK 578 omits
    # projectNumber.  The raw Storage API response retains it, which is the
    # ownership proof required before this script can mutate or lock a bucket.
    return Get-GCloudJson "describe gs://$Name" @(
        'storage','buckets','describe',"gs://$Name",'--raw','--format=json')
}
function Assert-NoPublicIam([string]$Name) {
    $iam = Get-GCloudJson "IAM gs://$Name" @(
        'storage','buckets','get-iam-policy',"gs://$Name",'--format=json')
    $public = @($iam.bindings | Where-Object {
        $_.members -contains 'allUsers' -or $_.members -contains 'allAuthenticatedUsers'
    })
    if ($public.Count -ne 0) { Die "gs://$Name has public IAM bindings" }
}
function Get-LifecycleKeys($Configuration) {
    $rules = @(Get-Field $Configuration @('lifecycle_config.rule','lifecycle.rule'))
    $keys = @()
    foreach ($rule in $rules) {
        if ($rule.action.type -ne 'Delete') { Die "unexpected non-Delete lifecycle action" }
        $condition = $rule.condition
        $prefixes = @($condition.matchesPrefix | Where-Object { $null -ne $_ })
        if ($null -ne $condition.daysSinceNoncurrentTime) {
            if ($condition.isLive -ne $false -or $prefixes.Count -ne 0) {
                Die "invalid noncurrent lifecycle rule"
            }
            $keys += "noncurrent|$($condition.daysSinceNoncurrentTime)"
        } elseif ($null -ne $condition.age -and $prefixes.Count -eq 1) {
            $keys += "age|$($condition.age)|$($prefixes[0])"
        } else { Die "unrecognized lifecycle rule" }
    }
    return @($keys | Sort-Object)
}
function Get-ExpectedLifecycleKeys {
    return @(
        "age|$QuarantineDays|flywheel/quarantine/",
        "age|$StagingDays|flywheel/staging/",
        "age|$($StagingDays + $StagingMetaExtraDays)|flywheel/staging_meta/",
        "noncurrent|$NoncurrentDays"
    ) | Sort-Object
}
function Assert-MainLifecycleCompatible($Configuration) {
    # --lifecycle-file replaces the complete lifecycle configuration.  Before
    # mutation, reject any rule outside this approved policy instead of silently
    # deleting a separately managed retention rule.
    $expected = @(Get-ExpectedLifecycleKeys)
    $unexpected = @((Get-LifecycleKeys $Configuration) | Where-Object { $_ -notin $expected })
    if ($unexpected.Count -ne 0) {
        Die "main bucket has lifecycle rules outside the approved policy: [$($unexpected -join '; ')]"
    }
}
function Assert-MainBucket($Configuration) {
    $expected = @(Get-ExpectedLifecycleKeys)
    $actual = @(Get-LifecycleKeys $Configuration)
    if (($actual -join "`n") -cne ($expected -join "`n")) {
        Die "lifecycle mismatch; actual=[$($actual -join '; ')] expected=[$($expected -join '; ')]"
    }
    if ($Configuration.location -ne $Region.ToUpperInvariant()) { Die "main bucket location mismatch" }
    if ((Get-Field $Configuration @('public_access_prevention','iamConfiguration.publicAccessPrevention')) -ne 'enforced') { Die "public access prevention not enforced" }
    if ((Get-Field $Configuration @('uniform_bucket_level_access','iamConfiguration.uniformBucketLevelAccess.enabled')) -ne $true) { Die "UBLA not enabled" }
    if ((Get-Field $Configuration @('versioning_enabled','versioning.enabled')) -ne $true) { Die "versioning not enabled" }
    $soft = [int64](Get-Field $Configuration @('soft_delete_policy.retentionDurationSeconds','softDeletePolicy.retentionDurationSeconds'))
    if ($soft -ne ($SoftDeleteDays * 86400L)) { Die "soft delete duration mismatch: $soft" }
}
function Assert-AuditBucket($Configuration, [bool]$RequireLocked) {
    if ($Configuration.location -ne $Region.ToUpperInvariant()) { Die "audit bucket location mismatch" }
    if ((Get-Field $Configuration @('public_access_prevention','iamConfiguration.publicAccessPrevention')) -ne 'enforced') { Die "audit public access prevention not enforced" }
    if ((Get-Field $Configuration @('uniform_bucket_level_access','iamConfiguration.uniformBucketLevelAccess.enabled')) -ne $true) { Die "audit UBLA not enabled" }
    $retention = [int64](Get-Field $Configuration @('retention_policy.retentionPeriod','retentionPolicy.retentionPeriod'))
    if ($retention -ne $script:AUDIT_RETENTION_SECONDS) { Die "audit retention mismatch: $retention" }
    $locked = (Get-Field $Configuration @('retention_policy.isLocked','retentionPolicy.isLocked')) -eq $true
    if ($RequireLocked -and -not $locked) { Die "audit retention is not locked" }
    return $locked
}
function Get-AuditObjectNames([string]$Name) {
    # `storage ls gs://.../**` returns a non-zero exit for an empty bucket on
    # some gcloud versions.  Use the object-list API instead.  Parse its
    # explicit one-name-per-line format rather than the CLI's normalized JSON:
    # SDK 578 changed the latter's object representation during script runs.
    # A list failure is never evidence that a bucket is empty, especially before
    # an irreversible lock.
    $raw = Invoke-GCloudScoped @(
        'storage','objects','list',"gs://$Name/**",'--format=value(name)')
    if ($LASTEXITCODE -ne 0) { Die "cannot list audit bucket objects" }
    if (-not $raw) { return @() }
    $names = @()
    foreach ($line in @($raw)) {
        $objectName = [string]$line
        if ([string]::IsNullOrWhiteSpace($objectName)) {
            Die "audit object listing returned an object without name"
        }
        $urlPrefix = "gs://$Name/"
        if ($objectName.StartsWith($urlPrefix, [StringComparison]::Ordinal)) {
            $objectName = $objectName.Substring($urlPrefix.Length)
        } elseif ($objectName.StartsWith("$Name/", [StringComparison]::Ordinal)) {
            $objectName = $objectName.Substring($Name.Length + 1)
        }
        $names += $objectName.TrimStart('/')
    }
    return @($names)
}
function Assert-AuditObjectsExpected([string]$Name, [switch]$RequireEmpty) {
    $items = @(Get-AuditObjectNames $Name)
    $unexpected = @($items | Where-Object {
        $_ -notmatch '^flywheel/(?:audit\.jsonl/[0-9]{20}\.jsonl|receipts/(?:[A-Za-z0-9][A-Za-z0-9._-]*/)*[A-Za-z0-9][A-Za-z0-9._-]*\.json)$'
    })
    if ($unexpected.Count -ne 0) {
        Die "audit bucket contains unexpected, legacy, or non-slot object paths (count=$($unexpected.Count))"
    }
    if ($RequireEmpty -and $items.Count -ne 0) {
        Die "refuse to lock a non-empty audit bucket (objects=$($items.Count))"
    }
    if ($items.Count -eq 0) { Write-Host "  audit bucket is empty (clean locked-era bucket)" }
    else { Write-Host "  expected numeric audit slots/receipts: $($items.Count)" }
}

if ($LockRetention) {
    if (-not $Audit -or -not $Apply) { Die "-LockRetention requires -Audit -Apply" }
    Test-AuthorisationRef $LockAuthorisationRef
    if (-not $PSBoundParameters.ContainsKey('AuditBucket')) {
        Die "-LockRetention requires an explicit fresh -AuditBucket; the default <main>-audit is forbidden"
    }
    if ($AuditBucket -ceq "$Bucket-audit") {
        Die "-LockRetention refuses the legacy default audit bucket gs://$AuditBucket"
    }
    if ($AuditBucket -notmatch 'audit') {
        Die "-LockRetention requires an audit-specific -AuditBucket name"
    }
}
foreach ($durationDays in @($QuarantineDays,$StagingDays,$StagingMetaExtraDays,$NoncurrentDays,$SoftDeleteDays)) {
    if ($durationDays -lt 1) { Die "retention durations must be positive" }
}
if ([string]::IsNullOrWhiteSpace($AuditBucket)) { Die "-AuditBucket must not be empty" }
if ($AuditBucket -eq $Bucket) { Die "-AuditBucket must differ from -Bucket" }
foreach ($bucketCandidate in @($Bucket,$AuditBucket)) {
    if ($bucketCandidate -notmatch '^[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]$') {
        Die "invalid bucket name: [$bucketCandidate]"
    }
}

$lifecycle = @"
{"lifecycle":{"rule":[
{"action":{"type":"Delete"},"condition":{"age":$QuarantineDays,"matchesPrefix":["flywheel/quarantine/"]}},
{"action":{"type":"Delete"},"condition":{"age":$StagingDays,"matchesPrefix":["flywheel/staging/"]}},
{"action":{"type":"Delete"},"condition":{"age":$($StagingDays + $StagingMetaExtraDays),"matchesPrefix":["flywheel/staging_meta/"]}},
{"action":{"type":"Delete"},"condition":{"daysSinceNoncurrentTime":$NoncurrentDays,"isLive":false}}
]}}
"@

Say "plan"
$mode = if ($Apply) { 'APPLY' } else { 'READ-ONLY' }
Write-Host "  mode: $mode"
Write-Host "  main: gs://$Bucket; staging=$StagingDays; staging_meta=$($StagingDays + $StagingMetaExtraDays); noncurrent=$NoncurrentDays; soft-delete=$SoftDeleteDays days"
if ($Audit) { Write-Host "  audit: gs://$AuditBucket; retention=7y; lock=$([bool]$LockRetention)" }

Say "project and main-bucket preflight"
Assert-ProjectTarget
$mainPreflight = Get-Bucket $Bucket
Assert-BucketProject $mainPreflight $Bucket
Assert-BucketLocation $mainPreflight $Bucket
Assert-MainLifecycleCompatible $mainPreflight
Write-Host "  project: $ProjectId ($script:PROJECT_NUMBER); main target: gs://$Bucket"

if ($Apply) {
    Say "apply main-bucket controls"
    Invoke-GCloudChecked "main bucket controls" @(
        'storage','buckets','update',"gs://$Bucket",'--public-access-prevention',
        '--uniform-bucket-level-access','--versioning',"--soft-delete-duration=$($SoftDeleteDays)d",'--quiet')
    $temp = [IO.Path]::GetTempFileName()
    try {
        [IO.File]::WriteAllText($temp, $lifecycle, (New-Object Text.UTF8Encoding $false))
        Invoke-GCloudChecked "main lifecycle" @(
            'storage','buckets','update',"gs://$Bucket","--lifecycle-file=$temp",'--quiet')
    } finally { Remove-Item -LiteralPath $temp -Force -ErrorAction SilentlyContinue }

    if ($Audit) {
        # A failed describe is followed by an explicit create of this *scoped*
        # name.  Any other failure (for example permission on an existing
        # foreign bucket) still fails on create/readback; it is never treated as
        # evidence that the target belongs to this project.
        Invoke-GCloudScoped @('storage','buckets','describe',"gs://$AuditBucket",'--format=json') | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Invoke-GCloudChecked "create audit bucket" @(
                'storage','buckets','create',"gs://$AuditBucket","--location=$Region",
                '--uniform-bucket-level-access','--public-access-prevention','--quiet')
        }

        # Do not set a retention policy, much less lock it, until the named
        # bucket has been re-described, tied to ProjectId, and completely
        # enumerated.  A valid-looking name or a failed listing is not enough.
        $auditBeforeRetention = Get-Bucket $AuditBucket
        Assert-BucketProject $auditBeforeRetention $AuditBucket
        Assert-BucketLocation $auditBeforeRetention $AuditBucket
        Assert-NoPublicIam $AuditBucket
        Assert-AuditObjectsExpected $AuditBucket -RequireEmpty:$LockRetention

        Invoke-GCloudChecked "audit bucket access controls" @(
            'storage','buckets','update',"gs://$AuditBucket",'--public-access-prevention',
            '--uniform-bucket-level-access','--quiet')
        $auditAfterAccess = Get-Bucket $AuditBucket
        Assert-BucketProject $auditAfterAccess $AuditBucket
        Assert-BucketLocation $auditAfterAccess $AuditBucket
        Assert-NoPublicIam $AuditBucket
        Assert-AuditObjectsExpected $AuditBucket -RequireEmpty:$LockRetention

        Invoke-GCloudChecked "audit retention policy" @(
            'storage','buckets','update',"gs://$AuditBucket",'--retention-period=7y','--quiet')
        $preLock = Get-Bucket $AuditBucket
        Assert-BucketProject $preLock $AuditBucket
        [void](Assert-AuditBucket $preLock $false)
        # Re-list immediately before the irreversible operation.  A concurrent
        # unexpected write must stop the lock rather than become seven-year
        # evidence by accident.
        Assert-AuditObjectsExpected $AuditBucket -RequireEmpty:$LockRetention
        if ($LockRetention) {
            $alreadyLocked = (Get-Field $preLock @('retention_policy.isLocked','retentionPolicy.isLocked')) -eq $true
            if (-not $alreadyLocked) {
                Say "IRREVERSIBLE: lock audit retention"
                Invoke-GCloudChecked "lock audit retention" @(
                    'storage','buckets','update',"gs://$AuditBucket",'--lock-retention-period','--quiet')
            }
        }
    }
}

Say "fail-closed readback"
$main = Get-Bucket $Bucket
Assert-BucketProject $main $Bucket
Assert-MainBucket $main
Assert-NoPublicIam $Bucket
Write-Host "  main bucket: verified"
if ($Audit) {
    $auditConfig = Get-Bucket $AuditBucket
    Assert-BucketProject $auditConfig $AuditBucket
    $locked = Assert-AuditBucket $auditConfig ([bool]$LockRetention)
    Assert-NoPublicIam $AuditBucket
    Assert-AuditObjectsExpected $AuditBucket -RequireEmpty:$LockRetention
    Write-Host "  audit bucket: verified; locked=$locked"
}

Write-Host "`nPASS: bucket configuration matches the P0-4 Phase A policy." -ForegroundColor Green
