"""Signed IAP + live App-admin verification, with no credential discovery.

The runtime must supply a server-side session resolver and an authenticated
MMH transport. This module neither creates sessions nor grants cloud access.
"""
from dataclasses import dataclass, field
import time

import jwt

from .web import ORG, VerifiedAdmin

IAP_ISSUER = "https://cloud.google.com/iap"
IAP_KEYS = "https://www.gstatic.com/iap/verify/public_key"
SESSION_URL = ("https://candidate---woundai-backend-mmhps20261007-z4kgfkob4a-de.a.run.app"
               "/api/v1/users/session")


class AuthenticationError(RuntimeError):
    pass


@dataclass(frozen=True)
class BoundSession:
    """Loaded from server storage, never from a client JSON object."""
    google_subject: str
    app_token: str = field(repr=False)
    nonce: str


class IapAssertionVerifier:
    def __init__(self, *, audience, public_http):
        if not isinstance(audience, str) or not audience.startswith("/projects/"):
            raise ValueError("iap_resource_audience_required")
        self.audience = audience
        self.http = public_http

    def __call__(self, token):
        try:
            if not isinstance(token, str) or not token or len(token) > 16384:
                raise ValueError()
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "ES256" or not isinstance(header.get("kid"), str):
                raise ValueError()
            # Fixed unauthenticated endpoint; never follow token-provided URLs.
            response = self.http.get(IAP_KEYS, timeout=10, allow_redirects=False)
            if response.status_code != 200:
                raise ValueError()
            keys = response.json()
            key = keys.get(header["kid"]) if isinstance(keys, dict) else None
            if not isinstance(key, str):
                raise ValueError()
            claims = jwt.decode(token, key, algorithms=["ES256"],
                                audience=self.audience, issuer=IAP_ISSUER,
                                options={"require": ["exp", "iat", "aud", "iss", "sub", "email"]})
            if (not isinstance(claims["sub"], str) or not claims["sub"].startswith("accounts.google.com:")
                    or not isinstance(claims["email"], str) or "@" not in claims["email"]):
                raise ValueError()
            return claims
        except Exception:
            raise AuthenticationError("iap_assertion_invalid") from None


class LiveAdminAuthenticator:
    def __init__(self, *, verify_iap, resolve_session, backend_http, admin_emails):
        self.verify_iap = verify_iap
        self.resolve_session = resolve_session
        self.http = backend_http
        self.allowed = frozenset(email.lower() for email in admin_emails)

    def __call__(self, request):
        try:
            claims = self.verify_iap(request.headers.get("X-Goog-IAP-JWT-Assertion"))
            email, subject = claims["email"].lower(), claims["sub"]
            if email not in self.allowed:
                raise ValueError()
            session = self.resolve_session(request)
            if (not isinstance(session, BoundSession) or session.google_subject != subject
                    or not session.nonce or not session.app_token):
                raise ValueError()
            # The injected transport carries a separately authorized IAP entry
            # credential. The application bearer must not be replaced by it.
            response = self.http.get(SESSION_URL,
                headers={"Authorization": "Bearer " + session.app_token},
                timeout=15, allow_redirects=False)
            if response.status_code != 200:
                raise ValueError()
            data = response.json()
            if (data.get("org") != ORG or data.get("role") != "admin"
                    or not isinstance(data.get("identity"), str)
                    or not data["identity"].startswith(ORG + ":")
                    or not isinstance(data.get("session_id"), str) or not data["session_id"]
                    or type(data.get("expires_at")) not in (int, float)
                    or not time.time() < data["expires_at"] < float("inf")):
                raise ValueError()
            return VerifiedAdmin(email, subject, data["identity"], "admin", ORG,
                                 session.nonce + ":" + data["session_id"])
        except Exception:
            raise AuthenticationError("administrator_verification_failed") from None
