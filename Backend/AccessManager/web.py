"""Private access-management web application factory, not a deployed entry point.

Authentication and account data must be injected by a verified server-side
adapter. There is no default login, ADC, or cloud write capability. The local
demo uses separate in-memory adapters and cannot contact Google.
"""
from dataclasses import dataclass
from functools import wraps
import hashlib
import hmac
import secrets
from urllib.parse import urlsplit

from flask import Flask, g, jsonify, request, send_from_directory

from .iap_policy import (ChangeUnconfirmed, ENTRY_ROLE, PolicyError,
                         PROTECTED_MEMBERS, RESOURCE, apply_confirmed_change,
                         snapshot_digest, validate_policy)
from .accounts import AccountError, AccountUnconfirmed, account_snapshot

ORG = "mmhps20261007"
CONSOLE = "https://woundai-backend-mmhps20261007-z4kgfkob4a-de.a.run.app/console#users"


@dataclass(frozen=True)
class VerifiedAdmin:
    """Only construct after IAP signature/audience and live App admin checks.

    This object is a server adapter result, never deserialized from HTTP input.
    session_id must rotate at logout/login and identify both verified sessions.
    """
    google_email: str
    google_subject: str
    app_identity: str
    role: str
    org: str
    session_id: str


def create_app(*, origin, authenticate=None, policy_adapter=None, audit=None,
               list_accounts=None, admin_emails=(), csrf_secret=None,
               demonstration=False, account_adapter_for=None, session_controller=None):
    parts = urlsplit(origin)
    if parts.path or parts.query or parts.fragment or parts.username or parts.password:
        raise ValueError("origin_must_be_origin_only")
    if parts.scheme != "https" and not (demonstration and parts.scheme == "http" and parts.hostname in ("127.0.0.1", "localhost")):
        raise ValueError("https_required")
    if not parts.netloc:
        raise ValueError("origin_required")
    if csrf_secret is None:
        # Suitable only for one instance; production must inject shared secret.
        csrf_secret = secrets.token_bytes(32)
    if not isinstance(csrf_secret, bytes) or len(csrf_secret) < 32:
        raise ValueError("csrf_secret_too_short")
    allowed = frozenset(e.lower() for e in admin_emails)
    app = Flask(__name__, static_folder="static")
    app.config["MAX_CONTENT_LENGTH"] = 4096
    if session_controller is not None:
        if session_controller.origin != origin or demonstration:
            raise ValueError("session_controller_origin_mismatch")
        authenticate = session_controller.authenticate
        account_adapter_for = session_controller.account_adapter
        session_controller.mount(app)

    def csrf_for(principal):
        data = "\0".join((origin, principal.google_subject, principal.app_identity, principal.session_id))
        return hmac.new(csrf_secret, data.encode(), hashlib.sha256).hexdigest()

    def guarded(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if (authenticate is None or policy_adapter is None or audit is None
                    or (list_accounts is None and account_adapter_for is None)):
                return jsonify(error="management_not_configured"), 503
            try:
                principal = authenticate(request)
            except Exception:
                return jsonify(error="administrator_verification_failed"), 401
            if not isinstance(principal, VerifiedAdmin):
                return jsonify(error="administrator_verification_required"), 401
            if (not all(isinstance(value, str) and value and "\0" not in value for value in vars(principal).values())
                    or principal.google_email.lower() not in allowed or principal.role != "admin"
                    or principal.org != ORG or not principal.app_identity.startswith(ORG + ":")
                    or not principal.google_subject or not principal.session_id):
                return jsonify(error="administrator_access_denied"), 403
            if request.method != "GET":
                if request.headers.get("Origin") != origin:
                    return jsonify(error="origin_rejected"), 403
                if not request.is_json:
                    return jsonify(error="json_required"), 415
                provided = request.headers.get("X-MMH-CSRF", "")
                if not provided.isascii() or not hmac.compare_digest(provided, csrf_for(principal)):
                    return jsonify(error="csrf_rejected"), 403
            g.principal = principal
            return fn(*args, **kwargs)
        return wrapped

    @app.after_request
    def secure_response(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = ("default-src 'none'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        return response

    @app.get("/")
    def index():
        return send_from_directory(app.static_folder, "members.html")

    @app.get("/api/members")
    @guarded
    def members():
        try:
            policy = policy_adapter.get_policy(RESOURCE)
            validate_policy(policy)
            entries = {}
            for binding in policy.get("bindings", []):
                if binding["role"] != ENTRY_ROLE:
                    continue
                for member in binding["members"]:
                    if not member.startswith("user:"):
                        continue
                    email = member[5:].lower()
                    row = entries.setdefault(email, {"email": email, "direct": False, "conditional": False,
                                                    "protected": "user:" + email in PROTECTED_MEMBERS})
                    row["conditional" if "condition" in binding else "direct"] = True
            # Whitelist display fields, never return password hashes or tokens.
            accounts = []
            source_accounts = (account_adapter_for(request, g.principal).list_accounts()
                               if account_adapter_for is not None else list_accounts())
            for account in source_accounts:
                if account.get("org") != ORG:
                    raise ValueError("foreign_account")
                accounts.append({key: account.get(key) for key in
                                 ("identity", "user", "role", "role_zh", "display_name", "disabled")})
            return jsonify(resource=RESOURCE, entries=sorted(entries.values(), key=lambda r: r["email"]),
                           accounts=accounts, policy_snapshot=snapshot_digest(policy),
                           csrf=csrf_for(g.principal), demonstration=demonstration,
                           account_management_enabled=account_adapter_for is not None,
                           account_snapshot=account_snapshot(source_accounts) if account_adapter_for is not None else None,
                           actor={"google_email": g.principal.google_email, "app_identity": g.principal.app_identity},
                           app_console=CONSOLE, effective_access_verified=False)
        except Exception:
            return jsonify(error="member_state_unavailable"), 503

    @app.post("/api/entry")
    @guarded
    def entry():
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or set(data) != {"email", "action", "policy_snapshot"}:
            return jsonify(error="invalid_change_fields"), 400
        try:
            result = apply_confirmed_change(adapter=policy_adapter, audit=audit,
                actor=g.principal.google_email + "|" + g.principal.app_identity,
                email=data["email"], action=data["action"], expected_snapshot=data["policy_snapshot"])
        except PolicyError as exc:
            code = str(exc)
            return jsonify(error=code), 409 if code == "stale_policy_refresh_required" else 400
        except ChangeUnconfirmed as exc:
            return jsonify(error="change_unconfirmed", stage=exc.stage,
                           operation_id=exc.operation_id, retry_safe=False), 502
        except Exception:
            return jsonify(error="change_not_started"), 503
        return jsonify(status="direct_policy_verified", action=result.action, changed=result.changed,
                       operation_id=result.operation_id, conditional_entry_for_target=result.conditional_entry_for_target,
                       effective_access_verified=False, existing_sessions_revoked=False)

    @app.post("/api/accounts")
    @guarded
    def accounts():
        if account_adapter_for is None:
            return jsonify(error="account_management_not_configured"), 503
        try:
            adapter = account_adapter_for(request, g.principal)
            result = adapter.change(request.get_json(silent=True), actor=g.principal.app_identity)
            return jsonify(result)
        except AccountError as exc:
            code = str(exc)
            status = 409 if code in ("stale_account_refresh_required", "account_existence_changed") else 400
            if code == "account_state_unavailable":
                status = 503
            return jsonify(error=code), status
        except AccountUnconfirmed:
            return jsonify(error="account_change_unconfirmed", retry_safe=False), 502
        except Exception:
            return jsonify(error="account_change_unconfirmed", retry_safe=False), 502

    return app
