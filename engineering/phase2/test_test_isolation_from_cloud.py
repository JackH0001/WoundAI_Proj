# -*- coding: utf-8 -*-
"""Test isolation from cloud storage using fake SDKs.

The runner removes inherited routing; the store rejects recognized test
entrypoints, direct GcsStore construction and previously cached cloud clients.
These checks prevent accidental access, not arbitrary code importing another
network client. No test in this file discovers ADC or contacts Google.

    python engineering/phase2/test_test_isolation_from_cloud.py
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "tools" / "windows" / "run_python_tests.py"

# Install before importing store: even a broken guard cannot reach real ADC.
FAKE_SDK = """
import os, sys, types, json
calls = []
class FakeClient:
    def __init__(self):
        calls.append(['client'])
    def bucket(self, name):
        calls.append(['bucket', name])
        return types.SimpleNamespace(name=name)
google = types.ModuleType('google')
cloud = types.ModuleType('google.cloud')
storage = types.ModuleType('google.cloud.storage')
storage.Client = FakeClient
google.cloud = cloud
cloud.storage = storage
sys.modules.update({'google': google, 'google.cloud': cloud,
                    'google.cloud.storage': storage})
import store
"""


def load_runner():
    spec = importlib.util.spec_from_file_location("isolation_runner", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestIsolationFromCloudStorage(unittest.TestCase):
    def probe(self, code, *, env_update=None, script_name=None):
        env = dict(os.environ)
        for key in list(env):
            upper = key.upper()
            if upper.startswith("WOUNDAI_GCS_") or upper in {
                "WOUNDAI_STORE", "WOUNDAI_AUDIT_BUCKET",
                "WOUNDAI_REQUIRE_FUNCTIONAL_TESTS", "PYTEST_CURRENT_TEST",
            }:
                env.pop(key)
        env.update({
            "PYTHONPATH": str(ROOT / "Backend" / "Flask"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "WOUNDAI_STORE": "gcs", "WOUNDAI_GCS_BUCKET": "synthetic-main",
            "WOUNDAI_GCS_PREFIX": "synthetic-prefix",
            "WOUNDAI_AUDIT_BUCKET": "synthetic-audit",
        })
        env.update(env_update or {})
        program = FAKE_SDK + "\n" + textwrap.dedent(code)
        with tempfile.TemporaryDirectory() as tmp:
            env["WOUNDAI_FLYWHEEL_DIR"] = str(Path(tmp) / "local")
            if script_name:
                script = Path(tmp) / script_name
                script.write_text(program, encoding="utf-8")
                command = [sys.executable, "-B", str(script)]
            else:
                command = [sys.executable, "-B", "-c", program]
            proc = subprocess.run(command, env=env, capture_output=True,
                                  text=True, encoding="utf-8", timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return json.loads(proc.stdout.strip())

    def assert_blocked(self, expression, *, before="", **kwargs):
        out = self.probe(before + "\n" + textwrap.dedent("""
            try:
                EXPRESSION
            except RuntimeError as exc:
                print(json.dumps({'blocked': str(exc), 'calls': calls}))
            else:
                print(json.dumps({'blocked': False, 'calls': calls}))
        """).replace("EXPRESSION", expression), **kwargs)
        self.assertIn("refused inside a test process", str(out["blocked"]))
        return out

    def test_sanitizer_removes_all_cloud_routing_without_mutating_parent(self):
        runner = load_runner()
        contaminated = {
            "WOUNDAI_STORE": "gcs", "WOUNDAI_GCS_BUCKET": "synthetic-main",
            "WOUNDAI_GCS_PREFIX": "synthetic-prefix",
            "WOUNDAI_GCS_FUTURE_SETTING": "synthetic",
            "WOUNDAI_AUDIT_BUCKET": "synthetic-audit",
            "WOUNDAI_REQUIRE_FUNCTIONAL_TESTS": "0",
            "GOOGLE_APPLICATION_CREDENTIALS": "operator-adc.json",
            "CLOUDSDK_CONFIG": "operator-gcloud", "HTTPS_PROXY": "operator-proxy",
            "ADMIN_PASSWORD": "operator-secret",
            "WOUNDAI_HTTP_TEST_PASSWORD": "operator-http-secret",
            "WOUNDAI_API_TOKEN": "operator-api-token",
            "WOUNDAI_PW": "operator-export-password",
            "LITE_IP_SALT": "operator-lite-salt", "PATH": "keep-me",
        }
        original = dict(contaminated)
        self.assertEqual(runner.sanitized_test_environment(contaminated), {
            "PATH": "keep-me", "WOUNDAI_REQUIRE_FUNCTIONAL_TESTS": "1"})
        self.assertEqual(contaminated, original)

    def test_sanitizer_handles_windows_mixed_case(self):
        self.assertEqual(load_runner().sanitized_test_environment({
            "woundai_store": "gcs", "WoundAI_Gcs_Bucket": "synthetic-main",
            "woundai_audit_bucket": "synthetic-audit",
            "woundai_require_functional_tests": "0",
            "WoundAI_Http_Test_Password": "operator-http-secret",
            "WoundAI_Api_Token": "operator-api-token",
            "WoundAI_Pw": "operator-export-password",
            "Lite_Ip_Salt": "operator-lite-salt",
        }), {"WOUNDAI_REQUIRE_FUNCTIONAL_TESTS": "1"})

    def test_runner_passes_sanitized_environment_to_actual_spawn_boundary(self):
        runner = load_runner()
        captured = []
        class FakeProcess:
            returncode = 0
            def __init__(self, command, **kwargs):
                captured.append(dict(kwargs["env"]))
            def communicate(self, timeout=None):
                return "synthetic pass\n", None
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            test = repo / "engineering" / "phase2" / "test_synthetic.py"
            test.parent.mkdir(parents=True)
            test.write_text("if __name__ == '__main__':\n    pass\n", encoding="utf-8")
            with mock.patch.object(sys, "argv", [str(RUNNER), "--repo", str(repo),
                    "--out", str(Path(tmp) / "out")]), \
                    mock.patch.dict(os.environ, {
                        "WOUNDAI_STORE": "gcs", "WOUNDAI_GCS_BUCKET": "synthetic-main",
                        "WOUNDAI_AUDIT_BUCKET": "synthetic-audit"}), \
                    mock.patch.object(runner, "source_snapshot", return_value={}), \
                    mock.patch.object(runner.subprocess, "Popen", FakeProcess):
                self.assertEqual(runner.main(), 0)
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]["WOUNDAI_REQUIRE_FUNCTIONAL_TESTS"], "1")
        self.assertIn("WOUNDAI_RUNTIME_DIR", captured[0])
        self.assertIn("WOUNDAI_FLYWHEEL_DIR", captured[0])
        self.assertIn("no-google-credentials.json",
                      captured[0]["GOOGLE_APPLICATION_CREDENTIALS"])
        self.assertFalse(any(k == "WOUNDAI_STORE" or k.startswith("WOUNDAI_GCS_")
                             or k == "WOUNDAI_AUDIT_BUCKET" for k in captured[0]))

    def test_source_snapshot_covers_non_source_extension_and_tracked_diff(self):
        runner = load_runner()
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            def git(*args):
                subprocess.run(["git", "-C", str(repo), *args], check=True,
                               capture_output=True)
            git("init")
            git("config", "user.name", "Synthetic Test")
            git("config", "user.email", "synthetic@example.invalid")
            (repo / "base.txt").write_text("base\n", encoding="utf-8")
            git("add", "base.txt")
            git("commit", "-m", "synthetic base")
            before = runner.source_snapshot(repo)
            (repo / "policy.json").write_text('{"gate":true}\n', encoding="utf-8")
            with_untracked = runner.source_snapshot(repo)
            self.assertNotEqual(before, with_untracked)
            self.assertIn("policy.json", with_untracked["untracked_sha256"])
            (repo / "base.txt").write_text("changed\n", encoding="utf-8")
            with_diff = runner.source_snapshot(repo)
            self.assertNotEqual(with_untracked["tracked_diff_sha256"],
                                with_diff["tracked_diff_sha256"])

    def test_runner_timeout_is_recorded_and_invokes_tree_cleanup(self):
        runner = load_runner()
        cleaned = []
        class TimeoutProcess:
            returncode = None
            def __init__(self, command, **kwargs):
                self.calls = 0
            def communicate(self, timeout=None):
                self.calls += 1
                if self.calls == 1:
                    raise subprocess.TimeoutExpired(["synthetic-test"], timeout)
                return "partial output\n", None
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            test = repo / "engineering" / "phase2" / "test_synthetic.py"
            test.parent.mkdir(parents=True)
            test.write_text("if __name__ == '__main__':\n    pass\n", encoding="utf-8")
            out = Path(tmp) / "out"
            with mock.patch.object(sys, "argv", [str(RUNNER), "--repo", str(repo),
                    "--out", str(out), "--timeout", "1"]), \
                    mock.patch.object(runner, "source_snapshot", return_value={}), \
                    mock.patch.object(runner.subprocess, "Popen", TimeoutProcess), \
                    mock.patch.object(runner, "stop_process_tree",
                                      side_effect=lambda process: cleaned.append(process)):
                self.assertEqual(runner.main(), 1)
            summary = json.loads((out / "python-summary.json").read_text(encoding="utf-8"))
            self.assertEqual(len(cleaned), 1)
            self.assertEqual(summary["results"][0]["status"], "timeout")
            self.assertEqual(summary["results"][0]["exit_code"], 124)

    def test_runner_invalidates_a_stale_pass_before_snapshot_or_spawn(self):
        runner = load_runner()
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            test = repo / "engineering" / "phase2" / "test_synthetic.py"
            test.parent.mkdir(parents=True)
            test.write_text("if __name__ == '__main__':\n    pass\n", encoding="utf-8")
            out = Path(tmp) / "out"
            out.mkdir()
            (out / "python-summary.json").write_text(
                json.dumps({"state": "passed", "run_id": "stale"}), encoding="utf-8")
            with mock.patch.object(sys, "argv", [str(RUNNER), "--repo", str(repo),
                    "--out", str(out)]), \
                    mock.patch.object(runner, "source_snapshot",
                                      side_effect=RuntimeError("synthetic snapshot failure")):
                with self.assertRaisesRegex(RuntimeError, "synthetic snapshot failure"):
                    runner.main()
            latest = json.loads((out / "python-summary.json").read_text(encoding="utf-8"))
            self.assertEqual(latest["state"], "running")
            self.assertNotEqual(latest["run_id"], "stale")

    def test_runner_can_reuse_output_root_without_reusing_a_run_directory(self):
        runner = load_runner()
        class FakeProcess:
            returncode = 0
            def __init__(self, command, **kwargs):
                pass
            def communicate(self, timeout=None):
                return "synthetic pass\n", None
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            test = repo / "engineering" / "phase2" / "test_synthetic.py"
            test.parent.mkdir(parents=True)
            test.write_text("if __name__ == '__main__':\n    pass\n", encoding="utf-8")
            out = Path(tmp) / "out"
            with mock.patch.object(sys, "argv", [str(RUNNER), "--repo", str(repo),
                    "--out", str(out)]), \
                    mock.patch.object(runner, "source_snapshot", return_value={}), \
                    mock.patch.object(runner.subprocess, "Popen", FakeProcess):
                self.assertEqual(runner.main(), 0)
                first = json.loads((out / "python-summary.json").read_text(encoding="utf-8"))
                self.assertEqual(runner.main(), 0)
                second = json.loads((out / "python-summary.json").read_text(encoding="utf-8"))
            self.assertEqual(first["state"], "passed")
            self.assertEqual(second["state"], "passed")
            self.assertNotEqual(first["run_id"], second["run_id"])
            self.assertTrue((out / first["run_directory"] / "python-summary.json").is_file())
            self.assertTrue((out / second["run_directory"] / "python-summary.json").is_file())
            self.assertEqual(len([p for p in out.iterdir() if p.is_dir()]), 2)

    def test_runner_refuses_unignored_output_inside_the_repository_before_writing(self):
        runner = load_runner()
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            subprocess.run(["git", "-C", str(repo), "init"], check=True,
                           capture_output=True)
            out = repo / "validation-evidence"
            with mock.patch.object(sys, "argv", [str(RUNNER), "--repo", str(repo),
                    "--out", str(out)]):
                with self.assertRaisesRegex(RuntimeError, "must be wholly git-ignored"):
                    runner.main()
            self.assertFalse(out.exists())

    def test_runner_accepts_an_untracked_repo_output_root_ignored_as_a_directory(self):
        runner = load_runner()
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            subprocess.run(["git", "-C", str(repo), "init"], check=True,
                           capture_output=True)
            (repo / ".gitignore").write_text("/artifacts/\n", encoding="utf-8")
            out = repo / "artifacts" / "validation"
            runner.ensure_output_root_safe(repo.resolve(), out.resolve())
            self.assertFalse(out.exists())

    def test_marked_factory_refuses_before_sdk_construction(self):
        out = self.assert_blocked("store.get_store()", env_update={
            "WOUNDAI_REQUIRE_FUNCTIONAL_TESTS": "1"})
        self.assertEqual(out["calls"], [])

    def test_marked_direct_constructor_refuses_before_sdk_construction(self):
        out = self.assert_blocked("store.GcsStore('synthetic-main')", env_update={
            "WOUNDAI_REQUIRE_FUNCTIONAL_TESTS": "1"})
        self.assertEqual(out["calls"], [])

    def test_direct_test_file_without_runner_marker_is_refused(self):
        out = self.assert_blocked("store.get_store()", script_name="test_synthetic.py")
        self.assertEqual(out["calls"], [])

    def test_pytest_entrypoint_without_runner_marker_is_refused(self):
        for entry in ("pytest", "pytest.exe", "/venv/bin/py.test",
                      "/venv/lib/pytest/__main__.py"):
            with self.subTest(entry=entry):
                out = self.assert_blocked("store.get_store()", before=
                                          "sys.argv[0] = " + repr(entry))
                self.assertEqual(out["calls"], [])

    def test_module_test_entrypoints_are_refused_before_collection(self):
        for module in ("pytest.__main__", "unittest.__main__"):
            with self.subTest(module=module):
                out = self.assert_blocked("store.get_store()", before=
                    "sys.modules['__main__'].__spec__ = types.SimpleNamespace(name="
                    + repr(module) + ")")
                self.assertEqual(out["calls"], [])

    def test_programmatic_pytest_active_test_is_refused(self):
        out = self.assert_blocked("store.get_store()", env_update={
            "PYTEST_CURRENT_TEST": "test_synthetic.py::test_example (call)"})
        self.assertEqual(out["calls"], [])

    def test_cached_cloud_client_is_refused_when_tests_start(self):
        out = self.assert_blocked("store.get_store()", before="""
