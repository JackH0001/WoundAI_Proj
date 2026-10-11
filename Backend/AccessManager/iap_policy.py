"""MMH direct-entry policy operations for a separately authenticated admin service.

No default credentials, HTTP routes, or automatic retries. The eventual service
must validate both administrator identities and CSRF before invoking this module.
Its adapter must use the dedicated management identity, never the measurement
runtime. A verified direct binding does not prove effective access or revocation.
"""
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Protocol
from uuid import uuid4

RESOURCE = ("projects/421209514056/iap_web/cloud_run-asia-east1/services/"
            "woundai-backend-mmhps20261007")
ENTRY_ROLE = "roles/iap.httpsResourceAccessor"
PROTECTED_MEMBERS = frozenset({"user:jack.hou@gmail.com"})


class PolicyError(ValueError):
    """Invalid input or stale policy; no mutation attempted."""


class ChangeUnconfirmed(RuntimeError):
    """Stop and reconcile: a write may already have occurred. Never retry blindly."""

    def __init__(self, stage, operation_id):
        self.stage = stage
        self.operation_id = operation_id
        super().__init__(f"access_change_unconfirmed:{stage}:{operation_id}")


class PolicyAdapter(Protocol):
    def get_policy(self, resource: str) -> dict: ...
    def set_policy(self, resource: str, policy: dict) -> None: ...


class DurableAudit(Protocol):
    def append(self, event: dict) -> None:
        """Return only after durable persistence; raise on any failure."""
        ...


def member_for(email):
    # Intentionally accept ordinary named mailboxes only, not IAM expressions.
    if not isinstance(email, str) or len(email) > 254:
        raise PolicyError("invalid_named_google_email")
    email = email.strip().lower()
    if email.endswith(".gserviceaccount.com"):
        raise PolicyError("service_account_not_named_google_user")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._+\-]*@[a-z0-9](?:[a-z0-9\-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9\-]*[a-z0-9])?)+", email):
        raise PolicyError("invalid_named_google_email")
    return "user:" + email


def _principal(value):
    return value.lower() if value.startswith("user:") else value


def validate_policy(policy):
    if not isinstance(policy, dict):
        raise PolicyError("invalid_policy")
    if not isinstance(policy.get("etag"), str) or not policy["etag"].strip():
        raise PolicyError("missing_policy_etag")
    if type(policy.get("version", 1)) is not int or policy.get("version", 1) not in (1, 3):
        raise PolicyError("unsupported_policy_version")
    if not isinstance(policy.get("bindings", []), list):
        raise PolicyError("invalid_bindings")
    unconditional = 0
    for binding in policy.get("bindings", []):
        if not isinstance(binding, dict) or not isinstance(binding.get("role"), str):
            raise PolicyError("invalid_binding")
        members = binding.get("members")
        if not isinstance(members, list) or not all(isinstance(m, str) and m for m in members):
            raise PolicyError("invalid_members")
        if len({_principal(m) for m in members}) != len(members):
            raise PolicyError("duplicate_member")
        if "condition" in binding:
            if policy.get("version") != 3 or not isinstance(binding["condition"], dict) or not binding["condition"].get("expression"):
                raise PolicyError("invalid_condition")
        elif binding["role"] == ENTRY_ROLE:
            unconditional += 1
    if unconditional > 1:
        raise PolicyError("ambiguous_entry_bindings")


def _canonical(policy, include_etag=True):
    validate_policy(policy)
    result = deepcopy(policy)
    result.setdefault("version", 1)
    result.setdefault("bindings", [])
    if not include_etag:
        result.pop("etag")
    for binding in result["bindings"]:
        binding["members"] = sorted(_principal(m) for m in binding["members"])
    result["bindings"].sort(key=lambda b: json.dumps(b, sort_keys=True))
    return json.dumps(result, sort_keys=True, separators=(",", ":"))


def snapshot_digest(policy):
    """Bind confirmation to the entire observed policy, including its etag."""
    return hashlib.sha256(_canonical(policy).encode()).hexdigest()


