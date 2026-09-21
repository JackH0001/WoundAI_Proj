#!/usr/bin/env python3
"""The service must not invent its own signing keys, and the deployment must
supply every key the service requires.

Until 2026-09-20 app.py read both keys with a literal fallback:

    SECRET_KEY=os.environ.get('FLASK_SECRET_KEY', <a constant in this repo>),

so a missing variable produced a service that started, looked healthy, and
signed with a constant published in this repository. The audit that found it
also found the other half of the problem: deploy_cloudrun.ps1 supplied
JWT_SECRET_KEY from Secret Manager but never supplied FLASK_SECRET_KEY, and
nothing compared the two lists. The code needed a key the deployment did not
provide, for months, silently.

2026-09-20 follow-up: the first version of this test compared app.py against
deploy_cloudrun.ps1 only, and passed while provision_runtime_identity.ps1 still
bound secretAccessor for just the two original secrets. The runtime service
account could not read woundai-flask-secret, so the revision would have failed
to start -- the same "the code needs a key some part of the infrastructure does
not supply" failure this file exists to catch, one layer over. The check is now
three-way.

These tests pin all of it:
  * no literal fallback survives, and a serving process with no key refuses to
    start (rather than generating one, which would break tokens across
    instances and read as an intermittent login bug);
  * every secret app.py requires appears in the deploy script's --set-secrets
    AND in its post-deploy contract check;
  * the Secret Manager name each one maps to is granted to the runtime identity.
"""
import ast
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FLASK_DIR = ROOT / "Backend" / "Flask"
APP = FLASK_DIR / "app.py"
DEPLOY = FLASK_DIR / "deploy_cloudrun.ps1"
PROVISION = FLASK_DIR / "provision_runtime_identity.ps1"

sys.path.insert(0, str(FLASK_DIR))
import runtime_secrets  # noqa: E402


def text(path: Path) -> str:
    if not path.is_file():
        raise AssertionError("missing file: %s" % path)
    return path.read_text(encoding="utf-8-sig")


def required_secret_names():
    """Every name app.py passes to resolve_secret(), read from the AST.

    Read from the syntax tree rather than by regex so a call split across lines,
    reindented or reordered still counts.
    """
    tree = ast.parse(text(APP))
    names = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        target = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if target != "resolve_secret" or not node.args:
            continue
        first = node.args[0]
        if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
            raise AssertionError(
                "resolve_secret is called with a non-literal name at app.py line %d; "
                "this test can no longer tell which secrets the service needs"
                % getattr(node, "lineno", -1))
        names.append(first.value)
    if not names:
        raise AssertionError(
            "app.py calls resolve_secret() nowhere. Either the fail-closed path was "
            "removed or this test is reading the wrong file.")
    return sorted(set(names))


