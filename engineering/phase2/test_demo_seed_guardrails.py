#!/usr/bin/env python3
"""The boot-time demo-account seed must stay a very narrow door.

Why the door exists at all: an App Review submission needs a working login, and
the candidate revision runs WOUNDAI_STORE=local whose root is inside the
container (api_flywheel.FLYWHEEL_DIR). Cloud Run recycles instances, so an
account created by hand through /console is gone by the time a reviewer tries
it days later -- which reads to Apple as a broken app. The account therefore has
to be rebuilt on every cold start rather than created once.

Why it is dangerous: bootstrap_from_env already proves an env-var account
factory is reachable at import time, and its own docstring says why it only
fires on a completely empty account file -- otherwise the variable "would become
a permanent backdoor". This seed is a second such factory, and it is aimed at
an account a stranger will hold. So the guardrails are the feature.

These tests pin all five gates, and one of them is not about today's code:
test_refuses_a_whitelisted_role_that_gained_a_forbidden_permission mutates PERMS
to give nurse gt.verify. Nothing in the seed's own source changes in that
scenario -- the role name is still on the whitelist -- and a name-only check
would hand doctor endorsement to an external reviewer while every existing test
stayed green. That is exactly the failure auth_users' own `lite` role comment
records having already happened once.
"""
import ast
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FLASK_DIR = ROOT / "Backend" / "Flask"
AUTH = FLASK_DIR / "auth_users.py"
APP = FLASK_DIR / "app.py"

sys.path.insert(0, str(FLASK_DIR))
import store as store_mod       # noqa: E402
import auth_users               # noqa: E402

SEED_ENV = ("WOUNDAI_DEMO_SEED_USER", "WOUNDAI_DEMO_SEED_ROLE",
            "WOUNDAI_DEMO_SEED_PASSWORD")
GOOD_PW = "synthetic-test-only-seed-password"
PRIOR_PW = "synthetic-test-only-prior-password"
# Both carry the literal `synthetic-test-only`, which is what activates the
# .gitleaks.toml allowlist for engineering/phase2/test_*.py. The custom rules
# do not match these lines, but gitleaks also runs its default set, and a
# quoted string next to the word "password" is exactly what generic-api-key
# looks for. PR #8 lost three rounds to a false positive of that shape.


def text(path: Path) -> str:
    if not path.is_file():
        raise AssertionError("missing file: %s" % path)
    return path.read_text(encoding="utf-8-sig")


def function_source(module_path: Path, name: str) -> str:
    src = text(module_path)
    node = next((n for n in ast.parse(src).body
                 if isinstance(n, ast.FunctionDef) and n.name == name), None)
    if node is None:
        raise AssertionError("function %s not found in %s" % (name, module_path.name))
    return ast.get_source_segment(src, node) or ""


def _calls(nodes, func_name) -> bool:
    """True if any node in this statement list calls func_name."""
    for node in nodes:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                f = sub.func
                if isinstance(f, ast.Attribute) and f.attr == func_name:
                    return True
                if isinstance(f, ast.Name) and f.id == func_name:
                    return True
    return False


def _disables_auth(nodes) -> bool:
    """True if any node assigns None to the name auth_users."""
    for node in nodes:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Assign) and isinstance(sub.value, ast.Constant) \
                    and sub.value.value is None:
                for t in sub.targets:
                    if isinstance(t, ast.Name) and t.id == "auth_users":
                        return True
    return False


class NotALocalStore(object):
    """Stands in for any non-LocalStore backend without touching the network.

    Deliberately not a GcsStore instance: constructing one is what the test
    isolation guard refuses, and the seed's check is `is this LocalStore`, not
    `is this GcsStore` -- so a future third backend is covered too.
    """

    def append_line(self, key, line):
        raise AssertionError("the seed wrote to a non-local store")

    def read_text(self, key):
        return ""


