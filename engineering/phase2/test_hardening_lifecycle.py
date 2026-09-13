#!/usr/bin/env python3
"""Run the real PowerShell lifecycle verifier without invoking any cloud CLI."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
HARDEN = ROOT / "Backend" / "Flask" / "harden_bucket.ps1"
SHELL = shutil.which("pwsh") or shutil.which("powershell")
HARNESS = r'''
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
    $env:WOUNDAI_LIFECYCLE_TEST_SOURCE, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'hardening script syntax error' }
function Die([string]$Message) { throw $Message }
foreach ($name in @('Get-Field','Assert-LifecyclePropertySet','Get-LifecycleKeys',
                    'Get-ExpectedLifecycleKeys','Assert-MainLifecycleCompatible')) {
    $node = $ast.Find({param($n)
        $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name
    }, $true)
    if ($null -eq $node) { throw "missing verifier function $name" }
    . ([scriptblock]::Create($node.Extent.Text))
}
$QuarantineDays=30; $StagingDays=30; $StagingMetaExtraDays=7; $NoncurrentDays=30
try {
    $config = $env:WOUNDAI_LIFECYCLE_TEST_JSON | ConvertFrom-Json
    Assert-MainLifecycleCompatible $config
    $keys = @(Get-LifecycleKeys $config)
    Write-Host ('PASS ' + ($keys -join ';'))
    exit 0
} catch { Write-Host ('REJECT ' + $_); exit 1 }
'''


def policy(container="lifecycle"):
    return {container: {"rule": [
        {"action": {"type": "Delete"}, "condition": {
            "age": 30, "matchesPrefix": ["flywheel/quarantine/"]}},
        {"action": {"type": "Delete"}, "condition": {
            "age": 30, "matchesPrefix": ["flywheel/staging/"]}},
        {"action": {"type": "Delete"}, "condition": {
            "age": 37, "matchesPrefix": ["flywheel/staging_meta/"]}},
        {"action": {"type": "Delete"}, "condition": {
            "daysSinceNoncurrentTime": 30, "isLive": False}},
    ]}}


@unittest.skipUnless(SHELL, "PowerShell is required for lifecycle verifier tests")
class HardeningLifecycleTests(unittest.TestCase):
    def verify(self, config):
        with tempfile.TemporaryDirectory(prefix="woundai-lifecycle-") as temp:
            script = Path(temp) / "verify-policy.ps1"
            script.write_text(HARNESS, encoding="utf-8-sig")
            env = os.environ.copy()
            env["WOUNDAI_LIFECYCLE_TEST_SOURCE"] = str(HARDEN)
            env["WOUNDAI_LIFECYCLE_TEST_JSON"] = json.dumps(config)
            return subprocess.run(
                [SHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-File", str(script)], env=env, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=20,
            )

    def test_reviewed_policy_passes_raw_and_normalized_sdk_containers(self):
        for container in ("lifecycle", "lifecycle_config"):
            with self.subTest(container=container):
                result = self.verify(policy(container))
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("age|30|flywheel/staging/", result.stdout)
                self.assertIn("noncurrent|30", result.stdout)

    def test_extra_age_condition_cannot_silently_disable_staging_ttl(self):
        for field, value in (("matchesSuffix", [".never"]),
                             ("numNewerVersions", 9999),
                             ("matchesStorageClass", ["ARCHIVE"]),
                             ("isLive", False)):
            with self.subTest(field=field):
                config = policy()
                config["lifecycle"]["rule"][1]["condition"][field] = value
                result = self.verify(config)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("unreviewed or missing fields", result.stdout)

    def test_extra_noncurrent_condition_is_rejected(self):
        config = policy("lifecycle_config")
        config["lifecycle_config"]["rule"][3]["condition"]["numNewerVersions"] = 9999
        result = self.verify(config)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("unreviewed or missing fields", result.stdout)

    def test_missing_live_condition_is_rejected(self):
        config = policy()
        del config["lifecycle"]["rule"][3]["condition"]["isLive"]
        result = self.verify(config)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("unreviewed or missing fields", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
