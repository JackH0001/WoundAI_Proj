# P0-4 merge gate. Proves that the required GitHub Actions workflows really ran
# and really succeeded for one exact commit that is really on the remote, before
# a pull request is marked ready, merged, or promoted.
#
# Read-only by construction: this script never pushes, never marks a pull
# request ready and never merges. It answers one question and sets an exit code.
#
# Why it exists (2026-09-06 post-mortem, two fail-open paths in one session):
#   1. `gh pr checks <n> --watch` printed "no checks reported" because the check
#      runs had not been created yet. That is "not yet", not "green". It exits
#      non-zero, but the calling PowerShell line did not test $LASTEXITCODE, so
#      `gh pr ready` and `gh pr merge` ran with no CI result at all.
#   2. A later attempt reported "already completed with 'success'" -- for a run
#      created by an EARLIER push, while the push actually being gated had
#      failed. Looking a commit up by SHA alone cannot tell those apart.
# This script closes both: the named remote ref must already point at the gated
# commit, and the run that satisfies the gate must have been created at or after
# an instant the caller captured before pushing.
#
# Windows PowerShell 5.1 compatible; keep UTF-8 BOM + CRLF on disk.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Repo,
    [Parameter(Mandatory = $true)][string]$Ref,
    [Parameter(Mandatory = $true)][string]$Sha,
    [Parameter(Mandatory = $true)][string[]]$Workflow,
    [Parameter(Mandatory = $true)][datetime]$NotBefore,
    [int]$TimeoutSeconds = 1800,
    [int]$PollSeconds = 15
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

