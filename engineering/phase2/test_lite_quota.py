"""Offline Lite allowance contract. No cloud credentials, model or real images.

This tests sequential policy, NOT a distributed/transactional credit ledger.
"""
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Backend" / "Flask"))
os.environ["WOUNDAI_STORE"] = "local"
os.environ.pop("LITE_LIMIT_ANON", None)

from flask import Flask
import numpy as np
from PIL import Image
import api_lite as lite
import api_flywheel as fw
from store import LocalStore


class LiteQuotaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = LocalStore(self.tmp.name)
        for target, value in [("FLYWHEEL_DIR", self.tmp.name), ("_store", lambda: self.store)]:
            p = patch.object(fw, target, value)
            p.start()
            self.addCleanup(p.stop)
        for target, value in [("LIMIT_PER_IP", 200), ("LIMIT_ATTEMPT_PER_ANON", 30),
                              ("LIMIT_ANNOTATION_PER_ANON", 30), ("FACE_REJECT", False),
                              ("_SEGMENT", self.segment)]:
            p = patch.object(lite, target, value)
            p.start()
            self.addCleanup(p.stop)
        app = Flask(__name__)
        app.register_blueprint(lite.lite_bp)
        self.client = app.test_client()
        buf = io.BytesIO()
        Image.new("RGB", (64, 64), "white").save(buf, "JPEG")
        self.jpeg = buf.getvalue()

    @staticmethod
    def segment(rgb):
        mask = np.zeros(rgb.shape[:2], np.uint8)
        mask[10:40, 10:40] = 1
        return mask

    def post(self, anon="test-installation", raw=None):
        return self.client.post("/api/v1/lite/segment", data={
            "anon_id": anon, "research_consent": "true",
            "image": (io.BytesIO(self.jpeg if raw is None else raw), "synthetic.jpg"),
        })

    def snapshot(self):
        return lite.quota_snapshot("test-installation", "127.0.0.1")[0]

    def test_default_is_five(self):
        self.assertEqual(lite.LIMIT_PER_ANON, 5)

    def test_five_successes_then_sixth_is_blocked(self):
        for remaining in [4, 3, 2, 1, 0]:
            response = self.post()
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json["quota"]["remaining"], remaining)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
        response = self.post()
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json["reason"], "device_quota")
        self.assertEqual(response.json["quota"]["used"], 5)
        self.assertGreater(response.json["retry_after"], 0)

    def test_annotations_work_after_fifth_and_do_not_spend_inference(self):
        for _ in range(5):
            image_id = self.post().json["image_id"]
        for _ in range(2):
            response = self.client.post("/api/v1/lite/annotation", json={
                "anon_id": "test-installation", "image_id": image_id,
                "research_consent": True, "image_w": 64, "image_h": 64,
                "polygons": [[[10, 10], [39, 10], [39, 39], [10, 39]]],
            })
            self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(self.snapshot()["used"], 5)

    def test_invalid_image_spends_attempt_not_inference(self):
        self.assertEqual(self.post(raw=b"not an image").status_code, 400)
        self.assertEqual(self.snapshot()["remaining"], 5)
        self.assertEqual(lite.quota_snapshot("test-installation", "127.0.0.1", "attempt")[0]["used"], 1)

    def test_model_error_does_not_spend_inference(self):
        with patch.object(lite, "_SEGMENT", side_effect=RuntimeError("synthetic failure")):
            self.assertEqual(self.post().status_code, 500)
        self.assertEqual(self.snapshot()["remaining"], 5)

    def test_empty_results_are_free_but_attempts_are_bounded(self):
        with patch.object(lite, "_SEGMENT", lambda rgb: np.zeros(rgb.shape[:2], np.uint8)), \
                patch.object(lite, "LIMIT_ATTEMPT_PER_ANON", 2):
            for _ in range(2):
                response = self.post()
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json["quota"]["remaining"], 5)
            response = self.post()
            self.assertEqual(response.status_code, 429)
            self.assertEqual(response.json["reason"], "attempt_quota")
            self.assertEqual(response.json["quota"]["remaining"], 5)

    def test_network_limit_does_not_claim_personal_balance_exhausted(self):
        with patch.object(lite, "LIMIT_PER_IP", 1):
            self.post("another-installation")
            response = self.post()
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json["reason"], "network_quota")
        self.assertEqual(response.json["quota"]["remaining"], 5)
        self.assertIn("網路", response.json["message"])

    def test_legacy_rows_survive_limit_change_without_reset(self):
        day = lite.time.strftime("%Y%m%d", lite.time.gmtime())
        for _ in range(7):
            fw.append_jsonl(lite._rate_path(day), {"anon_id": "test-installation"})
        self.assertEqual(self.snapshot()["used"], 7)
        self.assertEqual(self.snapshot()["remaining"], 0)
        self.assertEqual(self.post().status_code, 429)

    def test_utc_boundary_starts_a_new_allowance(self):
        with patch.object(lite.time, "time", return_value=1704153599):
            lite.rate_record("test-installation", "127.0.0.1")
            self.assertEqual(self.snapshot()["remaining"], 4)
            self.assertEqual(self.snapshot()["resets_at"], "2024-01-02T00:00:00Z")
        with patch.object(lite.time, "time", return_value=1704153600):
            self.assertEqual(self.snapshot()["remaining"], 5)

    def test_ledger_failure_does_not_admit_inference(self):
        with patch.object(fw, "read_jsonl", side_effect=IOError("unavailable")), \
                patch.object(lite, "_SEGMENT") as model:
            self.assertEqual(self.post().status_code, 500)
            model.assert_not_called()

    def test_malformed_ledger_does_not_reset_allowance(self):
        with patch.object(fw, "read_jsonl", return_value=([], 1)), \
                patch.object(lite, "_SEGMENT") as model:
            self.assertEqual(self.post().status_code, 500)
            model.assert_not_called()

    def test_post_success_snapshot_failure_does_not_report_model_failure(self):
        original = lite.quota_snapshot
        calls = 0
        def observed(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise IOError("readback unavailable")
            return original(*args, **kwargs)
        with patch.object(lite, "quota_snapshot", side_effect=observed):
            response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("quota", response.json)
        self.assertEqual(self.snapshot()["used"], 1)


if __name__ == "__main__":
    unittest.main()
