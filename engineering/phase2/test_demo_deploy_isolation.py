#!/usr/bin/env python3
"""The demo deployment path must not be able to reach production.

The demo submission service runs WOUNDAI_STORE=local. If that revision lived in
the production Cloud Run service, one `update-traffic` away is a live service
that no longer persists to GCS and whose audit chain forks per instance --
silently, with a green health check. deploy_cloudrun.ps1 even asserts
WOUNDAI_STORE='gcs' as an invariant on revisions it inspects, so a local-store
revision in that service is a landmine for the production path too.

Separating the services turns "we will be careful" into "it cannot happen":
Cloud Run traffic routing is a within-service operation.

These tests pin that separation, and pin that the production script was NOT
loosened to accommodate the demo -- the cheapest way to break this property is
to add a -Local switch to deploy_cloudrun.ps1 and delete this file's reason to
exist.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FLASK = ROOT / "Backend" / "Flask"
DEMO = FLASK / "deploy_demo_candidate.ps1"
PROD = FLASK / "deploy_cloudrun.ps1"
AUTH = FLASK / "auth_users.py"


def text(path: Path) -> str:
    if not path.is_file():
        raise AssertionError("missing file: %s" % path)
    return path.read_text(encoding="utf-8-sig")


def code_only(src: str) -> str:
    """PowerShell source with comments removed.

    Needed because this script explains at length why it does NOT call
    update-traffic. A test that searched the raw text would be satisfied by
    deleting the explanation and would fail on honest prose -- exactly
    backwards. What matters is whether a line can execute.
    """
    src = re.sub(r"<#.*?#>", "", src, flags=re.DOTALL)
    out = []
    for line in src.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        # Drop a trailing comment only when the '#' is not inside a string.
        cut = None
        in_single = in_double = False
        for i, ch in enumerate(line):
            if ch == "'" and not in_double:
                in_single = not in_single
            elif ch == '"' and not in_single:
                in_double = not in_double
            elif ch == "#" and not in_single and not in_double:
                cut = i
                break
        out.append(line if cut is None else line[:cut])
    return "\n".join(out)


class DemoPathCannotTouchProduction(unittest.TestCase):

    def test_no_executable_line_routes_traffic(self):
        code = code_only(text(DEMO))
        for verb in ("update-traffic", "--to-latest", "--to-revisions"):
            self.assertNotIn(verb, code,
                             "the demo script can route traffic via %r" % verb)

    def test_it_refuses_the_production_service_by_name_and_by_shape(self):
        code = code_only(text(DEMO))
        self.assertIn("if ($Service -ceq $ProductionService) {", code)
        self.assertIn("refuse to deploy the demo revision into the production service", code)
        self.assertIn("-demo$", code, "demo service name shape is not enforced")

    def test_it_takes_no_bucket_parameters_at_all(self):
        code = code_only(text(DEMO))
        for param in ("$Bucket", "$AuditBucket"):
            self.assertNotIn(
                "[string]%s" % param, code,
                "%s is a parameter of the demo script; it must not be able to "
                "name a production bucket" % param)

    def test_it_deploys_local_storage_and_never_gcs(self):
        code = code_only(text(DEMO))
        self.assertIn("WOUNDAI_STORE=local", code)
        self.assertNotIn("WOUNDAI_STORE=gcs", code)
        for leaked in ("WOUNDAI_GCS_BUCKET=", "WOUNDAI_AUDIT_BUCKET=", "WOUNDAI_GCS_PREFIX="):
            self.assertNotIn(leaked, code)

    def test_it_verifies_the_deployed_revision_rather_than_trusting_the_flags(self):
        # Passing --set-env-vars is not evidence; reading the revision back is.
        code = code_only(text(DEMO))
        self.assertIn("function Assert-DemoRevisionConfiguration", code)
        self.assertIn("run revisions describe", code)
        # Pin the CONDITIONS, not the error messages. Replacing a condition
        # with $false leaves its message sitting in the file, so a
        # message-only assertion passes while the check is gone -- both of
        # these survived mutation testing until the conditions were pinned.
        self.assertIn("if ($envs['WOUNDAI_STORE'] -cne 'local') {", code)
        self.assertIn("if ($envs.ContainsKey($forbidden)) {", code)
        self.assertIn("demo revision must run WOUNDAI_STORE=local", code)
        self.assertIn("demo revision carries", code)

    def test_the_single_instance_pin_is_enforced_and_read_back(self):
        # LocalStore's chain-integrity lock is in-process. Two instances are two
        # unrelated datasets, and a reviewer would see the record list change
        # between consecutive requests. This is correctness, not cost control.
        code = code_only(text(DEMO))
        self.assertIn("--max-instances 1", code)
        self.assertIn("autoscaling.knative.dev/maxScale", code)
        self.assertIn("if ($limit -cne '1') {", code)
        self.assertIn("demo revision must be pinned to a single instance", code)

    def test_the_build_source_does_not_depend_on_where_it_was_run_from(self):
        # `--source .` uploads whatever directory the operator happens to be in.
        # Run as .\Backend\Flask\deploy_demo_candidate.ps1 from the repo root,
        # that is the whole repository. The script's own directory is the only
        # source that is correct regardless of the caller's location.
        code = code_only(text(DEMO))
        self.assertIn("--source $PSScriptRoot", code)
        self.assertNotRegex(code, r"--source\s+\.\s")

    def test_it_refuses_the_production_runtime_identity(self):
        code = code_only(text(DEMO))
        self.assertIn("function Assert-NotTheProductionIdentity", code)
        self.assertIn("demo service must not run as the production runtime identity", code)
        self.assertIn("if ($prodSa -ieq $RuntimeServiceAccount) {", code)


class DemoSeedFlagsMatchTheBackend(unittest.TestCase):

    def test_the_role_whitelist_matches_auth_users(self):
        # Two lists that must agree. If someone widens DEMO_SEED_ROLES without
        # touching the script the deploy silently stops accepting a role the
        # backend allows; if they widen the script without the backend, the
        # deploy succeeds and the seed refuses at boot -- with the reviewer
        # locked out and no one watching the logs.
        code = code_only(text(DEMO))
        match = re.search(r"\$DemoSeedRole -cnotin @\(([^)]*)\)", code)
        self.assertIsNotNone(match, "the script no longer whitelists the seed role")
        script_roles = set(re.findall(r"'([a-z]+)'", match.group(1)))

        auth = text(AUTH)
        block = re.search(r"DEMO_SEED_ROLES = frozenset\(\{([^}]*)\}\)", auth)
        self.assertIsNotNone(block)
        backend_roles = set(re.findall(r'"([a-z]+)"', block.group(1)))

        self.assertEqual(script_roles, backend_roles,
                         "deploy script and auth_users disagree on the seed roles")

    def test_the_user_name_shape_matches_auth_users(self):
        self.assertIn("'^demo[0-9]{2}$'", code_only(text(DEMO)))
        self.assertIn(r'r"^demo[0-9]{2}$"', text(AUTH))

    def test_it_requires_hand_written_authorisation(self):
        code = code_only(text(DEMO))
        self.assertIn("[Parameter(Mandatory = $true)][string]$DemoAuthorisationRef", code)
        self.assertRegex(code, r"placeholder\|範本\|填入\|輸入")

    def test_it_never_handles_the_demo_password_itself(self):
        code = code_only(text(DEMO))
        # The secret is mounted by name; the value must never be read, logged
        # or used to log in from the script.
        self.assertIn("'WOUNDAI_DEMO_SEED_PASSWORD' = $DemoSeedSecret", code)
        for leak in ("secrets versions access", "/api/auth/login"):
            self.assertNotIn(leak, code,
                             "the deploy script reaches for the demo password (%s)" % leak)


def production_mounts():
    """(env var -> secret name) that deploy_cloudrun.ps1 mounts, variables resolved."""
    code = code_only(text(PROD))
    line = re.search(r'--set-secrets\s+"([^"]+)"', code)
    if line is None:
        raise AssertionError("cannot find --set-secrets in deploy_cloudrun.ps1")
    defaults = dict(re.findall(r'\[string\]\$(\w+)\s*=\s*"([^"]*)"', code))
    out = {}
    for item in line.group(1).split(","):
        env, ref = item.split("=", 1)
        name = ref.replace("`", "").rsplit(":", 1)[0]
        if name.startswith("$"):
            name = defaults[name[1:]]
        out[env.strip()] = name
    return out


def demo_production_list():
    body = re.search(r"function Get-ProductionSecretNames \{(.*?)\n\}", code_only(text(DEMO)), re.S)
    if body is None:
        raise AssertionError("Get-ProductionSecretNames not found")
    return set(re.findall(r"'([a-z0-9-]+)'", body.group(1)))


def demo_secret_map():
    body = re.search(r"function Get-DemoSecretMap \{(.*?)\n\}", code_only(text(DEMO)), re.S)
    if body is None:
        raise AssertionError("Get-DemoSecretMap not found")
    return dict(re.findall(r"'([A-Z0-9_]+)'\s*=\s*\$(\w+)", body.group(1)))


class DemoKeysAreNotProductionKeys(unittest.TestCase):
    """The demo service must sign nothing with a key production trusts.

    Production reads the role straight from an access token's claims and never
    re-checks that the account exists; tokens live 24 hours. A shared JWT key
    therefore makes every demo-issued token a production token. The first
    version of this script shared three keys and passed every test and the CI
    gate, because no test looked at which secrets the demo service mounted.
    """

    def test_the_production_list_matches_what_production_mounts(self):
        # If production gains a secret and this list does not, the demo
        # script's refusals and read-back silently stop covering it.
        self.assertEqual(demo_production_list(), set(production_mounts().values()))

    def test_every_key_production_signs_with_has_a_demo_counterpart(self):
        # ADMIN_PASSWORD is deliberately absent: the demo needs no administrator.
        # Anything else production mounts is a key the demo must hold its own
        # copy of, or the demo service either breaks or borrows production's.
        needed = set(production_mounts()) - {"ADMIN_PASSWORD"}
        self.assertTrue(needed <= set(demo_secret_map()),
                        "demo has no key of its own for: %s" % sorted(needed - set(demo_secret_map())))

    def test_no_administrator_credential_is_mounted(self):
        self.assertNotIn("ADMIN_PASSWORD", demo_secret_map())
        self.assertNotIn("ADMIN_PASSWORD=", code_only(text(DEMO)))

    def test_the_mount_is_built_from_the_demo_map_and_nothing_else(self):
        code = code_only(text(DEMO))
        self.assertIn("--set-secrets (@((Get-DemoSecretMap).GetEnumerator()", code)
        self.assertEqual(len(re.findall(r"--set-secrets", code)), 1)

    def test_no_production_secret_name_appears_outside_the_refusal_list(self):
        code = code_only(text(DEMO))
        body = re.search(r"function Get-ProductionSecretNames \{.*?\n\}", code, re.S)
        rest = code.replace(body.group(0), "")
        for name in demo_production_list():
            self.assertNotIn(name, rest,
                             "production secret [%s] is named in executable code" % name)

    def test_every_demo_default_is_in_the_demo_namespace(self):
        defaults = dict(re.findall(r'\[string\]\$(\w+)\s*=\s*"([^"]*)"', code_only(text(DEMO))))
        for env, var in demo_secret_map().items():
            with self.subTest(env=env):
                self.assertRegex(defaults[var], r"^woundai-demo-[a-z0-9-]+$")

    def test_the_identity_check_runs_before_anything_is_deployed(self):
        code = code_only(text(DEMO))
        call = code.index("\nAssert-DemoIdentityCannotReadProductionSecrets\n")
        self.assertLess(call, code.index("Invoke-GCloud run deploy"))

    def test_the_read_back_conditions_are_present(self):
        # Conditions, not messages: replacing a condition with $false leaves
        # its message behind, so a message-only pin would pass with no check.
        code = code_only(text(DEMO))
        for cond in ("if ($production -ccontains $refs[$envName]) {",
                     "if ($refs.ContainsKey('ADMIN_PASSWORD')) {",
                     "if ($refs[$envName] -cne $map[$envName]) {",
                     "if ($production -ccontains $name) {",
                     "if ($null -ne $binding -and @($binding.members) -ccontains $member) {"):
            with self.subTest(cond=cond):
                self.assertIn(cond, code)

    def test_the_care_keyring_is_confirmed_live_after_deploy(self):
        # Without its own keyring the demo answers 503 on the consent path,
        # which a reviewer reads as a broken app.
        self.assertIn("if (-not ($care -is [bool] -and $care)) {", code_only(text(DEMO)))


def function_body(code: str, name: str) -> str:
    body = re.search(r"function %s\b[^{]*\{(.*?)\n\}" % re.escape(name), code, re.S)
    if body is None:
        raise AssertionError("%s not found" % name)
    return body.group(1)


def main_flow(code: str) -> str:
    start = code.index('Say "示範服務部署（送審用）"')
    return code[start:]


class DemoBuildContextMatchesProduction(unittest.TestCase):
    """Reported 2026-09-23 on b24a47c: the demo build had no vendor/ step.

    The Docker build context is Backend/Flask only, so the engineering modules
    classify imports do not exist in the image unless they are copied into
    vendor/ first. deploy_cloudrun.ps1 has always done that; the demo script
    did not. The service would start, logins would work, and measurement
    would answer 503 -- or, for color_calib, silently fall back to gray-world.
    """

    @staticmethod
    def production_vendor_list():
        code = code_only(text(PROD))
        block = re.search(r"\$needed = @\((.*?)\)", code, re.S)
        if block is None:
            raise AssertionError("deploy_cloudrun.ps1 no longer has its $needed list")
        return re.findall(r'"([^"]+)"', block.group(1))

    @staticmethod
    def demo_vendor_list():
        return re.findall(r'"([^"]+)"',
                          function_body(code_only(text(DEMO)), "Get-EngineeringVendorFiles"))

    def test_the_vendor_list_is_production_list(self):
        prod = self.production_vendor_list()
        self.assertGreaterEqual(len(prod), 6, "production vendor list parsed too short")
        self.assertEqual(self.demo_vendor_list(), prod)

    def test_vendor_is_copied_before_the_build_and_only_when_building(self):
        flow = main_flow(code_only(text(DEMO)))
        call = "Copy-EngineeringVendor -FlaskDir $PSScriptRoot"
        self.assertEqual(flow.count(call), 1)
        branch = flow.index("if (-not $VerifyOnly) {")
        self.assertLess(branch, flow.index(call))
        self.assertLess(flow.index(call), flow.index("Invoke-GCloud run deploy $Service"))
        self.assertLess(flow.index("Invoke-GCloud run deploy $Service"),
                        flow.index("} else {", branch))

    def test_the_copy_is_checked_not_assumed(self):
        body = function_body(code_only(text(DEMO)), "Copy-EngineeringVendor")
        for cond in ("if ($missing.Count -gt 0) {",
                     "if ($a -cne $b) { throw",
                     "if (($present -join '|') -cne ($expected -join '|')) {"):
            with self.subTest(cond=cond):
                self.assertIn(cond, body)
        # The source check precedes the delete, so a refused copy leaves the
        # previous vendor/ in place.
        self.assertLess(body.index("if ($missing.Count -gt 0) {"),
                        body.index("Remove-Item -LiteralPath $vendor"))

    def test_the_build_can_see_vendor_and_git_cannot(self):
        # Copying into vendor/ must not dirty the worktree (Get-DemoGitCommit
        # refuses a dirty tree), and must reach the build upload.
        self.assertIn("Backend/Flask/vendor/", text(ROOT / ".gitignore"))
        for ignore in (FLASK / ".gcloudignore", FLASK / ".dockerignore"):
            with self.subTest(ignore=ignore.name):
                lines = [l.strip() for l in text(ignore).splitlines()
                         if l.strip() and not l.strip().startswith("#")]
                self.assertFalse(any(l.rstrip("/") == "vendor" for l in lines),
                                 "%s excludes vendor/" % ignore.name)


class DemoAcceptanceGateCannotBeWavedThrough(unittest.TestCase):
    """Reported 2026-09-23 on b24a47c: the post-deploy checks could print "done"
    over an incomplete service -- no measurement-module check, the commit read
    from a field that does not exist (health.git_commit; it is under build),
    and a version mismatch or a missing seed line answered with a warning.
    Behaviour is exercised in test_demo_deploy_checks; these pin the wiring.
    """

    GATES = ("$state = Assert-DemoServiceState -ExpectedRevision $ExpectedRevision",
             "Assert-DemoRevisionConfiguration -Revision $liveRevision -ExpectedGitCommit $GitCommit",
             "Assert-DemoHealth -Health $health -ExpectedGitCommit $GitCommit -ExpectedRevision $liveRevision",
             "Assert-DemoSeedLogged -Revision $liveRevision")

    def test_every_gate_runs_after_the_deploy_and_before_done(self):
        flow = main_flow(code_only(text(DEMO)))
        deploy = flow.index("Invoke-GCloud run deploy $Service")
        done = flow.index('Say "完成"')
        self.assertEqual(flow.count('Say "完成"'), 1)
        last = deploy
        for gate in self.GATES:
            with self.subTest(gate=gate):
                self.assertEqual(flow.count(gate), 1, gate)
                at = flow.index(gate)
                self.assertLess(last, at, "gate out of order: %s" % gate)
                self.assertLess(at, done)
                last = at

    def test_a_fresh_deploy_is_verified_against_the_revision_it_created(self):
        # Without this binding, a revision that fails to become ready leaves
        # latestReadyRevisionName on the previous one, and every gate below
        # would verify the old revision and pass.
        flow = main_flow(code_only(text(DEMO)))
        bind = '$ExpectedRevision = "$Service-$RevisionSuffix"'
        self.assertEqual(flow.count(bind), 1)
        deploy = flow.index("Invoke-GCloud run deploy $Service")
        self.assertLess(deploy, flow.index(bind))
        self.assertLess(flow.index(bind), flow.index("} else {", deploy))
        self.assertIn("--revision-suffix $RevisionSuffix", flow)

    def test_no_warning_stands_in_for_a_failed_gate(self):
        flow = main_flow(code_only(text(DEMO)))
        section = flow[flow.index("Invoke-GCloud run deploy $Service"):flow.index('Say "完成"')]
        lines = section.splitlines()
        warns = [i for i, l in enumerate(lines) if re.search(r"\bWarn\b", l)]
        # The only warning left is the A-U ensemble reminder, which is not
        # part of degraded by product decision (see app.py).
        self.assertEqual(len(warns), 1, [lines[i] for i in warns])
        context = "\n".join(lines[max(0, warns[0] - 2):warns[0] + 1])
        self.assertIn("au_ensemble_files_present", context)
        for fn in ("Assert-DemoServiceState", "Assert-DemoRevisionConfiguration",
                   "Assert-DemoHealth", "Assert-NotTheProductionIdentity",
                   "ConvertFrom-GCloudJson"):
            with self.subTest(fn=fn):
                self.assertNotRegex(function_body(code_only(text(DEMO)), fn), r"\bWarn\b")

    def test_health_is_read_where_the_fields_are(self):
        code = code_only(text(DEMO))
        self.assertNotRegex(code, r"(?i)\$health\.git_commit",
                            "the commit is under build, not at the top level")
        body = function_body(code, "Assert-DemoHealth")
        for cond in ("if ([string]$Health.status -cne 'healthy') {",
                     "if ([string]$Health.build.git_commit -cne $ExpectedGitCommit) {",
                     "if ([string]$Health.build.revision -cne $ExpectedRevision) {",
                     "if (-not ($v -is [bool] -and $v)) {",
                     "if ($failures.Count -gt 0) {"):
            with self.subTest(cond=cond):
                self.assertIn(cond, body)
        for module in ("'segmentation_model'", "'classify_modules'", "'color_calibration'",
                       "'endpoints_registered'", "'canonicalization_golden'"):
            with self.subTest(module=module):
                self.assertIn(module, body)

    def test_the_seed_is_confirmed_from_this_revision_only(self):
        code = code_only(text(DEMO))
        self.assertNotIn("logs read", code, "service-wide log tail is back")
        body = function_body(code, "Assert-DemoSeedLogged")
        self.assertIn("resource.labels.revision_name=$Revision", body)
        self.assertIn("'--order=asc'", body)
        for cond in (r"$_ -cmatch '\[demo-seed:(refused|error)\]'",
                     r"$_ -cmatch '\[demo-seed:ok\]'",
                     "if ($r.Exit -ne 0) {"):
            with self.subTest(cond=cond):
                self.assertIn(cond, body)
        # Falling out of the polling loop is a failure, not a shrug.
        self.assertTrue(body.rstrip().splitlines()[-1].strip().startswith(
            'throw "no [demo-seed:ok] line from revision'), body.rstrip().splitlines()[-1])

    def test_the_markers_the_gate_reads_are_the_ones_app_prints(self):
        app = text(FLASK / "app.py")
        for marker in ("[demo-seed:ok]", "[demo-seed:exists]",
                       "[demo-seed:refused]", "[demo-seed:error]"):
            with self.subTest(marker=marker):
                self.assertIn('"%s ' % marker, app)

    def test_the_production_identity_lookup_cannot_be_skipped(self):
        code = code_only(text(DEMO))
        body = function_body(code, "Assert-NotTheProductionIdentity")
        self.assertIn("ConvertFrom-GCloudJson", body)
        self.assertNotRegex(body, r"\breturn\s*$", "a bare return skips the comparison")
        self.assertIn("if ($Result.Exit -ne 0) {", function_body(code, "ConvertFrom-GCloudJson"))
        flow = main_flow(code)
        self.assertIn("$ProductionRuntimeIdentity = Assert-NotTheProductionIdentity", flow)
        self.assertIn("-ProductionIdentity $ProductionRuntimeIdentity", flow)

    def test_the_revision_identity_is_read_back(self):
        body = function_body(code_only(text(DEMO)), "Assert-DemoRevisionConfiguration")
        for cond in ("if ($runsAs -cne $RuntimeServiceAccount) {",
                     "if ($runsAs -ieq $ProductionIdentity) {",
                     "if ($envs[$k] -cne $plain[$k]) {",
                     "if (-not $map.Contains($envName)) {"):
            with self.subTest(cond=cond):
                self.assertIn(cond, body)


class ProductionScriptWasNotLoosened(unittest.TestCase):

    def test_production_still_hardcodes_gcs(self):
        code = code_only(text(PROD))
        self.assertIn("WOUNDAI_STORE=gcs", code)
        self.assertNotIn("WOUNDAI_STORE=local", code)

    def test_production_gained_no_local_store_switch(self):
        code = code_only(text(PROD))
        for switch in ("$Local", "$DemoMode", "$UseLocalStore", "$DemoSeedUser"):
            self.assertNotIn(
                "[switch]%s" % switch, code,
                "deploy_cloudrun.ps1 grew a demo/local mode; the isolation this "
                "file exists to guarantee is gone")

    def test_production_still_requires_its_bucket_parameters(self):
        code = code_only(text(PROD))
        self.assertIn("[Parameter(Mandatory = $true)][string]$AuditBucket", code)
        self.assertIn("[Parameter(Mandatory = $true)][string]$Bucket", code)


class CommentStripperIsHonest(unittest.TestCase):
    """The stripper decides what every other test in this file sees."""

    def test_it_removes_full_line_and_trailing_comments(self):
        self.assertEqual(code_only("# gone\nkept").strip(), "kept")
        self.assertEqual(code_only("code # gone").strip(), "code")

    def test_it_keeps_a_hash_inside_a_string(self):
        self.assertIn('"a#b"', code_only('$x = "a#b"'))
        self.assertIn("'a#b'", code_only("$x = 'a#b'"))

    def test_the_demo_script_really_does_discuss_traffic_in_prose(self):
        # If this ever fails, the explanation was deleted rather than the call,
        # and test_no_executable_line_routes_traffic became vacuous.
        self.assertIn("update-traffic", text(DEMO))
        self.assertNotIn("update-traffic", code_only(text(DEMO)))


class PowerShellScriptsAreReadableOnWindows(unittest.TestCase):
    """Windows PowerShell 5.1 reads a BOM-less file as ANSI, not UTF-8.

    Every .ps1 here carries Traditional Chinese in comments, output and -- the
    part that bites -- inside guard regexes. deploy_cloudrun.ps1 rejects
    authorisation text matching (placeholder|範本|填入|輸入); read as
    Windows-1252 those alternatives stop matching, so a placeholder written in
    Chinese sails through a guard that still looks present in the diff.

    Every existing script already had the BOM. deploy_demo_candidate.ps1 was
    written without one and nothing in the suite noticed, which is why this
    test covers all of them rather than just the new file.
    """

    def test_every_deployment_script_starts_with_a_utf8_bom(self):
        """Scoped to Backend/Flask, where the guards live.

        Six scripts under Windows/ and tools/windows/ are BOM-less today, three
        of them carrying non-ASCII. That is a pre-existing gap and it is
        reported separately rather than swept into this change -- but it is NOT
        excluded here by an allowlist, because an allowlist is how a gap
        becomes permanent. This assertion covers the scripts whose regexes
        decide whether a deployment is allowed to proceed; mojibake there is a
        silently weakened safety guard, not cosmetic output.
        """
        scripts = sorted((ROOT / "Backend" / "Flask").glob("*.ps1"))
        self.assertGreater(len(scripts), 2, "no deployment scripts found to check")
        missing = [str(p.relative_to(ROOT)) for p in scripts
                   if not p.read_bytes().startswith(b"\xef\xbb\xbf")]
        self.assertEqual(missing, [],
                         "BOM-less PowerShell, read as ANSI by 5.1: %s" % missing)

    def test_the_guard_regex_really_does_carry_non_ascii(self):
        # If this ever fails the BOM test above became decorative.
        for script in (DEMO, PROD):
            src = text(script)
            self.assertTrue(any(ord(ch) > 127 for ch in src),
                            "%s is pure ASCII now" % script.name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