function Say([string]$Message) { Write-Host "`n=== $Message ===" -ForegroundColor Cyan }
function Die([string]$Message) { throw $Message }
function Get-Prop($Object, [string]$Name) {
    if ($null -eq $Object) { return $null }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) { return $null }
    return $property.Value
}
function Test-OperatorSupplied([string]$Label, [string]$Value) {
    # Same mechanical rejection as harden_bucket.ps1: a value that still carries
    # placeholder shape is an unanswered question, not an argument. On
    # 2026-09-06 a literal "<branch>" was pasted into a push command; the push
    # failed, and the gate that followed still reported success.
    if ([string]::IsNullOrWhiteSpace($Value) -or $Value -match '[\r\n<>（）]' -or
            $Value -match '輸入|填入|placeholder|範本|TODO') {
        Die "$Label must be operator-supplied text; placeholder shape refused: [$Value]"
    }
}
function Invoke-GhJson([string]$What, [string[]]$Arguments) {
    $raw = & gh @Arguments
    if ($LASTEXITCODE -ne 0) { Die "$What failed (gh exit $LASTEXITCODE)" }
    if (-not $raw) { Die "$What returned no output" }
    try { return (($raw | Out-String) | ConvertFrom-Json) }
    catch { Die "$What returned invalid JSON: $_" }
}
function ConvertTo-Utc([string]$Value, [string]$What) {
    if ([string]::IsNullOrWhiteSpace($Value)) { Die "$What is empty" }
    $styles = [System.Globalization.DateTimeStyles]::AdjustToUniversal -bor `
              [System.Globalization.DateTimeStyles]::AssumeUniversal
    $parsed = [datetime]::MinValue
    if (-not [datetime]::TryParse($Value, [System.Globalization.CultureInfo]::InvariantCulture,
            $styles, [ref]$parsed)) {
        Die "$What is not a timestamp: [$Value]"
    }
    return $parsed
}

$exitCode = 1
try {
    Test-OperatorSupplied '-Repo' $Repo
    Test-OperatorSupplied '-Ref' $Ref
    Test-OperatorSupplied '-Sha' $Sha
    foreach ($workflowName in $Workflow) { Test-OperatorSupplied '-Workflow' $workflowName }

    if ($Repo -notmatch '^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$') {
        Die "-Repo must be owner/name, got [$Repo]"
    }
    if ($Ref -notmatch '^(heads|tags)/[A-Za-z0-9._/-]+$') {
        Die "-Ref must be heads/<branch> or tags/<tag>, got [$Ref]"
    }
    if ($Sha -cnotmatch '^[0-9a-f]{40}$') {
        # An abbreviated id cannot be compared with a run headSha and can be
        # ambiguous. Refuse it rather than compare prefixes.
        Die "-Sha must be the full 40-character lowercase commit id, got [$Sha]"
    }
    if ($Workflow.Count -lt 1) { Die "-Workflow must name at least one workflow" }
    if ($TimeoutSeconds -lt 1 -or $PollSeconds -lt 1) {
        Die "-TimeoutSeconds and -PollSeconds must be positive"
    }

    $notBeforeUtc = $NotBefore.ToUniversalTime()
    if ($notBeforeUtc -gt (Get-Date).ToUniversalTime()) {
        Die "-NotBefore is in the future; capture it immediately before the push"
    }

    if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
        Die "GitHub CLI (gh) is not on PATH"
    }
    & gh auth status | Out-Null
    if ($LASTEXITCODE -ne 0) { Die "gh is not authenticated (gh auth status exit $LASTEXITCODE)" }

    Say "The remote ref must already point at the commit being gated"
    $refObject = Invoke-GhJson "read ref $Ref" @(
        'api', "repos/$Repo/git/ref/$Ref", '--jq', '.object')
    $remoteSha = [string](Get-Prop $refObject 'sha')
    if ($remoteSha -cne $Sha) {
        Die "remote $Repo $Ref is at [$remoteSha], not the gated commit [$Sha]; the push did not land"
    }
    Write-Host "OK: $Repo $Ref = $Sha"

    Say "Waiting for a run of each required workflow created at or after $($notBeforeUtc.ToString('o'))"
    $deadline = (Get-Date).ToUniversalTime().AddSeconds($TimeoutSeconds)
    $completed = @{}
    while ($true) {
        $pending = @()
        foreach ($workflowName in $Workflow) {
            if ($completed.ContainsKey($workflowName)) { continue }
            $runs = Invoke-GhJson "list runs of $workflowName" @(
                'run', 'list', '--repo', $Repo, '--workflow', $workflowName, '--limit', '50',
                '--json', 'databaseId,headSha,status,conclusion,createdAt,event,url')
            $matched = @()
            foreach ($run in @($runs)) {
                if ([string](Get-Prop $run 'headSha') -cne $Sha) { continue }
                $created = ConvertTo-Utc ([string](Get-Prop $run 'createdAt')) "createdAt of a run"
                # A run created before the caller pushed belongs to an earlier
                # push of the same commit. It cannot answer for this one.
                if ($created -lt $notBeforeUtc) { continue }
                # Keep every match, not just the newest. One commit can carry a
                # push run and a pull_request run of the same workflow under
                # separate ids; taking only the newest lets a red one hide
                # behind a green one.
                $matched += $run
            }
            if ($matched.Count -eq 0) { $pending += "$workflowName (no run yet)"; continue }
            $unfinished = @()
            foreach ($run in $matched) {
                $status = [string](Get-Prop $run 'status')
                if ($status -cne 'completed') {
                    $unfinished += ("run " + [string](Get-Prop $run 'databaseId') + " $status")
                }
            }
            if ($unfinished.Count -gt 0) {
                $pending += ("$workflowName (" + ($unfinished -join ', ') + ")")
                continue
            }
            $completed[$workflowName] = $matched
        }
        if ($pending.Count -eq 0) { break }
        if ((Get-Date).ToUniversalTime() -ge $deadline) {
            Die "timed out after $TimeoutSeconds s; unresolved: $($pending -join '; ')"
        }
        Write-Host ("waiting: " + ($pending -join '; '))
        Start-Sleep -Seconds $PollSeconds
    }

    Say "Verdict"
    $failed = @()
    foreach ($workflowName in $Workflow) {
        # Every matching run is judged, so a second run of the same workflow for
        # the same commit cannot be skipped over.
        foreach ($run in @($completed[$workflowName])) {
            $conclusion = [string](Get-Prop $run 'conclusion')
            $runEvent = [string](Get-Prop $run 'event')
            # Only 'success' passes. 'skipped' and 'neutral' mean the gate did
            # not actually run, which is the state this script exists to refuse.
            $verdict = 'FAIL'
            if ($conclusion -ceq 'success') { $verdict = 'PASS' }
            Write-Host ("{0,-5} {1,-24} {2,-13} run {3,-12} {4,-10} {5}" -f $verdict, $workflowName,
                $runEvent, [string](Get-Prop $run 'databaseId'), $conclusion, [string](Get-Prop $run 'url'))
            if ($verdict -cne 'PASS') { $failed += "$workflowName/$runEvent=$conclusion" }
        }
    }
    if ($failed.Count -gt 0) { Die "required workflows not successful: $($failed -join ', ')" }

    Write-Host ""
    Write-Host "PASS: every required workflow succeeded for $Sha on $Repo $Ref" -ForegroundColor Green
    Write-Host "      workflows: $($Workflow -join ', ')"
    $exitCode = 0
}
catch {
    Write-Host ""
    Write-Host "[DIE] $_" -ForegroundColor Red
    $exitCode = 1
}
exit $exitCode