class SeedBase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="woundai-demo-seed-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._saved_env = {k: os.environ.get(k) for k in SEED_ENV}
        self.addCleanup(self._restore_env)
        for k in SEED_ENV:
            os.environ.pop(k, None)
        store_mod.reset_store(store_mod.LocalStore(self.tmp))
        self.addCleanup(store_mod.reset_store, None)

    def _restore_env(self):
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def set_env(self, user="demo01", role="nurse", pw=GOOD_PW):
        for k, v in (("WOUNDAI_DEMO_SEED_USER", user),
                     ("WOUNDAI_DEMO_SEED_ROLE", role),
                     ("WOUNDAI_DEMO_SEED_PASSWORD", pw)):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class TestSeedHappyPath(SeedBase):

    def test_off_when_the_user_variable_is_absent(self):
        self.assertIsNone(auth_users.seed_demo_from_env())
        self.assertEqual(auth_users.list_users(), [])

    def test_creates_the_account_and_the_password_authenticates(self):
        self.set_env()
        out = auth_users.seed_demo_from_env()
        self.assertTrue(out.get("seeded"), out)
        self.assertEqual(out["identity"], "default:demo01")
        self.assertEqual(out["role"], "nurse")
        rec, why = auth_users.authenticate("default", "demo01", GOOD_PW)
        self.assertEqual(why, "ok")
        self.assertEqual(rec["role"], "nurse")

    def test_the_record_says_where_the_account_came_from(self):
        # The seed writes no audit entry on purpose (the chain on an ephemeral
        # local store is rebuilt every boot too, so an entry there proves
        # nothing). updated_by is then the only provenance the account carries,
        # and it has to survive.
        self.set_env()
        auth_users.seed_demo_from_env()
        self.assertEqual(auth_users.get_user("default", "demo01")["updated_by"],
                         "demo-seed")

    def test_the_minimum_password_length_is_accepted(self):
        self.set_env(pw="a" * auth_users.DEMO_SEED_MIN_PW)
        self.assertTrue(auth_users.seed_demo_from_env().get("seeded"))


class TestSeedRefusals(SeedBase):

    def test_refuses_when_the_store_is_not_local(self):
        self.set_env()
        store_mod.reset_store(NotALocalStore())
        out = auth_users.seed_demo_from_env()
        self.assertFalse(out.get("seeded"))
        self.assertIn("NotALocalStore", out["reason"])

    def test_refuses_user_names_outside_the_demo_nn_shape(self):
        for bad in ("admin", "admin2", "demo1", "demo001", "DEMO01",
                    "demo01x", "demo0a", "0demo1", "demo-01", "demo01.bak"):
            with self.subTest(user=bad):
                self.set_env(user=bad)
                out = auth_users.seed_demo_from_env()
                self.assertFalse(out.get("seeded"), "%s was seeded" % bad)
                self.assertIn("demoNN", out["reason"])
                self.assertEqual(auth_users.list_users(), [])

    def test_refuses_roles_outside_the_whitelist(self):
        for bad in ("admin", "engineer", "physician", "lite", "", "nurse2", "NURSE"):
            with self.subTest(role=bad):
                self.set_env(role=bad)
                out = auth_users.seed_demo_from_env()
                self.assertFalse(out.get("seeded"), "role %s was seeded" % bad)
                self.assertEqual(auth_users.list_users(), [])

    def test_refuses_a_whitelisted_role_that_gained_a_forbidden_permission(self):
        # The scenario: someone later adds nurse to gt.verify. The seed's own
        # source does not change, the role is still on the whitelist, and a
        # name-only gate would quietly start handing doctor endorsement to an
        # external App Review account.
        self.set_env(role="nurse")
        original = set(auth_users.PERMS["gt.verify"])
        auth_users.PERMS["gt.verify"] = original | {"nurse"}
        try:
            out = auth_users.seed_demo_from_env()
        finally:
            auth_users.PERMS["gt.verify"] = original
        self.assertFalse(out.get("seeded"),
                         "nurse held gt.verify and was still seeded")
        self.assertIn("gt.verify", out["reason"])
        self.assertEqual(auth_users.list_users(), [])

    def test_refuses_a_missing_password(self):
        self.set_env(pw=None)
        out = auth_users.seed_demo_from_env()
        self.assertFalse(out.get("seeded"))
        self.assertIn("WOUNDAI_DEMO_SEED_PASSWORD", out["reason"])

    def test_refuses_a_password_one_character_short(self):
        self.set_env(pw="a" * (auth_users.DEMO_SEED_MIN_PW - 1))
        out = auth_users.seed_demo_from_env()
        self.assertFalse(out.get("seeded"))
        self.assertEqual(auth_users.list_users(), [])

    def test_a_trailing_newline_from_a_powershell_pipe_is_removed(self):
        # `$pw | gcloud secrets versions add NAME --data-file=-` stores the
        # password plus a newline (CRLF from Windows PowerShell 5.1, LF from
        # pwsh). Seeded verbatim, the account's password would end in CRLF and
        # the reviewer typing exactly what they were given could never log in.
        for suffix in ("\r\n", "\n"):
            with self.subTest(suffix=repr(suffix)):
                store_mod.reset_store(store_mod.LocalStore(tempfile.mkdtemp(dir=self.tmp)))
                self.set_env(pw=GOOD_PW + suffix)
                self.assertTrue(auth_users.seed_demo_from_env().get("seeded"))
                self.assertEqual(
                    auth_users.authenticate("default", "demo01", GOOD_PW)[1], "ok",
                    "the password as typed does not authenticate")

    def test_the_length_floor_is_measured_after_the_newline_is_removed(self):
        self.set_env(pw="a" * (auth_users.DEMO_SEED_MIN_PW - 1) + "\r\n")
        self.assertFalse(auth_users.seed_demo_from_env().get("seeded"))

    def test_whitespace_or_control_characters_inside_are_refused(self):
        for bad in ("synthetic-test-only\nsplit-password", "synthetic test only password",
                    "synthetic-test-only-password\t", "synthetic-test-only\x1bpassword"):
            with self.subTest(pw=repr(bad)):
                self.set_env(pw=bad)
                out = auth_users.seed_demo_from_env()
                self.assertFalse(out.get("seeded"), "%r was seeded" % bad)
                self.assertEqual(auth_users.list_users(), [])

    def test_a_twelve_character_password_is_refused(self):
        # Spelled out rather than derived from DEMO_SEED_MIN_PW. A test that
        # computes its own boundary from the constant moves with the constant,
        # so lowering the floor back to auth_users' generic 10 would pass it.
        self.set_env(pw="abcdefghijkl")
        out = auth_users.seed_demo_from_env()
        self.assertFalse(out.get("seeded"), "a 12-character password was seeded")
        self.assertEqual(auth_users.list_users(), [])

    def test_a_refusal_is_never_reported_as_an_existing_account(self):
        # The deploy gate lets [demo-seed:exists] through and stops on
        # [demo-seed:refused]. A refusal flagged `exists` would be waved on.
        for user, role, pw in (("demo1", "nurse", GOOD_PW),
                               ("demo01", "doctor", GOOD_PW),
                               ("demo01", "nurse", None),
                               ("demo01", "nurse", "x" * 13)):
            with self.subTest(user=user, role=role, pw=pw and len(pw)):
                self.set_env(user=user, role=role, pw=pw)
                out = auth_users.seed_demo_from_env()
                self.assertFalse(out.get("seeded"))
                self.assertNotIn("exists", out)
        store_mod.reset_store(NotALocalStore())
        self.set_env()
        self.assertNotIn("exists", auth_users.seed_demo_from_env())

    def test_never_creates_an_admin_whatever_the_variables_say(self):
        for user, role in (("demo01", "admin"), ("admin", "admin"),
                           ("demo99", "engineer")):
            with self.subTest(user=user, role=role):
                self.set_env(user=user, role=role)
                auth_users.seed_demo_from_env()
        self.assertEqual([u for u in auth_users.list_users()
                          if u.get("role") in ("admin", "engineer")], [])


