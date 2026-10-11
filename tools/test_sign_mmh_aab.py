"""Real JDK signing/verification against disposable local test keys, never user keys."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from unittest.mock import patch
import sign_mmh_aab as m

JDK = Path(os.environ.get('MMH_TEST_JAVA_HOME', '/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home'))


class InputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.aab = self.root/'test.aab'
        self.write()

    def write(self, extra=None):
        with zipfile.ZipFile(self.aab, 'w') as z:
            z.writestr('BundleConfig.pb', b'synthetic fixture')
            z.writestr('base/manifest/AndroidManifest.xml', b'synthetic fixture')
            if extra: z.writestr(extra, b'not a signature')

    def test_valid_pinned_bundle(self):
        m.check_input(self.aab, m.digest(self.aab))

    def test_wrong_digest(self):
        with self.assertRaises(m.SigningError): m.check_input(self.aab, '0'*64)

    def test_invalid_hash(self):
        with self.assertRaises(m.SigningError): m.check_input(self.aab, 'not-a-hash')

    def test_already_signed_metadata(self):
        for name in ['META-INF/A.SF', 'META-INF/A.RSA', 'META-INF/MANIFEST.MF']:
            self.write(name)
            with self.subTest(name=name), self.assertRaises(m.SigningError):
                m.check_input(self.aab, m.digest(self.aab))

    def test_missing_bundle_layout(self):
        with zipfile.ZipFile(self.aab, 'w') as z: z.writestr('other', b'x')
        with self.assertRaises(m.SigningError): m.check_input(self.aab, m.digest(self.aab))

    def test_nonzero_diagnostics_not_exposed(self):
        with patch.object(m.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, b'secret', b'secret')):
            with self.assertRaises(m.SigningError) as e:
                m.run(['/example/keytool'], {})
        self.assertNotIn('secret', str(e.exception))


@unittest.skipUnless((JDK/'bin/java').is_file(), 'JDK 17 required')
class RealSignatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='mmh-disposable-signing-test-')
        cls.root = Path(cls.temp.name)
        cls.key = cls.root/'disposable.jks'
        cls.password = secrets.token_hex(20)
        cls.env = os.environ.copy()
        cls.env['MMH_SIGN_STORE_PASS'] = cls.password
        m.run([m.tool(JDK, 'keytool'), '-genkeypair', '-alias', 'test-upload',
               '-keystore', str(cls.key), '-storetype', 'JKS', '-keyalg', 'RSA',
               '-keysize', '2048', '-validity', '3', '-dname', 'CN=Disposable Local Test',
               '-storepass:env', 'MMH_SIGN_STORE_PASS', '-keypass:env', 'MMH_SIGN_STORE_PASS'], cls.env)
        cls.aab = cls.root/'fixture.aab'
        with zipfile.ZipFile(cls.aab, 'w') as z:
            z.writestr('BundleConfig.pb', b'synthetic bundle test')
            z.writestr('base/manifest/AndroidManifest.xml', b'synthetic manifest test')
            z.writestr('base/assets/example.txt', b'payload')
        cls.signed = cls.root/'signed.aab'
        cls.report = m.sign_bundle(cls.aab, m.digest(cls.aab), cls.key, 'test-upload',
                                  cls.password, cls.password, cls.signed, JDK)

    @classmethod
    def tearDownClass(cls):
        cls.env.pop('MMH_SIGN_STORE_PASS', None)
        cls.password = None
        cls.temp.cleanup()

    def test_private_alias_detection(self):
        self.assertEqual(m.private_aliases(m.tool(JDK, 'keytool'), self.key, self.env), ['test-upload'])

    def test_all_entries_signed_by_expected_certificate(self):
        self.assertEqual(self.report['payload_entries'], 3)
        self.assertTrue(self.report['verified'])
        self.assertFalse(self.report['uploaded'])
        self.assertFalse(self.report['play_app_signing_certificate'])
        self.assertEqual(self.signed.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.signed.with_suffix('.verification.json').stat().st_mode & 0o777, 0o600)
        self.assertNotIn(self.password, self.signed.with_suffix('.verification.json').read_text())

    def test_unsigned_archive_rejected(self):
        with self.assertRaises(m.SigningError):
            m.verify_signature(self.aab, self.report['upload_certificate_sha256'], JDK)

    def test_other_certificate_rejected(self):
        with self.assertRaises(m.SigningError):
            m.verify_signature(self.signed, '0'*64, JDK)

    def test_appended_unsigned_payload_rejected(self):
        dst = self.root/'unsigned-extra.aab'; shutil.copyfile(self.signed, dst)
        with zipfile.ZipFile(dst, 'a') as z: z.writestr('base/assets/extra.txt', b'unsigned')
        with self.assertRaises(m.SigningError):
            m.verify_signature(dst, self.report['upload_certificate_sha256'], JDK)

    def test_tampered_payload_rejected(self):
        dst = self.root/'tampered.aab'
        with zipfile.ZipFile(self.signed) as src, zipfile.ZipFile(dst, 'w') as out:
            for e in src.infolist():
                out.writestr(e, b'changed' if e.filename == 'base/assets/example.txt' else src.read(e))
        with self.assertRaises(m.SigningError):
            m.verify_signature(dst, self.report['upload_certificate_sha256'], JDK)

    def test_existing_output_never_overwritten(self):
        old = m.digest(self.signed)
        with self.assertRaises(m.SigningError):
            m.sign_bundle(self.aab, m.digest(self.aab), self.key, 'test-upload',
                          self.password, self.password, self.signed, JDK)
        self.assertEqual(m.digest(self.signed), old)

    def test_wrong_password_produces_no_output(self):
        dst = self.root/'wrong-password.aab'
        with self.assertRaises(m.SigningError):
            m.sign_bundle(self.aab, m.digest(self.aab), self.key, 'test-upload',
                          'wrong-test-password', 'wrong-test-password', dst, JDK)
        self.assertFalse(dst.exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
