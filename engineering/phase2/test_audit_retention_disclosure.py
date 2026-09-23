#!/usr/bin/env python3
"""What /api/health says about the audit bucket must match the bucket.

The revision deployed on 2026-08-23 (505ff2e) rendered any configured audit
bucket as plain "WORM":

    d += " (稽核→gcs://%s，WORM)" % self._audit_bucket_name

A 2026-09-22 readback of gs://woundai-flywheel-jackh001-audit returned
retentionPeriod 220903200 (7 years) with isLocked unset. Unlocked retention does
stop deletions today, but the policy can be removed with one API call and then
they are possible. WORM promises the half that survives an administrator, and
that is the half an audit trail is for -- so the word was doing work the
configuration did not support. Anyone reading that health output, including a
hospital's IT, would have concluded the trail was immutable.

These tests pin two things:
  * the word WORM appears only when the readback actually said locked;
  * describe() renders from what the gate last observed and never reaches the
    network itself, because a Cloud Run health probe must not depend on GCS.

And one thing that must NOT change: retention_info() stays uncached. It is the
gate's only evidence, and a cached "locked" is exactly the stale yes this layer
exists to refuse.
"""
import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FLASK_DIR = ROOT / "Backend" / "Flask"
STORE = FLASK_DIR / "store.py"
APP = FLASK_DIR / "app.py"

sys.path.insert(0, str(FLASK_DIR))
from store import GcsStore  # noqa: E402

SEVEN_YEARS = 220903200


def text(path: Path) -> str:
    if not path.is_file():
        raise AssertionError("missing file: %s" % path)
    return path.read_text(encoding="utf-8-sig")


def function_source(module_path: Path, name: str, cls: str = None) -> str:
    tree = ast.parse(text(module_path))
    scope = tree
    if cls:
        scope = next((n for n in tree.body
                      if isinstance(n, ast.ClassDef) and n.name == cls), None)
        if scope is None:
            raise AssertionError("class %s not found in %s" % (cls, module_path.name))
    node = next((n for n in ast.walk(scope)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name), None)
    if node is None:
        raise AssertionError("function %s not found in %s" % (name, module_path.name))
    return ast.get_source_segment(text(module_path), node) or ""


class RetentionDisclosureTests(unittest.TestCase):

    # -- the word is earned, not assumed --------------------------------

    def test_worm_only_when_the_readback_said_locked(self):
        locked = GcsStore.render_retention(
            {"verified": True, "retention_seconds": SEVEN_YEARS, "locked": True})
        self.assertIn("WORM", locked)
        self.assertIn("LOCKED", locked)

        for info, why in (
            ({"verified": True, "retention_seconds": SEVEN_YEARS, "locked": False},
             "7-year retention that is not locked is revocable, not WORM"),
            ({"verified": True, "retention_seconds": 0, "locked": False},
             "no retention policy at all"),
            ({"verified": False, "reason": "readback failed", "locked": False},
             "a failed readback is not evidence of anything"),
            ({}, "an empty info dict"),
            (None, "never read back"),
        ):
            rendered = GcsStore.render_retention(info)
            self.assertNotIn(
                "WORM", rendered,
                "WORM must not appear for %s; got [%s]" % (why, rendered))

    def test_the_unlocked_case_says_the_policy_can_be_revoked(self):
        # This is the live legacy bucket's actual state on 2026-09-22.
        rendered = GcsStore.render_retention(
            {"verified": True, "retention_seconds": SEVEN_YEARS, "locked": False})
        self.assertIn("NOT locked", rendered)
        self.assertIn("revocable", rendered)
        self.assertIn("7.0y", rendered, "the period itself must still be stated")

    def test_never_read_back_is_said_out_loud(self):
        self.assertIn("not yet read back", GcsStore.render_retention(None))

    # -- health must not depend on GCS ----------------------------------

    def test_describe_does_not_reach_the_network(self):
        src = function_source(STORE, "describe", cls="GcsStore")
        for forbidden in ("retention_info(", "reload(", "list_blobs(", "download_as"):
            self.assertNotIn(
                forbidden, src,
                "describe() feeds /api/health; [%s] would make a health probe "
                "depend on GCS being reachable" % forbidden)
        self.assertIn("_last_retention_seen", src,
                      "describe() must render what the gate last observed")

    # -- the gate must stay uncached ------------------------------------

    def test_retention_info_reads_back_every_time(self):
        src = function_source(STORE, "retention_info", cls="GcsStore")
        self.assertIn(
            "self._audit_bucket.reload()", src,
            "retention_info is the gate's only evidence; it must read the bucket "
            "back on every call")
        self.assertNotIn(
            "if self._last_retention_seen", src,
            "a cached retention answer would let a policy removed minutes ago "
            "still read as locked")

    def test_the_gate_itself_is_unchanged_in_strictness(self):
        src = function_source(STORE, "require_locked_audit_epoch", cls="GcsStore")
        self.assertIn('info.get("locked") is True', src)
        self.assertIn("retention == AUDIT_RETENTION_SECONDS", src)
        self.assertIn("self.retention_info()", src,
                      "the gate must call retention_info, not read the cache")

    # -- the escalation ensemble is reported ----------------------------

    def test_health_reports_whether_the_au_ensemble_is_present(self):
        src = text(APP)
        self.assertIn("'au_ensemble_files_present': au_ensemble_files_present,", src)
        self.assertIn("_resolve_au_paths()", src)

    def test_au_ensemble_is_deliberately_not_part_of_degraded(self):
        # It is an escalation path, not the primary one: without it the service
        # still measures, difficult cases just stop being rescued. Whether that
        # should block a deploy is a product decision about what is claimed.
        src = function_source(APP, "health_check")
        start = src.index("degraded = ")
        condition = src[start:src.index("status = {", start)]
        self.assertNotIn(
            "au_ensemble_files_present", condition,
            "au_ensemble entered the degraded condition; that is a product "
            "decision, not a refactor -- it would block every deploy built on a "
            "machine without the two .onnx files, including a rollback")


