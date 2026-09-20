#!/usr/bin/env python3
"""Execute the real deployment input gate without invoking any cloud command."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "Backend" / "Flask" / "deploy_cloudrun.ps1"
SHELLS = []
for candidate in (shutil.which("powershell"), shutil.which("pwsh")):
    if candidate and candidate.lower() not in {p.lower() for p in SHELLS}:
        SHELLS.append(candidate)

HARNESS = r'''
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
    $env:WOUNDAI_DEPLOY_GATE_SOURCE, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'deployment script syntax error' }
$node = $ast.Find({param($n)
    $n -is [Management.Automation.Language.FunctionDefinitionAst] -and
    $n.Name -eq 'Assert-DeploymentInputs'
}, $true)
if ($null -eq $node) { throw 'missing Assert-DeploymentInputs' }
. ([scriptblock]::Create($node.Extent.Text))

$Setup = $false
$VerifyOnly = $false
$CandidateOnly = $true
$PromoteCandidate = $false
$ExpectedCandidateRevision = ''
$CandidateE2EEvidencePath = ''
$PromotionAuthorisationRef = ''
$ProjectId = 'woundai-jackh001'
$Bucket = 'woundai-flywheel-jackh001'
$AuditBucket = $env:WOUNDAI_DEPLOY_GATE_AUDIT_BUCKET
$RuntimeServiceAccount = 'woundai-runtime@woundai-jackh001.iam.gserviceaccount.com'
try {
    Assert-DeploymentInputs
    Write-Host 'PASS'
    exit 0
} catch {
    Write-Host ('REJECT ' + $_)
    exit 1
}
'''


@unittest.skipUnless(SHELLS, "PowerShell is required for deployment input tests")
class DeploymentInputGateTests(unittest.TestCase):
    def invoke(self, shell, audit_bucket):
        with tempfile.TemporaryDirectory(prefix="woundai-deploy-gate-") as temp:
            script = Path(temp) / "gate.ps1"
            script.write_text(HARNESS, encoding="utf-8-sig")
            env = os.environ.copy()
            env["WOUNDAI_DEPLOY_GATE_SOURCE"] = str(DEPLOY)
            env["WOUNDAI_DEPLOY_GATE_AUDIT_BUCKET"] = audit_bucket
            return subprocess.run(
                [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-File", str(script)], env=env, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=20,
            )

    def test_retired_epoch_exact_and_case_variant_are_rejected(self):
        values = (
            "woundai-flywheel-jackh001-audit-epoch-20260905",
            "WOUNDAI-FLYWHEEL-JACKH001-AUDIT-EPOCH-20260905",
        )
        for shell in SHELLS:
            for value in values:
                with self.subTest(shell=shell, value=value):
                    result = self.invoke(shell, value)
                    self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn("retired after the 2026-09-06 test contamination",
                                  result.stdout)

    def test_uppercase_and_malformed_bucket_names_are_rejected(self):
        values = ("WoundAI-valid-shape-but-uppercase", "bad bucket", "-bad", "bad-")
        for shell in SHELLS:
            for value in values:
                with self.subTest(shell=shell, value=value):
                    result = self.invoke(shell, value)
                    self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn("invalid bucket name", result.stdout)

    def test_valid_candidate_input_passes_without_cloud_access(self):
        value = "woundai-flywheel-jackh001-audit-epoch-20260913"
        for shell in SHELLS:
            with self.subTest(shell=shell):
                result = self.invoke(shell, value)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("PASS", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
