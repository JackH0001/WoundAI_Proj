"""Offline checks: the HTTP harness cannot wander into remote storage."""
from contextlib import redirect_stderr
import io
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_backend_http as harness

RUN_ID = "1234567890abcdef" * 2


class HarnessSafetyTests(unittest.TestCase):
    def test_remote_ambiguous_and_credentialed_origins_are_rejected(self):
        bad = ["https://example.org", "http://example.org:5000", "http://localhost:5000",
               "http://127.0.0.1.example.org:5000", "http://2130706433:5000",
               "http://127.1:5000", "http://127.0.0.1:5000/x",
               "http://user:pw@127.0.0.1:5000", "http://127.0.0.1:5000?next=https://evil",
               "http://127.0.0.1", "http://[::ffff:127.0.0.1]:5000"]
        for url in bad:
            with self.subTest(url=url), self.assertRaises(harness.TestRefused):
                harness.LocalTestClient(url, RUN_ID)
        self.assertEqual(harness.loopback_origin("http://127.0.0.1:5000/"), "http://127.0.0.1:5000")
        self.assertEqual(harness.loopback_origin("http://[::1]:5000"), "http://[::1]:5000")

    def test_credentials_cannot_leave_before_server_marker(self):
        client = harness.LocalTestClient("http://127.0.0.1:5000", RUN_ID)
        client.session.request = Mock()
        with self.assertRaises(harness.TestRefused):
            client.request("POST", "/api/auth/login", json={"password": "synthetic"})
        client.session.request.assert_not_called()

    def test_redirect_and_missing_wrong_marker_fail_without_following(self):
        for status, marker in [(302, RUN_ID), (200, None), (200, "0" * 32)]:
            client = harness.LocalTestClient("http://127.0.0.1:5000", RUN_ID)
            response = Mock(status_code=status, history=[], headers={"X-WoundAI-Local-Test-Run": marker})
            client.session.request = Mock(return_value=response)
            with self.assertRaises(harness.TestRefused):
                client.verify_server()
            self.assertFalse(client.verified)
            self.assertFalse(client.session.trust_env)
            self.assertFalse(client.session.request.call_args.kwargs["allow_redirects"])

    def test_marker_is_checked_on_every_response(self):
        client = harness.LocalTestClient("http://127.0.0.1:5000", RUN_ID)
        response = Mock(status_code=200, history=[], headers={"X-WoundAI-Local-Test-Run": RUN_ID})
        client.session.request = Mock(return_value=response)
        client.verify_server()
        self.assertTrue(client.verified)
        response.headers = {}
        with self.assertRaises(harness.TestRefused):
            client.request("GET", "/api/v1/flywheel/stats")

    def test_synthetic_is_required_and_arbitrary_image_not_accepted(self):
        args = ["--url", "http://127.0.0.1:5000", "--test-run-id", RUN_ID]
        with patch.dict(os.environ, {"WOUNDAI_HTTP_TEST_PASSWORD": "s" * 32}), \
                patch.object(harness.requests.Session, "request") as request:
            for argv in (args, args + ["--synthetic", "--img", "patient.jpg"]):
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
                    harness.main(argv)
                self.assertEqual(exc.exception.code, 2)
            request.assert_not_called()

    def test_password_is_child_environment_only_and_old_cli_flag_is_rejected(self):
        args = ["--url", "http://127.0.0.1:5000", "--test-run-id", RUN_ID,
                "--synthetic"]
        clean = dict(os.environ)
        clean.pop("WOUNDAI_HTTP_TEST_PASSWORD", None)
        with patch.dict(os.environ, clean, clear=True), self.assertRaisesRegex(
                harness.TestRefused, "child-only synthetic HTTP password"):
            harness.main(args)
        with patch.dict(os.environ, {"WOUNDAI_HTTP_TEST_PASSWORD": "s" * 32}), \
                redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
            harness.main(args + ["--pw", "visible-on-process-list"])
        self.assertEqual(exc.exception.code, 2)

    def test_generated_pixels_are_unique_strict_canonical_jpeg(self):
        first = harness.synthetic_jpeg(RUN_ID)
        second = harness.synthetic_jpeg("abcdef0123456789" * 2)
        self.assertNotEqual(first, second)
        self.assertEqual(first[:2], b"\xff\xd8")
        self.assertEqual(first[-2:], b"\xff\xd9")
        from image_canonical import canonicalize, InvalidImage
        self.assertEqual(canonicalize(first).data, first)
        with self.assertRaises(InvalidImage):
            canonicalize(first + b"old-test-tail")


if __name__ == "__main__":
    unittest.main()
