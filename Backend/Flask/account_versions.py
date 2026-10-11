"""Account history replay and optimistic version contract, without I/O."""
import hashlib
import json
import re

EMPTY_REVISION = "0" * 64


class AccountConflict(ValueError):
    pass


def revision(record):
    if record is None:
        return EMPTY_REVISION
    return hashlib.sha256(json.dumps(record, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def resolve(lines):
    """Replay legacy rows, then per-identity CAS chains regardless of listing order.

    A legacy write made after migration invalidates the chain root and fails
    closed. Mixed old/new runtime writers are deliberately unsupported.
    """
    current, chains = {}, {}
    for line in lines:
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict) or not row.get("org") or not row.get("user"):
            raise ValueError("invalid_account_history")
        identity = row["org"] + ":" + row["user"]
        marker = row.get("_account_cas")
        if marker is None:
            current[identity] = row
            continue
        if (not isinstance(marker, dict) or set(marker) != {"parent", "nonce"}
                or not isinstance(marker["parent"], str) or not re.fullmatch(r"[0-9a-f]{64}", marker["parent"])
                or not isinstance(marker["nonce"], str) or not re.fullmatch(r"[0-9a-f]{32}", marker["nonce"])):
            raise ValueError("invalid_account_version")
        chain = chains.setdefault(identity, {})
        if marker["parent"] in chain:
            raise ValueError("duplicate_account_version")
        chain[marker["parent"]] = row
    for identity, remaining in chains.items():
        parent = revision(current.get(identity))
        while parent in remaining:
            current[identity] = remaining.pop(parent)
            parent = revision(current[identity])
        if remaining:
            raise ValueError("orphaned_account_version")
    return current


def validate_append(lines, record):
    identity = record["org"] + ":" + record["user"]
    parent = record["_account_cas"]["parent"]
    current = resolve(lines)
    if revision(current.get(identity)) != parent:
        raise AccountConflict("stale_account_revision")
    resolve(list(lines) + [json.dumps(record)])