class NoInventedSecretsTests(unittest.TestCase):

    # -- the constant is gone ------------------------------------------

    def test_no_placeholder_constant_survives_anywhere_in_the_backend(self):
        offenders = []
        for path in sorted(FLASK_DIR.rglob("*.py")):
            if "REPLACE_ME_SET" in path.read_text(encoding="utf-8-sig"):
                offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(
            offenders, [],
            "a published constant is not a default; found REPLACE_ME_SET in %s" % offenders)

    def test_app_reads_both_keys_through_the_fail_closed_helper(self):
        tree = ast.parse(text(APP))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "get"):
                continue
            if not (isinstance(func.value, ast.Name) and func.value.id == "os"):
                if not (isinstance(func.value, ast.Attribute) and func.value.attr == "environ"):
                    continue
            if len(node.args) < 2 or not isinstance(node.args[0], ast.Constant):
                continue
            name = node.args[0].value
            if isinstance(name, str) and "SECRET" in name.upper():
                self.fail(
                    "app.py line %d reads %s with a default. A signing key read with a "
                    "default cannot fail closed." % (node.lineno, name))

    # -- behaviour ------------------------------------------------------

    def test_configured_value_is_returned_unchanged(self):
        env = {"JWT_SECRET_KEY": "a-real-configured-value"}
        self.assertEqual(
            runtime_secrets.resolve_secret("JWT_SECRET_KEY", "test", env=env),
            "a-real-configured-value")

    def test_serving_process_refuses_to_start_without_a_key(self):
        for env in ({"K_SERVICE": "woundai-backend"}, {"WOUNDAI_STORE": "gcs"}):
            with self.assertRaises(RuntimeError) as caught:
                runtime_secrets.resolve_secret("JWT_SECRET_KEY", "test", env=env)
            self.assertIn("JWT_SECRET_KEY", str(caught.exception))

    def test_blank_and_whitespace_values_count_as_unset(self):
        for blank in ("", "   ", "\t"):
            with self.assertRaises(RuntimeError):
                runtime_secrets.resolve_secret(
                    "JWT_SECRET_KEY", "test", env={"K_SERVICE": "x", "JWT_SECRET_KEY": blank})

    def test_development_gets_a_fresh_key_every_time_and_says_so(self):
        warnings = []
        first = runtime_secrets.resolve_secret(
            "JWT_SECRET_KEY", "test", env={}, warn=lambda *a: warnings.append(a))
        second = runtime_secrets.resolve_secret(
            "JWT_SECRET_KEY", "test", env={}, warn=lambda *a: warnings.append(a))
        self.assertNotEqual(first, second, "a repeated value would become something to rely on")
        self.assertGreaterEqual(len(first), 32)
        self.assertEqual(len(warnings), 2, "the development path must announce itself")
        self.assertNotIn(first, text(FLASK_DIR / "runtime_secrets.py"))

    def test_serving_detection_does_not_fire_on_an_isolated_test_environment(self):
        # The isolation layer strips WOUNDAI_STORE before tests run; a local store
        # must never be mistaken for a serving deployment.
        self.assertFalse(runtime_secrets.is_serving({}))
        self.assertFalse(runtime_secrets.is_serving({"WOUNDAI_STORE": "local"}))
        self.assertTrue(runtime_secrets.is_serving({"K_SERVICE": "woundai-backend"}))

    # -- the two lists must agree --------------------------------------

    def test_deployment_supplies_every_secret_the_service_requires(self):
        deploy = text(DEPLOY)
        set_secrets = re.search(r"--set-secrets\s+\"([^\"]+)\"", deploy)
        if not set_secrets:
            raise AssertionError("deploy_cloudrun.ps1 has no --set-secrets argument")
        supplied = {pair.split("=", 1)[0].strip()
                    for pair in set_secrets.group(1).split(",") if "=" in pair}
        verified = set(re.findall(r"EnvironmentName\s*=\s*'([^']+)'", deploy))
        if not verified:
            raise AssertionError(
                "deploy_cloudrun.ps1 has no post-deploy secret contract to compare against")

        for name in required_secret_names():
            self.assertIn(
                name, supplied,
                "app.py requires %s but the deploy script never sets it; the service "
                "would refuse to start, or (before this gate) run on an invented key." % name)
            self.assertIn(
                name, verified,
                "the deploy script sets %s but never verifies it on the ready revision, "
                "so a revision missing it would still be accepted." % name)

    def test_runtime_identity_can_read_every_secret_the_deployment_injects(self):
        """Code -> deploy -> IAM must agree, not just code -> deploy.

        A secret the deploy script injects but the runtime service account cannot
        read does not fail at deploy time with a useful message; the revision
        simply never becomes ready.
        """
        deploy = text(DEPLOY)
        pairs = dict(re.findall(
            r"EnvironmentName\s*=\s*'([^']+)'\s*;\s*SecretName\s*=\s*'([^']+)'", deploy))
        if not pairs:
            raise AssertionError(
                "cannot read the EnvironmentName/SecretName contract from "
                "deploy_cloudrun.ps1; this test can no longer map env vars to secrets")

        provision = text(PROVISION)
        declared = re.search(
            r"\$script:RUNTIME_SECRET_NAMES\s*=\s*@\((?P<body>[^)]*)\)", provision)
        if not declared:
            raise AssertionError(
                "provision_runtime_identity.ps1 no longer declares "
                "$script:RUNTIME_SECRET_NAMES; this test cannot tell which secrets the "
                "runtime identity is actually granted")
        granted = set(re.findall(r"'([^']+)'", declared.group("body")))

        # Presence of the string somewhere in the file is not enough: the list has
        # to be the one the binding loops iterate. Before 2026-09-20 the names were
        # written out four times over, and woundai-flask-secret was added to none of
        # them. A literal @('woundai-... array is that shape coming back.
        self.assertNotIn(
            "@('woundai-", provision,
            "provision_runtime_identity.ps1 has an inline literal secret array again; "
            "parallel lists are how the runtime identity came to be missing a grant")

        for env_name in required_secret_names():
            secret_name = pairs.get(env_name)
            self.assertIsNotNone(
                secret_name,
                "app.py requires %s but deploy_cloudrun.ps1's secret contract does not "
                "say which Secret Manager secret it comes from." % env_name)
            self.assertIn(
                secret_name, granted,
                "provision_runtime_identity.ps1 never grants the runtime identity access "
                "to %s (needed for %s). The revision would deploy and then fail to start."
                % (secret_name, env_name))


if __name__ == "__main__":
    unittest.main(verbosity=2)