class TestSeedDoesNotDisturbExistingAccounts(SeedBase):

    def test_does_not_overwrite_an_existing_account(self):
        auth_users.upsert_user("default", "demo01", "nurse", PRIOR_PW,
                               actor="human")
        self.set_env()
        out = auth_users.seed_demo_from_env()
        self.assertFalse(out.get("seeded"))
        self.assertIn("已存在", out["reason"])
        # Marked as an expected skip, not a refusal: a gunicorn worker restart
        # inside the same container runs the seed again and lands here. The
        # deploy gate treats [demo-seed:refused] as fatal, so this case must
        # never be reported that way.
        self.assertIs(out.get("exists"), True)
        # The old password still works and the new one never took effect.
        self.assertEqual(auth_users.authenticate("default", "demo01",
                                                 PRIOR_PW)[1], "ok")
        self.assertIsNone(auth_users.authenticate("default", "demo01", GOOD_PW)[0])

    def test_a_cold_start_on_an_empty_store_recreates_a_disabled_account(self):
        """The uncomfortable half of the disable story, pinned on purpose.

        Raised by the review partner on 2026-09-22. "Already exists, do not
        touch" only holds against a store that still HAS the record. A recycled
        Cloud Run instance starts from an empty container filesystem, so the
        account does not exist, so the seed creates it -- enabled. Disabling
        demo01 therefore stops logins on that instance and no further.

        The only durable off switch is removing the environment variables and
        redeploying. Whoever reads the test above needs to read this one too.
        """
        auth_users.upsert_user("default", "demo01", "nurse", PRIOR_PW,
                               disabled=True, actor="human")
        self.assertTrue(auth_users.get_user("default", "demo01")["disabled"])

        fresh = tempfile.mkdtemp(prefix="woundai-cold-start-")
        self.addCleanup(shutil.rmtree, fresh, True)
        store_mod.reset_store(store_mod.LocalStore(fresh))

        self.set_env()
        out = auth_users.seed_demo_from_env()
        self.assertTrue(out.get("seeded"),
                        "if this ever stops seeding, the docs can promise a "
                        "durable disable -- until then they must not")
        self.assertFalse(auth_users.get_user("default", "demo01")["disabled"])

    def test_does_not_re_enable_a_disabled_account_on_the_same_instance(self):
        # Scope: the SAME store. Across a cold start the record is gone and the
        # seed does recreate it enabled -- see the test above, which pins that.
        auth_users.upsert_user("default", "demo01", "nurse", PRIOR_PW,
                               disabled=True, actor="human")
        self.set_env()
        out = auth_users.seed_demo_from_env()
        self.assertFalse(out.get("seeded"))
        self.assertTrue(auth_users.get_user("default", "demo01")["disabled"])
        self.assertEqual(auth_users.authenticate("default", "demo01",
                                                 PRIOR_PW)[1], "disabled")