def propose(policy, email, action):
    """Return a copy; modify only one unconditional named-user entry binding."""
    validate_policy(policy)
    member = member_for(email)
    if action not in ("grant", "revoke"):
        raise PolicyError("invalid_action")
    if action == "revoke" and member in PROTECTED_MEMBERS:
        raise PolicyError("protected_recovery_administrator")
    result = deepcopy(policy)
    bindings = result.setdefault("bindings", [])
    entry = next((b for b in bindings if b["role"] == ENTRY_ROLE and "condition" not in b), None)
    present = entry is not None and member in [_principal(m) for m in entry["members"]]
    if action == "grant" and not present:
        if entry is None:
            bindings.append({"role": ENTRY_ROLE, "members": [member]})
        else:
            entry["members"].append(member)
    elif action == "revoke" and present:
        entry["members"] = [m for m in entry["members"] if _principal(m) != member]
        if not entry["members"]:
            bindings.remove(entry)
    return result


@dataclass(frozen=True)
class ChangeResult:
    operation_id: str
    action: str
    changed: bool
    direct_binding_verified: bool
    conditional_entry_for_target: bool
    effective_access_verified: bool = False
    existing_sessions_revoked: bool = False


def apply_confirmed_change(*, adapter: PolicyAdapter, audit: DurableAudit,
                           actor: str, email: str, action: str,
                           expected_snapshot: str):
    """Internal operation, not an authorization boundary or public endpoint.

    The UI confirmation supplies a snapshot digest, never a replacement policy,
    resource, or role. Fresh read + etag makes stale/concurrent updates stop.
    Audit intent is durable before writing. Ambiguous failures are not retried
    or rolled back, because rollback could overwrite another administrator.
    """
    if not isinstance(actor, str) or not actor.strip() or len(actor) > 256:
        raise PolicyError("missing_verified_actor")
    if not isinstance(expected_snapshot, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_snapshot):
        raise PolicyError("invalid_snapshot")
    member = member_for(email)
    before = adapter.get_policy(RESOURCE)
    if snapshot_digest(before) != expected_snapshot:
        raise PolicyError("stale_policy_refresh_required")
    after = propose(before, email, action)
    changed = _canonical(before) != _canonical(after)
    operation_id = str(uuid4())
    event = {"operation_id": operation_id, "actor": actor, "member": member,
             "resource": RESOURCE, "role": ENTRY_ROLE, "action": action,
             "before_sha256": snapshot_digest(before),
             "proposed_sha256": snapshot_digest(after)}
    # An intent failure prevents the setIamPolicy call entirely.
    audit.append(dict(event, phase="intent", changed=changed))
    if changed:
        try:
            adapter.set_policy(RESOURCE, deepcopy(after))
        except Exception:
            # Do not expose adapter responses (which may contain credentials).
            try:
                audit.append(dict(event, phase="unconfirmed", stage="write"))
            except Exception:
                pass
            raise ChangeUnconfirmed("write", operation_id) from None
    try:
        readback = adapter.get_policy(RESOURCE)
        if _canonical(readback, False) != _canonical(after, False):
            raise PolicyError("readback_differs")
    except Exception:
        try:
            audit.append(dict(event, phase="unconfirmed", stage="readback"))
        except Exception:
            pass
        raise ChangeUnconfirmed("readback", operation_id) from None
    conditional = any(b["role"] == ENTRY_ROLE and "condition" in b
                      and member in [_principal(m) for m in b["members"]]
                      for b in readback.get("bindings", []))
    try:
        audit.append(dict(event, phase="verified", changed=changed,
                          readback_sha256=snapshot_digest(readback),
                          effective_access_verified=False,
                          conditional_entry_for_target=conditional))
    except Exception:
        raise ChangeUnconfirmed("completion_audit", operation_id) from None
    return ChangeResult(operation_id, action, changed, True, conditional)
