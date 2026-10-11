"""Create-only MMH management audit objects, separate from clinical audit chains.

No credential discovery or cloud side effects at import/construction. Inject a
dedicated authorized HTTP transport with automatic POST retries disabled. This
is an unlocked validation archive, NOT retention-lock or WORM assurance.
"""
from datetime import datetime, timezone
import hashlib
import json
import re
from urllib.parse import quote
from uuid import UUID

from .iap_policy import ENTRY_ROLE, RESOURCE, member_for

BUCKET = "woundai-mmhps20261007-audit-421209514056"
PREFIX = "access-management/v1/"
LIMIT = 16384
BASE = frozenset({"operation_id", "actor", "member", "resource", "role",
                  "action", "before_sha256", "proposed_sha256"})
EXTRA = {
    "intent": {"phase", "changed"},
    "verified": {"phase", "changed", "readback_sha256",
                 "effective_access_verified", "conditional_entry_for_target"},
    "unconfirmed": {"phase", "stage"},
}


class AuditError(RuntimeError):
    """Persistence is not confirmed. Never include transport bodies in errors."""


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def validate_event(event):
    try:
        if type(event) is not dict or event.get("phase") not in EXTRA:
            raise ValueError()
        if set(event) != BASE | EXTRA[event["phase"]]:
            raise ValueError()
        op = UUID(event["operation_id"])
        if str(op) != event["operation_id"] or op.version != 4:
            raise ValueError()
        if event["resource"] != RESOURCE or event["role"] != ENTRY_ROLE:
            raise ValueError()
        if event["action"] not in ("grant", "revoke"):
            raise ValueError()
        if member_for(event["member"].removeprefix("user:")) != event["member"]:
            raise ValueError()
        google, app = event["actor"].split("|")
        if member_for(google) != "user:" + google:
            raise ValueError()
        if not re.fullmatch(r"mmhps20261007:[a-z0-9][a-z0-9._-]{1,30}", app):
            raise ValueError()
        for key in ("before_sha256", "proposed_sha256", "readback_sha256"):
            if key in event and not re.fullmatch(r"[0-9a-f]{64}", event[key]):
                raise ValueError()
        for key in ("changed", "conditional_entry_for_target"):
            if key in event and type(event[key]) is not bool:
                raise ValueError()
        if event["phase"] == "verified" and event["effective_access_verified"] is not False:
            raise ValueError()
        if event["phase"] == "unconfirmed" and event["stage"] not in ("write", "readback"):
            raise ValueError()
        if "changed" in event and event["changed"] != (event["before_sha256"] != event["proposed_sha256"]):
            raise ValueError()
    except Exception:
        raise AuditError("audit_event_rejected") from None


class GcsAuditObjects:
    """Fixed bucket/prefix, create-if-absent, then exact-generation media read."""
    def __init__(self, authorized_http):
        self.http = authorized_http

    @staticmethod
    def _key(key):
        if not re.fullmatch(re.escape(PREFIX) + r"[0-9a-f-]{36}/(?:intent|outcome)\.json", key):
            raise AuditError("audit_key_rejected")
        return quote(key, safe="")

    @staticmethod
    def _generation(value):
        if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,24}", value):
            raise AuditError("audit_generation_rejected")
        return value

    def create(self, key, data):
        self._key(key)
        if not isinstance(data, bytes) or not 0 < len(data) <= LIMIT:
            raise AuditError("audit_payload_rejected")
        try:
            response = self.http.post(
                f"https://storage.googleapis.com/upload/storage/v1/b/{BUCKET}/o",
                params={"uploadType": "media", "name": key, "ifGenerationMatch": "0"},
                data=data, headers={"Content-Type": "application/json"},
                timeout=20, allow_redirects=False)
            if response.status_code not in (200, 201):
                raise ValueError()
            metadata = response.json()
            if metadata["bucket"] != BUCKET or metadata["name"] != key or metadata["size"] != str(len(data)):
                raise ValueError()
            generation = self._generation(metadata["generation"])
            if self.read(key, generation=generation)[1] != data:
                raise ValueError()
            return generation
        except Exception:
            raise AuditError("audit_create_or_readback_unconfirmed") from None

    def read(self, key, *, generation=None):
        escaped = self._key(key)
        url = f"https://storage.googleapis.com/storage/v1/b/{BUCKET}/o/{escaped}"
        try:
            if generation is None:
                metadata = self.http.get(url, timeout=20, allow_redirects=False)
                if metadata.status_code != 200:
                    raise ValueError()
                body = metadata.json()
                if body["bucket"] != BUCKET or body["name"] != key or not 0 < int(body["size"]) <= LIMIT:
                    raise ValueError()
                generation = self._generation(body["generation"])
            generation = self._generation(generation)
            response = self.http.get(url, params={"alt": "media", "generation": generation,
                "ifGenerationMatch": generation}, timeout=20, allow_redirects=False)
            if response.status_code != 200 or not 0 < len(response.content) <= LIMIT:
                raise ValueError()
            return generation, response.content
        except Exception:
            raise AuditError("audit_read_unconfirmed") from None


class ManagementAudit:
    """Intent before IAM; one create-only terminal outcome bound to that intent."""
    def __init__(self, objects, *, clock=None):
        self.objects = objects
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def append(self, event):
        validate_event(event)
        # Copy so caller mutation cannot change validation/persistence in flight.
        event = json.loads(encode(event))
        root = PREFIX + event["operation_id"] + "/"
        predecessor = None
        if event["phase"] != "intent":
            try:
                generation, data = self.objects.read(root + "intent.json")
                intent = json.loads(data)
                validate_event(intent["event"])
                if (set(intent) != {"schema", "recorded_at", "event", "intent"}
                        or intent["schema"] != "mmh-management-audit-v1"
                        or intent["intent"] is not None or intent["event"]["phase"] != "intent"
                        or any(intent["event"][key] != event[key] for key in BASE)):
                    raise ValueError()
                predecessor = {"key": root + "intent.json", "generation": generation,
                               "sha256": digest(data)}
            except Exception:
                raise AuditError("audit_intent_missing_or_mismatched") from None
        now = self.clock()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise AuditError("audit_clock_rejected")
        envelope = {"schema": "mmh-management-audit-v1", "event": event,
                    "recorded_at": now.astimezone(timezone.utc).isoformat(),
                    "intent": predecessor}
        key = root + ("intent.json" if event["phase"] == "intent" else "outcome.json")
        self.objects.create(key, encode(envelope))
