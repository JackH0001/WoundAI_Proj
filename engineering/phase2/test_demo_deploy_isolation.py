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
        self.assertIn("WOUNDAI_DEMO_SEED_PASSWORD=${DemoSeedSecret}:latest", code)
        for leak in ("secrets versions access", "/api/auth/login"):
            self.assertNotIn(leak, code,
                             "the deploy script reaches for the demo password (%s)" % leak)


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
