"""Runtime guards for the legacy clinical measurement persistence endpoint.

``/api/analyze`` is deliberately not a training-data path, but it does write
clinical measurements to SQLite.  These checks prevent a JWT-only caller or a
failed audit/transaction from being represented as a successful record.
"""
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
FLASK = ROOT / "Backend" / "Flask"
sys.path.insert(0, str(FLASK))


class AnalysisPersistenceGuards(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="analysis-guard-synthetic-")
        os.environ["WOUNDAI_FLYWHEEL_DIR"] = cls.tmp.name
        os.environ["WOUNDAI_ENABLE_LITE_API"] = "0"
        import app as app_module
        import api_flywheel as fw
        from flask_jwt_extended import create_access_token
        cls.mod, cls.fw, cls.create_token = app_module, fw, create_access_token
        cls.client = app_module.app.test_client()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def token(self, role):
        with self.mod.app.app_context():
            return type(self).create_token(identity="default:synthetic",
                                            additional_claims={"role": role, "org": "default"})

    def request(self, role):
        return self.client.post("/api/analyze", data={
            "image": (io.BytesIO(b"synthetic-image"), "synthetic.jpg"),
        }, content_type="multipart/form-data", headers={
            "Authorization": "Bearer " + self.token(role),
            "Session-ID": "synthetic-session",
        })

    def patches(self, events, intent_side_effect=None, outcome_side_effect=None):
        def intent(*_args, **_kwargs):
            events.append("intent")
            if intent_side_effect:
                raise intent_side_effect

        def save(*_args, **_kwargs):
            events.append("save")

        def outcome(*_args, **_kwargs):
            events.append("outcome")
            if outcome_side_effect:
                raise outcome_side_effect

        return (patch.object(self.mod, "process_uploaded_image",
                             return_value=np.zeros((8, 8, 3), np.uint8)),
                patch.object(self.mod, "calculate_image_hash", return_value="a" * 40),
                patch.object(self.mod, "perform_comprehensive_analysis",
                             return_value={"measurements": {}, "confidence_metrics": {}}),
                patch.object(self.mod, "save_analysis_record", side_effect=save),
                patch.object(self.fw, "audit_intent", side_effect=intent),
                patch.object(self.fw, "audit", side_effect=outcome))

    def test_nonclinical_role_is_rejected_before_analysis_or_storage(self):
        with patch.object(self.mod, "process_uploaded_image") as image:
            response = self.request("patient")
        self.assertEqual(403, response.status_code)
        image.assert_not_called()

    def test_lite_public_routes_are_absent_without_explicit_feature_flag(self):
        rules = {rule.rule for rule in self.mod.app.url_map.iter_rules()}
        self.assertFalse(any(rule.startswith("/api/v1/lite/") for rule in rules), rules)
        health = self.client.get("/api/health").get_json() or {}
        self.assertIs(False, (health.get("services") or {}).get("lite_public_api_enabled"))

    def test_intent_precedes_sqlite_write_and_outcome(self):
        events = []
        patches = self.patches(events)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            response = self.request("physician")
        self.assertEqual(200, response.status_code)
        self.assertEqual(["intent", "save", "outcome"], events)

    def test_audit_failure_never_becomes_a_false_success(self):
        for phase in ("intent", "outcome"):
            with self.subTest(phase=phase):
                events = []
                patches = self.patches(
                    events,
                    intent_side_effect=RuntimeError("synthetic intent failure") if phase == "intent" else None,
                    outcome_side_effect=RuntimeError("synthetic outcome failure") if phase == "outcome" else None,
                )
                with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
                    response = self.request("physician")
                self.assertEqual(503, response.status_code)
                if phase == "intent":
                    self.assertEqual(["intent"], events)
                else:
                    self.assertEqual(["intent", "save", "outcome"], events)


if __name__ == "__main__":
    unittest.main(verbosity=2)
