# -*- coding: utf-8 -*-
"""Pure, shared contract for the immutable clinical audit chain.

`api_flywheel` constructs and verifies records while `store` admits a record
to an irreversible GCS slot.  The validation must live in one module: copying
the current-field list or SHA-256 formula into both layers would let a future
version bump silently make the storage guard weaker than the verifier.
"""
import hashlib
import json
import re


# Field definitions are append-only.  Existing versions must never be edited:
# old locked records cannot be re-signed after a formula change.
CHAIN_FIELD_VERSIONS = {
    1: ("seq", "ts", "actor", "action", "code", "result", "prev"),
    2: ("seq", "ts", "actor", "role", "org", "action", "code", "result", "prev"),
    3: ("chain_v", "seq", "ts", "actor", "role", "org", "action", "code", "result", "prev"),
    4: ("chain_v", "nonce", "seq", "ts", "actor", "role", "org", "action", "code", "result", "prev"),
}
CHAIN_V = 4
UNVERSIONED_CHAIN_VERSIONS = (2, 1)
AUDIT_CHAIN_FIELDS = CHAIN_FIELD_VERSIONS[CHAIN_V]

_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_HEX_32 = re.compile(r"^[0-9a-f]{32}$")


def loads_json_object_strict(text: str) -> dict:
    """Parse one JSON object without silent duplicate-key or NaN coercion.

    Python's default ``json.loads`` follows the last duplicate-key occurrence.
    That is unacceptable for an audit record: the bytes being reviewed must map
    to exactly one field set.  This helper is shared by slot admission and the
    offline verifier so their definition of a valid v4 object cannot diverge.
    """
    def reject_duplicate_keys(pairs):
        record = {}
        for name, value in pairs:
            if name in record:
                raise ValueError("duplicate JSON key: " + name)
            record[name] = value
        return record

    def reject_non_finite(value):
        raise ValueError("non-finite JSON constant: " + value)

    record = json.loads(text, object_pairs_hook=reject_duplicate_keys,
                        parse_constant=reject_non_finite)
    if not isinstance(record, dict):
        raise ValueError("audit record must be a JSON object")
    return record


def audit_hash(record: dict, version: int = None) -> str:
    """Hash the fixed canonical representation of one declared chain version."""
    v = version if version is not None else record.get("chain_v", CHAIN_V)
    fields = CHAIN_FIELD_VERSIONS.get(v)
    if fields is None:
        raise ValueError("unknown chain_v: %r" % (v,))
    payload = json.dumps({k: record.get(k) for k in fields}, ensure_ascii=False,
                         sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_current_record(record: dict, seq: int) -> None:
    """Reject a noncanonical v4 slot before an immutable object is created.

    This is intentionally stricter than historical-chain verification.  A final
    WORM epoch starts empty and may admit only current, self-authenticating v4
    records; legacy versions belong only to the retained pre-lock epoch.
    """
    if not isinstance(record, dict):
        raise ValueError("chain slot record must be an object")
    expected_fields = set(AUDIT_CHAIN_FIELDS) | {"hash"}
    actual_fields = set(record)
    if actual_fields != expected_fields:
        missing = sorted(expected_fields - actual_fields)
        extra = sorted(actual_fields - expected_fields)
        raise ValueError("audit slot schema mismatch: missing=%s extra=%s" % (missing, extra))
    if type(record.get("seq")) is not int or record["seq"] != seq:
        raise ValueError("chain slot name/content seq mismatch")
    if type(record.get("chain_v")) is not int or record["chain_v"] != CHAIN_V:
        raise ValueError("new audit slots must declare current chain_v=%d" % CHAIN_V)
    if not isinstance(record.get("nonce"), str) or not _HEX_32.fullmatch(record["nonce"]):
        raise ValueError("new audit slots must carry a 32-lowerhex nonce")
    if not isinstance(record.get("hash"), str) or not _HEX_64.fullmatch(record["hash"]):
        raise ValueError("chain slot record must carry a SHA-256 hash")
    prev = record.get("prev")
    if prev != "GENESIS" and (not isinstance(prev, str) or not _HEX_64.fullmatch(prev)):
        raise ValueError("chain slot record must carry a valid prev link")
    # Required event fields cannot be omitted and folded into a deceptively
    # valid JSON null by dict.get() during hash calculation.  A new locked
    # epoch must never create identity-incomplete rows: unauthenticated and
    # maintenance events use explicit sentinel roles instead of null.
    for field in ("ts", "actor", "role", "org", "action", "code", "result"):
        if not isinstance(record.get(field), str) or not record[field]:
            raise ValueError("new audit slot requires non-empty %s" % field)
    if audit_hash(record, CHAIN_V) != record["hash"]:
        raise ValueError("chain slot self-hash mismatch")
