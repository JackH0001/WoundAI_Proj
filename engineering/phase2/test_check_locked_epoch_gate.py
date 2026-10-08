#!/usr/bin/env python3
"""Unit tests for the read-only locked-epoch pre-deployment gate."""

from __future__ import annotations

from contextlib import redirect_stdout
import io
import os
import sys
import types
import unittest
from unittest import mock

import check_locked_epoch_gate as gate


FORMAL_BUCKET = "woundai-flywheel-synthetic-audit-epoch-20990101"
MAIN_BUCKET = "woundai-flywheel-jackh001"


class _Blob:
    def __init__(self, name):
        self.name = name


class _Bucket:
    def __init__(self, *, project_number="421209514056", location="ASIA-EAST1", objects=None):
        self._properties = {"projectNumber": project_number, "location": location}
        self.objects = list(objects or [])

    def reload(self):
        return None

    def list_blobs(self, *, max_results):
        assert max_results == 1
        return iter([_Blob(name) for name in self.objects[:max_results]])


class _GcsStore:
    def __init__(self, *, info=None, objects=None, gate_error=None,
                 project_number="421209514056", location="ASIA-EAST1"):
        self.info = info or {
            "verified": True,
            "bucket": FORMAL_BUCKET,
            "locked": True,
            "retention_seconds": 220903200,
        }
        self.gate_error = gate_error
        self.calls = []
        self._bucket_name = MAIN_BUCKET
        self._audit_bucket_name = FORMAL_BUCKET
        self.prefix = "flywheel"
        self._audit_bucket = _Bucket(
            project_number=project_number, location=location, objects=objects
        )

    def retention_info(self):
        self.calls.append("retention_info")
        return dict(self.info)

    def require_locked_audit_epoch(self):
        self.calls.append("require_locked_audit_epoch")
        if self.gate_error:
            raise self.gate_error

def _store_module(instance):
    module = types.ModuleType("store")
    module.AUDIT_RETENTION_SECONDS = 220903200
    module.GcsStore = _GcsStore
    module.get_store = lambda _root: instance
    return module


