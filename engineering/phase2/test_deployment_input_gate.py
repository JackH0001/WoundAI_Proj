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
DEMO = ROOT / "Backend" / "Flask" / "deploy_demo_candidate.ps1"
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


DEMO_HARNESS = r"""
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
    $env:WOUNDAI_DEMO_GATE_SOURCE, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'demo deployment script syntax error' }
foreach ($fn in @('Get-ProductionSecretNames', 'Get-DemoSecretMap', 'Assert-DemoInputs')) {
    $node = $ast.Find({param($n)
        $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $fn
    }, $true)
    if ($null -eq $node) { throw "missing $fn" }
    . ([scriptblock]::Create($node.Extent.Text))
}

$ProjectId = 'woundai-jackh001'
$RuntimeServiceAccount = $env:WOUNDAI_DEMO_GATE_SA
$DemoAuthorisationRef = $env:WOUNDAI_DEMO_GATE_AUTH
$Service = $env:WOUNDAI_DEMO_GATE_SERVICE
$ProductionService = 'woundai-backend'
$DemoSeedUser = $env:WOUNDAI_DEMO_GATE_USER
$DemoSeedRole = $env:WOUNDAI_DEMO_GATE_ROLE
$DemoSeedSecret = $env:WOUNDAI_DEMO_GATE_SEED_SECRET
$DemoJwtSecret = $env:WOUNDAI_DEMO_GATE_JWT_SECRET
$DemoFlaskSecret = $env:WOUNDAI_DEMO_GATE_FLASK_SECRET
$DemoCareReceiptSecret = $env:WOUNDAI_DEMO_GATE_CARE_SECRET
try {
    Assert-DemoInputs
    Write-Host 'PASS'
    exit 0
} catch {
    Write-Host ('REJECT ' + $_)
    exit 1
}
"""

GOOD_DEMO_INPUT = {
    "SERVICE": "woundai-backend-demo",
    "SA": "woundai-demo-run@woundai-jackh001.iam.gserviceaccount.com",
    "AUTH": "Jack 2026-09-22 approve demo revision for App Review",
    "USER": "demo01",
    "ROLE": "nurse",
    "SEED_SECRET": "woundai-demo-password",
    "JWT_SECRET": "woundai-demo-jwt-secret",
    "FLASK_SECRET": "woundai-demo-flask-secret",
    "CARE_SECRET": "woundai-demo-care-receipt-secret",
}


@unittest.skipUnless(SHELLS, "PowerShell is required for deployment input tests")
class DemoDeploymentInputGateTests(unittest.TestCase):
    """Run the demo script's real guard, with no cloud command reachable.

    Static string matching proves the guard is written; this proves it refuses.
    The two that matter most are the service-boundary cases: a demo revision
    landing in the production service is one update-traffic away from serving
    live clinical traffic off ephemeral local storage.
    """

    def invoke(self, shell, **overrides):
        values = dict(GOOD_DEMO_INPUT)
        values.update(overrides)
        with tempfile.TemporaryDirectory(prefix="woundai-demo-gate-") as temp:
            script = Path(temp) / "gate.ps1"
            script.write_text(DEMO_HARNESS, encoding="utf-8-sig")
            env = os.environ.copy()
            env["WOUNDAI_DEMO_GATE_SOURCE"] = str(DEMO)
            for key, value in values.items():
                env["WOUNDAI_DEMO_GATE_" + key] = value
            return subprocess.run(
                [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-File", str(script)], env=env, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=20,
            )

    def reject(self, shell, expected, **overrides):
        result = self.invoke(shell, **overrides)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(expected, result.stdout)

    def test_the_production_service_is_refused(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                self.reject(shell, "refuse to deploy the demo revision into the "
                                   "production service",
                            SERVICE="woundai-backend")

    def test_a_service_name_not_marked_demo_is_refused(self):
        for shell in SHELLS:
            for name in ("woundai-staging", "woundai-backend-2", "demo-woundai"):
                with self.subTest(shell=shell, service=name):
                    self.reject(shell, "must end in -demo", SERVICE=name)

    def test_placeholder_authorisation_is_refused(self):
        values = ("", "   ", "placeholder for the demo deploy", "範本：請填入授權字句",
                  "TODO write this later", "example authorisation text",
                  "line one\nline two", "xxx approve this")
        for shell in SHELLS:
            for value in values:
                with self.subTest(shell=shell, auth=value[:24]):
                    result = self.invoke(shell, AUTH=value)
                    self.assertNotEqual(result.returncode, 0,
                                        result.stdout + result.stderr)
                    self.assertIn("DemoAuthorisationRef", result.stdout)

    def test_a_role_carrying_doctor_endorsement_is_refused(self):
        # physician holds gt.verify and annotation.submit. Handing that to an
        # external App Review account is the failure auth_users' `lite` comment
        # records having already happened once.
        for shell in SHELLS:
            for role in ("physician", "admin", "engineer", "lite", "NURSE", ""):
                with self.subTest(shell=shell, role=role):
                    self.reject(shell, "-DemoSeedRole must be nurse or assistant",
                                ROLE=role)

    def test_a_seed_user_outside_the_demo_shape_is_refused(self):
        for shell in SHELLS:
            for user in ("admin", "admin2", "demo1", "demo001", "DEMO01", "lite01"):
                with self.subTest(shell=shell, user=user):
                    self.reject(shell, "-DemoSeedUser must match demoNN", USER=user)

    def test_default_and_foreign_service_accounts_are_refused(self):
        for shell in SHELLS:
            for sa in ("421209514056-compute@developer.gserviceaccount.com",
                       "woundai-demo@other-project.iam.gserviceaccount.com",
                       "woundai-demo@woundai-jackh001.iam.gserviceaccount.com.evil.com"):
                with self.subTest(shell=shell, sa=sa):
                    result = self.invoke(shell, SA=sa)
                    self.assertNotEqual(result.returncode, 0,
                                        result.stdout + result.stderr)

    def test_a_production_secret_is_refused_for_every_demo_key(self):
        # Sharing the JWT key is what would have let a demo-issued nurse token
        # work on production for 24 hours; the care-receipt HMAC key the same
        # for receipts. The error must name the production secret, not merely
        # fail the woundai-demo-* shape, so an operator can see what went wrong.
        cases = (("JWT_SECRET", "woundai-jwt-secret"),
                 ("FLASK_SECRET", "woundai-flask-secret"),
                 ("CARE_SECRET", "woundai-care-receipt-secret"),
                 ("SEED_SECRET", "woundai-admin-password"))
        for shell in SHELLS:
            for key, value in cases:
                with self.subTest(shell=shell, key=key):
                    self.reject(shell, "would use production secret [%s]" % value,
                                **{key: value})

    def test_a_secret_outside_the_demo_namespace_is_refused(self):
        for shell in SHELLS:
            for value in ("my-jwt-secret", "woundai-demojwt", "WOUNDAI-DEMO-JWT-SECRET"):
                with self.subTest(shell=shell, value=value):
                    self.reject(shell, "must use a woundai-demo-* secret", JWT_SECRET=value)

    def test_one_key_may_not_serve_two_purposes(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                self.reject(shell, "demo secrets must be distinct",
                            FLASK_SECRET="woundai-demo-jwt-secret")

    def test_valid_demo_input_passes_without_cloud_access(self):
        for shell in SHELLS:
            for role in ("nurse", "assistant"):
                with self.subTest(shell=shell, role=role):
                    result = self.invoke(shell, ROLE=role)
                    self.assertEqual(result.returncode, 0,
                                     result.stdout + result.stderr)
                    self.assertIn("PASS", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
