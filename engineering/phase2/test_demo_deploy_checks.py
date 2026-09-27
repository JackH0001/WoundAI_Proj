#!/usr/bin/env python3
"""Run the demo deploy script's cloud-facing checks against a fake gcloud.

Static string tests prove a check is written; they cannot prove it refuses.
The checks here only ever talk to gcloud (or to the filesystem, for the vendor
copy), so the harness replaces the script's Invoke-GCloud with a call to a tiny
Python stand-in. Using a real child process rather than a PowerShell function
matters: the checks depend on native stderr and $LASTEXITCODE, and those only
behave like gcloud's when they come from an actual process -- on Windows
PowerShell 5.1 as much as on pwsh.

The checks exist because two earlier versions of the demo script passed every
test and the CI gate while being wrong in ways no test looked at:

  * the first mounted production's JWT, Flask and care-receipt secrets. The
    production service reads the role straight from the token's claims and
    never re-checks that the account exists, with a 24-hour lifetime -- so a
    nurse token minted for an App Review account on the demo service would
    have been accepted by production;
  * the second (b24a47c, reviewed 2026-09-23) built the image without the
    engineering vendor/ modules, read the commit from a health field that does
    not exist, skipped the production identity comparison whenever the lookup
    failed, and answered a missing seed line or a commit mismatch with a
    yellow warning before printing "done".

Every case below is a scenario the script must accept or refuse, run on each
PowerShell available (Windows PowerShell 5.1 and pwsh on Windows; pwsh in CI).
"""
import hashlib
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
SERVICE = "woundai-backend-demo"
REVISION = "woundai-backend-demo-00001"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
URL = "https://woundai-backend-demo-421209514056.asia-east1.run.app"
PRODUCTION = ["woundai-admin-password", "woundai-jwt-secret",
              "woundai-flask-secret", "woundai-care-receipt-secret"]
DEMO_MAP = {"JWT_SECRET_KEY": "woundai-demo-jwt-secret",
            "FLASK_SECRET_KEY": "woundai-demo-flask-secret",
            "CARE_RECEIPT_SECRET": "woundai-demo-care-receipt-secret",
            "WOUNDAI_DEMO_SEED_PASSWORD": "woundai-demo-password"}
VENDOR_FILES = ["phase2/wound_classifier.py", "phase1/clinical_rules.py",
                "phase2/aruco_calibrate.py", "phase2/verify_area_sheet.py",
                "phase2/color_calib.py", "phase0/preprocessing.json"]

# The key is every argument that is not a --flag, joined by spaces. A scenario
# entry answers the longest key it is a whole-word prefix of, so
# "run services describe woundai-backend" never answers a describe of
# woundai-backend-demo. A list reply is consumed one entry per call (the last
# entry repeats), which is how the log polling is exercised.
FAKE_GCLOUD = r'''
import hashlib, json, os, sys
scenario = json.loads(os.environ["WOUNDAI_FAKE_SCENARIO"])
key = " ".join(a for a in sys.argv[1:] if not a.startswith("--"))
matches = [p for p in scenario if key == p or key.startswith(p + " ")]
if not matches:
    sys.stderr.write("ERROR: fake gcloud has no reply for [%s]\n" % key)
    sys.exit(3)
prefix = max(matches, key=len)
reply = scenario[prefix]
if isinstance(reply, list):
    state = os.path.join(os.environ["WOUNDAI_FAKE_STATE"],
                         hashlib.sha1(prefix.encode("utf-8")).hexdigest())
    n = int(open(state).read()) if os.path.exists(state) else 0
    with open(state, "w") as f:
        f.write(str(n + 1))
    reply = reply[min(n, len(reply) - 1)]
# Bytes, not text: gcloud emits UTF-8 whatever the console code page is, and
# a text write would fail on a cp1252 Windows pipe before PowerShell saw it.
sys.stdout.buffer.write(reply.get("stdout", "").encode("utf-8"))
sys.stderr.buffer.write(reply.get("stderr", "").encode("utf-8"))
sys.stdout.flush()
sys.stderr.flush()
sys.exit(reply.get("exit", 0))
'''