class LockedEpochGateTests(unittest.TestCase):
    def run_mmh(self, instance, overrides=None, constructed_security=None):
        env = {"WOUNDAI_STORE": "gcs", "WOUNDAI_SERVICE_PROFILE": "medical",
               "WOUNDAI_INSTITUTION_ORG": "mmhps20261007", "WOUNDAI_GCS_PREFIX": "flywheel",
               "WOUNDAI_GCS_BUCKET": "woundai-mmhps20261007-media-421209514056",
               "WOUNDAI_SECURITY_BUCKET": "woundai-mmhps20261007-security-421209514056",
               "WOUNDAI_AUDIT_BUCKET": "woundai-mmhps20261007-audit-421209514056"}
        instance._bucket_name = env["WOUNDAI_GCS_BUCKET"]
        instance._audit_bucket_name = env["WOUNDAI_AUDIT_BUCKET"]
        instance._security_bucket_name = (env["WOUNDAI_SECURITY_BUCKET"]
                                          if constructed_security is None else constructed_security)
        instance.info["bucket"] = env["WOUNDAI_AUDIT_BUCKET"]
        env.update(overrides or {})
        output = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.dict(
            sys.modules, {"store": _store_module(instance)}
        ), redirect_stdout(output):
            code = gate.main(["--audit-bucket", instance._audit_bucket_name,
                              "--project-number", "421209514056", "--location", "asia-east1"])
        return code, output.getvalue()

    def test_mmh_exact_three_bucket_tuple_accepts_only_locked_empty_epoch(self):
        store = _GcsStore()
        code, _ = self.run_mmh(store)
        self.assertEqual(code, 0)
        self.assertEqual(store.calls, ["retention_info", "require_locked_audit_epoch"])

    def test_mmh_unlocked_and_wrong_retention_are_still_rejected(self):
        for field,value in [("locked", False), ("retention_seconds", 86400), ("verified", False)]:
            store = _GcsStore();store.info[field] = value
            code, _ = self.run_mmh(store)
            self.assertEqual(code, 1)

    def test_mmh_missing_or_wrong_institution_tuple_rejected_before_store(self):
        for field,value in [("WOUNDAI_INSTITUTION_ORG", "default"),
                            ("WOUNDAI_SERVICE_PROFILE", "lite"),
                            ("WOUNDAI_GCS_BUCKET", MAIN_BUCKET),
                            ("WOUNDAI_SECURITY_BUCKET", ""),
                            ("WOUNDAI_SECURITY_BUCKET", "other-security")]:
            store = _GcsStore()
            code, _ = self.run_mmh(store, {field:value})
            self.assertEqual(code, 2);self.assertEqual(store.calls, [])

    def test_mmh_constructed_security_bucket_mismatch_rejected_before_readback(self):
        for bucket in ["", "other-security"]:
            with self.subTest(bucket=bucket):
                store = _GcsStore()
                code, output = self.run_mmh(store, constructed_security=bucket)
                self.assertEqual(code, 2)
                self.assertIn("constructed GcsStore identity", output)
                self.assertEqual(store.calls, [])

    def test_mmh_nonempty_or_production_gate_failure_rejected(self):
        for store in [_GcsStore(objects=["outside-prefix/synthetic.json"]),
                      _GcsStore(gate_error=RuntimeError("retention unreadable"))]:
            code, _ = self.run_mmh(store)
            self.assertEqual(code, 1)

    def test_mmh_wrong_resource_project_or_location_rejected(self):
        for store in [_GcsStore(project_number="999"), _GcsStore(location="US")]:
            code, _ = self.run_mmh(store)
            self.assertEqual(code, 1)

    def run_gate(self, instance, *, store="gcs", bucket=FORMAL_BUCKET):
        env = {
            "WOUNDAI_STORE": store,
            "WOUNDAI_GCS_BUCKET": MAIN_BUCKET,
            "WOUNDAI_AUDIT_BUCKET": bucket,
            "WOUNDAI_GCS_PREFIX": "flywheel",
        }
        output = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=False), mock.patch.dict(
            sys.modules, {"store": _store_module(instance)}
        ), redirect_stdout(output):
            code = gate.main([
                "--audit-bucket", FORMAL_BUCKET,
                "--project-number", "421209514056",
                "--location", "asia-east1",
            ])
        return code, output.getvalue()

    def test_accepts_locked_empty_formal_epoch_without_a_write_api(self):
        store = _GcsStore()
        code, output = self.run_gate(store)
        self.assertEqual(code, 0)
        self.assertIn("PASS", output)
        self.assertEqual(
            store.calls,
            ["retention_info", "require_locked_audit_epoch"],
        )

    def test_rejects_unlocked_epoch_before_production_gate(self):
        store = _GcsStore(
            info={"verified": True, "bucket": FORMAL_BUCKET,
                  "locked": False, "retention_seconds": 220903200}
        )
        code, _ = self.run_gate(store)
        self.assertEqual(code, 1)
        self.assertEqual(store.calls, ["retention_info"])

    def test_rejects_wrong_retention(self):
        store = _GcsStore(
            info={"verified": True, "bucket": FORMAL_BUCKET,
                  "locked": True, "retention_seconds": 86400}
        )
        code, _ = self.run_gate(store)
        self.assertEqual(code, 1)

    def test_propagates_production_gate_failure_as_failed_check(self):
        store = _GcsStore(gate_error=RuntimeError("not locked"))
        code, output = self.run_gate(store)
        self.assertEqual(code, 1)
        self.assertIn("rejected", output)

    def test_rejects_nonempty_formal_epoch(self):
        store = _GcsStore(objects=["flywheel/receipts/promotion/example.json"])
        code, output = self.run_gate(store)
        self.assertEqual(code, 1)
        self.assertIn("not empty", output)

    def test_rejects_wrong_project_or_location(self):
        code, _ = self.run_gate(_GcsStore(project_number="999"))
        self.assertEqual(code, 1)
        code, _ = self.run_gate(_GcsStore(location="US"))
        self.assertEqual(code, 1)

    def test_refuses_disposable_or_non_gcs_targets(self):
        store = _GcsStore()
        code, _ = self.run_gate(store, store="local")
        self.assertEqual(code, 2)
        code, _ = self.run_gate(store, bucket="woundai-audit-smoke-epoch")
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
