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

These tests pin three things:
  * the word WORM appears only when the readback actually said locked;
  * describe() renders only the readback its caller hands it -- nothing about
    the bucket is remembered on the store between calls;
  * /api/health reads the bucket once and builds BOTH `audit_retention` and
    `store` from that one result, so one response cannot describe one bucket
    two ways.

The third is the one that broke. A version of this change kept "the last
readback seen" on the store object and had describe() render from it. The
store object is shared by every request thread in the worker (gunicorn runs
--threads 8), so another request's readback could land between this request's
read and its render. The review partner reproduced it through the real Flask
route on 2026-09-23: request A's audit_retention paired with request B's
verdict. Reordering the two calls did not help; removing the shared value did.

And one thing that must NOT change: retention_info() stays uncached. It is the
gate's only evidence, and a cached "locked" is exactly the stale yes this layer
exists to refuse.
"""
import ast
import sys
import threading
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


def method_node(module_path: Path, cls: str, name: str):
    tree = ast.parse(text(module_path))
    klass = next((n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls), None)
    if klass is None:
        raise AssertionError("class %s not found in %s" % (cls, module_path.name))
    fn = next((n for n in klass.body
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name), None)
    if fn is None:
        raise AssertionError("%s.%s not found in %s" % (cls, name, module_path.name))
    return fn


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

    def test_no_readback_is_said_out_loud(self):
        self.assertIn("not read back", GcsStore.render_retention(None))

    def test_describe_without_a_readback_makes_no_retention_claim(self):
        # Callers other than /api/health (dataset manifests, stats) pass no
        # readback. They must get "not read back", never a verdict some other
        # request happened to observe.
        st = gcs_store_with(FakeAuditBucket([LOCKED_POLICY]))
        st.retention_info()
        described = st.describe()
        self.assertIn("not read back", described)
        self.assertNotIn("WORM", described)

    # -- describe() renders what it is handed, nothing else --------------

    def test_describe_does_not_reach_the_network(self):
        # describe() adds no GCS round trip of its own. /api/health still
        # reaches GCS -- once, through retention_info() -- and says so.
        # Checked on the call graph, not the text: the docstring explains
        # retention_info() at length, and prose is not a call.
        fn = method_node(STORE, "GcsStore", "describe")
        called = {node.func.attr for node in ast.walk(fn)
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        for forbidden in ("retention_info", "reload", "list_blobs",
                          "download_as_bytes", "download_as_text", "exists"):
            self.assertNotIn(forbidden, called,
                             "describe() must not add a GCS round trip [%s]" % forbidden)
        self.assertIn("self.render_retention(retention)",
                      function_source(STORE, "describe", cls="GcsStore"),
                      "describe() must render the readback its caller passed in")

    def test_no_readback_is_remembered_on_the_store(self):
        # Structural, so a rename cannot slip past it: neither method may write
        # any attribute of the store. A value stored on `self` is shared by
        # every request thread in the worker.
        for name in ("retention_info", "describe", "render_retention"):
            fn = method_node(STORE, "GcsStore", name)
            for node in ast.walk(fn):
                targets = []
                if isinstance(node, ast.Assign):
                    targets = node.targets
                elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                    targets = [node.target]
                for t in targets:
                    for sub in ast.walk(t):
                        if (isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name)
                                and sub.value.id == "self"):
                            self.fail("GcsStore.%s writes self.%s; a readback kept on "
                                      "the store leaks between concurrent requests"
                                      % (name, sub.attr))
                if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "setattr":
                    self.fail("GcsStore.%s calls setattr" % name)
        self.assertNotIn("_last_retention_seen", text(STORE))

    # -- the gate must stay uncached ------------------------------------

    def test_retention_info_reads_back_every_time(self):
        src = function_source(STORE, "retention_info", cls="GcsStore")
        self.assertIn(
            "self._audit_bucket.reload()", src,
            "retention_info is the gate's only evidence; it must read the bucket "
            "back on every call")

    def test_retention_info_really_reads_back_on_every_call(self):
        bucket = FakeAuditBucket([LOCKED_POLICY, UNLOCKED_POLICY])
        st = gcs_store_with(bucket)
        self.assertTrue(st.retention_info()["locked"])
        self.assertFalse(st.retention_info()["locked"],
                         "the second call answered from the first call's readback")
        self.assertEqual(bucket.reloads, 2)

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


LOCKED_POLICY = {"retentionPeriod": str(SEVEN_YEARS), "isLocked": True}
UNLOCKED_POLICY = {"retentionPeriod": str(SEVEN_YEARS)}
FAIL = "fail"


class FakeAuditBucket(object):
    """Stands in for google.cloud.storage's Bucket: reload() then _properties.

    Each reload() takes the next scripted policy; FAIL raises, the way a
    permission or network error does.
    """

    def __init__(self, script):
        self._script = list(script)
        self._properties = {}
        self.reloads = 0
        self._lock = threading.Lock()

    def reload(self):
        with self._lock:
            self.reloads += 1
            step = self._script.pop(0) if self._script else FAIL
        if step == FAIL:
            raise RuntimeError("synthetic readback failure")
        self._properties = {"retentionPolicy": dict(step)}


def gcs_store_with(bucket, cls=GcsStore):
    """A real GcsStore -- its own retention_info, describe and render_retention
    -- without constructing a storage client. Only the four attributes those
    methods read are set."""
    st = cls.__new__(cls)
    st._bucket_name = "main-bucket"
    st.prefix = "flywheel"
    st._audit_bucket_name = "audit-bucket"
    st._audit_bucket = bucket
    return st


class HealthRouteBase(unittest.TestCase):

    def setUp(self):
        import api_flywheel  # noqa: F401  (ensures Backend/Flask is importable)
        import store as store_mod
        import app as app_mod
        self.store_mod = store_mod
        self.app = app_mod.app
        self.client = app_mod.app.test_client()
        self.addCleanup(store_mod.reset_store, None)

    def _health(self, client=None):
        return (client or self.client).get("/api/health").get_json()

    def assertOneVerdict(self, body):
        # The invariant itself: the store line renders exactly the readback
        # that audit_retention reports, in the same response.
        self.assertTrue(
            body["store"].endswith("; %s)" % GcsStore.render_retention(body["audit_retention"])),
            "store [%s] does not render the audit_retention in the same "
            "response [%s]" % (body["store"], body["audit_retention"]))


class TestHealthResponseIsSelfConsistent(HealthRouteBase):
    """One response must not state two different verdicts about one bucket."""

    def test_a_failed_readback_does_not_leave_worm_in_the_store_line(self):
        self.store_mod.reset_store(gcs_store_with(FakeAuditBucket([LOCKED_POLICY, FAIL])))

        first = self._health()
        self.assertIn("WORM", first["store"])
        self.assertTrue(first["audit_retention"]["verified"])
        self.assertOneVerdict(first)

        second = self._health()
        self.assertFalse(second["audit_retention"]["verified"])
        self.assertNotIn(
            "WORM", second["store"],
            "the store line still said WORM while audit_retention in the SAME "
            "response reported a failed readback")
        self.assertOneVerdict(second)

    def test_losing_the_lock_is_visible_in_the_same_response(self):
        self.store_mod.reset_store(
            gcs_store_with(FakeAuditBucket([LOCKED_POLICY, UNLOCKED_POLICY])))
        self._health()
        second = self._health()
        self.assertFalse(second["audit_retention"]["locked"])
        self.assertNotIn("WORM", second["store"])
        self.assertIn("NOT locked", second["store"])
        self.assertOneVerdict(second)

    def test_one_request_reads_the_bucket_exactly_once(self):
        # Both fields from one readback: a second read inside the same request
        # could disagree with the first.
        bucket = FakeAuditBucket([LOCKED_POLICY, UNLOCKED_POLICY])
        self.store_mod.reset_store(gcs_store_with(bucket))
        body = self._health()
        self.assertEqual(bucket.reloads, 1)
        self.assertOneVerdict(body)

    def test_a_readback_landing_between_read_and_render_cannot_leak_in(self):
        """Deterministic form of the review partner's reproduction.

        Another request's readback is forced to land after this request's
        retention_info() and before its describe(). With the value kept on the
        store, this response carried LOCKED in audit_retention and "retention
        unknown (readback failed)" in store.
        """
        class OtherRequestInBetween(GcsStore):
            def describe(self, *args, **kwargs):
                self.retention_info()        # request B reads: the bucket now fails
                return GcsStore.describe(self, *args, **kwargs)

        st = gcs_store_with(FakeAuditBucket([LOCKED_POLICY, FAIL]), cls=OtherRequestInBetween)
        self.store_mod.reset_store(st)
        body = self._health()
        self.assertTrue(body["audit_retention"]["locked"])
        self.assertIn("LOCKED", body["store"],
                      "request B's readback leaked into request A's store line")
        self.assertOneVerdict(body)

    def test_two_concurrent_requests_each_describe_their_own_readback(self):
        """The same interleaving on two real threads through the Flask route.

        A reads (LOCKED) and then waits inside describe() until B has read
        (failed) and finished. Every wait has a timeout, so a regression shows
        up as a failed assertion, not a hung suite.
        """
        a_read, b_done = threading.Event(), threading.Event()

        class Gated(GcsStore):
            def retention_info(self):
                info = GcsStore.retention_info(self)
                if threading.current_thread().name == "request-A":
                    a_read.set()
                return info

            def describe(self, *args, **kwargs):
                if threading.current_thread().name == "request-A":
                    b_done.wait(10)
                return GcsStore.describe(self, *args, **kwargs)

        self.store_mod.reset_store(
            gcs_store_with(FakeAuditBucket([LOCKED_POLICY, FAIL]), cls=Gated))
        bodies, errors = {}, []

        def run(name):
            try:
                bodies[name] = self._health(self.app.test_client())
            except Exception as exc:  # surfaced below, not swallowed
                errors.append("%s: %r" % (name, exc))
            finally:
                if name == "request-B":
                    b_done.set()

        a = threading.Thread(target=run, args=("request-A",), name="request-A")
        a.start()
        self.assertTrue(a_read.wait(10), "request A never read the bucket")
        b = threading.Thread(target=run, args=("request-B",), name="request-B")
        b.start()
        b.join(15)
        a.join(15)
        self.assertEqual(errors, [])
        self.assertEqual(sorted(bodies), ["request-A", "request-B"])
        self.assertTrue(bodies["request-A"]["audit_retention"]["locked"])
        self.assertFalse(bodies["request-B"]["audit_retention"]["verified"])
        for name, body in bodies.items():
            with self.subTest(request=name):
                self.assertOneVerdict(body)


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
