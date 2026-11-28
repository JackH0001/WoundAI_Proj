"""Offline revision API tests: actual Flask route and LocalStore, synthetic image only."""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Backend" / "Flask"))
os.environ["WOUNDAI_STORE"] = "local"
from flask import Flask
import api_lite as lite
import api_flywheel as fw
from store import LocalStore


class LiteRevisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store = LocalStore(self.tmp.name)
        for obj, key, value in [(fw, "FLYWHEEL_DIR", self.tmp.name), (fw, "_store", lambda: self.store),
                                (lite, "rate_check", lambda *a: (True, 0, ""))]:
            p = patch.object(obj, key, value); p.start(); self.addCleanup(p.stop)
        self.app = Flask(__name__); self.app.register_blueprint(lite.lite_bp)
        self.store.put_blob("lite/install/image.jpg", b"synthetic placeholder")
        self.store.put_blob("lite/install/image.json", json.dumps({"image_w": 32, "image_h": 32}).encode())
        self.payload = dict(polygons=[[[0, 0], [10, 0], [10, 10]]], image_w=32, image_h=32,
                            surface_cm2=4.5, projected_cm2=4.2, volume_ml=1.0, max_depth_mm=2.1,
                            wound_id="9ce45d4c-241c-400a-a29b-9635908fd271", wound_side="left", wound_site="heel",
                            source="manual", consent_version="2026-10-03.1")

    def body(self, rev=1, payload=None):
        return dict(anon_id="install", image_id="image", revision=rev,
                    research_consent=True, payload_json=json.dumps(self.payload if payload is None else payload, sort_keys=True))

    def post(self, body=None):
        with self.app.test_client() as c:
            return c.post("/api/v1/lite/annotation/revision", json=self.body() if body is None else body)

    def rows(self):
        return lite._lite_label_rows_fresh()

    @property
    def charges(self):
        return [json.loads(line) for p in Path(self.tmp.name).glob("lite_annotation_rate_*.jsonl")
                for line in self.store.read_lines(p.name)]

    def test_current_ios_disclosure_is_accepted_for_ai_and_manual_revisions(self):
        import re
        source=(Path(__file__).resolve().parents[2]/"iOS/WoundLite/LiteApp.swift").read_text()
        values=re.findall(r'static let consentVersion = "([^"\n]+)"',source)
        self.assertEqual(len(values),1)
        for rev,kind in enumerate(["ai","manual"],1):
            result=self.post(self.body(rev,dict(self.payload,source=kind,consent_version=values[0])))
            self.assertEqual(result.status_code,200,result.json)
        for invalid in ["",None,"future",[],"2026-10-04.1 ",values[0]+" ","2026-10-16.2"]:
            self.assertEqual(self.post(self.body(3,dict(self.payload,consent_version=invalid))).status_code,400)

    def test_ai_measurement_is_persisted_but_never_counted_as_lay_label(self):
        p=dict(self.payload,source="ai")
        self.assertEqual(self.post(self.body(payload=p)).status_code,200)
        self.assertEqual(self.post(self.body(payload=p)).status_code,200)
        rows=self.rows();self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["label_grade"],"ai_unverified")
        self.assertEqual(rows[0]["surface_cm2"],4.5)
        self.assertEqual(rows[0]["wound_site"],"heel")
        self.assertEqual(lite._latest_lite_labels(rows),{})
        self.assertEqual(lite._latest_lite_labels(rows,include_ai=True)[("install","image")]["source"],"ai")
        self.assertFalse(self.store.exists("retrain_queue.jsonl"))

    def test_ai_to_manual_transition_is_a_new_revision_not_a_duplicate_upload(self):
        self.assertEqual(self.post(self.body(payload=dict(self.payload,source="ai"))).status_code,200)
        self.assertEqual(self.post(self.body(2)).status_code,200)
        rows=self.rows();self.assertEqual(len(rows),2)
        self.assertEqual(lite._latest_lite_labels(rows)[("install","image")]["revision"],2)
        self.assertEqual(self.store.get_blob("lite/install/image.jpg"),b"synthetic placeholder")
        self.assertEqual(self.post(self.body(payload=self.payload)).status_code,409)

    def test_reader_excludes_ai_from_label_statistics_and_overlay(self):
        import secrets
        from flask_jwt_extended import JWTManager,create_access_token
        self.app.config["JWT_SECRET_KEY"]=secrets.token_hex(32);JWTManager(self.app)
        self.store.append_line("lite_index.jsonl",json.dumps(dict(anon_id="install",image_id="image",polygons=1)))
        self.assertEqual(self.post(self.body(payload=dict(self.payload,source="ai"))).status_code,200)
        with self.app.app_context():token=create_access_token(identity="reviewer",additional_claims={"role":"admin"})
        headers={"Authorization":"Bearer "+token}
        with self.app.test_client() as c:
            response=c.get("/api/v1/lite/records",headers=headers)
            self.assertEqual(response.status_code,200,response.json)
            self.assertEqual(response.json["labels"],0)
            self.assertEqual(response.json["hard_with_lay"],0)
            row=response.json["records"][0]
            self.assertIsNone(row["lay_polygons"])
            self.assertEqual(row["measurement_source"],"ai")
            self.assertEqual(row["measurement"]["surface_cm2"],4.5)
            self.assertEqual(row["wound_location"]["wound_side"],"left")
            preview=c.get("/api/v1/lite/record/install/image/preview.svg",headers=headers)
            self.assertEqual(preview.status_code,200)
            self.assertNotIn(b'<polygon',preview.data)

    def test_newer_ai_measurement_does_not_relabel_older_human_label(self):
        self.assertEqual(self.post().status_code,200)
        self.assertEqual(self.post(self.body(2,dict(self.payload,source="ai",surface_cm2=5))).status_code,200)
        self.assertEqual(lite._latest_lite_labels(self.rows())[("install","image")]["revision"],1)
        self.assertEqual(lite._latest_lite_labels(self.rows(),include_ai=True)[("install","image")]["revision"],2)

    def test_other_site_accepts_fixed_code_and_preserves_midline(self):
        payload = dict(self.payload, wound_site="other", wound_side="midline")
        response = self.post(self.body(payload=payload))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.rows()[0]["wound_site"], "other")
        self.assertEqual(self.rows()[0]["wound_side"], "midline")
        invalid = self.post(self.body(rev=2, payload=dict(payload, wound_site="other patient name")))
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(len(self.rows()), 1)

    def test_retry_creates_one_label_and_one_charge(self):
        body = self.body()
        for _ in range(3):
            r = self.post(body); self.assertEqual(r.status_code, 200, r.json)
            self.assertEqual(r.json["payload_sha256"], hashlib.sha256(body["payload_json"].encode()).hexdigest())
        self.assertEqual(len(self.rows()), 1); self.assertEqual(len(self.charges), 1)
        self.assertEqual(self.rows()[0]["surface_cm2"], 4.5)

    def test_conflicting_revision_returns_409_without_overwrite(self):
        self.assertEqual(self.post().status_code, 200)
        r = self.post(self.body(payload=dict(self.payload, surface_cm2=9)))
        self.assertEqual(r.status_code, 409); self.assertEqual(self.rows()[0]["surface_cm2"], 4.5)

    def test_latest_selection_uses_revision_not_storage_order(self):
        self.assertEqual(self.post(self.body(2, dict(self.payload, surface_cm2=2))).status_code, 200)
        self.assertEqual(self.post().status_code, 200)
        for rows in [self.rows(), list(reversed(self.rows()))]:
            latest = lite._latest_lite_labels(rows)[("install", "image")]
            self.assertEqual(latest["revision"], 2); self.assertEqual(latest["surface_cm2"], 2)
        self.assertEqual(self.store.get_blob("lite/install/image.jpg"), b"synthetic placeholder")

    def test_concurrent_duplicates_create_one_slot(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            codes = list(pool.map(lambda _: self.post().status_code, range(12)))
        self.assertEqual(codes, [200] * 12)
        self.assertEqual(len(self.rows()), 1); self.assertEqual(len(self.charges), 1)

    def test_concurrent_conflict_has_only_one_winner(self):
        bodies = [self.body(payload=dict(self.payload, surface_cm2=i + 1)) for i in range(8)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            codes = list(pool.map(lambda b: self.post(b).status_code, bodies))
        self.assertEqual(codes.count(200), 1); self.assertEqual(codes.count(409), 7)
        self.assertEqual(len(self.rows()), 1)

    def test_failure_after_durable_append_can_retry_without_duplicate(self):
        with patch.object(lite, "_confirm_revision_usage", side_effect=IOError("simulated crash before accounting")):
            self.assertEqual(self.post().status_code, 503)
        self.assertEqual((len(self.rows()), len(self.charges)), (1, 0))
        self.assertEqual(self.post().status_code, 200)
        self.assertEqual(self.post().status_code, 200)
        self.assertEqual((len(self.rows()), len(self.charges)), (1, 1))

    def test_lost_rate_write_response_replay_keeps_first_charge(self):
        write = self.store.append_record_once
        def lose_reply(key, slot, row):
            created = write(key, slot, row)
            if key.startswith("lite_annotation_rate_"):
                raise IOError("committed before disconnect")
            return created
        with patch.object(self.store, "append_record_once", side_effect=lose_reply):
            self.assertEqual(self.post().status_code, 503)
        original = self.charges
        self.assertEqual(len(original), 1)
        with patch.object(lite, "_client_ip", return_value="192.0.2.2"):
            self.assertEqual(self.post().status_code, 200)
        self.assertEqual(self.charges, original)

    def test_delayed_confirmation_uses_original_utc_day_and_keeps_network_out_of_label(self):
        with patch.object(fw, "utc_now", return_value="2026-11-02T23:59:59Z"), patch.object(
                lite, "_confirm_revision_usage", side_effect=OSError("unavailable before midnight")):
            self.assertEqual(self.post().status_code, 503)
        label = self.rows()[0]
        self.assertNotIn("ip_hash", label)
        with patch.object(fw, "utc_now", return_value="2026-11-03T00:00:01Z"):
            self.assertEqual(self.post().status_code, 200)
        self.assertEqual(len(self.store.read_lines("lite_annotation_rate_20261102.jsonl")), 1)
        self.assertEqual(self.store.read_lines("lite_annotation_rate_20261103.jsonl"), [])
        self.assertEqual(self.rows(), [label])

    def test_legacy_revision_replay_never_invents_a_rate_entry(self):
        with patch.object(lite, "_confirm_revision_usage", side_effect=OSError("fixture")):
            self.assertEqual(self.post().status_code, 503)
        old = self.rows()[0]
        del old["rate_accounting"]
        self.store.put_blob("lite_labels.jsonl", (json.dumps(old)+"\n").encode())
        self.assertEqual(self.post().status_code, 200)
        self.assertEqual(self.rows(), [old])
        self.assertEqual(self.charges, [])

    def test_different_payload_cannot_complete_pending_accounting(self):
        with patch.object(lite, "_confirm_revision_usage", side_effect=OSError("before accounting")):
            self.assertEqual(self.post().status_code, 503)
        self.assertEqual(self.post(self.body(payload=dict(self.payload, surface_cm2=9))).status_code, 409)
        self.assertEqual(self.charges, [])
        self.assertEqual(self.post().status_code, 200)
        self.assertEqual(len(self.charges), 1)

    def test_retry_allowed_when_daily_annotation_limit_exhausted(self):
        self.assertEqual(self.post().status_code, 200)
        with patch.object(lite, "rate_check", return_value=(False, 60, "quota")):
            self.assertEqual(self.post().status_code, 200)
            self.assertEqual(self.post(self.body(2)).status_code, 429)

    def test_missing_image_dimension_mismatch_and_consent_fail_closed(self):
        b = self.body(); b["image_id"] = "missing"; self.assertEqual(self.post(b).status_code, 404)
        self.assertEqual(self.post(self.body(payload=dict(self.payload, image_w=16))).status_code, 409)
        b = self.body(); b["research_consent"] = False; self.assertEqual(self.post(b).status_code, 400)
        self.assertEqual(self.rows(), [])

    def test_withdrawn_identity_cannot_update_or_acknowledge_old_retry(self):
        self.assertEqual(self.post().status_code, 200)
        self.store.append_line("lite_labels.jsonl", json.dumps({"anon_id": "install", "action": "deleted"}))
        self.assertEqual(self.post().status_code, 410)
        self.assertEqual(lite._latest_lite_labels(self.rows()), {})

    def test_invalid_coordinates_numbers_free_text_and_identity_rejected(self):
        payloads = [dict(self.payload, image_w=True), dict(self.payload, surface_cm2=float("nan")),
                    dict(self.payload, polygons=[[[0, 0], [999, 0], [0, 1]]]),
                    dict(self.payload, wound_site="patient name"), dict(self.payload, unexpected="name"),
                    dict(self.payload, consent_version="old"), dict(self.payload, source="physician")]
        for p in payloads:
            self.assertEqual(self.post(self.body(payload=p)).status_code, 400)
        for field, value in [("revision", True), ("revision", 0), ("anon_id", "../escape"), ("image_id", "a/b"), ("payload_json", "\ud800")]:
            b = self.body(); b[field] = value; self.assertEqual(self.post(b).status_code, 400)
        self.assertEqual(self.rows(), [])

    def test_latest_selection_isolates_installations_and_legacy_rows(self):
        rows = [dict(anon_id="install", image_id="image", revision=2),
                dict(anon_id="other", image_id="image", revision=3),
                dict(anon_id="install", image_id="image")]
        latest = lite._latest_lite_labels(rows)
        self.assertEqual(latest[("install", "image")]["revision"], 2)
        self.assertEqual(latest[("other", "image")]["revision"], 3)

    def test_corrupt_ledger_rejects_instead_of_silently_losing_receipts(self):
        self.store.append_line("lite_labels.jsonl", "not json")
        self.assertEqual(self.post().status_code, 503)

    def test_gcs_conditional_slot_and_lexical_order_with_fake_transport(self):
        from google.api_core.exceptions import PreconditionFailed
        from test_audit_chain_concurrency import _FakeGcs, _FakeBlob, make_gcs_store
        fake = _FakeGcs(PreconditionFailed)
        gcs = make_gcs_store(fake)
        fake.put_object("flywheel/lite/install/image.jpg", b"synthetic")
        fake.put_object("flywheel/lite/install/image.json", json.dumps({"image_w": 32, "image_h": 32}).encode())
        with patch.object(_FakeBlob, "exists", lambda blob: blob.name in blob.store.objects, create=True), \
                patch.object(fw, "_store", return_value=gcs):
            self.assertEqual(self.post(self.body(2)).status_code, 200)
            self.assertEqual(self.post().status_code, 200)
            self.assertEqual(self.post().status_code, 200)
            self.assertEqual(self.post(self.body(payload=dict(self.payload, surface_cm2=9))).status_code, 409)
            rows = lite._lite_label_rows_fresh()
            self.assertEqual(len(rows), 2)
            self.assertEqual(lite._latest_lite_labels(rows)[("install", "image")]["revision"], 2)
        writes = [c for c in fake.calls if c[0] == "upload"]
        label_writes = [call for call in writes if "lite_labels.jsonl/" in call[1]]
        rate_writes = [call for call in writes if "lite_annotation_rate_" in call[1]]
        self.assertEqual(len(label_writes), 2)
        self.assertEqual(len(set(call[1] for call in rate_writes)), 2)
        self.assertEqual({call[1] for call in rate_writes},
                         {name for name in fake.objects if "lite_annotation_rate_" in name})
        self.assertEqual(len(rate_writes) + len(label_writes), len(writes))
        self.assertTrue(all(c[2] == 0 for c in writes))

    def test_wrong_payload_readback_is_not_acknowledged(self):
        original = self.store.append_record_once
        def corrupted(key, slot, row):
            return original(key, slot, dict(row, payload_sha256="wrong"))
        with patch.object(self.store, "append_record_once", side_effect=corrupted):
            self.assertEqual(self.post().status_code, 409)

    def test_index_only_revocation_blocks_new_revision(self):
        for action in ("deleted", "withdrawal_requested", "delete_incomplete"):
            with self.subTest(action=action):
                self.store.put_blob("lite_index.jsonl", json.dumps({"anon_id":"install", "action":action}).encode())
                self.assertEqual(self.post().status_code, 410)
                self.assertEqual(self.rows(), [])

    def test_index_only_revocation_blocks_existing_receipt_retry(self):
        self.assertEqual(self.post().status_code, 200)
        self.store.append_line("lite_index.jsonl", json.dumps({"anon_id":"install", "action":"delete_incomplete"}))
        self.assertEqual(self.post().status_code, 410)

    def test_corrupt_index_revocation_state_is_not_assumed_active(self):
        self.store.put_blob("lite_index.jsonl", b"not json")
        self.assertEqual(self.post().status_code, 503)
        self.assertEqual(self.rows(), [])

    def test_revocation_during_append_does_not_return_success_receipt(self):
        original=self.store.append_record_once
        def append_then_revoke(key, slot, row):
            created=original(key, slot, row)
            self.store.append_line("lite_index.jsonl", json.dumps({"anon_id":"install", "action":"withdrawal_requested"}))
            return created
        with patch.object(self.store, "append_record_once", side_effect=append_then_revoke):
            self.assertEqual(self.post().status_code, 410)

    def test_revocation_appearing_in_final_readback_is_not_acknowledged(self):
        original=lite._lite_label_rows_fresh
        reads=[]
        def read_then_revoke():
            rows=original()
            reads.append(1)
            if len(reads)==2:
                rows.append(dict(anon_id="install",action="withdrawal_requested"))
            return rows
        with patch.object(lite, "_lite_label_rows_fresh", side_effect=read_then_revoke):
            self.assertEqual(self.post().status_code, 410)

    def test_label_selection_excludes_all_revocation_states(self):
        for action in ("deleted", "withdrawal_requested", "delete_incomplete"):
            with self.subTest(action=action):
                rows=[dict(anon_id="install",image_id="image",revision=1),
                      dict(anon_id="install",action=action)]
                self.assertEqual(lite._latest_lite_labels(rows), {})

    def test_oversized_body_is_rejected_before_ledger_write(self):
        b = self.body(); b["payload_json"] = "x" * 262144
        self.assertEqual(self.post(b).status_code, 413)
        self.assertEqual(self.rows(), [])


if __name__ == "__main__":
    unittest.main()
