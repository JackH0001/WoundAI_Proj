"""Synthetic ES256 verification and live-session fault injection; no network."""
import time
import unittest
from unittest.mock import Mock

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
import jwt

from .authentication import (AuthenticationError, BoundSession, IapAssertionVerifier,
                             LiveAdminAuthenticator, IAP_ISSUER, IAP_KEYS, SESSION_URL)
from .web import ORG


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        public = self.key.public_key().public_bytes(serialization.Encoding.PEM,
                                                  serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        self.public_http = Mock()
        self.public_http.get.return_value.status_code = 200
        self.public_http.get.return_value.json.return_value = {"test-key": public}
        self.audience = "/projects/421209514056/locations/asia-east1/services/test-manager"
        self.verifier = IapAssertionVerifier(audience=self.audience, public_http=self.public_http)
        self.claims = dict(iss=IAP_ISSUER, aud=self.audience, iat=int(time.time())-1,
                           exp=int(time.time())+120, sub="accounts.google.com:synthetic",
                           email="qa-admin@example.test")
        self.backend = Mock()
        self.backend.get.return_value.status_code = 200
        self.data = dict(identity=ORG+":admin", org=ORG, role="admin", session_id="jwt-session",
                         expires_at=int(time.time())+120)
        self.backend.get.return_value.json.side_effect = lambda: dict(self.data)
        self.session = BoundSession(self.claims["sub"], "synthetic-app-bearer", "server-nonce")
        self.resolver = Mock(side_effect=lambda request: self.session)
        self.auth = LiveAdminAuthenticator(verify_iap=self.verifier, resolve_session=self.resolver,
                                          backend_http=self.backend,
                                          admin_emails=["qa-admin@example.test"])

    def token(self, **overrides):
        return jwt.encode(dict(self.claims, **overrides), self.key, algorithm="ES256",
                          headers={"kid": "test-key"})

    def request(self, **overrides):
        return Mock(headers={"X-Goog-IAP-JWT-Assertion": self.token(**overrides)})

    def test_valid_signed_assertion_and_fixed_key_endpoint(self):
        self.assertEqual(self.verifier(self.token())["sub"], self.claims["sub"])
        self.public_http.get.assert_called_once_with(IAP_KEYS, timeout=10, allow_redirects=False)

    def test_invalid_signature_rejected(self):
        other = ec.generate_private_key(ec.SECP256R1())
        token = jwt.encode(self.claims, other, algorithm="ES256", headers={"kid":"test-key"})
        with self.assertRaises(AuthenticationError): self.verifier(token)

    def test_wrong_issuer_audience_expiry_and_future_issue_rejected(self):
        for change in ({"iss":"https://attacker.test"}, {"aud":"wrong"},
                       {"exp":1}, {"iat":int(time.time())+3600}, {"sub":""}, {"email":12}):
            with self.subTest(change=change), self.assertRaises(AuthenticationError):
                self.verifier(self.token(**change))

    def test_missing_claims_and_wrong_algorithm_rejected(self):
        for field in ("exp", "iat", "iss", "aud", "sub", "email"):
            claims = dict(self.claims); del claims[field]
            token = jwt.encode(claims, self.key, algorithm="ES256", headers={"kid":"test-key"})
            with self.subTest(field=field), self.assertRaises(AuthenticationError): self.verifier(token)
        with self.assertRaises(AuthenticationError):
            self.verifier(jwt.encode(self.claims, "x"*32, algorithm="HS256", headers={"kid":"test-key"}))

    def test_key_fetch_failure_redirect_and_unknown_key_fail_closed(self):
        for status in (302, 403, 500):
            self.public_http.get.return_value.status_code = status
            with self.assertRaises(AuthenticationError): self.verifier(self.token())
        self.public_http.get.return_value.status_code = 200
        self.public_http.get.return_value.json.return_value = {}
        with self.assertRaises(AuthenticationError): self.verifier(self.token())
        self.public_http.get.side_effect = TimeoutError("sensitive diagnostic")
        with self.assertRaisesRegex(AuthenticationError, "^iap_assertion_invalid$"): self.verifier(self.token())

    def test_live_admin_verified_on_every_request(self):
        request = self.request()
        principal = self.auth(request)
        self.assertEqual(principal.session_id, "server-nonce:jwt-session")
        self.assertEqual(principal.role, "admin")
        self.auth(request)
        self.assertEqual(self.backend.get.call_count, 2)
        self.backend.get.assert_called_with(SESSION_URL,
            headers={"Authorization":"Bearer synthetic-app-bearer"}, timeout=15, allow_redirects=False)

    def test_nonallowlisted_google_and_session_subject_mismatch(self):
        with self.assertRaises(AuthenticationError): self.auth(self.request(email="other@example.test"))
        self.resolver.assert_not_called()
        self.session = BoundSession("accounts.google.com:other", "secret", "nonce")
        with self.assertRaises(AuthenticationError): self.auth(self.request())
        self.backend.get.assert_not_called()

    def test_live_downgrade_disabled_missing_and_unavailable_refused(self):
        for field, value in (("role","physician"), ("org","other"), ("identity","other:admin"),
                             ("expires_at",1), ("expires_at",float("nan")),
                             ("session_id",None)):
            original = self.data[field]; self.data[field] = value
            with self.subTest(field=field), self.assertRaises(AuthenticationError): self.auth(self.request())
            self.data[field] = original
        for status in (302, 401, 403, 503):
            self.backend.get.return_value.status_code = status
            with self.assertRaises(AuthenticationError): self.auth(self.request())

    def test_no_bearer_in_repr_or_exception(self):
        self.assertNotIn(self.session.app_token, repr(self.session))
        self.backend.get.side_effect = RuntimeError(self.session.app_token)
        with self.assertRaisesRegex(AuthenticationError, "^administrator_verification_failed$"):
            self.auth(self.request())

    def test_rotating_session_changes_csrf_binding(self):
        first = self.auth(self.request())
        self.session = BoundSession(self.claims["sub"], "next-bearer", "new-nonce")
        self.assertNotEqual(first.session_id, self.auth(self.request()).session_id)


if __name__ == "__main__":
    unittest.main()