class ScriptedRetentionStore(object):
    """Mirrors GcsStore's caching relationship: describe() renders whatever the
    last retention_info() saw. That coupling is the whole point -- it is what
    lets a stale verdict leak into a fresh response if the caller renders
    before it reads."""

    def __init__(self, script):
        self._script = list(script)
        self._last = None
        self.calls = []

    def retention_info(self):
        self.calls.append("retention_info")
        info = self._script.pop(0) if self._script else {
            "verified": False, "locked": False, "reason": "readback failed"}
        self._last = dict(info)
        return info

    def describe(self):
        self.calls.append("describe")
        return "gcs://audit-bucket/flywheel (\u7a3d\u6838\u2192gcs://audit-bucket; %s)" % (
            GcsStore.render_retention(self._last),)

    def __getattr__(self, name):
        raise AttributeError(name)


class TestHealthResponseIsSelfConsistent(unittest.TestCase):
    """One response must not state two different verdicts about one bucket.

    Reported by the review partner on 2026-09-22: pinning that describe() makes
    no network call of its own says nothing about the endpoint, which calls
    retention_info() on the very next line. describe() renders from the last
    readback, so rendering before reading prints the PREVIOUS request's verdict
    beside this request's -- `store` saying WORM while `audit_retention` says
    the readback failed, about the same bucket, in the same JSON.

    The original 8 tests all passed while that was true, which is exactly why
    this one exists.
    """

    def setUp(self):
        import api_flywheel  # noqa: F401  (ensures Backend/Flask is importable)
        import store as store_mod
        import app as app_mod
        self.store_mod = store_mod
        self.client = app_mod.app.test_client()
        self.addCleanup(store_mod.reset_store, None)

    def _health(self):
        return self.client.get("/api/health").get_json()

    def test_a_failed_readback_does_not_leave_worm_in_the_store_line(self):
        locked = {"verified": True, "bucket": "audit-bucket",
                  "retention_seconds": SEVEN_YEARS, "locked": True}
        failed = {"verified": False, "locked": False, "reason": "readback failed"}
        fake = ScriptedRetentionStore([locked, failed])
        self.store_mod.reset_store(fake)

        first = self._health()
        self.assertIn("WORM", first["store"])
        self.assertTrue(first["audit_retention"]["verified"])

        second = self._health()
        self.assertFalse(second["audit_retention"]["verified"])
        self.assertNotIn(
            "WORM", second["store"],
            "the store line still said WORM while audit_retention in the SAME "
            "response reported a failed readback -- describe() rendered the "
            "previous call's verdict")

    def test_losing_the_lock_is_visible_in_the_same_response(self):
        locked = {"verified": True, "bucket": "audit-bucket",
                  "retention_seconds": SEVEN_YEARS, "locked": True}
        unlocked = {"verified": True, "bucket": "audit-bucket",
                    "retention_seconds": SEVEN_YEARS, "locked": False}
        fake = ScriptedRetentionStore([locked, unlocked])
        self.store_mod.reset_store(fake)

        self._health()
        second = self._health()
        self.assertFalse(second["audit_retention"]["locked"])
        self.assertNotIn("WORM", second["store"])
        self.assertIn("NOT locked", second["store"])

    def test_the_endpoint_reads_before_it_renders(self):
        fake = ScriptedRetentionStore([
            {"verified": True, "bucket": "b", "retention_seconds": SEVEN_YEARS,
             "locked": True}])
        self.store_mod.reset_store(fake)
        self._health()
        self.assertEqual(
            fake.calls, ["retention_info", "describe"],
            "describe() ran before the readback, so it rendered a stale verdict")


class TestHealthClaimsMatchEvidence(unittest.TestCase):

    def test_the_au_field_says_it_only_checked_for_files(self):
        # Reported 2026-09-22: `au_ensemble: true` reads as "the ensemble
        # works", but the check is onnxruntime-importable plus two files on
        # disk via os.path.isfile. It does not load a session or run inference.
        src = text(APP)
        self.assertIn("'au_ensemble_files_present': au_ensemble_files_present,", src)
        self.assertNotIn(
            "'au_ensemble':", src,
            "the bare name claims a working ensemble; the code only stats files")

    def test_the_au_check_really_is_only_a_file_check(self):
        # If someone later makes this actually load a model, the name has to
        # change with it -- this test is the reminder.
        src = function_source(APP, "health_check")
        window = src[src.index("au_ensemble_files_present ="):]
        window = window[:window.index("degraded = ")]
        for real_inference in ("InferenceSession", "session.run", ".run("):
            self.assertNotIn(
                real_inference, window,
                "inference now happens here, so 'files_present' understates it")


if __name__ == "__main__":
    unittest.main(verbosity=2)
