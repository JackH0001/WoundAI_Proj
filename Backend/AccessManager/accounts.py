"""MMH account bridge. Uses the existing backend audit and password handling.

Snapshot checks reject stale forms but are NOT a cross-service transaction or
an atomic backend compare-and-swap. Never retry a write with uncertain outcome.
"""
import hashlib
import json
import re

ORG = "mmhps20261007"
URL = "https://woundai-backend-mmhps20261007-z4kgfkob4a-de.a.run.app/api/v1/users"
ROLES = {"physician", "nurse", "assistant", "engineer", "admin"}
FIELDS = ("identity", "org", "user", "role", "role_zh", "display_name", "disabled", "created_at", "updated_at", "revision")


class AccountError(ValueError):
    pass


class AccountUnconfirmed(RuntimeError):
    pass


def account_rows(rows):
    if not isinstance(rows, list):
        raise AccountError("account_state_unavailable")
    clean, seen = [], set()
    for row in rows:
        if (not isinstance(row, dict) or row.get("org") != ORG
                or not isinstance(row.get("user"), str)
                or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,30}", row["user"])
                or row.get("identity") != ORG+":"+row["user"]
                or row["identity"] in seen or type(row.get("disabled")) is not bool
                or row.get("role") not in ROLES
                or not isinstance(row.get("revision"), str) or not re.fullmatch(r"[0-9a-f]{64}", row["revision"])):
            raise AccountError("account_state_unavailable")
        seen.add(row["identity"])
        clean.append({key: row.get(key) for key in FIELDS})
    return sorted(clean, key=lambda row: row["identity"])


def account_snapshot(rows):
    return hashlib.sha256(json.dumps(account_rows(rows), sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


class AccountAdapter:
    """Transport must bind the verified administrator's App bearer and IAP.

    Configure zero automatic write retries. No ADC or client-provided URL.
    """
    def __init__(self, authenticated_http):
        self.http = authenticated_http

    def list_accounts(self):
        try:
            response = self.http.get(URL, timeout=20, allow_redirects=False)
            if response.status_code != 200:
                raise ValueError()
            data = response.json()
            if data.get("account_versioning") != "cas-v1":
                raise ValueError()
            return account_rows(data["users"])
        except Exception:
            raise AccountError("account_state_unavailable") from None

    def change(self, data, *, actor):
        if not isinstance(data, dict) or set(data) != {"action", "user", "role", "display_name", "disabled", "account_snapshot"}:
            raise AccountError("invalid_account_fields")
        action, user = data["action"], data["user"]
        if (action not in ("create", "update") or not isinstance(user, str)
                or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,30}", user)
                or not isinstance(data["role"], str) or data["role"] not in ROLES
                or not isinstance(data["display_name"], str) or len(data["display_name"]) > 80
                or any(ord(c) < 32 for c in data["display_name"])
                or type(data["disabled"]) is not bool):
            raise AccountError("invalid_account_fields")
        identity = ORG+":"+user
        if user == "admin" or identity == actor:
            raise AccountError("protected_recovery_administrator")
        rows = self.list_accounts()
        if data["account_snapshot"] != account_snapshot(rows):
            raise AccountError("stale_account_refresh_required")
        current = next((row for row in rows if row["identity"] == identity), None)
        if (action == "create") == (current is not None):
            raise AccountError("account_existence_changed")
        desired = {key: data[key] for key in ("role", "display_name", "disabled")}
        if current and all(current[key] == value for key, value in desired.items()):
            return dict(status="account_readback_verified", changed=False, identity=identity)
        expected = current.get("revision") if current else "0" * 64
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise AccountError("account_version_unavailable")
        payload = dict(desired, org=ORG, user=user, expected_revision=expected)
        if action == "create":
            payload["generate_password"] = True
        # Once a write starts, even a malformed/failed reply may follow a
        # committed account/audit record. No automatic retry or rollback.
        try:
            response = self.http.post(URL, json=payload, timeout=90, allow_redirects=False)
            result = response.json()
            if response.status_code == 409 and result.get("error") == "stale_account_revision":
                raise AccountError("stale_account_refresh_required")
            if response.status_code != 200 or result.get("status") != "ok":
                raise ValueError()
            saved = result.get("user")
            if (not isinstance(saved, dict) or saved.get("identity") != identity
                    or saved.get("org") != ORG or saved.get("user") != user
                    or any(saved.get(key) != value for key, value in desired.items())):
                raise ValueError()
            after = next((row for row in self.list_accounts() if row["identity"] == identity), None)
            if (after is None or any(after[key] != value for key, value in desired.items())
                    or after["revision"] != saved.get("revision")):
                raise ValueError()
            password = result.get("generated_password")
            if action == "create" and (not isinstance(password, str) or not 10 <= len(password) <= 256
                                       or not password.isascii() or not password.isprintable()):
                raise ValueError()
        except AccountError:
            raise
        except Exception:
            raise AccountUnconfirmed("account_change_unconfirmed") from None
        out = dict(status="account_readback_verified", changed=True, identity=identity)
        if action == "create":
            out["generated_password"] = password
        return out