class TestPermissionEnvelope(unittest.TestCase):
    """What a seeded account may and may not do, independent of the seed run."""

    def test_every_forbidden_permission_name_is_a_real_permission(self):
        # A typo here would be silent: `can(role, "gt.verifiy")` is False for
        # every role, so the guard would pass while checking nothing.
        unknown = sorted(auth_users.DEMO_SEED_FORBIDDEN_PERMS - set(auth_users.PERMS))
        self.assertEqual(unknown, [],
                         "not real permission names, so these guard nothing: %s" % unknown)

    def test_whitelisted_roles_hold_no_forbidden_permission_today(self):
        for role in sorted(auth_users.DEMO_SEED_ROLES):
            for perm in sorted(auth_users.DEMO_SEED_FORBIDDEN_PERMS):
                with self.subTest(role=role, perm=perm):
                    self.assertFalse(auth_users.can(role, perm))

    def test_the_password_floor_matches_the_console_generator(self):
        # api_users._gen_password mints 14. auth_users' own generic floor is 10,
        # which is too short for an account a stranger holds on the public
        # internet, so the seed sets its own and it must not drift back down.
        self.assertGreaterEqual(auth_users.DEMO_SEED_MIN_PW, 14)

    def test_whitelisted_roles_exist(self):
        self.assertTrue(auth_users.DEMO_SEED_ROLES)
        for role in auth_users.DEMO_SEED_ROLES:
            self.assertIn(role, auth_users.ROLES)

    def test_doctor_endorsement_is_on_the_forbidden_list(self):
        # doctor_verified is the flywheel's only ground-truth source; an
        # external reviewer must not be able to produce it.
        for perm in ("gt.verify", "annotation.submit", "user.manage", "audit.read"):
            self.assertIn(perm, auth_users.DEMO_SEED_FORBIDDEN_PERMS)


