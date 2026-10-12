"""Explicit, separately deployed MMH management runtime. Import is inert.

Only startup in the named Cloud Run service may use its attached identity.
No ADC, local key files, user credentials, automatic HTTP/auth retries, or
automatic IAM writes. Cloud resources/permissions require separate approval.
"""
import os
import re
import secrets
import threading
import time

import jwt
import requests
from requests.adapters import HTTPAdapter

from .accounts import URL as ACCOUNTS_URL
from .audit import BUCKET, PREFIX, GcsAuditObjects, ManagementAudit
from .authentication import IAP_KEYS, SESSION_URL, IapAssertionVerifier
from .iap_adapter import IapPolicyAdapter
from .iap_policy import RESOURCE
from .sessions import LOGIN_URL, SessionController
from .web import create_app

SERVICE = "woundai-mmh-access-manager"
IDENTITY = "mmh-access-manager@woundai-jackh001.iam.gserviceaccount.com"
ORIGIN = f"https://{SERVICE}-421209514056.asia-east1.run.app"
AUDIENCE = f"/projects/421209514056/locations/asia-east1/services/{SERVICE}"
BACKEND_AUDIENCE = "421209514056-c62hg9vv5p7g2l96n2efig269kcsads6.apps.googleusercontent.com"
ADMINS = ("jack.hou@gmail.com",)
METADATA = "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/"


class RuntimeErrorSafe(RuntimeError):
    pass


def new_http():
    http = requests.Session()
    http.trust_env = False  # No .netrc credentials, inherited proxies or CA override.
    http.mount("https://", HTTPAdapter(max_retries=0))
    http.mount("http://", HTTPAdapter(max_retries=0))
    return http


class AttachedTokens:
    """Trusted platform metadata only; cache short-lived tokens in memory."""
    def __init__(self, http, *, clock=time.time):
        self.http, self.clock = http, clock
        self.lock = threading.RLock()
        self.cache = {}

    def _get(self, path, **params):
        try:
            response = self.http.get(METADATA + path, params=params,
                headers={"Metadata-Flavor": "Google"}, timeout=5, allow_redirects=False)
            if response.status_code != 200 or response.headers.get("Metadata-Flavor") != "Google":
                raise ValueError()
            return response
        except Exception:
            raise RuntimeErrorSafe("attached_identity_unavailable") from None

    def verify_identity(self):
        if self._get("email").text.strip() != IDENTITY:
            raise RuntimeErrorSafe("unexpected_attached_identity")

    def token(self, purpose):
        if purpose not in ("cloud", "backend"):
            raise RuntimeErrorSafe("token_purpose_rejected")
        with self.lock:
            now = self.clock()
            cached = self.cache.get(purpose)
            if cached and cached[1] > now + 60:
                return cached[0]
            self.verify_identity()
            try:
                if purpose == "cloud":
                    data = self._get("token").json()
                    token = data["access_token"]
                    duration = data["expires_in"]
                    if data["token_type"].lower() != "bearer" or type(duration) is not int or not 60 < duration <= 3600:
                        raise ValueError()
                    expires = now + duration
                else:
                    token = self._get("identity", audience=BACKEND_AUDIENCE, format="full").text
                    # Metadata transport is the trust boundary, not these
                    # unverified claims. Inspect only for identity/cache bounds;
                    # downstream IAP independently verifies Google's signature.
                    claims = jwt.decode(token, options={"verify_signature": False})
                    expires = claims["exp"]
                    if (claims.get("email") != IDENTITY or claims.get("aud") != BACKEND_AUDIENCE
                            or claims.get("iss") not in ("https://accounts.google.com", "accounts.google.com")
                            or type(expires) is not int or not now + 60 < expires <= now + 3700):
                        raise ValueError()
                if not isinstance(token, str) or not 1 <= len(token) <= 16384 or any(c.isspace() for c in token):
                    raise ValueError()
                self.cache[purpose] = (token, expires)
                return token
            except Exception:
                raise RuntimeErrorSafe("attached_token_unavailable") from None


