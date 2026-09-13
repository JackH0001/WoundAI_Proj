#!/usr/bin/env python3
"""Exercise the actual PowerShell CI gate with an in-process, network-free gh.

Unlike source-pattern checks, these cases change GitHub's apparent state while
the gate waits. The gh function shadows the CLI, so no credentials or network
are used; all fixture files live under a temporary directory.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
GATE = ROOT / "engineering" / "phase2" / "Assert-CIGreen.ps1"
SHELL = shutil.which("pwsh") or shutil.which("powershell")
HARNESS = r'''
$ErrorActionPreference = 'Stop'
$global:ciMockCounts = @{}
$global:ciMockRefReads = 0
$global:ciMockSha = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
function global:gh {
    $global:LASTEXITCODE = 0
    if ($args[0] -eq 'auth') { return 'mock authenticated' }
    if ($args[0] -eq 'api') {
        $global:ciMockRefReads++
        $refSha = $global:ciMockSha
        if ($env:WOUNDAI_CI_MOCK_SCENARIO -eq 'ref_moves' -and $global:ciMockRefReads -gt 1) {
            $refSha = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
        }
        return (@{sha=$refSha} | ConvertTo-Json -Compress)
    }
    if ($args[0] -ne 'run' -or $args[1] -ne 'list') {
        throw 'mock refuses unexpected gh command'
    }
    $index = [Array]::IndexOf($args, '--workflow')
    if ($index -lt 0) { throw 'missing workflow in mock gh call' }
    $workflowName = [string]$args[$index + 1]
    if (-not $global:ciMockCounts.ContainsKey($workflowName)) {
        $global:ciMockCounts[$workflowName] = 0
    }
    $global:ciMockCounts[$workflowName]++
    $count = $global:ciMockCounts[$workflowName]
    $run = @{
        databaseId=1; headSha=$global:ciMockSha; status='completed';
        conclusion='success'; createdAt=(Get-Date).ToUniversalTime().ToString('o');
        event='pull_request'; url='https://example.invalid/mock-run'
    }
    if ($workflowName -eq 'B.yml' -and $count -eq 1) {
        $run.status = 'in_progress'; $run.conclusion = ''
    }
    if ($env:WOUNDAI_CI_MOCK_SCENARIO -eq 'late_failure' -and
            $workflowName -eq 'A.yml' -and $count -gt 1) {
        $run.databaseId = 2; $run.conclusion = 'failure'
    }
    return ($run | ConvertTo-Json -Compress)
}
function global:Start-Sleep { }
& $env:WOUNDAI_CI_MOCK_GATE -Repo owner/repo -Ref heads/main -Sha $global:ciMockSha `
    -Workflow A.yml,B.yml -NotBefore (Get-Date).AddMinutes(-1) `
    -TimeoutSeconds 3 -PollSeconds 1
exit $LASTEXITCODE
'''


@unittest.skipUnless(SHELL, "PowerShell is required for the runtime CI gate tests")
class CIGateRuntimeTests(unittest.TestCase):
    def run_gate(self, scenario):
        with tempfile.TemporaryDirectory(prefix="woundai-ci-gate-") as temp:
            script = Path(temp) / "mock-github.ps1"
            script.write_text(HARNESS, encoding="utf-8-sig")
            env = os.environ.copy()
            env["WOUNDAI_CI_MOCK_SCENARIO"] = scenario
            env["WOUNDAI_CI_MOCK_GATE"] = str(GATE)
            return subprocess.run(
                [SHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-File", str(script)], env=env, capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=20,
            )

    def test_stable_workflows_and_ref_pass_after_pending_finishes(self):
        result = self.run_gate("stable")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("after workflow verification", result.stdout)
        self.assertIn("observed snapshot only", result.stdout)

    def test_earlier_green_workflow_gaining_failure_is_rejected(self):
        result = self.run_gate("late_failure")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("required workflows not successful", result.stdout)

    def test_remote_ref_moving_while_waiting_is_rejected(self):
        result = self.run_gate("ref_moves")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("the ref moved", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
