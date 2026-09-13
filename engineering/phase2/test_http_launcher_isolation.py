#!/usr/bin/env python3
"""Exercise the environment shared by both local HTTP test children."""
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "windows"))
import run_backend_http_test as launcher


class HttpLauncherIsolationTests(unittest.TestCase):
    def test_contaminated_parent_cannot_select_cloud_or_reuse_secrets(self):
        source = {"PATH": "keep", "WOUNDAI_STORE": "gcs",
                  "WOUNDAI_GCS_BUCKET": "synthetic-never-contact",
                  "WOUNDAI_AUDIT_BUCKET": "synthetic-never-contact-audit",
                  "WOUNDAI_GCS_PREFIX": "production",
                  "ADMIN_PASSWORD": "parent-value", "JWT_SECRET_KEY": "parent-value",
                  "GOOGLE_APPLICATION_CREDENTIALS": "operator-adc.json",
                  "CLOUDSDK_CONFIG": "operator-gcloud", "GH_TOKEN": "operator-token",
                  "AWS_SECRET_ACCESS_KEY": "operator-aws", "HTTPS_PROXY": "operator-proxy",
                  "USERPROFILE": "operator-profile", "APPDATA": "operator-appdata"}
        original = dict(source)
        with tempfile.TemporaryDirectory() as tmp:
            env = launcher.build_environment(source, Path(tmp), "a" * 32)
            self.assertEqual(env["WOUNDAI_STORE"], "local")
            self.assertEqual(env["WOUNDAI_REQUIRE_FUNCTIONAL_TESTS"], "1")
            self.assertEqual(env["WOUNDAI_FLYWHEEL_DIR"], str(Path(tmp) / "flywheel"))
            self.assertEqual(env["WOUNDAI_RUNTIME_DIR"], str(Path(tmp)))
            self.assertEqual(env["WOUNDAI_LOCAL_TEST_RUN"], "a" * 32)
            self.assertNotIn("WOUNDAI_GCS_BUCKET", env)
            self.assertNotIn("WOUNDAI_GCS_PREFIX", env)
            self.assertNotIn("WOUNDAI_AUDIT_BUCKET", env)
            for key in ("GOOGLE_APPLICATION_CREDENTIALS", "GH_TOKEN",
                        "AWS_SECRET_ACCESS_KEY", "HTTPS_PROXY"):
                self.assertNotIn(key, env)
            for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
                        "CLOUDSDK_CONFIG", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"):
                self.assertTrue(Path(env[key]).resolve().is_relative_to(Path(tmp).resolve()),
                                (key, env[key]))
            for key in ("ADMIN_PASSWORD", "JWT_SECRET_KEY", "FLASK_SECRET_KEY", "CARE_RECEIPT_SECRET"):
                self.assertGreaterEqual(len(env[key]), 32)
                self.assertNotEqual(env[key], "parent-value")
            self.assertEqual(env["ADMIN_PASSWORD"], env["WOUNDAI_HTTP_TEST_PASSWORD"])
            server_env = launcher._server_environment(env)
            client_env = launcher._client_environment(env)
            self.assertNotIn("WOUNDAI_HTTP_TEST_PASSWORD", server_env)
            self.assertIn("WOUNDAI_HTTP_TEST_PASSWORD", client_env)
            for key in launcher._SERVER_ONLY_SECRETS:
                self.assertNotIn(key, client_env)
        self.assertEqual(source, original)

    def test_care_keyring_material_is_valid_base64url(self):
        sys.path.insert(0, str(ROOT / "Backend" / "Flask"))
        from consent_staging import CareKeys
        with tempfile.TemporaryDirectory() as tmp:
            env = launcher.build_environment({}, Path(tmp), "c" * 32)
            keys = CareKeys(json.loads(env["CARE_RECEIPT_SECRET"]))
            self.assertEqual(keys.active, "http-synthetic")
            self.assertEqual(len(keys.keys[keys.active]), 48)

    def test_client_argv_never_contains_password(self):
        command = launcher._client_command("http://127.0.0.1:54321", "d" * 32)
        self.assertNotIn("--pw", command)
        self.assertNotIn("visible-on-process-list", " ".join(command))

    def test_redaction_is_narrow_and_preserves_evidence(self):
        source = "HTTP 401 from 127.0.0.1 local; Bearer abc.def; secret-value"
        clean = launcher._redact(source, ["secret-value"])
        self.assertIn("HTTP 401", clean)
        self.assertIn("127.0.0.1", clean)
        self.assertIn(" local", clean)
        self.assertNotIn("abc.def", clean)
        self.assertNotIn("secret-value", clean)

    def test_new_run_replaces_stale_pass_with_in_progress_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / "http-summary.json").write_text(
                json.dumps({"passed": True, "run_id": "old"}), encoding="utf-8")
            first, _ = launcher._start_run(out, "e" * 32)
            current = json.loads((out / "http-summary.json").read_text(encoding="utf-8"))
            self.assertFalse(current["passed"])
            self.assertEqual(current["state"], "running")
            self.assertEqual(current["run_id"], "e" * 32)
            second, _ = launcher._start_run(out, "f" * 32)
            self.assertNotEqual(first, second)
            self.assertTrue((first / "http-summary.json").is_file())

    def test_cleanup_failure_forces_failed_result_without_hiding_primary(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp) / "still-present"
            runtime.mkdir()
            result = {"passed": True, "error_type": "PrimaryFailure"}
            self.assertFalse(launcher._record_cleanup(result, runtime))
            self.assertFalse(result["passed"])
            self.assertEqual(result["error_type"], "PrimaryFailure")
            self.assertIn("cleanup_error", result)

    def test_source_change_forces_failed_evidence(self):
        result = {"passed": True}
        self.assertFalse(launcher._record_source_comparison(
            result, {"head": "a", "tracked_diff_sha256": "1"},
            {"head": "a", "tracked_diff_sha256": "2"}))
        self.assertFalse(result["passed"])
        self.assertFalse(result["source_unchanged"])
        self.assertEqual(result["error_type"], "SourceChangedError")

    def test_timeout_drains_output_and_cleanup_exception_does_not_escape(self):
        process = Mock(pid=12345, returncode=None)
        process.communicate.side_effect = [
            subprocess.TimeoutExpired(["fixed-command"], 1),
            ("current stdout", "current stderr"),
        ]
        with patch.object(launcher, "stop_child_tree", return_value=True) as stop:
            stdout, stderr, timed_out = launcher._communicate_client(process, 1)
        self.assertTrue(timed_out)
        self.assertEqual(stdout, "current stdout")
        self.assertEqual(stderr, "current stderr")
        stop.assert_called_once_with(process, worker_pid=None)

    def test_windows_tree_cleanup_uses_worker_and_launcher_and_never_throws(self):
        process = Mock(pid=11111)
        process.poll.side_effect = [None, None, None]
        process.wait.side_effect = subprocess.TimeoutExpired(["wait"], 10)
        process.kill.return_value = None
        with patch.object(launcher.os, "name", "nt"), \
                patch.object(launcher.subprocess, "run",
                             side_effect=subprocess.TimeoutExpired(["taskkill"], 15)), \
                patch.object(launcher.os, "kill", side_effect=ProcessLookupError):
            result = launcher.stop_child_tree(process, worker_pid=22222)
        self.assertFalse(result)
        process.kill.assert_called_once()

    def test_server_refuses_cloud_before_importing_application(self):
        with patch.dict(os.environ, {"WOUNDAI_STORE": "gcs",
                                     "WOUNDAI_REQUIRE_FUNCTIONAL_TESTS": "1"}):
            with self.assertRaisesRegex(RuntimeError, "isolated LocalStore"):
                launcher.serve(Path("not-created.json"))

    def test_server_refuses_missing_instance_identity(self):
        with patch.dict(os.environ, {"WOUNDAI_STORE": "local",
                                     "WOUNDAI_REQUIRE_FUNCTIONAL_TESTS": "1",
                                     "WOUNDAI_LOCAL_TEST_RUN": ""}):
            with self.assertRaisesRegex(RuntimeError, "run identity"):
                launcher.serve(Path("not-created.json"))

    def test_application_mutable_paths_stay_under_runtime_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            runtime, working = base / "runtime", base / "working"
            runtime.mkdir(); working.mkdir()
            env = launcher.build_environment(os.environ, runtime, "b" * 32)
            probe = (
                "import json; from app import app; "
                "print('RUNTIME_PATHS=' + json.dumps({"
                "'upload':app.config['UPLOAD_FOLDER'],"
                "'processed':app.config['PROCESSED_FOLDER'],"
                "'database':app.config['DATABASE']}))"
            )
            result = subprocess.run(
                [sys.executable, "-B", "-c", probe], cwd=working, env=env,
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=90,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            line = next(line for line in result.stdout.splitlines()
                        if line.startswith("RUNTIME_PATHS="))
            paths = json.loads(line.split("=", 1)[1])
            self.assertEqual(paths, {
                "upload": str(runtime / "uploads"),
                "processed": str(runtime / "processed"),
                "database": str(runtime / "wound_analysis.db"),
            })
            self.assertTrue((runtime / "logs" / "wound_analysis.log").is_file())
            self.assertEqual(list(working.iterdir()), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
