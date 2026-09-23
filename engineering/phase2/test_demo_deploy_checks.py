#!/usr/bin/env python3
"""Run the demo deploy script's cloud-facing checks against a fake gcloud.

Static string tests prove a check is written; they cannot prove it refuses.
These three functions only ever talk to gcloud, so the harness replaces the
script's Invoke-GCloud with a call to a tiny Python stand-in. Using a real
child process rather than a PowerShell function matters: the checks depend on
native stderr and $LASTEXITCODE, and those only behave like gcloud's when they
come from an actual process -- on Windows PowerShell 5.1 as much as on pwsh.

The checks exist because the first version of the demo script mounted
production's JWT, Flask and care-receipt secrets. The production service reads
the role straight from the token's claims and never re-checks that the account
exists, with a 24-hour lifetime -- so a nurse token minted for an App Review
account on the demo service would have been accepted by production. That
version passed every test and the CI gate, because nothing looked at which
secrets the demo service mounted.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "Backend" / "Flask" / "deploy_demo_candidate.ps1"
SHELLS = []
for candidate in (shutil.which("powershell"), shutil.which("pwsh")):
    if candidate and candidate.lower() not in {p.lower() for p in SHELLS}:
        SHELLS.append(candidate)

DEMO_SA = "woundai-demo-run@woundai-jackh001.iam.gserviceaccount.com"
PROD_SA = "woundai-runtime@woundai-jackh001.iam.gserviceaccount.com"
PRODUCTION = ["woundai-admin-password", "woundai-jwt-secret",
              "woundai-flask-secret", "woundai-care-receipt-secret"]
DEMO_MAP = {"JWT_SECRET_KEY": "woundai-demo-jwt-secret",
            "FLASK_SECRET_KEY": "woundai-demo-flask-secret",
            "CARE_RECEIPT_SECRET": "woundai-demo-care-receipt-secret",
            "WOUNDAI_DEMO_SEED_PASSWORD": "woundai-demo-password"}

FAKE_GCLOUD = r'''
import json, os, sys
scenario = json.loads(os.environ["WOUNDAI_FAKE_SCENARIO"])
key = " ".join(a for a in sys.argv[1:] if not a.startswith("--"))
for prefix, reply in scenario.items():
    if key.startswith(prefix):
        sys.stdout.write(reply.get("stdout", ""))
        sys.stderr.write(reply.get("stderr", ""))
        sys.exit(reply.get("exit", 0))
sys.stderr.write("ERROR: fake gcloud has no reply for [%s]\n" % key)
sys.exit(3)
'''

HARNESS = r'''
$ErrorActionPreference = 'Continue'
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
    $env:WOUNDAI_DEMO_CHECK_SOURCE, [ref]$tokens, [ref]$errors)
if ($errors.Count) { Write-Output 'HARNESS syntax error'; exit 2 }
foreach ($name in @('Ok', 'Warn', 'Get-ProductionSecretNames', 'Get-DemoSecretMap',
                    'Assert-DemoIdentityCannotReadProductionSecrets',
                    'Assert-DemoRevisionConfiguration', 'Assert-DemoSecretReady')) {
    $node = $ast.Find({param($n)
        $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name
    }, $true)
    if ($null -eq $node) { Write-Output "HARNESS missing $name"; exit 2 }
    . ([scriptblock]::Create($node.Extent.Text))
}
# Every gcloud call goes through Invoke-GCloud; replace it with a real child
# process so stderr and exit codes behave like the real CLI.
function Invoke-GCloud { & $env:WOUNDAI_FAKE_PYTHON $env:WOUNDAI_FAKE_GCLOUD @args }

$ProjectId = 'woundai-jackh001'
$Region = 'asia-east1'
$DemoSeedSecret = 'woundai-demo-password'
$DemoJwtSecret = 'woundai-demo-jwt-secret'
$DemoFlaskSecret = 'woundai-demo-flask-secret'
$DemoCareReceiptSecret = 'woundai-demo-care-receipt-secret'

$cases = Get-Content -Raw -LiteralPath $env:WOUNDAI_DEMO_CHECK_CASES | ConvertFrom-Json
foreach ($c in $cases) {
    $RuntimeServiceAccount = [string]$c.sa
    $env:WOUNDAI_FAKE_SCENARIO = ($c.scenario | ConvertTo-Json -Depth 20 -Compress)
    try {
        switch ([string]$c.fn) {
            'identity' { Assert-DemoIdentityCannotReadProductionSecrets | Out-Null }
            'revision' { Assert-DemoRevisionConfiguration -Revision 'woundai-backend-demo-00001' | Out-Null }
            'ready'    { Assert-DemoSecretReady | Out-Null }
            default    { throw "unknown fn $($c.fn)" }
        }
        Write-Output ("CASE {0} PASS" -f $c.id)
    } catch {
        Write-Output ("CASE {0} REJECT {1}" -f $c.id, ($_.Exception.Message -replace "[`r`n]+", ' '))
    }
}
'''


def policy(*members):
    return {"stdout": json.dumps({"bindings": [
        {"role": "roles/secretmanager.secretAccessor", "members": list(members)}],
        "etag": "BwX"})}


def clean_identity(**overrides):
    s = {"secrets get-iam-policy %s" % n: policy("serviceAccount:" + PROD_SA)
         for n in PRODUCTION}
    s.update(overrides)
    return s


def revision(env=None, secrets=None, max_scale="1"):
    env = {"WOUNDAI_STORE": "local", "WOUNDAI_DEMO_SEED_USER": "demo01",
           "WOUNDAI_DEMO_SEED_ROLE": "nurse"} if env is None else env
    secrets = dict(DEMO_MAP) if secrets is None else secrets
    items = [{"name": k, "value": v} for k, v in env.items()]
    items += [{"name": k, "valueFrom": {"secretKeyRef": {"key": "latest", "name": v}}}
              for k, v in secrets.items()]
    doc = {"metadata": {"annotations": {"autoscaling.knative.dev/maxScale": max_scale}},
           "spec": {"containers": [{"env": items}]}}
    return {"run revisions describe": {"stdout": json.dumps(doc)}}


def ready(**overrides):
    s = {"secrets versions list %s" % n: {"stdout": "projects/p/secrets/%s/versions/1\n" % n}
         for n in DEMO_MAP.values()}
    s.update(overrides)
    return s


NOT_FOUND = {"exit": 1, "stderr": "ERROR: (gcloud.secrets.get-iam-policy) NOT_FOUND: "
                                  "Secret [projects/1/secrets/x] not found.\n"}
DENIED = {"exit": 1, "stderr": "ERROR: (gcloud.secrets.get-iam-policy) PERMISSION_DENIED: "
                               "Permission denied on resource.\n"}

CASES = {
    # -- the demo identity must not be able to read any production secret --
    "identity_clean": ("identity", DEMO_SA, clean_identity(), None),
    "identity_holds_jwt": ("identity", DEMO_SA, clean_identity(**{
        "secrets get-iam-policy woundai-jwt-secret":
            policy("serviceAccount:" + PROD_SA, "serviceAccount:" + DEMO_SA)}),
        "holds [roles/secretmanager.secretAccessor] on production secret [woundai-jwt-secret]"),
    "identity_holds_admin_pw": ("identity", DEMO_SA, clean_identity(**{
        "secrets get-iam-policy woundai-admin-password": policy("serviceAccount:" + DEMO_SA)}),
        "production secret [woundai-admin-password]"),
    "identity_missing_secret_is_fine": ("identity", DEMO_SA, clean_identity(**{
        "secrets get-iam-policy woundai-care-receipt-secret": NOT_FOUND}), None),
    "identity_unreadable_policy_refuses": ("identity", DEMO_SA, clean_identity(**{
        "secrets get-iam-policy woundai-flask-secret": DENIED}),
        "cannot read the IAM policy of production secret [woundai-flask-secret]"),
    "identity_stderr_noise_does_not_break_json": ("identity", DEMO_SA, clean_identity(**{
        "secrets get-iam-policy woundai-jwt-secret": dict(
            policy("serviceAccount:" + PROD_SA),
            stderr="WARNING: a newer gcloud is available.\n")}), None),
    "identity_lookalike_member_is_not_a_match": ("identity", DEMO_SA, clean_identity(**{
        "secrets get-iam-policy woundai-jwt-secret":
            policy("serviceAccount:x" + DEMO_SA, "group:" + DEMO_SA)}), None),
    "identity_no_bindings": ("identity", DEMO_SA, clean_identity(**{
        "secrets get-iam-policy woundai-jwt-secret": {"stdout": '{"etag": "ACAB"}'}}), None),

    # -- what the deployed revision actually mounts --
    # Overrides are dict literals on purpose: the values are secret NAMES, but
    # a keyword argument of the form JWT_SECRET_KEY=<quoted name> is textually
    # a hardcoded-secret assignment and .gitleaks.toml rightly flags it. The
    # mapping is data (env var -> secret name), so it is written as data.
    "revision_clean": ("revision", DEMO_SA, revision(), None),
    "revision_mounts_prod_jwt": ("revision", DEMO_SA,
        revision(secrets={**DEMO_MAP, "JWT_SECRET_KEY": "woundai-jwt-secret"}),
        "mounts production secret [woundai-jwt-secret]"),
    "revision_mounts_prod_care": ("revision", DEMO_SA,
        revision(secrets={**DEMO_MAP, "CARE_RECEIPT_SECRET": "woundai-care-receipt-secret"}),
        "mounts production secret [woundai-care-receipt-secret]"),
    "revision_carries_admin_password": ("revision", DEMO_SA,
        revision(secrets={**DEMO_MAP, "ADMIN_PASSWORD": "woundai-demo-admin-password"}),
        "ADMIN_PASSWORD"),
    "revision_swapped_keys": ("revision", DEMO_SA,
        revision(secrets={**DEMO_MAP, "JWT_SECRET_KEY": "woundai-demo-flask-secret"}),
        "maps [JWT_SECRET_KEY]"),
    "revision_missing_care_keyring": ("revision", DEMO_SA,
        revision(secrets={k: v for k, v in DEMO_MAP.items() if k != "CARE_RECEIPT_SECRET"}),
        "maps [CARE_RECEIPT_SECRET]"),
    "revision_on_gcs": ("revision", DEMO_SA,
        revision(env={"WOUNDAI_STORE": "gcs"}), "WOUNDAI_STORE=local"),
    "revision_two_instances": ("revision", DEMO_SA, revision(max_scale="3"),
        "single instance"),

    # -- every demo secret must exist before deploying --
    "ready_all_present": ("ready", DEMO_SA, ready(), None),
    "ready_jwt_missing": ("ready", DEMO_SA, ready(**{
        "secrets versions list woundai-demo-jwt-secret": {
            "exit": 1, "stderr": "ERROR: NOT_FOUND\n"}}),
        "secret [woundai-demo-jwt-secret] not found"),
    "ready_care_no_enabled_version": ("ready", DEMO_SA, ready(**{
        "secrets versions list woundai-demo-care-receipt-secret": {"stdout": ""}}),
        "[woundai-demo-care-receipt-secret] has no ENABLED version"),
}


@unittest.skipUnless(SHELLS, "PowerShell is required for demo deploy check tests")
class DemoDeployChecksAgainstFakeGcloud(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="woundai-demo-checks-")
        fake = Path(cls.tmp) / "fake_gcloud.py"
        fake.write_text(FAKE_GCLOUD, encoding="utf-8")
        harness = Path(cls.tmp) / "harness.ps1"
        harness.write_text(HARNESS, encoding="utf-8-sig")
        cases = Path(cls.tmp) / "cases.json"
        cases.write_text(json.dumps([
            {"id": cid, "fn": fn, "sa": sa, "scenario": scenario}
            for cid, (fn, sa, scenario, _) in CASES.items()]), encoding="utf-8")
        cls.results = {}
        for shell in SHELLS:
            env = os.environ.copy()
            env.update({"WOUNDAI_DEMO_CHECK_SOURCE": str(DEMO),
                        "WOUNDAI_DEMO_CHECK_CASES": str(cases),
                        "WOUNDAI_FAKE_PYTHON": sys.executable,
                        "WOUNDAI_FAKE_GCLOUD": str(fake)})
            proc = subprocess.run(
                [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-File", str(harness)], env=env, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=180)
            seen = {}
            for line in proc.stdout.splitlines():
                if line.startswith("CASE "):
                    _, cid, rest = line.split(" ", 2)
                    seen[cid] = rest
            cls.results[shell] = (proc, seen)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_every_case_ran_in_every_shell(self):
        for shell, (proc, seen) in self.results.items():
            with self.subTest(shell=shell):
                self.assertEqual(sorted(seen), sorted(CASES),
                                 proc.stdout[-2000:] + proc.stderr[-2000:])

    def test_each_check_accepts_or_refuses_as_specified(self):
        for shell, (proc, seen) in self.results.items():
            for cid, (_fn, _sa, _scenario, expected) in CASES.items():
                with self.subTest(shell=shell, case=cid):
                    got = seen.get(cid, "<missing>")
                    if expected is None:
                        self.assertEqual(got, "PASS", got)
                    else:
                        self.assertTrue(got.startswith("REJECT "), got)
                        self.assertIn(expected, got)


if __name__ == "__main__":
    unittest.main(verbosity=2)