class TestSeedShape(unittest.TestCase):
    """Static pins for decisions a passing functional test would not notice."""

    def test_the_store_gate_asks_the_object_not_the_environment(self):
        src = function_source(AUTH, "seed_demo_from_env")
        self.assertIn("isinstance(st, _st.LocalStore)", src)
        self.assertNotIn(
            "WOUNDAI_STORE", src,
            "the seed read the env var instead of the live store object; those "
            "two drift, and the one that matters is what the process is using")

    def test_the_seed_writes_no_audit_entry(self):
        src = function_source(AUTH, "seed_demo_from_env")
        for call in ("audit(", "audit_intent("):
            self.assertNotIn(call, src)

    def test_the_seed_does_not_generate_its_own_password(self):
        # A per-boot random password would lock the reviewer out on the next
        # cold start, which is the exact failure this feature exists to prevent.
        src = function_source(AUTH, "seed_demo_from_env")
        for bad in ("secrets.", "random", "_gen_password", "token_urlsafe"):
            self.assertNotIn(bad, src)

    def test_app_runs_the_seed_in_a_try_that_never_disables_auth(self):
        # Asserted over the AST, not a character window: the seed call sits a
        # few lines below the import block, so any distance-based check matches
        # that block's own `auth_users = None` and proves nothing.
        tree = ast.parse(text(APP))
        seed_tries = [n for n in ast.walk(tree)
                      if isinstance(n, ast.Try) and _calls(n.body, "seed_demo_from_env")]
        self.assertEqual(len(seed_tries), 1,
                         "expected exactly one try block around the seed call")
        boot_tries = [n for n in ast.walk(tree)
                      if isinstance(n, ast.Try) and _calls(n.body, "bootstrap_from_env")]
        self.assertEqual(len(boot_tries), 1)
        self.assertIsNot(seed_tries[0], boot_tries[0],
                         "the seed shares the import try block; a seed failure "
                         "would then set auth_users = None and lock everyone out")
        for handler in seed_tries[0].handlers:
            self.assertFalse(
                _disables_auth(handler.body),
                "the seed's own except handler sets auth_users = None, which "
                "turns a submission convenience into a full outage")

    def _boot_block(self):
        """The module-level `if auth_users is not None:` block that runs the seed."""
        tree = ast.parse(text(APP))
        blocks = [n for n in tree.body
                  if isinstance(n, ast.If) and _calls(n.body, "seed_demo_from_env")]
        self.assertEqual(len(blocks), 1, "expected one module-level seed block in app.py")
        return blocks[0]

    def test_every_seed_outcome_prints_an_ascii_marker_and_flushes(self):
        # deploy_demo_candidate.ps1 reads these lines back from Cloud Logging.
        # ASCII, because Windows PowerShell 5.1 decodes native output with the
        # console code page and Chinese can arrive as mojibake. flush=True,
        # because Cloud Run's stdout is a pipe and Python block-buffers pipes:
        # without it the line can sit in the buffer and the gate reports that
        # the seed never ran.
        prints = [n for n in ast.walk(self._boot_block())
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                  and n.func.id == "print"]
        markers = []
        for call in prints:
            flush = [k for k in call.keywords if k.arg == "flush"]
            self.assertTrue(flush and isinstance(flush[0].value, ast.Constant)
                            and flush[0].value.value is True,
                            "a seed print at line %d does not flush" % call.lineno)
            first = call.args[0]
            while isinstance(first, ast.BinOp):
                first = first.left
            self.assertIsInstance(first, ast.Constant)
            markers.append(first.value.split(" ", 1)[0])
        self.assertEqual(sorted(markers), sorted([
            "[demo-seed:ok]", "[demo-seed:exists]", "[demo-seed:refused]", "[demo-seed:error]"]))

    def test_the_boot_block_marks_each_outcome(self):
        # Run the real block from app.py against a stand-in auth_users so each
        # branch is exercised, not just present.
        code = compile(ast.Module(body=[self._boot_block()], type_ignores=[]),
                       str(APP), "exec")

        class FakeAuth(object):
            def __init__(self, outcome):
                self.outcome = outcome

            def seed_demo_from_env(self):
                if isinstance(self.outcome, Exception):
                    raise self.outcome
                return self.outcome

        cases = (
            ({"seeded": True, "identity": "default/demo01", "role": "nurse"}, "[demo-seed:ok]"),
            ({"seeded": False, "exists": True, "reason": "already there"}, "[demo-seed:exists]"),
            ({"seeded": False, "reason": "bad role"}, "[demo-seed:refused]"),
            (RuntimeError("boom"), "[demo-seed:error]"),
        )
        for outcome, marker in cases:
            with self.subTest(marker=marker):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    exec(code, {"auth_users": FakeAuth(outcome)})
                lines = out.getvalue().splitlines()
                self.assertEqual(len(lines), 1, lines)
                self.assertTrue(lines[0].startswith(marker + " "), lines[0])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            exec(code, {"auth_users": FakeAuth(None)})
        self.assertEqual(out.getvalue(), "", "a disabled seed must print nothing")

    def test_the_windows_runner_strips_the_seed_variables(self):
        # An operator who just pulled the demo password out of Secret Manager to
        # fill in the Apple form plausibly still has it exported. Unstripped, it
        # would reach every test subprocess -- and the USER/ROLE pair would make
        # the seed actually fire inside some unrelated test's store.
        runner = text(ROOT / "tools" / "windows" / "run_python_tests.py")
        stripped = runner[runner.index("_STRIPPED_ENVIRONMENT_KEYS"):]
        stripped = stripped[:stripped.index('""".split())')]
        for var in ("WOUNDAI_DEMO_SEED_USER", "WOUNDAI_DEMO_SEED_ROLE",
                    "WOUNDAI_DEMO_SEED_PASSWORD"):
            self.assertIn(var, stripped)

    def test_bootstrap_still_refuses_to_run_on_a_populated_account_file(self):
        src = function_source(AUTH, "bootstrap_from_env")
        self.assertIn("if _read_all():", src)
        self.assertIn("return None", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
