import hashlib
import io
import plistlib
import tempfile
import unittest
import warnings
from email.message import Message
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from verify_lite_release_privacy import LIMIT, NOTICE_URL, NoRedirect, compare, read_public_notice


class ReleasePrivacyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.ipa = Path(temp.name) / "candidate.ipa"
        self.page = b"<html>synthetic beta policy</html>"
        self.info = {"CFBundleIdentifier": "com.woundai.lite", "CFBundleVersion": "52"}
        self.write_ipa()

    def write_ipa(self, notices=1, apps=("WoundLite",)):
        with ZipFile(self.ipa, "w") as archive, warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            for app in apps:
                root = f"Payload/{app}.app/"
                archive.writestr(root + "Info.plist", plistlib.dumps(self.info))
                for _ in range(notices):
                    archive.writestr(root + "woundlite.html", self.page)
        self.pin = hashlib.sha256(self.ipa.read_bytes()).hexdigest()

    def test_all_three_copies_match(self):
        self.assertTrue(compare(self.ipa, self.pin, "52", self.page, self.page)["matches"])

    def test_different_copies_report_which_hash_differs(self):
        for source, public in [(self.page, b"old"), (b"new", self.page), (b"new", b"new")]:
            with self.subTest(source=source, public=public):
                result = compare(self.ipa, self.pin, "52", source, public)
                self.assertFalse(result["matches"])
                self.assertEqual(result["source_sha256"], hashlib.sha256(source).hexdigest())
                self.assertEqual(result["public_sha256"], hashlib.sha256(public).hexdigest())

    def test_requires_pinned_ipa_and_build(self):
        for pin, build in [(self.pin, "51"), ("0" * 64, "52")]:
            with self.subTest(build=build), self.assertRaises(ValueError):
                compare(self.ipa, pin, build, self.page, self.page)

    def test_rejects_another_app_even_with_matching_policy(self):
        self.info["CFBundleIdentifier"] = "com.woundai.app"
        self.write_ipa()
        with self.assertRaises(ValueError):
            compare(self.ipa, self.pin, "52", self.page, self.page)

    def test_ambiguous_or_missing_bundle_resources(self):
        for notices, apps in [(0, ("Lite",)), (2, ("Lite",)), (1, ("Lite", "Other"))]:
            with self.subTest(notices=notices, apps=apps), self.assertRaises(ValueError):
                self.write_ipa(notices, apps)
                compare(self.ipa, self.pin, "52", self.page, self.page)

    def test_incomplete_or_oversized_response(self):
        for public in [b"", b"x" * (LIMIT + 1)]:
            with self.subTest(length=len(public)), self.assertRaises(ValueError):
                compare(self.ipa, self.pin, "52", self.page, public)


class PublishedNoticeTests(unittest.TestCase):
    def response(self, status, url, kind, body):
        response = io.BytesIO(body)
        response.status = status
        response.geturl = lambda: url
        response.headers = Message()
        response.headers["Content-Type"] = kind
        return response

    @patch("verify_lite_release_privacy.build_opener")
    def test_reads_only_the_published_html(self, opener):
        opener.return_value.open.return_value = self.response(200, NOTICE_URL, "text/html; charset=utf-8", b"page")
        self.assertEqual(read_public_notice(), b"page")
        request = opener.return_value.open.call_args.args[0]
        self.assertEqual(request.full_url, NOTICE_URL)
        self.assertEqual(request.get_method(), "GET")
        self.assertIsInstance(opener.call_args.args[0], NoRedirect)

    @patch("verify_lite_release_privacy.build_opener")
    def test_requires_success_at_the_exact_policy_url(self, opener):
        for status, url, kind in [(404, NOTICE_URL, "text/html"), (200, NOTICE_URL, "application/json"),
                                  (200, NOTICE_URL + "/other", "text/html")]:
            with self.subTest(status=status, url=url, kind=kind), self.assertRaises(ValueError):
                opener.return_value.open.return_value = self.response(status, url, kind, b"page")
                read_public_notice()
