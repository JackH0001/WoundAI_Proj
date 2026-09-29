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

# The key is every argument that is not a --flag, joined by spaces, plus any
# --permission= flag (the Policy Troubleshooter is asked several questions about
# one resource, and each needs its own answer). A scenario entry answers the
# longest key it is a whole-word prefix of, so
# "run services describe woundai-backend" never answers a describe of
# woundai-backend-demo. A list reply is consumed one entry per call (the last
# entry repeats), which is how the log polling is exercised.
FAKE_GCLOUD = r'''
import hashlib, json, os, sys
scenario = json.loads(os.environ["WOUNDAI_FAKE_SCENARIO"])
key = " ".join(a for a in sys.argv[1:]
               if not a.startswith("--") or a.startswith("--permission="))
with open(os.path.join(os.environ["WOUNDAI_FAKE_STATE"], "calls.jsonl"), "a") as log:
    log.write(json.dumps(sys.argv[1:]) + "\n")
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
                    'Get-PrincipalReach', 'Assert-DemoIdentityHoldsNoProjectRole',
                    'Get-ServiceAccountsInPolicy', 'Get-GoogleServiceAgentDomains',
                    'Test-ThisProjectServiceAgent',
                    'Get-DemoGuardedServiceAccounts', 'Get-DemoEscalationChecks',
                    'Assert-DemoIdentityEffectivelyDenied',
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
            'projroles' { Assert-DemoIdentityHoldsNoProjectRole | Out-Null }
            'effective' {
                Assert-DemoIdentityEffectivelyDenied `
                    -ProductionIdentity ([string]$c.production_identity) | Out-Null
            }
            'effective-no-production' {
                Assert-DemoIdentityEffectivelyDenied -ProductionIdentity '' | Out-Null
            }
            'identity-all' {
                # The three pre-deploy identity checks in the order the script runs them.
                Assert-DemoIdentityCannotReadProductionSecrets | Out-Null
                Assert-DemoIdentityHoldsNoProjectRole | Out-Null
                Assert-DemoIdentityEffectivelyDenied `
                    -ProductionIdentity ([string]$c.production_identity) | Out-Null
            }
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


def iam_policy(bindings):
    """bindings: (role, [members], condition-or-None)."""
    return {"bindings": [dict({"role": r, "members": list(m)}, **({"condition": c} if c else {}))
                         for r, m, c in bindings], "etag": "BwY", "version": 3}


ANCESTORS_KEY = "projects get-ancestors-iam-policy woundai-jackh001"


def project_policy(*bindings, ancestors=()):
    """The project's own policy, then any (type, id, bindings) above it, as
    gcloud projects get-ancestors-iam-policy lists them."""
    entries = [{"id": "woundai-jackh001", "type": "project", "policy": iam_policy(bindings)}]
    entries += [{"id": i, "type": t, "policy": iam_policy(b)} for t, i, b in ancestors]
    return {ANCESTORS_KEY: {"stdout": json.dumps(entries)}}


COMPUTE_SA = "421209514056-compute@developer.gserviceaccount.com"
PROJECT_NUMBER = "421209514056"
# The service-agent domains the script knows (Get-GoogleServiceAgentDomains): the
# ones the project's own policy named on 2026-09-27, each checked in Google's
# documentation. Matched whole, for this project's number only.
AGENT_DOMAINS = ["containerregistry.iam.gserviceaccount.com",
                 "gcp-sa-artifactregistry.iam.gserviceaccount.com",
                 "gcp-sa-cloudbuild.iam.gserviceaccount.com",
                 "gcp-sa-cloudscheduler.iam.gserviceaccount.com",
                 "gcp-sa-pubsub.iam.gserviceaccount.com",
                 "serverless-robot-prod.iam.gserviceaccount.com"]
LISTED_AGENTS = ["service-%s@%s" % (PROJECT_NUMBER, d) for d in AGENT_DOMAINS]
API_AGENT = "%s@cloudservices.gserviceaccount.com" % PROJECT_NUMBER
LEGACY_BUILD = "%s@cloudbuild.gserviceaccount.com" % PROJECT_NUMBER
# This project's Google accounts, as they appear in a real project policy.
# None of them lives in a customer project, so none can be granted to anyone.
AGENTS = ["service-%s@serverless-robot-prod.iam.gserviceaccount.com" % PROJECT_NUMBER,
          API_AGENT, LEGACY_BUILD,
          "service-%s@gcp-sa-cloudbuild.iam.gserviceaccount.com" % PROJECT_NUMBER]
EXT_SA = "ci-runner@other-project-123.iam.gserviceaccount.com"

CLEAN_PROJECT = (("roles/owner", ["user:jack.hou@gmail.com"], None),
                 ("roles/run.invoker", ["serviceAccount:" + PROD_SA], None),
                 ("roles/logging.logWriter", ["serviceAccount:" + PROD_SA], None),
                 ("roles/editor", ["serviceAccount:" + COMPUTE_SA,
                                   "serviceAccount:" + AGENTS[1]], None),
                 ("roles/run.serviceAgent", ["serviceAccount:" + AGENTS[0]], None),
                 ("roles/cloudbuild.builds.builder", ["serviceAccount:" + AGENTS[2]], None),
                 ("roles/cloudbuild.serviceAgent", ["serviceAccount:" + AGENTS[3]], None))


LISTED_SAS = [PROD_SA, DEMO_SA, COMPUTE_SA]
SA_PERMISSIONS = ["iam.serviceAccounts.actAs", "iam.serviceAccounts.getAccessToken",
                  "iam.serviceAccounts.getOpenIdToken", "iam.serviceAccounts.signBlob",
                  "iam.serviceAccounts.signJwt", "iam.serviceAccounts.implicitDelegation",
                  "iam.serviceAccountKeys.create", "iam.serviceAccounts.setIamPolicy"]
PROJECT_RESOURCE = "//cloudresourcemanager.googleapis.com/projects/woundai-jackh001"


def secret_resource(name):
    return "//secretmanager.googleapis.com/projects/woundai-jackh001/secrets/" + name


def sa_resource(email):
    return "//iam.googleapis.com/projects/-/serviceAccounts/" + email


def named_accounts(bindings):
    return [m[len("serviceAccount:"):].lower() for _role, members, _cond in bindings
            for m in members if m.startswith("serviceAccount:")]


def is_this_projects_agent(email):
    return email in LISTED_AGENTS + [API_AGENT, LEGACY_BUILD]


def guarded(listed=None, named=(), production=PROD_SA):
    """Listed accounts, the production identity and every account a protecting
    policy names -- except this project's service agents and the demo identity."""
    listed = [e.lower() for e in (LISTED_SAS if listed is None else listed)]
    extra = [e for e in named if e not in listed and not is_this_projects_agent(e)]
    return sorted(set(listed + [production] + extra) - {DEMO_SA})


def escalation_matrix(accounts=None, secrets=PRODUCTION):
    """(resource, permission) for every question the demo identity must fail."""
    accounts = guarded(named=named_accounts(CLEAN_PROJECT)) if accounts is None else accounts
    rows = []
    for name in secrets:
        rows += [(secret_resource(name), "secretmanager.versions.access"),
                 (secret_resource(name), "secretmanager.secrets.setIamPolicy")]
    rows.append((PROJECT_RESOURCE, "resourcemanager.projects.setIamPolicy"))
    for email in accounts:
        rows += [(sa_resource(email), p) for p in SA_PERMISSIONS]
    return rows


def troubleshoot(states=None, listed=None, project_bindings=CLEAN_PROJECT, ancestors=(),
                 secret_members=None, missing=(), production=PROD_SA, **overrides):
    """Everything the identity checks read, kept consistent with each other: the
    project and ancestor policies, each production secret's policy (NOT_FOUND for
    the missing ones), the project number, the service-account listing, and one
    Policy Troubleshooter answer per (resource, permission) the effective check
    will ask -- and none for anything it must not ask, so asking one fails the
    run. states maps (resource, permission) to a verdict string or a whole reply."""
    states = states or {}
    listed_now = LISTED_SAS if listed is None else listed
    secrets = {n: ["serviceAccount:" + production] for n in PRODUCTION}
    secrets.update(secret_members or {})
    for name in missing:
        secrets[name] = []
    s = dict(project_policy(*project_bindings, ancestors=ancestors))
    for name in PRODUCTION:
        if name in missing:
            s["secrets get-iam-policy " + name] = NOT_FOUND
        elif secrets[name]:
            s["secrets get-iam-policy " + name] = policy(*secrets[name])
        else:
            s["secrets get-iam-policy " + name] = {"stdout": json.dumps({"etag": "BwX"})}
    s["projects describe woundai-jackh001"] = {"stdout": json.dumps(
        {"projectId": "woundai-jackh001", "projectNumber": PROJECT_NUMBER})}
    s["iam service-accounts list"] = {"stdout": json.dumps(
        [{"email": e, "name": "projects/woundai-jackh001/serviceAccounts/" + e}
         for e in listed_now])}
    named = named_accounts(list(project_bindings) + [b for _t, _i, bs in ancestors for b in bs])
    named += [m[len("serviceAccount:"):].lower() for members in secrets.values()
              for m in members if m.startswith("serviceAccount:")]
    present = [n for n in PRODUCTION if n not in missing]
    for resource, permission in escalation_matrix(guarded(listed_now, named, production), present):
        state = states.get((resource, permission), "CANNOT_ACCESS")
        key = "policy-intelligence troubleshoot-policy iam %s --permission=%s" % (resource, permission)
        s[key] = state if isinstance(state, dict) else {"stdout": json.dumps({
            "accessTuple": {"principal": DEMO_SA, "permission": permission},
            "overallAccessState": state})}
    s.update(overrides)
    return s


def verdicts(by_label):
    """Readable overrides: secret name, 'project' or an account email, then
    '::permission'."""
    out = {}
    for label, state in by_label.items():
        target, permission = label.split("::")
        if target == "project":
            resource = PROJECT_RESOURCE
        elif "@" in target:
            resource = sa_resource(target)
        else:
            resource = secret_resource(target)
        out[(resource, permission)] = state
    return out


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

# The project as the read-only inventory of 2026-09-27 found it. Production runs
# as the Compute Engine default account, which holds Editor and reads two of the
# secrets directly; six Google service agents and the legacy Cloud Build account
# hold their service roles; woundai-care-receipt-secret does not exist yet. The
# demo identity is listed because it has to exist before anything is deployed.
REAL_PROJECT = (("roles/artifactregistry.serviceAgent", ["serviceAccount:" + LISTED_AGENTS[1]], None),
                ("roles/cloudbuild.builds.builder", ["serviceAccount:" + LEGACY_BUILD], None),
                ("roles/cloudbuild.serviceAgent", ["serviceAccount:" + LISTED_AGENTS[2]], None),
                ("roles/cloudscheduler.serviceAgent", ["serviceAccount:" + LISTED_AGENTS[3]], None),
                ("roles/containerregistry.ServiceAgent", ["serviceAccount:" + LISTED_AGENTS[0]], None),
                ("roles/editor", ["serviceAccount:" + COMPUTE_SA], None),
                ("roles/owner", ["user:jack.hou@gmail.com"], None),
                ("roles/pubsub.serviceAgent", ["serviceAccount:" + LISTED_AGENTS[4]], None),
                ("roles/run.serviceAgent", ["serviceAccount:" + LISTED_AGENTS[5]], None))
REAL_MISSING = ("woundai-care-receipt-secret",)


def real_project(states=None):
    return troubleshoot(states, listed=[COMPUTE_SA, DEMO_SA], project_bindings=REAL_PROJECT,
                        secret_members={"woundai-admin-password": ["serviceAccount:" + COMPUTE_SA],
                                        "woundai-jwt-secret": ["serviceAccount:" + COMPUTE_SA],
                                        "woundai-flask-secret": []},
                        missing=REAL_MISSING, production=COMPUTE_SA)


# Cases whose production service runs as someone other than PROD_SA.
PRODUCTION_IDENTITY = {"effective_real_project": COMPUTE_SA,
                       "effective_real_project_compute_default_reachable": COMPUTE_SA,
                       "identity_all_real_project": COMPUTE_SA}

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
            policy("serviceAccount:x" + DEMO_SA, "user:someone@example.com",
                   "deleted:serviceAccount:%s?uid=1" % DEMO_SA)}), None),
    "identity_email_case_does_not_hide_it": ("identity", DEMO_SA, clean_identity(**{
        "secrets get-iam-policy woundai-jwt-secret": policy("serviceAccount:" + DEMO_SA.upper())}),
        "holds [roles/secretmanager.secretAccessor] on production secret [woundai-jwt-secret]"),
    # Who can read a production secret must be nameable: the effective check
    # asks, for each such account, whether the demo identity can use it. A
    # group's members cannot be listed from here (review of ea82c57).
    "identity_group_on_a_secret": ("identity", DEMO_SA, clean_identity(**{
        "secrets get-iam-policy woundai-flask-secret":
            policy("serviceAccount:" + PROD_SA, "group:ops@example.com")}),
        "production secret [woundai-flask-secret] is granted to principals whose members cannot be listed"),
    "identity_all_users_on_a_secret": ("identity", DEMO_SA, clean_identity(**{
        "secrets get-iam-policy woundai-admin-password": policy("allAuthenticatedUsers")}),
        "<- allAuthenticatedUsers"),
    "identity_no_bindings": ("identity", DEMO_SA, clean_identity(**{
        "secrets get-iam-policy woundai-jwt-secret": {"stdout": '{"etag": "ACAB"}'}}), None),

    # -- no role on the project or above it, by any route --
    # Every role there is inherited by every secret, and a principal that might
    # contain the demo identity is refused because membership cannot be proven
    # from here (review of 086c406, 2026-09-27).
    "projroles_clean": ("projroles", DEMO_SA, project_policy(*CLEAN_PROJECT), None),
    "projroles_viewer": ("projroles", DEMO_SA, project_policy(
        *CLEAN_PROJECT, ("roles/viewer", ["serviceAccount:" + DEMO_SA], None)),
        "holds roles on the project or above it [project/woundai-jackh001 roles/viewer]"),
    "projroles_secret_accessor": ("projroles", DEMO_SA, project_policy(
        *CLEAN_PROJECT, ("roles/secretmanager.secretAccessor",
                         ["user:jack.hou@gmail.com", "serviceAccount:" + DEMO_SA], None)),
        "roles/secretmanager.secretAccessor"),
    "projroles_conditional_binding_still_counts": ("projroles", DEMO_SA, project_policy(
        *CLEAN_PROJECT, ("roles/editor", ["serviceAccount:" + DEMO_SA],
                         {"title": "until review", "expression": "request.time < timestamp('2026-12-31T00:00:00Z')"})),
        "project/woundai-jackh001 roles/editor"),
    "projroles_email_case_does_not_hide_it": ("projroles", DEMO_SA, project_policy(
        *CLEAN_PROJECT, ("roles/viewer", ["serviceAccount:" + DEMO_SA.upper()], None)),
        "holds roles on the project or above it"),
    "projroles_written_as_a_user_is_still_it": ("projroles", DEMO_SA, project_policy(
        *CLEAN_PROJECT, ("roles/viewer", ["user:" + DEMO_SA], None)),
        "holds roles on the project or above it"),
    "projroles_lookalikes_and_deleted_are_not_it": ("projroles", DEMO_SA, project_policy(
        *CLEAN_PROJECT, ("roles/viewer", ["serviceAccount:x" + DEMO_SA,
                                          "deleted:serviceAccount:%s?uid=123456789" % DEMO_SA,
                                          "user:someone@example.com"], None)),
        None),
    # The review's counterexample: a group grants projectIamAdmin. The role
    # reads no secret, so every secretmanager.versions.access answer is
    # CANNOT_ACCESS -- but the holder can grant itself secretAccessor.
    "projroles_group_with_project_iam_admin": ("projroles", DEMO_SA, project_policy(
        *CLEAN_PROJECT, ("roles/resourcemanager.projectIamAdmin",
                         ["group:woundai-ops@googlegroups.com"], None)),
        "cannot prove the demo identity is outside these principals"),
    "projroles_any_group_whatever_the_role": ("projroles", DEMO_SA, project_policy(
        *CLEAN_PROJECT, ("roles/viewer", ["group:readers@example.com"], None)),
        "project/woundai-jackh001 roles/viewer <- group:readers@example.com"),
    "projroles_domain": ("projroles", DEMO_SA, project_policy(
        *CLEAN_PROJECT, ("roles/viewer", ["domain:example.com"], None)),
        "<- domain:example.com"),
    "projroles_all_authenticated_users": ("projroles", DEMO_SA, project_policy(
        *CLEAN_PROJECT, ("roles/viewer", ["allAuthenticatedUsers"], None)),
        "<- allAuthenticatedUsers"),
    "projroles_all_users": ("projroles", DEMO_SA, project_policy(
        *CLEAN_PROJECT, ("roles/viewer", ["allUsers"], None)),
        "<- allUsers"),
    "projroles_principal_set": ("projroles", DEMO_SA, project_policy(
        *CLEAN_PROJECT, ("roles/editor", [
            "principalSet://iam.googleapis.com/projects/421209514056/locations/global/"
            "workloadIdentityPools/ci/*"], None)),
        "<- principalSet://"),
    "projroles_principal": ("projroles", DEMO_SA, project_policy(
        *CLEAN_PROJECT, ("roles/editor", [
            "principal://iam.googleapis.com/projects/421209514056/locations/global/"
            "workloadIdentityPools/ci/subject/x"], None)),
        "<- principal://"),
    "projroles_unfamiliar_principal_form": ("projroles", DEMO_SA, project_policy(
        *CLEAN_PROJECT, ("roles/viewer", ["projectOwner:woundai-jackh001"], None)),
        "<- projectOwner:woundai-jackh001"),
    "projroles_folder_grants_it": ("projroles", DEMO_SA, project_policy(
        *CLEAN_PROJECT, ancestors=[("folder", "123456789012", [
            ("roles/editor", ["serviceAccount:" + DEMO_SA], None)])]),
        "folder/123456789012 roles/editor"),
    "projroles_organization_group": ("projroles", DEMO_SA, project_policy(
        *CLEAN_PROJECT, ancestors=[("organization", "987654321098", [
            ("roles/owner", ["group:admins@example.com"], None)])]),
        "organization/987654321098 roles/owner <- group:admins@example.com"),
    "projroles_clean_ancestors": ("projroles", DEMO_SA, project_policy(
        *CLEAN_PROJECT, ancestors=[("organization", "987654321098", [
            ("roles/owner", ["user:jack.hou@gmail.com"], None)])]), None),
    "projroles_listing_without_the_project": ("projroles", DEMO_SA, {ANCESTORS_KEY: {
        "stdout": json.dumps([{"id": "987654321098", "type": "organization",
                               "policy": iam_policy(())}])}},
        "did not include project [woundai-jackh001] itself"),
    "projroles_entry_without_a_policy": ("projroles", DEMO_SA, {ANCESTORS_KEY: {
        "stdout": json.dumps([{"id": "woundai-jackh001", "type": "project"}])}},
        "has no policy for [project/woundai-jackh001]"),
    "projroles_empty_listing": ("projroles", DEMO_SA, {ANCESTORS_KEY: {"stdout": "[]"}},
        "project [woundai-jackh001]"),
    "projroles_not_json": ("projroles", DEMO_SA, {ANCESTORS_KEY: {"stdout": "bindings: []\n"}},
        "did not come back as JSON"),
    "projroles_unreadable": ("projroles", DEMO_SA, {ANCESTORS_KEY: {
        "exit": 1, "stderr": "ERROR: (gcloud.projects.get-ancestors-iam-policy) PERMISSION_DENIED\n"}},
        "cannot read IAM policies of project [woundai-jackh001] and its ancestors"),

    # -- effective access, as the Policy Troubleshooter evaluates it --
    # Not only "can it read a production secret now" but every way it could
    # get one: re-grant the secret, change project IAM, use another account.
    "effective_all_denied": ("effective", DEMO_SA, troubleshoot(), None),
    "effective_reads_a_secret": ("effective", DEMO_SA, troubleshoot(verdicts({
        "woundai-jwt-secret::secretmanager.versions.access": "CAN_ACCESS"})),
        "is not provably unable to read production secret [woundai-jwt-secret]: [CAN_ACCESS]"),
    "effective_can_grant_itself_a_secret": ("effective", DEMO_SA, troubleshoot(verdicts({
        "woundai-admin-password::secretmanager.secrets.setIamPolicy": "CAN_ACCESS"})),
        "unable to grant itself production secret [woundai-admin-password]: [CAN_ACCESS]"),
    "effective_can_change_project_iam": ("effective", DEMO_SA, troubleshoot(verdicts({
        "project::resourcemanager.projects.setIamPolicy": "CAN_ACCESS"})),
        "unable to change the IAM policy of project [woundai-jackh001]: [CAN_ACCESS]"),
    # With a group the troubleshooter cannot see into, the answer is unknown.
    "effective_project_iam_through_an_unreadable_group": ("effective", DEMO_SA, troubleshoot(verdicts({
        "project::resourcemanager.projects.setIamPolicy": "UNKNOWN_INFO"})),
        "change the IAM policy of project [woundai-jackh001]: [UNKNOWN_INFO]"),
    "effective_can_mint_tokens_for_production": ("effective", DEMO_SA, troubleshoot(verdicts({
        PROD_SA + "::iam.serviceAccounts.getAccessToken": "CAN_ACCESS"})),
        "use service account [%s] (iam.serviceAccounts.getAccessToken): [CAN_ACCESS]" % PROD_SA),
    "effective_can_create_keys_for_production": ("effective", DEMO_SA, troubleshoot(verdicts({
        PROD_SA + "::iam.serviceAccountKeys.create": "CAN_ACCESS"})),
        "(iam.serviceAccountKeys.create): [CAN_ACCESS]"),
    "effective_can_act_as_the_compute_default": ("effective", DEMO_SA, troubleshoot(verdicts({
        COMPUTE_SA + "::iam.serviceAccounts.actAs": "CAN_ACCESS"})),
        "use service account [%s] (iam.serviceAccounts.actAs)" % COMPUTE_SA),
    "effective_production_identity_checked_even_if_unlisted": ("effective", DEMO_SA, troubleshoot(
        verdicts({PROD_SA + "::iam.serviceAccounts.signJwt": "CAN_ACCESS"}),
        listed=[DEMO_SA, COMPUTE_SA]),
        "use service account [%s] (iam.serviceAccounts.signJwt)" % PROD_SA),
    # Named in a protecting policy, from another project: asked like any other.
    "effective_external_reader_is_asked": ("effective", DEMO_SA, troubleshoot(verdicts({
        EXT_SA + "::iam.serviceAccounts.getAccessToken": "CAN_ACCESS"}),
        project_bindings=CLEAN_PROJECT + (("roles/secretmanager.secretAccessor",
                                           ["serviceAccount:" + EXT_SA], None),)),
        "use service account [%s] (iam.serviceAccounts.getAccessToken): [CAN_ACCESS]" % EXT_SA),
    "effective_external_reader_that_cannot_be_evaluated": ("effective", DEMO_SA, troubleshoot(
        verdicts({EXT_SA + "::iam.serviceAccounts.actAs": "UNKNOWN_INFO"}),
        secret_members={"woundai-jwt-secret": ["serviceAccount:" + PROD_SA, "serviceAccount:" + EXT_SA]}),
        "use service account [%s] (iam.serviceAccounts.actAs): [UNKNOWN_INFO]" % EXT_SA),
    "effective_folder_named_account_is_asked": ("effective", DEMO_SA, troubleshoot(
        verdicts({EXT_SA + "::iam.serviceAccounts.signBlob": "CAN_ACCESS"}),
        ancestors=[("folder", "123456789012", [("roles/viewer", ["serviceAccount:" + EXT_SA], None)])]),
        "use service account [%s] (iam.serviceAccounts.signBlob)" % EXT_SA),
    # Another project's service agent is not ours to vouch for: asked, and here
    # the troubleshooter cannot see its policy.
    "effective_another_projects_agent_is_asked": ("effective", DEMO_SA, troubleshoot(
        verdicts({"service-999999999999@gcp-sa-cloudbuild.iam.gserviceaccount.com"
                  "::iam.serviceAccounts.actAs": "UNKNOWN_INFO"}),
        project_bindings=CLEAN_PROJECT + (("roles/run.admin", [
            "serviceAccount:service-999999999999@gcp-sa-cloudbuild.iam.gserviceaccount.com"], None),)),
        "service-999999999999@gcp-sa-cloudbuild.iam.gserviceaccount.com] (iam.serviceAccounts.actAs): [UNKNOWN_INFO]"),
    # Created in this project with an agent's name: a customer account, asked.
    "effective_lookalike_agent_in_this_project_is_asked": ("effective", DEMO_SA, troubleshoot(
        verdicts({"service-421209514056@woundai-jackh001.iam.gserviceaccount.com"
                  "::iam.serviceAccounts.getAccessToken": "CAN_ACCESS"}),
        listed=LISTED_SAS + ["service-421209514056@woundai-jackh001.iam.gserviceaccount.com"]),
        "use service account [service-421209514056@woundai-jackh001.iam.gserviceaccount.com]"),
    # Named like an agent but in this project's own domain: a customer account,
    # asked even when the listing does not show it.
    "effective_agent_named_account_in_this_projects_domain_is_asked": ("effective", DEMO_SA, troubleshoot(
        verdicts({"service-421209514056@woundai-jackh001.iam.gserviceaccount.com"
                  "::iam.serviceAccounts.actAs": "CAN_ACCESS"}),
        project_bindings=CLEAN_PROJECT + (("roles/viewer", [
            "serviceAccount:service-421209514056@woundai-jackh001.iam.gserviceaccount.com"], None),)),
        "use service account [service-421209514056@woundai-jackh001.iam.gserviceaccount.com] (iam.serviceAccounts.actAs)"),
    # Another project's Google APIs agent is not this project's: asked.
    "effective_another_projects_api_agent_is_asked": ("effective", DEMO_SA, troubleshoot(
        verdicts({"999999999999@cloudservices.gserviceaccount.com"
                  "::iam.serviceAccounts.actAs": "UNKNOWN_INFO"}),
        project_bindings=CLEAN_PROJECT + (("roles/editor", [
            "serviceAccount:999999999999@cloudservices.gserviceaccount.com"], None),)),
        "use service account [999999999999@cloudservices.gserviceaccount.com] (iam.serviceAccounts.actAs): [UNKNOWN_INFO]"),
    # Nor is another project's legacy Cloud Build account.
    "effective_another_projects_legacy_build_account_is_asked": ("effective", DEMO_SA, troubleshoot(
        verdicts({"999999999999@cloudbuild.gserviceaccount.com"
                  "::iam.serviceAccounts.signJwt": "UNKNOWN_INFO"}),
        project_bindings=CLEAN_PROJECT + (("roles/cloudbuild.builds.builder", [
            "serviceAccount:999999999999@cloudbuild.gserviceaccount.com"], None),)),
        "use service account [999999999999@cloudbuild.gserviceaccount.com] (iam.serviceAccounts.signJwt): [UNKNOWN_INFO]"),
    # Found re-reading this fix (2026-09-27): a first version recognised agents by
    # the shape service-<our number>@..., and any project can create an account
    # with that name. Whoever owns that project decides who may use it: asked.
    "effective_lookalike_agent_in_another_project_is_asked": ("effective", DEMO_SA, troubleshoot(
        verdicts({"service-421209514056@lookalike-project.iam.gserviceaccount.com"
                  "::iam.serviceAccounts.getAccessToken": "CAN_ACCESS"}),
        project_bindings=CLEAN_PROJECT + (("roles/secretmanager.secretAccessor", [
            "serviceAccount:service-421209514056@lookalike-project.iam.gserviceaccount.com"], None),)),
        "use service account [service-421209514056@lookalike-project.iam.gserviceaccount.com] "
        "(iam.serviceAccounts.getAccessToken): [CAN_ACCESS]"),
    # A Google domain the script has not been told about is asked like anything
    # else -- and refused until someone checks the documentation and lists it.
    "effective_agent_of_an_unlisted_service_is_asked": ("effective", DEMO_SA, troubleshoot(
        verdicts({"service-421209514056@gcp-sa-newservice.iam.gserviceaccount.com"
                  "::iam.serviceAccounts.actAs": "UNKNOWN_INFO"}),
        project_bindings=CLEAN_PROJECT + (("roles/newservice.serviceAgent", [
            "serviceAccount:service-421209514056@gcp-sa-newservice.iam.gserviceaccount.com"], None),)),
        "use service account [service-421209514056@gcp-sa-newservice.iam.gserviceaccount.com] "
        "(iam.serviceAccounts.actAs): [UNKNOWN_INFO]"),
    # Every listed domain for this project's number, and the two older Google
    # accounts: none of them may be asked (the fake has no answer for them).
    "effective_every_listed_agent_goes_unasked": ("effective", DEMO_SA, troubleshoot(
        project_bindings=CLEAN_PROJECT + tuple(
            ("roles/viewer", ["serviceAccount:" + a], None)
            for a in LISTED_AGENTS + [API_AGENT, LEGACY_BUILD])), None),
    "effective_deleted_accounts_are_not_asked": ("effective", DEMO_SA, troubleshoot(
        project_bindings=CLEAN_PROJECT + (("roles/viewer", [
            "deleted:serviceAccount:gone@other-project-123.iam.gserviceaccount.com?uid=1"], None),)),
        None),
    "effective_project_number_unreadable": ("effective", DEMO_SA, troubleshoot(**{
        "projects describe woundai-jackh001": {"exit": 1, "stderr": "ERROR: PERMISSION_DENIED\n"}}),
        "cannot read project [woundai-jackh001]"),
    "effective_project_number_missing": ("effective", DEMO_SA, troubleshoot(**{
        "projects describe woundai-jackh001": {"stdout": json.dumps({"projectId": "woundai-jackh001"})}}),
        "reports no project number"),
    # Nothing to read and no policy to change: a production secret that does not
    # exist is not asked about (the fake has no answer for it).
    "effective_missing_production_secret_is_not_asked": ("effective", DEMO_SA, troubleshoot(
        missing=("woundai-care-receipt-secret",)), None),
    # The project as inventoried on 2026-09-27: passes, and the one account that
    # can read the secrets is exactly the one the demo identity must not reach.
    "effective_real_project": ("effective", DEMO_SA, real_project(), None),
    "effective_real_project_compute_default_reachable": ("effective", DEMO_SA, real_project(verdicts({
        COMPUTE_SA + "::iam.serviceAccounts.actAs": "CAN_ACCESS"})),
        "use service account [%s] (iam.serviceAccounts.actAs): [CAN_ACCESS]" % COMPUTE_SA),
    "effective_unreadable_production_secret_policy": ("effective", DEMO_SA, troubleshoot(**{
        "secrets get-iam-policy woundai-flask-secret": DENIED}),
        "cannot read IAM policy of production secret [woundai-flask-secret]"),
    "effective_account_listing_unreadable": ("effective", DEMO_SA, troubleshoot(**{
        "iam service-accounts list": {"exit": 1, "stderr": "ERROR: PERMISSION_DENIED\n"}}),
        "cannot read service accounts of project [woundai-jackh001]"),
    "effective_account_without_an_email": ("effective", DEMO_SA, troubleshoot(**{
        "iam service-accounts list": {"stdout": json.dumps([{"email": PROD_SA}, {"name": "x"}])}}),
        "has an entry without an email"),
    "effective_production_identity_unknown": ("effective-no-production", DEMO_SA, troubleshoot(),
        "the production runtime identity is unknown"),
    "effective_unknown_info_is_not_evidence": ("effective", DEMO_SA, troubleshoot(verdicts({
        "woundai-flask-secret::secretmanager.versions.access": "UNKNOWN_INFO"})), "[UNKNOWN_INFO]"),
    "effective_unknown_conditional_is_not_evidence": ("effective", DEMO_SA, troubleshoot(verdicts({
        "woundai-care-receipt-secret::secretmanager.versions.access": "UNKNOWN_CONDITIONAL"})),
        "[UNKNOWN_CONDITIONAL]"),
    "effective_v1_not_granted_is_evidence": ("effective", DEMO_SA, troubleshoot(
        {row: {"stdout": json.dumps({"access": "NOT_GRANTED"})} for row in escalation_matrix()}),
        None),
    "effective_v1_granted": ("effective", DEMO_SA, troubleshoot(verdicts({
        "woundai-admin-password::secretmanager.versions.access":
            {"stdout": json.dumps({"access": "GRANTED"})}})),
        "read production secret [woundai-admin-password]: [GRANTED]"),
    "effective_missing_verdict": ("effective", DEMO_SA, troubleshoot(verdicts({
        "woundai-jwt-secret::secretmanager.versions.access":
            {"stdout": json.dumps({"accessTuple": {}})}})),
        "read production secret [woundai-jwt-secret]: []"),
    "effective_api_disabled_points_at_provisioning": ("effective", DEMO_SA, troubleshoot(verdicts({
        "woundai-admin-password::secretmanager.versions.access": {"exit": 1, "stderr":
            "ERROR: (gcloud.policy-intelligence.troubleshoot-policy.iam) PERMISSION_DENIED: "
            "Policy Troubleshooter API has not been used in project 421209514056 before or it "
            "is disabled. reason: SERVICE_DISABLED\n"}})),
        "gcloud services enable policytroubleshooter.googleapis.com"),
    "effective_other_failure_refuses": ("effective", DEMO_SA, troubleshoot(verdicts({
        "woundai-flask-secret::secretmanager.versions.access":
            {"exit": 1, "stderr": "ERROR: network unreachable\n"}})),
        "cannot evaluate whether the demo identity can read production secret [woundai-flask-secret]"),
    "effective_stderr_noise_is_fine": ("effective", DEMO_SA, troubleshoot(verdicts({
        "woundai-jwt-secret::secretmanager.versions.access":
            {"stdout": json.dumps({"overallAccessState": "CANNOT_ACCESS"}),
             "stderr": "WARNING: a newer gcloud is available.\n"}})), None),

    # -- the three identity checks together, in the order the script runs them --
    "identity_all_clean": ("identity-all", DEMO_SA, troubleshoot(), None),
    "identity_all_real_project": ("identity-all", DEMO_SA, real_project(), None),
    # The review of 086c406: a group grants projectIamAdmin, no secret names
    # the demo identity, and every answer the troubleshooter gives is
    # CANNOT_ACCESS. The run must still stop, at the project check.
    "identity_all_group_admin_with_a_blind_troubleshooter": ("identity-all", DEMO_SA, troubleshoot(
        project_bindings=CLEAN_PROJECT + (("roles/resourcemanager.projectIamAdmin",
                                           ["group:woundai-ops@googlegroups.com"], None),)),
        "cannot prove the demo identity is outside these principals"),
    # And had the project check let it through, the escalation check stops it
    # on its own: projects.setIamPolicy is not CANNOT_ACCESS.
    "identity_all_group_admin_seen_by_the_troubleshooter": ("identity-all", DEMO_SA, troubleshoot(
        verdicts({"project::resourcemanager.projects.setIamPolicy": "CAN_ACCESS"})),
        "unable to change the IAM policy of project [woundai-jackh001]"),
    # The review of ea82c57: an account from another project may read the
    # production secrets -- granted on the project, or on a secret -- and the
    # demo identity may mint tokens for it. Both used to pass all three checks,
    # because that account was never asked about.
    "identity_all_external_reader_granted_on_the_project": ("identity-all", DEMO_SA, troubleshoot(
        verdicts({EXT_SA + "::iam.serviceAccounts.getAccessToken": "CAN_ACCESS"}),
        project_bindings=CLEAN_PROJECT + (("roles/secretmanager.secretAccessor",
                                           ["serviceAccount:" + EXT_SA], None),)),
        "use service account [%s] (iam.serviceAccounts.getAccessToken): [CAN_ACCESS]" % EXT_SA),
    "identity_all_external_reader_granted_on_a_secret": ("identity-all", DEMO_SA, troubleshoot(
        verdicts({EXT_SA + "::iam.serviceAccounts.getAccessToken": "CAN_ACCESS"}),
        secret_members={"woundai-jwt-secret": ["serviceAccount:" + PROD_SA, "serviceAccount:" + EXT_SA]}),
        "use service account [%s] (iam.serviceAccounts.getAccessToken): [CAN_ACCESS]" % EXT_SA),

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
        "capped at one instance"),
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


def untracked_in_repo():
    """Untracked, non-ignored files in the working tree, or None without git."""
    try:
        out = subprocess.run(
            ["git", "-c", "safe.directory=" + ROOT.as_posix(), "--no-optional-locks",
             "-C", str(ROOT), "ls-files", "--others", "--exclude-standard", "-z"],
            capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return set(filter(None, out.stdout.decode("utf-8", "replace").split("\0")))


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
        cls.states = {}
        cls.untracked_before = untracked_in_repo()
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
                        "commit": COMMIT,
                        "production_identity": PRODUCTION_IDENTITY.get(cid, PROD_SA),
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
            cls.states[shell] = state
            env = os.environ.copy()
            env.update({"WOUNDAI_DEMO_CHECK_SOURCE": str(DEMO),
                        "WOUNDAI_DEMO_CHECK_CASES": str(case_file),
                        "WOUNDAI_DEMO_CHECK_STATE": str(state),
                        "WOUNDAI_FAKE_PYTHON": sys.executable,
                        "WOUNDAI_FAKE_GCLOUD": str(fake),
                        # Windows PowerShell 5.1 writes this cache on a background
                        # thread; under the validation runner's sandbox profile it
                        # resolved to a relative path and landed in the repository
                        # (2026-09-27). Keep it, and the working directory, here.
                        "PSModuleAnalysisCachePath": str(tmp / ("ModuleAnalysisCache-%d" % shell_index))})
            proc = subprocess.run(
                [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-File", str(harness)], env=env, cwd=str(tmp), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=300)
            seen = {}
            for line in proc.stdout.splitlines():
                if line.startswith("CASE "):
                    parts = line.split(" ", 3)
                    seen[parts[1]] = " ".join(parts[2:])
            cls.results[shell] = (proc, seen, shell_cases)

        cls.untracked_after = untracked_in_repo()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_the_harness_leaves_the_repository_untouched(self):
        # The validation runner fails a run whose source changed, but cannot
        # say which test changed it. This one can.
        if self.untracked_before is None or self.untracked_after is None:
            self.skipTest("not a git work tree")
        self.assertEqual(sorted(self.untracked_after - self.untracked_before), [])

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

    def calls(self, shell, case):
        path = Path(self.states[shell]) / case / "calls.jsonl"
        return [json.loads(l) for l in path.read_text().splitlines()]

    def test_the_troubleshooter_is_asked_every_question_about_the_right_principal(self):
        # The fake's routing key drops most --flags, so read what was actually sent.
        for shell in self.results:
            with self.subTest(shell=shell):
                calls = self.calls(shell, "effective_all_denied")
                reads = [["projects", "describe", "woundai-jackh001", "--format=json"],
                         ["iam", "service-accounts", "list", "--project=woundai-jackh001",
                          "--format=json"],
                         ["projects", "get-ancestors-iam-policy", "woundai-jackh001",
                          "--format=json"]]
                reads += [["secrets", "get-iam-policy", n, "--project=woundai-jackh001",
                           "--format=json"] for n in PRODUCTION]
                self.assertEqual(calls[:len(reads)], reads)
                asked = []
                for argv in calls[len(reads):]:
                    self.assertEqual(argv[:3], ["policy-intelligence", "troubleshoot-policy", "iam"])
                    self.assertIn("--principal-email=" + DEMO_SA, argv)
                    self.assertIn("--project=woundai-jackh001", argv)
                    self.assertIn("--quiet", argv, "without --quiet gcloud may stop to ask about enabling an API")
                    self.assertIn("--format=json", argv)
                    permission = [a for a in argv if a.startswith("--permission=")]
                    self.assertEqual(len(permission), 1, argv)
                    asked.append((argv[3], permission[0][len("--permission="):]))
                self.assertEqual(sorted(asked), sorted(escalation_matrix()))
                # Eight questions for each guarded account; none about the demo
                # identity itself or this project's Google service agents.
                self.assertFalse([r for r, _p in asked if DEMO_SA in r])
                for agent in AGENTS:
                    self.assertFalse([r for r, _p in asked if agent in r], agent)
                for email in (PROD_SA, COMPUTE_SA):
                    self.assertEqual(len([1 for r, _p in asked if r == sa_resource(email)]),
                                     len(SA_PERMISSIONS))

    def test_the_project_as_inventoried_is_asked_exactly_the_right_questions(self):
        # 2026-09-27: three production secrets exist, one account can reach
        # them, and the Google accounts go unasked. Fifteen questions.
        for shell in self.results:
            with self.subTest(shell=shell):
                asked = []
                for argv in self.calls(shell, "effective_real_project"):
                    if argv[:1] != ["policy-intelligence"]:
                        continue
                    permission = [a for a in argv if a.startswith("--permission=")]
                    asked.append((argv[3], permission[0][len("--permission="):]))
                present = [n for n in PRODUCTION if n not in REAL_MISSING]
                self.assertEqual(sorted(asked), sorted(escalation_matrix([COMPUTE_SA], present)))
                self.assertEqual(len(asked), 15)
                for account in LISTED_AGENTS + [LEGACY_BUILD, DEMO_SA] + list(REAL_MISSING):
                    self.assertFalse([r for r, _p in asked if account in r], account)

    def test_the_project_check_reads_the_whole_ancestry(self):
        for shell in self.results:
            with self.subTest(shell=shell):
                self.assertEqual(self.calls(shell, "projroles_clean"),
                                 [["projects", "get-ancestors-iam-policy", "woundai-jackh001",
                                   "--format=json"]])

    def test_the_review_counterexample_stops_before_any_escalation_question(self):
        # Refused at the project check: the troubleshooter is never reached, so
        # its (blind) CANNOT_ACCESS answers cannot be what let a run through.
        for shell in self.results:
            with self.subTest(shell=shell):
                calls = self.calls(shell, "identity_all_group_admin_with_a_blind_troubleshooter")
                self.assertTrue(calls, "no gcloud call was made at all")
                self.assertTrue(calls[-1][:2] == ["projects", "get-ancestors-iam-policy"], calls[-1])
                self.assertFalse([c for c in calls if c[:1] == ["policy-intelligence"]])

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
