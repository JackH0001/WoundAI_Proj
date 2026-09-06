#!/usr/bin/env python3
"""Read-only pre-deployment verification for the formal locked audit epoch.

The production GCS store refuses every audit write unless it can establish a
locked seven-year retention policy.  Run this after Bucket Lock and before a
candidate deployment to test that exact production gate without writing an
object.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
FLASK_ROOT = ROOT / "Backend" / "Flask"
sys.path.insert(0, str(FLASK_ROOT))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-bucket", required=True)
    parser.add_argument("--project-number", required=True)
    parser.add_argument("--location", required=True)
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    if os.environ.get("WOUNDAI_STORE") != "gcs":
        print("REFUSE: WOUNDAI_STORE must be gcs")
        return 2

    main_bucket = os.environ.get("WOUNDAI_GCS_BUCKET", "")
    audit_bucket = os.environ.get("WOUNDAI_AUDIT_BUCKET", "")
    lowered = audit_bucket.lower()
    if audit_bucket != args.audit_bucket or not main_bucket or main_bucket == audit_bucket:
        print("REFUSE: explicit audit/main bucket identity mismatch")
        return 2
    if os.environ.get("WOUNDAI_GCS_PREFIX") != "flywheel":
        print("REFUSE: WOUNDAI_GCS_PREFIX must be explicitly set to flywheel")
        return 2
    if "epoch" not in lowered or any(
        token in lowered for token in ("smoke", "test", "tmp", "temp", "dev", "sandbox")
    ):
        print(f"REFUSE: WOUNDAI_AUDIT_BUCKET is not a formal epoch bucket: {audit_bucket!r}")
        return 2

    import store as store_module

    store = store_module.get_store(None)
    if not isinstance(store, store_module.GcsStore):
        print(f"REFUSE: backend is {type(store).__name__}, not GcsStore")
        return 2

    if (
        store._bucket_name != main_bucket
        or store._audit_bucket_name != audit_bucket
        or store.prefix != "flywheel"
    ):
        print("REFUSE: constructed GcsStore identity does not match explicit inputs")
        return 2

    store._audit_bucket.reload()
    properties = store._audit_bucket._properties
    if str(properties.get("projectNumber", "")) != str(args.project_number):
        print("FAIL: audit bucket project number mismatch")
        return 1
    if str(properties.get("location", "")).upper() != args.location.upper():
        print("FAIL: audit bucket location mismatch")
        return 1

    info = store.retention_info()
    expected = {
        "verified": True,
        "bucket": audit_bucket,
        "locked": True,
        "retention_seconds": store_module.AUDIT_RETENTION_SECONDS,
    }
    mismatch = {
        key: {"actual": info.get(key), "expected": value}
        for key, value in expected.items()
        if info.get(key) != value
    }
    print(f"audit_bucket={audit_bucket}")
    print(f"retention_info={info}")
    if mismatch:
        print(f"FAIL: locked epoch mismatch: {mismatch}")
        return 1

    try:
        store.require_locked_audit_epoch()
    except Exception as exc:  # pragma: no cover - exercised against live GCS
        print(f"FAIL: production lock gate rejected the bucket: {exc!r}")
        return 1

    try:
        first_object = next(iter(store._audit_bucket.list_blobs(max_results=1)), None)
    except Exception as exc:  # pragma: no cover - exercised against live GCS
        print(f"FAIL: full-bucket object listing failed: {exc!r}")
        return 1
    if first_object is not None:
        print(f"FAIL: formal epoch is not empty (first_object={first_object.name!r})")
        return 1

    print("PASS: exact locked seven-year epoch accepted; whole bucket is empty; no object was written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
