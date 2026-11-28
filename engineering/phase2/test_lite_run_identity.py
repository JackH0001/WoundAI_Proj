"""Verify attached identity from fixed metadata fields, not configured SDK values."""
import os
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from unittest import TestCase
from unittest.mock import patch

import lite_run_identity as identity
from lite_key_startup import CloudProviders as KeyCloud
from lite_native_startup import CloudProviders as NativeCloud


class AttachedIdentityTests(TestCase):
    def setUp(self):
        self.deployment = dict(project="synthetic-only", project_number="123456789",
                               service_account="native@synthetic-only.iam.gserviceaccount.com")
        self.fields = {
            "instance/service-accounts/default/email": self.deployment["service_account"],
            "project/project-id": "synthetic-only",
            "project/numeric-project-id": "123456789",
            "instance/region": "projects/123456789/regions/asia-east1",
        }
        self.calls = []
        self.fail_on = None
        self.status = 200
        self.flavor = "Google"
        self.encoding = None
        self.extra = b""
        self.custom_length = None
        host = self

        class Metadata(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                host.calls.append((self.path, self.headers))
                path = self.path.removeprefix("/computeMetadata/v1/")
                body = host.fields.get(path, "unreviewed-path").encode()
                self.send_response(host.status if host.fail_on == path else 200)
                if host.flavor is not None:
                    self.send_header("Metadata-Flavor", host.flavor)
                if host.encoding:
                    self.send_header("Content-Encoding", host.encoding)
                if host.status == 302:
                    self.send_header("Location", "/forbidden")
                body += host.extra
                self.send_header("Content-Length", host.custom_length or str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Metadata)
        thread = Thread(target=lambda: self.http.serve_forever(poll_interval=0.01), daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(self.http.server_close)
        self.addCleanup(self.http.shutdown)
        env = patch.dict(os.environ, clear=False)
        env.start()
        self.addCleanup(env.stop)
        for key in ("GCE_METADATA_HOST","GCE_METADATA_IP","GCE_METADATA_ROOT"):
            os.environ.pop(key, None)
        def local(name, *, timeout):
            self.assertEqual(name, "metadata.google.internal")
            self.assertLessEqual(timeout, 5)
            return HTTPConnection("127.0.0.1", self.http.server_port, timeout=timeout)
        transport = patch.object(identity, "HTTPConnection", side_effect=local)
        transport.start()
        self.addCleanup(transport.stop)

    def check(self, provider=NativeCloud):
        provider().identity_checked({"deployment":self.deployment})

    def test_both_roles_use_only_exact_read_only_metadata_paths(self):
        for provider in (NativeCloud, KeyCloud):
            self.calls.clear()
            self.check(provider)
            self.assertEqual([p for p,_ in self.calls], [identity.ROOT + p for p in self.fields])
            for _, headers in self.calls:
                self.assertEqual(headers["Metadata-Flavor"], "Google")
                self.assertNotIn("Authorization", headers)

    def test_each_wrong_identity_project_or_region_halts_immediately(self):
        for path,expected in list(self.fields.items()):
            with self.subTest(path=path):
                self.calls.clear()
                self.fields[path] = "foreign-value"
                with self.assertRaises(ValueError) as exc:
                    self.check()
                self.assertNotIn("foreign-value", str(exc.exception))
                self.assertEqual(self.calls[-1][0], identity.ROOT+path)
                self.fields[path] = expected

    def test_denied_redirect_or_unavailable_metadata_is_not_retried(self):
        self.fail_on = next(iter(self.fields))
        for status in (302,403,404,429,500):
            self.calls.clear()
            self.status = status
            with self.subTest(status=status), self.assertRaises(ValueError):
                self.check(KeyCloud)
            self.assertEqual(len(self.calls), 1)

    def test_missing_provider_header_and_nonidentity_encoding_are_rejected(self):
        for flavor,encoding in ((None,None),("unknown",None),("Google","gzip")):
            self.flavor,self.encoding = flavor,encoding
            with self.subTest(flavor=flavor,encoding=encoding), self.assertRaises(ValueError):
                self.check()

    def test_oversize_undecodable_or_invalid_length_rejected(self):
        for extra,length in ((b"x"*300,None),(b"\xff",None),(b"","bad")):
            self.extra,self.custom_length=extra,length
            with self.subTest(length=length), self.assertRaises(ValueError):
                self.check()

    def test_alternate_google_auth_host_rejected_before_any_metadata(self):
        for key in ("GCE_METADATA_HOST","GCE_METADATA_ROOT","GCE_METADATA_IP"):
            with self.subTest(key=key), patch.dict(os.environ,{key:"example.invalid"}), self.assertRaises(ValueError):
                self.check()
        self.assertEqual(self.calls, [])

    def test_documented_metadata_endpoint_and_proxy_independence(self):
        with patch.dict(os.environ,{"HTTP_PROXY":"http://127.0.0.1:1","NO_PROXY":"",
                                   "GCE_METADATA_HOST":"metadata.google.internal","GCE_METADATA_IP":"169.254.169.254"}):
            self.check()

    def test_metadata_failure_fails_closed_without_echoing_provider_message(self):
        with patch.object(identity,"HTTPConnection",side_effect=OSError("provider-body-placeholder")):
            with self.assertRaises(ValueError) as failure:
                self.check()
        self.assertNotIn("provider-body-placeholder", str(failure.exception))