HARNESS = r'''
$ErrorActionPreference = 'Continue'
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
    $env:WOUNDAI_DEMO_CHECK_SOURCE, [ref]$tokens, [ref]$errors)
if ($errors.Count) { Write-Output 'HARNESS syntax error'; exit 2 }
foreach ($name in @('Ok', 'Warn', 'Invoke-GCloudCaptured', 'ConvertFrom-GCloudJson',
                    'Get-ProductionSecretNames', 'Get-DemoSecretMap',
                    'Get-EngineeringVendorFiles', 'Copy-EngineeringVendor',
                    'Assert-NotTheProductionIdentity',
                    'Assert-DemoIdentityCannotReadProductionSecrets',
                    'Assert-DemoServiceState', 'Assert-DemoRevisionConfiguration',
                    'Assert-DemoSecretReady', 'Assert-DemoHealth', 'Assert-DemoSeedLogged')) {
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
$Service = 'woundai-backend-demo'
$ProductionService = 'woundai-backend'
$DemoSeedUser = 'demo01'
$DemoSeedRole = 'nurse'
$DemoSeedSecret = 'woundai-demo-password'
$DemoJwtSecret = 'woundai-demo-jwt-secret'
$DemoFlaskSecret = 'woundai-demo-flask-secret'
$DemoCareReceiptSecret = 'woundai-demo-care-receipt-secret'

$cases = Get-Content -Raw -LiteralPath $env:WOUNDAI_DEMO_CHECK_CASES -Encoding UTF8 | ConvertFrom-Json
foreach ($c in $cases) {
    $RuntimeServiceAccount = [string]$c.sa
    # Handed over as the exact JSON Python wrote. Re-serializing with
    # ConvertTo-Json is not safe on Windows PowerShell 5.1, which can wrap
    # arrays as {"value": [...], "Count": n}.
    $env:WOUNDAI_FAKE_SCENARIO = [string]$c.scenario_json
    $env:WOUNDAI_FAKE_STATE = Join-Path $env:WOUNDAI_DEMO_CHECK_STATE ([string]$c.id)
    New-Item -ItemType Directory -Force -Path $env:WOUNDAI_FAKE_STATE | Out-Null
    try {
        $detail = ''
        switch ([string]$c.fn) {
            'prod-identity' { $detail = [string](Assert-NotTheProductionIdentity) }
            'identity' { Assert-DemoIdentityCannotReadProductionSecrets | Out-Null }
            'service'  {
                $s = Assert-DemoServiceState -ExpectedRevision ([string]$c.expected_revision)
                $detail = "$($s.Revision) $($s.Url)"
            }
            'revision' {
                Assert-DemoRevisionConfiguration -Revision 'woundai-backend-demo-00001' `
                    -ExpectedGitCommit ([string]$c.commit) `
                    -ProductionIdentity ([string]$c.production_identity) | Out-Null
            }
            'ready'    { Assert-DemoSecretReady | Out-Null }
            'health'   {
                Assert-DemoHealth -Health $c.health -ExpectedGitCommit ([string]$c.commit) `
                    -ExpectedRevision 'woundai-backend-demo-00001' | Out-Null
            }
            'seedlog'  {
                Assert-DemoSeedLogged -Revision 'woundai-backend-demo-00001' `
                    -Attempts 3 -IntervalSec 0 | Out-Null
            }
            'vendor'   { Copy-EngineeringVendor -FlaskDir ([string]$c.flask) | Out-Null }
            default    { throw "unknown fn $($c.fn)" }
        }
        Write-Output ("CASE {0} PASS {1}" -f $c.id, $detail).TrimEnd()
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


def production_service(sa=PROD_SA, **reply):
    doc = {"spec": {"template": {"spec": {"serviceAccountName": sa}}}}
    out = {"stdout": json.dumps(doc)}
    out.update(reply)
    return {"run services describe woundai-backend": out}


def demo_service(created=REVISION, ready=REVISION, sa=DEMO_SA, traffic=None, url=URL):
    traffic = [{"revisionName": ready, "percent": 100, "latestRevision": True}] \
        if traffic is None else traffic
    doc = {"spec": {"template": {"spec": {"serviceAccountName": sa}}},
           "status": {"latestCreatedRevisionName": created,
                      "latestReadyRevisionName": ready,
                      "traffic": traffic, "url": url}}
    # Production is present too, so a lookup of the wrong service cannot
    # accidentally succeed on the demo's reply.
    s = production_service()
    s["run services describe woundai-backend-demo"] = {"stdout": json.dumps(doc)}
    return s


def revision(env=None, secrets=None, max_scale="1", sa=DEMO_SA, ready="True",
             containers=1, extra_env=None):
    env = {"WOUNDAI_STORE": "local", "WOUNDAI_ENABLE_LITE_API": "0",
           "WOUNDAI_DEMO_SEED_USER": "demo01", "WOUNDAI_DEMO_SEED_ROLE": "nurse",
           "GIT_COMMIT": COMMIT, "DEPLOYED_AT": "2026-09-24T00:00:00Z"} if env is None else env
    secrets = dict(DEMO_MAP) if secrets is None else secrets
    items = [{"name": k, "value": v} for k, v in env.items()]
    items += [{"name": k, "valueFrom": {"secretKeyRef": {"key": "latest", "name": v}}}
              for k, v in secrets.items()]
    items += list(extra_env or [])
    container = {"env": items}
    doc = {"metadata": {"annotations": {"autoscaling.knative.dev/maxScale": max_scale}},
           "spec": {"serviceAccountName": sa, "containers": [container] * containers},
           "status": {"conditions": [{"type": "Ready", "status": ready}]}}
    return {"run revisions describe": {"stdout": json.dumps(doc)}}


def ready(**overrides):
    s = {"secrets versions list %s" % n: {"stdout": "projects/p/secrets/%s/versions/1\n" % n}
         for n in DEMO_MAP.values()}
    s.update(overrides)
    return s


def health(**changes):
    doc = {
        "status": "healthy",
        "services": {"segmentation_model": True, "classify_modules": True,
                     "color_calibration": True, "endpoints_registered": True,
                     "canonicalization_golden": True, "lite_public_api_enabled": False,
                     "au_ensemble_files_present": True},
        "store": "local:/app/flywheel",
        "care_receipt": {"configured": True, "signing_kid": "demo1"},
        "build": {"service": SERVICE, "revision": REVISION, "git_commit": COMMIT,
                  "deployed_at": "2026-09-24T00:00:00Z", "on_cloud_run": True},
    }
    for path, value in changes.items():
        node = doc
        parts = path.split("__")
        for part in parts[:-1]:
            node = node[part]
        node[parts[-1]] = value
    return doc


LOG_KEY = ("logging read resource.type=cloud_run_revision AND "
           "resource.labels.service_name=woundai-backend-demo AND "
           "resource.labels.revision_name=" + REVISION)


def logs(*replies):
    return {LOG_KEY: [{"stdout": r} if isinstance(r, str) else r for r in replies]}


STARTUP = ("[2026-09-24 00:00:01 +0000] [1] [INFO] Starting gunicorn 23.0.0\n"
           "\n")
# The real line, Chinese and all. On Windows PowerShell 5.1 the fake's UTF-8
# output is decoded with the console code page, so the Chinese arrives as
# mojibake -- which is exactly why the gate matches only the ASCII marker.
OK_LINE = "[demo-seed:ok] 已重建送審測試帳號 default:demo01（角色 nurse）\n"

NOT_FOUND = {"exit": 1, "stderr": "ERROR: (gcloud.secrets.get-iam-policy) NOT_FOUND: "
                                  "Secret [projects/1/secrets/x] not found.\n"}
DENIED = {"exit": 1, "stderr": "ERROR: (gcloud.secrets.get-iam-policy) PERMISSION_DENIED: "
                               "Permission denied on resource.\n"}
EXPIRED = {"exit": 1, "stderr": "ERROR: (gcloud.run.services.describe) There was a problem "
                                "refreshing your current auth tokens: Reauthentication "
                                "failed.\n"}
RUN_NOT_FOUND = {"exit": 1, "stderr": "ERROR: (gcloud.run.services.describe) Cannot find "
                                      "service [woundai-backend]\n"}
RUN_DENIED = {"exit": 1, "stderr": "ERROR: (gcloud.run.services.describe) PERMISSION_DENIED: "
                                   "Permission 'run.services.get' denied\n"}

CASES = {
    # -- the production runtime identity must be known before anything else --
    # The first version warned and skipped the comparison when this lookup
    # failed; every failure below used to read as "different identity".
    "prod_identity_clean": ("prod-identity", DEMO_SA, production_service(), None),
    "prod_identity_same_as_demo": ("prod-identity", PROD_SA, production_service(),
        "must not run as the production runtime identity"),
    "prod_identity_same_ignoring_case": ("prod-identity", DEMO_SA,
        production_service(sa=DEMO_SA.upper()),
        "must not run as the production runtime identity"),
    "prod_identity_permission_denied": ("prod-identity", DEMO_SA,
        production_service(**RUN_DENIED), "cannot read production service [woundai-backend]"),
    "prod_identity_expired_login": ("prod-identity", DEMO_SA,
        production_service(**EXPIRED), "cannot read production service [woundai-backend]"),
    "prod_identity_service_not_found": ("prod-identity", DEMO_SA,
        production_service(**RUN_NOT_FOUND), "cannot read production service [woundai-backend]"),
    "prod_identity_blank": ("prod-identity", DEMO_SA, production_service(sa=""),
        "reports no runtime identity"),
    "prod_identity_not_json": ("prod-identity", DEMO_SA,
        {"run services describe woundai-backend": {"stdout": "woundai-runtime@x\n"}},
        "did not come back as JSON"),
    "prod_identity_stderr_noise_is_fine": ("prod-identity", DEMO_SA,
        production_service(stderr="WARNING: a newer gcloud is available.\n"), None),

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

    # -- the service is serving the revision this run built --
    "service_clean": ("service", DEMO_SA, demo_service(), None),
    "service_new_revision_not_ready": ("service", DEMO_SA,
        demo_service(created="woundai-backend-demo-00002"),
        "latest created revision [woundai-backend-demo-00002] is not its latest ready"),
    "service_serving_an_older_revision": ("service", DEMO_SA,
        demo_service(created="woundai-backend-demo-00000", ready="woundai-backend-demo-00000"),
        "not the revision this run deployed"),
    "service_traffic_split": ("service", DEMO_SA, demo_service(traffic=[
        {"revisionName": REVISION, "percent": 50},
        {"revisionName": "woundai-backend-demo-00000", "percent": 50}]),
        "demo traffic is not 100% on ready revision"),
    "service_traffic_on_old_revision": ("service", DEMO_SA, demo_service(traffic=[
        {"revisionName": "woundai-backend-demo-00000", "percent": 100}]),
        "demo traffic is not 100% on ready revision"),
    "service_template_runs_as_production": ("service", DEMO_SA, demo_service(sa=PROD_SA),
        "demo service template does not run as"),
    "service_without_url": ("service", DEMO_SA, demo_service(url=""),
        "has no https URL"),
    "service_unreadable": ("service", DEMO_SA, dict(production_service(), **{
        "run services describe woundai-backend-demo": {
            "exit": 1, "stderr": "ERROR: PERMISSION_DENIED\n"}}),
        "cannot read demo service"),

    # -- what the deployed revision actually runs as, carries and mounts --
    # Overrides are dict literals on purpose: the values are secret NAMES, but
    # a keyword argument of the form JWT_SECRET_KEY=<quoted name> is textually
    # a hardcoded-secret assignment and .gitleaks.toml rightly flags it. The
    # mapping is data (env var -> secret name), so it is written as data.
    "revision_clean": ("revision", DEMO_SA, revision(), None),
    "revision_runs_as_another_identity": ("revision", DEMO_SA, revision(sa=PROD_SA),
        "demo revision runs as [%s], expected" % PROD_SA),
    "revision_identity_is_production": ("revision", PROD_SA, revision(sa=PROD_SA),
        "runs as the production runtime identity"),
    "revision_not_ready": ("revision", DEMO_SA, revision(ready="False"), "is not Ready"),
    "revision_two_containers": ("revision", DEMO_SA, revision(containers=2),
        "exactly one container"),
    "revision_other_commit": ("revision", DEMO_SA, revision(env={
        "WOUNDAI_STORE": "local", "WOUNDAI_ENABLE_LITE_API": "0",
        "WOUNDAI_DEMO_SEED_USER": "demo01", "WOUNDAI_DEMO_SEED_ROLE": "nurse",
        "GIT_COMMIT": "f" * 40}), "[GIT_COMMIT]"),
    "revision_public_lite_api_on": ("revision", DEMO_SA, revision(env={
        "WOUNDAI_STORE": "local", "WOUNDAI_ENABLE_LITE_API": "1",
        "WOUNDAI_DEMO_SEED_USER": "demo01", "WOUNDAI_DEMO_SEED_ROLE": "nurse",
        "GIT_COMMIT": COMMIT}), "[WOUNDAI_ENABLE_LITE_API]"),
    "revision_seed_role_changed": ("revision", DEMO_SA, revision(env={
        "WOUNDAI_STORE": "local", "WOUNDAI_ENABLE_LITE_API": "0",
        "WOUNDAI_DEMO_SEED_USER": "demo01", "WOUNDAI_DEMO_SEED_ROLE": "doctor",
        "GIT_COMMIT": COMMIT}), "[WOUNDAI_DEMO_SEED_ROLE]"),
    "revision_duplicate_env": ("revision", DEMO_SA,
        revision(extra_env=[{"name": "WOUNDAI_STORE", "value": "gcs"}]),
        "duplicate environment key [WOUNDAI_STORE]"),
    "revision_mounts_prod_jwt": ("revision", DEMO_SA,
        revision(secrets={**DEMO_MAP, "JWT_SECRET_KEY": "woundai-jwt-secret"}),
        "mounts production secret [woundai-jwt-secret]"),
    "revision_mounts_prod_care": ("revision", DEMO_SA,
        revision(secrets={**DEMO_MAP, "CARE_RECEIPT_SECRET": "woundai-care-receipt-secret"}),
        "mounts production secret [woundai-care-receipt-secret]"),
    "revision_carries_admin_password": ("revision", DEMO_SA,
        revision(secrets={**DEMO_MAP, "ADMIN_PASSWORD": "woundai-demo-admin-password"}),
        "ADMIN_PASSWORD"),
    "revision_mounts_an_unexpected_secret": ("revision", DEMO_SA,
        revision(secrets={**DEMO_MAP, "WOUNDAI_GCS_BUCKET": "woundai-demo-bucket-name"}),
        "mounts an unexpected secret"),
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
    "revision_unreadable": ("revision", DEMO_SA, {"run revisions describe": {
        "exit": 1, "stderr": "ERROR: NOT_FOUND\n"}}, "cannot read demo revision"),

    # -- every demo secret must exist before deploying --
    "ready_all_present": ("ready", DEMO_SA, ready(), None),
    "ready_jwt_missing": ("ready", DEMO_SA, ready(**{
        "secrets versions list woundai-demo-jwt-secret": {
            "exit": 1, "stderr": "ERROR: NOT_FOUND\n"}}),
        "secret [woundai-demo-jwt-secret] not found"),
    "ready_care_no_enabled_version": ("ready", DEMO_SA, ready(**{
        "secrets versions list woundai-demo-care-receipt-secret": {"stdout": ""}}),
        "[woundai-demo-care-receipt-secret] has no ENABLED version"),

    # -- the running service can actually measure, and is the code we built --
    "health_clean": ("health", DEMO_SA, health(), None),
    "health_degraded": ("health", DEMO_SA, health(status="degraded",
        degraded_reason="classify modules missing"), "status=[degraded]"),
    "health_no_segmentation_model": ("health", DEMO_SA,
        health(services__segmentation_model=False), "services.segmentation_model"),
    "health_no_classify_modules": ("health", DEMO_SA,
        health(services__classify_modules=False), "services.classify_modules"),
    "health_no_color_calibration": ("health", DEMO_SA,
        health(services__color_calibration=False), "services.color_calibration"),
    "health_endpoint_missing": ("health", DEMO_SA,
        health(services__endpoints_registered=False), "services.endpoints_registered"),
    "health_golden_mismatch": ("health", DEMO_SA,
        health(services__canonicalization_golden=False), "services.canonicalization_golden"),
    "health_truthy_string_is_not_true": ("health", DEMO_SA,
        health(services__classify_modules="true"), "services.classify_modules"),
    "health_public_lite_api_on": ("health", DEMO_SA,
        health(services__lite_public_api_enabled=True), "public lite API"),
    "health_on_gcs": ("health", DEMO_SA, health(store="gcs://woundai-flywheel-jackh001/flywheel"),
        "not local storage"),
    "health_care_missing": ("health", DEMO_SA, health(care_receipt__configured=False),
        "care receipt keyring is not configured"),
    "health_other_commit": ("health", DEMO_SA, health(build__git_commit="f" * 40),
        "build.git_commit"),
    "health_short_commit_is_not_enough": ("health", DEMO_SA,
        health(build__git_commit=COMMIT[:7]), "build.git_commit"),
    "health_commit_only_at_top_level": ("health", DEMO_SA,
        dict(health(build__git_commit=None), git_commit=COMMIT), "build.git_commit"),
    "health_other_revision": ("health", DEMO_SA,
        health(build__revision="woundai-backend-demo-00000"), "build.revision"),
    "health_other_service": ("health", DEMO_SA,
        health(build__service="woundai-backend"), "build.service"),
    "health_reports_every_failure_at_once": ("health", DEMO_SA,
        health(status="degraded", services__classify_modules=False,
               build__git_commit="f" * 40), "services.classify_modules"),

    # -- the seed ran on THIS revision --
    "seedlog_ok": ("seedlog", DEMO_SA, logs(STARTUP + OK_LINE), None),
    "seedlog_ok_after_ingestion_delay": ("seedlog", DEMO_SA,
        logs(STARTUP, STARTUP, STARTUP + OK_LINE), None),
    "seedlog_never_appears": ("seedlog", DEMO_SA, logs(STARTUP),
        "no [demo-seed:ok] line from revision"),
    "seedlog_refused": ("seedlog", DEMO_SA,
        logs(STARTUP + "[demo-seed:refused] ⚠ demo 種子未執行：角色 doctor 不在白名單\n"),
        "refused or failed"),
    "seedlog_error_after_ok": ("seedlog", DEMO_SA,
        logs(STARTUP + OK_LINE + "[demo-seed:error] boom\n"), "refused or failed"),
    "seedlog_exists_alone_is_not_ok": ("seedlog", DEMO_SA,
        logs(STARTUP + "[demo-seed:exists] already there\n"),
        "no [demo-seed:ok] line from revision"),
    "seedlog_exists_after_ok_is_fine": ("seedlog", DEMO_SA,
        logs(STARTUP + OK_LINE + "[demo-seed:exists] worker restart\n"), None),
    "seedlog_crlf_output": ("seedlog", DEMO_SA,
        logs((STARTUP + OK_LINE).replace("\n", "\r\n")), None),
    "seedlog_unreadable": ("seedlog", DEMO_SA,
        {LOG_KEY: {"exit": 1, "stderr": "ERROR: PERMISSION_DENIED\n"}},
        "cannot read the logs of demo revision"),
    # A success line from another revision must not count. The fake answers
    # only the exact revision filter, so a query for anything wider fails.
    "seedlog_other_revision_does_not_count": ("seedlog", DEMO_SA, {
        LOG_KEY.replace(REVISION, "woundai-backend-demo-00000"): [{"stdout": OK_LINE}]},
        "cannot read the logs of demo revision"),
    "seedlog_marker_is_case_sensitive": ("seedlog", DEMO_SA,
        logs(STARTUP + "[DEMO-SEED:OK] shouting\n"), "no [demo-seed:ok] line"),
}


def vendor_layout(root: Path, name: str, skip=(), stale=None):
    """A repo-shaped tree: <name>/Backend/Flask and <name>/engineering/..."""
    base = root / name
    flask = base / "Backend" / "Flask"
    flask.mkdir(parents=True)
    for rel in VENDOR_FILES:
        if rel in skip:
            continue
        src = base / "engineering" / rel
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(("# %s synthetic\n" % rel).encode("utf-8"))
    if stale is not None:
        (flask / "vendor").mkdir()
        for fname, body in stale.items():
            (flask / "vendor" / fname).write_bytes(body)
    return flask


@unittest.skipUnless(SHELLS, "PowerShell is required for demo deploy check tests")
class DemoDeployChecksAgainstFakeGcloud(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="woundai-demo-checks-")
        tmp = Path(cls.tmp)
        fake = tmp / "fake_gcloud.py"
        fake.write_text(FAKE_GCLOUD, encoding="utf-8")
        harness = tmp / "harness.ps1"
        harness.write_text(HARNESS, encoding="utf-8-sig")

        cases = dict(CASES)
        cls.layouts = {}
        cls.results = {}
        for shell_index, shell in enumerate(SHELLS):
            root = tmp / ("vendor-%d" % shell_index)
            layouts = {
                "vendor_copies_every_module": vendor_layout(root, "complete"),
                "vendor_replaces_a_stale_copy": vendor_layout(
                    root, "stale", stale={"color_calib.py": b"# stale\n",
                                          "left_over.py": b"# gone\n"}),
                "vendor_missing_color_calib_refuses": vendor_layout(
                    root, "missing", skip=("phase2/color_calib.py",),
                    stale={"keep.py": b"# untouched\n"}),
            }
            cls.layouts[shell] = layouts
            shell_cases = dict(cases)
            shell_cases["vendor_copies_every_module"] = ("vendor", DEMO_SA, {}, None)
            shell_cases["vendor_replaces_a_stale_copy"] = ("vendor", DEMO_SA, {}, None)
            shell_cases["vendor_missing_color_calib_refuses"] = (
                "vendor", DEMO_SA, {}, "phase2\\color_calib.py")
            payload = []
            for cid, (fn, sa, scenario, _) in shell_cases.items():
                item = {"id": cid, "fn": fn, "sa": sa, "scenario_json": json.dumps(scenario),
                        "commit": COMMIT, "production_identity": PROD_SA,
                        "expected_revision": REVISION}
                if fn == "health":
                    item["health"] = scenario
                    item["scenario_json"] = "{}"
                if fn == "vendor":
                    item["flask"] = str(layouts[cid])
                payload.append(item)
            case_file = tmp / ("cases-%d.json" % shell_index)
            case_file.write_text(json.dumps(payload), encoding="utf-8")
            state = tmp / ("state-%d" % shell_index)
            state.mkdir()
            env = os.environ.copy()
            env.update({"WOUNDAI_DEMO_CHECK_SOURCE": str(DEMO),
                        "WOUNDAI_DEMO_CHECK_CASES": str(case_file),
                        "WOUNDAI_DEMO_CHECK_STATE": str(state),
                        "WOUNDAI_FAKE_PYTHON": sys.executable,
                        "WOUNDAI_FAKE_GCLOUD": str(fake)})
            proc = subprocess.run(
                [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-File", str(harness)], env=env, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=300)
            seen = {}
            for line in proc.stdout.splitlines():
                if line.startswith("CASE "):
                    parts = line.split(" ", 3)
                    seen[parts[1]] = " ".join(parts[2:])
            cls.results[shell] = (proc, seen, shell_cases)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_every_case_ran_in_every_shell(self):
        for shell, (proc, seen, cases) in self.results.items():
            with self.subTest(shell=shell):
                self.assertEqual(sorted(seen), sorted(cases),
                                 proc.stdout[-2000:] + proc.stderr[-2000:])

    def test_each_check_accepts_or_refuses_as_specified(self):
        for shell, (proc, seen, cases) in self.results.items():
            for cid, (_fn, _sa, _scenario, expected) in cases.items():
                with self.subTest(shell=shell, case=cid):
                    got = seen.get(cid, "<missing>")
                    if expected is None:
                        self.assertTrue(got == "PASS" or got.startswith("PASS "), got)
                    else:
                        self.assertTrue(got.startswith("REJECT "), got)
                        self.assertIn(expected, got)

    def test_the_production_identity_is_returned_for_the_read_back(self):
        for shell, (_proc, seen, _cases) in self.results.items():
            with self.subTest(shell=shell):
                self.assertEqual(seen.get("prod_identity_clean"), "PASS " + PROD_SA)

    def test_the_service_check_returns_what_it_verified(self):
        for shell, (_proc, seen, _cases) in self.results.items():
            with self.subTest(shell=shell):
                self.assertEqual(seen.get("service_clean"), "PASS %s %s" % (REVISION, URL))

    def test_a_health_failure_lists_every_problem(self):
        # One throw carrying all failures: an operator fixing them one deploy
        # at a time would redeploy three times for this response.
        for shell, (_proc, seen, _cases) in self.results.items():
            with self.subTest(shell=shell):
                got = seen.get("health_reports_every_failure_at_once", "")
                for part in ("status=[degraded]", "services.classify_modules",
                             "build.git_commit"):
                    self.assertIn(part, got)

    def test_the_vendor_copy_is_exact(self):
        for shell, layouts in self.layouts.items():
            for cid in ("vendor_copies_every_module", "vendor_replaces_a_stale_copy"):
                with self.subTest(shell=shell, case=cid):
                    flask = layouts[cid]
                    vendor = flask / "vendor"
                    self.assertEqual(sorted(p.name for p in vendor.iterdir()),
                                     sorted(Path(r).name for r in VENDOR_FILES))
                    for rel in VENDOR_FILES:
                        src = flask.parent.parent / "engineering" / rel
                        self.assertEqual(
                            hashlib.sha256((vendor / Path(rel).name).read_bytes()).hexdigest(),
                            hashlib.sha256(src.read_bytes()).hexdigest(), rel)

    def test_a_refused_vendor_copy_leaves_the_existing_vendor_alone(self):
        # The source check runs before vendor/ is touched, so a failed deploy
        # does not also destroy the last good copy.
        for shell, layouts in self.layouts.items():
            with self.subTest(shell=shell):
                vendor = layouts["vendor_missing_color_calib_refuses"] / "vendor"
                self.assertEqual(sorted(p.name for p in vendor.iterdir()), ["keep.py"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