class ScopedHttp:
    """Keep App bearer and service IAP bearer distinct; reject other destinations."""
    def __init__(self, http, tokens, purpose):
        if purpose not in ("backend", "cloud", "public"):
            raise ValueError("invalid_transport_purpose")
        self.http, self.tokens, self.purpose = http, tokens, purpose

    def _allowed(self, method, url, args):
        if self.purpose == "public":
            return method == "GET" and url == IAP_KEYS
        if self.purpose == "backend":
            return (method, url) in {("POST", LOGIN_URL), ("GET", SESSION_URL),
                                    ("GET", ACCOUNTS_URL), ("POST", ACCOUNTS_URL)}
        if method == "POST" and url in {f"https://iap.googleapis.com/v1/{RESOURCE}:getIamPolicy",
                                         f"https://iap.googleapis.com/v1/{RESOURCE}:setIamPolicy"}:
            return True
        if method == "POST" and url == f"https://storage.googleapis.com/upload/storage/v1/b/{BUCKET}/o":
            params = args.get("params", {})
            return (params.get("ifGenerationMatch") == "0" and params.get("uploadType") == "media"
                    and self._audit_key(params.get("name", "")))
        if method == "GET":
            base = f"https://storage.googleapis.com/storage/v1/b/{BUCKET}/o/"
            if url.startswith(base):
                from urllib.parse import unquote
                return self._audit_key(unquote(url[len(base):]))
        return False

    @staticmethod
    def _audit_key(key):
        return isinstance(key, str) and re.fullmatch(re.escape(PREFIX) + r"[0-9a-f-]{36}/(?:intent|outcome)\.json", key) is not None

    def request(self, method, url, **kwargs):
        if not self._allowed(method, url, kwargs):
            raise RuntimeErrorSafe("transport_destination_rejected")
        if set(kwargs) - {"headers", "params", "json", "data", "timeout", "allow_redirects"}:
            raise RuntimeErrorSafe("transport_options_rejected")
        if kwargs.get("allow_redirects", False) is not False:
            raise RuntimeErrorSafe("transport_redirect_rejected")
        headers = dict(kwargs.pop("headers", {}))
        allowed = {"authorization"} if self.purpose == "backend" else {"content-type"}
        if any(k.lower() not in allowed for k in headers):
            raise RuntimeErrorSafe("transport_headers_rejected")
        if self.purpose == "backend":
            headers["Proxy-Authorization"] = "Bearer " + self.tokens.token("backend")
        elif self.purpose == "cloud":
            headers["Authorization"] = "Bearer " + self.tokens.token("cloud")
        timeout = kwargs.pop("timeout", 20)
        if type(timeout) not in (int, float) or not 0 < timeout <= 30:
            raise RuntimeErrorSafe("transport_timeout_rejected")
        kwargs["allow_redirects"] = False
        try:
            # Exactly one request; a 401 never silently replays a POST.
            return self.http.request(method, url, headers=headers, timeout=timeout, **kwargs)
        except Exception:
            raise RuntimeErrorSafe("transport_result_unconfirmed") from None

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)


def assemble(*, backend_http, cloud_http, public_http, csrf_secret=None):
    """Compose real adapters. No fake account list or permissive auth fallback."""
    secret = csrf_secret if csrf_secret is not None else secrets.token_bytes(32)
    verifier = IapAssertionVerifier(audience=AUDIENCE, public_http=public_http)
    controller = SessionController(origin=ORIGIN, verify_iap=verifier,
        backend_http=backend_http, admin_emails=ADMINS, csrf_secret=secret)
    return create_app(origin=ORIGIN, policy_adapter=IapPolicyAdapter(cloud_http),
        audit=ManagementAudit(GcsAuditObjects(cloud_http)), admin_emails=ADMINS,
        csrf_secret=secret, session_controller=controller)


def cloud_app():
    """Gunicorn factory, only after explicit deployment configuration."""
    if (os.environ.get("K_SERVICE") != SERVICE
            or os.environ.get("MMH_MANAGEMENT_ENABLED") != "private-synthetic-validation"
            or os.environ.get("WEB_CONCURRENCY", "1") != "1"):
        raise RuntimeErrorSafe("management_runtime_not_explicitly_configured")
    tokens = AttachedTokens(new_http())
    tokens.verify_identity()
    # Single sync worker avoids sharing requests.Session across request threads.
    return assemble(backend_http=ScopedHttp(new_http(), tokens, "backend"),
                    cloud_http=ScopedHttp(new_http(), tokens, "cloud"),
                    public_http=ScopedHttp(new_http(), None, "public"))