s = store.get_store()
os.environ['WOUNDAI_REQUIRE_FUNCTIONAL_TESTS'] = '1'
""")
        self.assertEqual(out["calls"], [
            ["client"], ["bucket", "synthetic-main"], ["bucket", "synthetic-audit"]])

    def test_cached_cloud_client_still_refused_after_environment_changes_to_local(self):
        self.assert_blocked("store.get_store()", before="""
s = store.get_store()
os.environ['WOUNDAI_REQUIRE_FUNCTIONAL_TESTS'] = '1'
os.environ['WOUNDAI_STORE'] = 'local'
""")

    def test_production_factory_works_with_exact_fake_sdk_parameters(self):
        out = self.probe("""
            # Importing testing libraries alone must not disable production.
            import unittest
            sys.modules['pytest'] = types.ModuleType('pytest')
            s = store.get_store()
            print(json.dumps({'kind': type(s).__name__, 'prefix': s.prefix,
                              'calls': calls, 'same': store.get_store() is s}))
        """)
        self.assertEqual(out, {"kind": "GcsStore", "prefix": "synthetic-prefix",
            "same": True, "calls": [["client"], ["bucket", "synthetic-main"],
                                       ["bucket", "synthetic-audit"]]})

    def test_local_store_remains_available_to_direct_tests(self):
        out = self.probe("""
            s = store.get_store()
            print(json.dumps({'kind': type(s).__name__, 'calls': calls}))
        """, script_name="test_synthetic.py", env_update={"WOUNDAI_STORE": "local"})
        self.assertEqual(out, {"kind": "LocalStore", "calls": []})

    def test_explicit_in_memory_double_remains_available(self):
        out = self.probe("""
            fake = store.Store()
            store.reset_store(fake)
            print(json.dumps({'same': store.get_store() is fake, 'calls': calls}))
        """, env_update={"WOUNDAI_STORE": "local", "WOUNDAI_REQUIRE_FUNCTIONAL_TESTS": "1"})
        self.assertEqual(out, {"same": True, "calls": []})

    def test_retired_epoch_is_readable_but_refused_for_write_admission(self):
        # The exact incident bucket name is tested only on a __new__ fake.
        out = self.probe("""
            g = store.GcsStore.__new__(store.GcsStore)
            g._audit_bucket_name = 'woundai-flywheel-jackh001-audit-epoch-20260905'
            g._audit_bucket = types.SimpleNamespace(
                reload=lambda: calls.append(['reload']), _properties={
                    'retentionPolicy': {'retentionPeriod': '220903200', 'isLocked': True}})
            info = g.retention_info()
            try:
                g.require_locked_audit_epoch()
            except PermissionError as exc:
                print(json.dumps({'read_verified': info['verified'], 'error': str(exc),
                                  'calls': calls}))
            else:
                print(json.dumps({'error': False}))
        """)
        self.assertTrue(out["read_verified"])
        self.assertIn("retired audit epoch is read-only", out["error"])
        self.assertIn("INCIDENT_TEST_ENV_LEAK_20260906.json", out["error"])
        self.assertEqual(out["calls"], [["reload"]])

    def test_other_locked_epoch_still_passes_write_admission(self):
        out = self.probe("""
            g = store.GcsStore.__new__(store.GcsStore)
            g._audit_bucket_name = 'synthetic-active-epoch'
            g._audit_bucket = types.SimpleNamespace(
                reload=lambda: calls.append(['reload']), _properties={
                    'retentionPolicy': {'retentionPeriod': '220903200', 'isLocked': True}})
            g.require_locked_audit_epoch()
            print(json.dumps({'admitted': True, 'calls': calls}))
        """)
        self.assertEqual(out, {"admitted": True, "calls": [["reload"]]})


if __name__ == "__main__":
    unittest.main(verbosity=2)
