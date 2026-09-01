"""Synthetic-only P0-4 regression suite. No server, cloud or patient data."""
import base64
import concurrent.futures
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from unittest.mock import patch

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Backend" / "Flask"))
from image_canonical import canonicalize, jpeg_safe_pass, InvalidImage
from consent_staging import (CareKeys, ConsentError, Promotion, issue_care, verify_care,
                             annotation_receipt, image_is_ratified, promotion_key)
from store import LocalStore, GcsStore, Store, ImmutableConflict


def key_config():
    return {"active_kid": "synthetic-v1", "keys": {"synthetic-v1": {
        "secret_b64": base64.urlsafe_b64encode(b"unit-test-only-not-a-secret-" * 2).decode().rstrip("=")}}}


def jpeg():
    pixels = np.zeros((20, 30, 3), np.uint8)
    pixels[:10, :15] = (40, 80, 200)
    pixels[10:, 15:] = (180, 120, 30)
    return cv2.imencode(".jpg", pixels, [cv2.IMWRITE_JPEG_QUALITY, 95])[1].tobytes()


class CanonicalTests(unittest.TestCase):
    def test_safe_jpeg_is_verbatim(self):
        raw = jpeg()
        self.assertTrue(jpeg_safe_pass(raw))
        c = canonicalize(raw)
        self.assertEqual(c.data, raw)
        np.testing.assert_array_equal(c.pixels, cv2.imdecode(np.frombuffer(c.data, np.uint8), cv2.IMREAD_COLOR))

    def test_orientation_all_eight_and_metadata_removal(self):
        for orientation in range(1, 9):
            with self.subTest(orientation=orientation):
                im = Image.open(io.BytesIO(jpeg()))
                exif = Image.Exif()
                exif[274] = orientation
                exif[270] = "synthetic metadata must disappear"
                out = io.BytesIO()
                im.save(out, "JPEG", exif=exif, quality=95)
                raw = out.getvalue()
                oriented = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
                expected = cv2.imencode(".jpg", oriented, [cv2.IMWRITE_JPEG_QUALITY, 95])[1].tobytes()
                c = canonicalize(raw)
                self.assertEqual(c.data, expected)
                self.assertTrue(c.had_metadata)
                self.assertNotIn(b"Exif", c.data)
                self.assertEqual(c.pixels.shape[:2], (30, 20) if orientation >= 5 else (20, 30))

    def test_opaque_png_to_jpeg(self):
        raw = cv2.imencode(".png", np.ones((10, 12, 3), np.uint8))[1].tobytes()
        c = canonicalize(raw)
        self.assertTrue(c.data.startswith(b"\xff\xd8"))
        self.assertEqual(c.pixels.shape, (10, 12, 3))

    def test_alpha_png_and_palette_transparency_rejected(self):
        for mode in ("RGBA", "LA", "P"):
            with self.subTest(mode=mode):
                im = Image.new(mode, (10, 10))
                out = io.BytesIO()
                im.save(out, "PNG", **({"transparency": 0} if mode == "P" else {}))
                with self.assertRaises(InvalidImage):
                    canonicalize(out.getvalue())

    def test_comment_and_jfif_thumbnail_force_reencode(self):
        raw = jpeg()
        comment = b"\xff\xfe" + struct.pack(">H", 2 + 9) + b"synthetic"
        self.assertTrue(canonicalize(raw[:2] + comment + raw[2:]).had_metadata)
        # Replace the normal 0x0 APP0 thumbnail by a 1x1 thumbnail.
        n = int.from_bytes(raw[4:6], "big")
        jfif = raw[6:6 + n - 2]
        thumb = jfif[:-2] + b"\x01\x01\x00\x00\x00"
        changed = raw[:2] + b"\xff\xe0" + struct.pack(">H", len(thumb) + 2) + thumb + raw[4 + n:]
        self.assertTrue(canonicalize(changed).had_metadata)

    def test_truncated_and_trailing_data_rejected(self):
        for raw in (b"", b"garbage", jpeg()[:-1], jpeg() + b"secret"):
            with self.subTest(size=len(raw)), self.assertRaises(InvalidImage):
                canonicalize(raw)


class StagingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="p0-4-synthetic-")
        self.addCleanup(self.tmp.cleanup)
        self.st = LocalStore(self.tmp.name)
        self.events, self.queue, self.withdrawn = [], [], set()
        self.clock = 1000000
        self.keys = CareKeys(key_config(), now=self.clock)
        self.svc = Promotion(self.st, lambda *a: self.events.append(a),
                             lambda c, i: c in self.withdrawn or i in self.withdrawn,
                             lambda: self.queue, self.keys, now=lambda: self.clock)
        self.image = canonicalize(jpeg())
        self.iid = self.image.image_id
        self.actor, self.org, self.code = "default:doctor", "default", "WD-synthetic"
        self.d = {"image_id": self.iid, "code": self.code, "image_w": 30, "image_h": 20,
                  "doctor_verified": True, "consent_train": True, "deidentified": True,
                  "gt_polygon": [[1, 1], [12, 1], [12, 12]], "exudate": 0, "source": "phantom"}
        self.rid = annotation_receipt(self.code, self.actor, self.iid, "poly", None)

    def token(self, role="nurse", org="default", code=None):
        return issue_care(self.st, self.keys, code or self.code, org + ":nurse", role,
                          org, self.svc.audit, self.clock)[0]

    def stage(self):
        return self.svc.stage(self.image, self.token(), self.actor, "physician", self.org)

    def prepare(self, rid=None, d=None, actor=None):
        return self.svc.prepare(d or self.d, actor or self.actor, "physician", self.org, rid or self.rid)

    def test_no_receipt_means_zero_writes(self):
        self.assertFalse(self.svc.stage(self.image, None, self.actor, "physician", self.org)["persisted"])
        self.assertEqual(list(Path(self.tmp.name).rglob("*")), [])

    def test_role_org_signature_expiry_rejected(self):
        token = self.token()
        for candidate, role, org, now in ((token, "lite", self.org, self.clock),
                                         (token, "physician", "other", self.clock),
                                         (token[:-2] + "zz", "physician", self.org, self.clock),
                                         (token, "physician", self.org, self.clock + 43200)):
            with self.subTest(role=role, org=org, now=now), self.assertRaises(ConsentError):
                verify_care(candidate, self.keys, role, org, now)
        self.assertFalse(self.st.list_keys("staging"))

    def test_handoff_and_short_lived_bind_has_no_identity(self):
        self.stage()
        b = self.st.get_json("staging_meta/bind/" + self.iid + ".json")
        self.assertFalse({"code", "actor", "org", "attested_by"} & set(b))
        self.assertFalse(self.st.exists("images/" + self.iid + ".jpg"))
        self.assertEqual(self.events[-1][2]["attested_by"], "default:nurse")

    def test_second_subject_cannot_overwrite_bind(self):
        self.stage()
        before = self.st.get_blob("staging_meta/bind/" + self.iid + ".json")
        with self.assertRaisesRegex(ConsentError, "promotion_code_mismatch"):
            self.svc.stage(self.image, self.token(code="WD-other"), self.actor, "physician", self.org)
        self.assertEqual(before, self.st.get_blob("staging_meta/bind/" + self.iid + ".json"))

    def test_receipt_durable_before_copy_queue_last(self):
        self.stage()
        copy_original = self.st.copy_immutable
        def checked_copy(src, dst):
            self.assertIsNotNone(self.st.get_json(promotion_key(self.iid)))
            self.assertEqual(self.queue, [])
            return copy_original(src, dst)
        with patch.object(self.st, "copy_immutable", checked_copy):
            fields = self.prepare()
        self.assertEqual(self.queue, [])
        self.assertTrue(self.st.exists("staging/" + self.iid + ".jpg"))
        self.queue.append({"annotation_receipt_id": fields["annotation_receipt_id"]})
        self.assertTrue(self.svc.finish(self.iid, self.rid))
        self.assertFalse(self.st.exists("staging/" + self.iid + ".jpg"))

    def test_promoted_identity_gate_even_after_bind_ttl(self):
        self.stage()
        self.prepare()
        self.st.delete("staging_meta/bind/" + self.iid + ".json")
        self.st.delete("staging/" + self.iid + ".jpg")
        for change in ({"code": "WD-other"}, {"image_w": 31}):
            with self.subTest(change=change), self.assertRaises(ConsentError):
                self.prepare(rid="f" * 16, d={**self.d, **change})
        self.assertFalse(self.prepare(rid="f" * 16, actor="default:second")["legacy_image"])
        self.assertFalse(any(e[0] == "staging_swept_early" for e in self.events))

    def test_layer_b_eight_states(self):
        self.stage()
        self.prepare()
        p = self.st.get_json(promotion_key(self.iid))
        seen = set()
        for i in (False, True):
            for s in (False, True):
                for q in (False, True):
                    with self.subTest(I=i, S=s, Q=q):
                        seen.add((i, s, q))
                        for prefix, present in (("images", i), ("staging", s)):
                            k = prefix + "/" + self.iid + ".jpg"
                            self.st.delete(k)
                            if present:
                                self.st.put_blob(k, self.image.data)
                        self.queue[:] = [{"annotation_receipt_id": self.rid}] if q else []
                        if not i and (not s or q):
                            with self.assertRaises(ConsentError): self.prepare()
                        else:
                            self.assertFalse(self.prepare()["legacy_image"])
                        self.assertEqual(self.st.get_json(promotion_key(self.iid)), p)
        self.assertEqual(len(seen), 8)

    def test_layer_a_parallel_four_states(self):
        self.stage()
        self.prepare()
        seen = set()
        for i in (False, True):
            for s in (False, True):
                with self.subTest(I=i, S=s):
                    seen.add((i, s))
                    self.events.clear()
                    for prefix, present in (("images", i), ("staging", s)):
                        k = prefix + "/" + self.iid + ".jpg"
                        self.st.delete(k)
                        if present: self.st.put_blob(k, self.image.data)
                    if i:
                        self.prepare(rid="f" * 16, actor="default:second")
                    else:
                        with self.assertRaisesRegex(ConsentError, "promotion_pending_original" if s else "promotion_lost"):
                            self.prepare(rid="f" * 16, actor="default:second")
                    self.assertFalse(any(e[0] == "staging_swept_early" for e in self.events))
        self.assertEqual(len(seen), 4)

    def test_repair_before_after_ttl_and_sweep_no_trigger_queue(self):
        self.stage()
        with patch.object(self.st, "copy_immutable", side_effect=IOError("synthetic crash")):
            with self.assertRaises(IOError): self.prepare()
        self.clock += 86401
        self.assertTrue(self.svc.repair(self.iid, "synthetic-operator"))
        self.assertFalse(self.st.exists("images/" + self.iid + ".jpg"))
        self.assertTrue(self.svc.repair(self.iid, "synthetic-operator", apply=True))
        self.assertEqual(self.queue, [])
        self.prepare(rid="f" * 16, actor="default:second")  # T-C7a: I & S
        self.queue.append({"annotation_receipt_id": "f" * 16})
        self.assertFalse(self.svc.sweep(self.iid, apply=True))  # T-C7c: not R_P
        self.assertTrue(self.st.exists("staging/" + self.iid + ".jpg"))
        self.st.delete("staging/" + self.iid + ".jpg")  # simulate TTL, not repair
        self.prepare(rid="f" * 16, actor="default:second")  # T-C7b: I & !S

    def test_withdrawal_rechecked_before_copy_repair_sweep(self):
        self.stage()
        with patch.object(self.st, "copy_immutable", side_effect=IOError("synthetic crash")):
            with self.assertRaises(IOError): self.prepare()
        self.withdrawn.add(self.code)
        self.clock += 86401
        with self.assertRaisesRegex(ConsentError, "withdrawn"): self.prepare()
        with self.assertRaisesRegex(ConsentError, "withdrawn"):
            self.svc.repair(self.iid, "synthetic", apply=True)
        self.assertFalse(self.st.exists("images/" + self.iid + ".jpg"))

    def test_legacy_default_excluded_and_identity_checked(self):
        self.assertFalse(image_is_ratified(self.st, self.iid))
        self.stage()
        self.prepare()
        self.assertTrue(image_is_ratified(self.st, self.iid, self.code, self.org))
        self.assertFalse(image_is_ratified(self.st, self.iid, "WD-other", self.org))
        self.assertFalse(image_is_ratified(self.st, self.iid, self.code, None))

    def test_key_rotation_preserves_binding_and_rejects_short_retention(self):
        self.stage()
        cfg = key_config()
        cfg["keys"]["synthetic-v1"].update(retired_at=self.clock, verify_until=self.clock + 37 * 86400)
        cfg["keys"]["synthetic-v2"] = {"secret_b64": cfg["keys"]["synthetic-v1"]["secret_b64"]}
        cfg["active_kid"] = "synthetic-v2"
        self.svc.keys = CareKeys(cfg, now=self.clock + 1)
        self.prepare()
        cfg["keys"]["synthetic-v1"]["verify_until"] -= 1
        with self.assertRaises(ConsentError): CareKeys(cfg, now=self.clock + 1)

    def test_protected_prefix_immutable_content_and_absolute_aliases(self):
        for key in ("receipts/promotion/x.json", "receipts/legacy_ratification.json", "audit.jsonl"):
            self.assertTrue(Store()._is_audit(key))
            self.st.put_json_immutable(key, {"synthetic": True})
            self.assertFalse(self.st.put_json_immutable(key, {"synthetic": True}))
            with self.assertRaises(ImmutableConflict): self.st.put_json_immutable(key, {"synthetic": False})
            for alias in (key, str(Path(self.tmp.name) / key)):
                with self.assertRaises(PermissionError): self.st.delete(alias)
                with self.assertRaises(PermissionError): self.st.move(alias, "elsewhere")
                with self.assertRaises(PermissionError): self.st.put_blob(alias, b"overwrite")
                if key.startswith("receipts/"):
                    with self.assertRaises(PermissionError): self.st.append_line(alias, "overwrite")
        self.assertFalse(Store()._is_audit("receiptsX/evil.json"))
        self.assertFalse(self.st.retention_info()["locked"])

    def test_concurrent_queue_append_keeps_one_receipt(self):
        row = {"annotation_receipt_id": self.rid, "synthetic": True}
        def put(_): return self.st.append_record_once("queue.jsonl", self.rid, row)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(put, range(8)))
        self.assertEqual(sum(results), 1)
        self.assertEqual(len(self.st.read_lines("queue.jsonl")), 1)

    def test_layer_a_new_legacy_all_four_states(self):
        for i in (False, True):
            for s in (False, True):
                with self.subTest(I=i, S=s), tempfile.TemporaryDirectory() as root:
                    st = LocalStore(root)
                    svc = Promotion(st, lambda *a: None, lambda *a: False, lambda: [], self.keys,
                                    now=lambda: self.clock)
                    if i: st.put_blob("images/" + self.iid + ".jpg", self.image.data)
                    if s:
                        # Explicit bind fixture is generated through the real stage path.
                        token = self.token()
                        svc.stage(self.image, token, self.actor, "physician", self.org)
                    if i or s:
                        result = svc.prepare(self.d, self.actor, "physician", self.org, self.rid)
                        self.assertEqual(result["legacy_image"], i)
                    else:
                        with self.assertRaisesRegex(ConsentError, "image_not_staged"):
                            svc.prepare(self.d, self.actor, "physician", self.org, self.rid)

    def test_repair_validation_failures_do_not_copy_or_enqueue(self):
        self.stage()
        with patch.object(self.st, "copy_immutable", side_effect=IOError("synthetic crash")):
            with self.assertRaises(IOError): self.prepare()
        self.clock += 86401
        bind_key = "staging_meta/bind/" + self.iid + ".json"
        original = self.st.get_blob(bind_key)
        for change in ({"schema": "invalid"}, {"subject_binding": "0" * 64}, {"kid": "gone"},
                       {"canonicalization_version": "other"}, {"sha256": "0" * 64}):
            with self.subTest(change=change):
                b = json.loads(original)
                self.st.put_blob(bind_key, json.dumps({**b, **change}).encode())
                with self.assertRaises(ConsentError): self.svc.repair(self.iid, "synthetic", apply=True)
                self.assertFalse(self.st.exists("images/" + self.iid + ".jpg"))
                self.assertEqual(self.queue, [])
        self.st.put_blob(bind_key, original)

    def test_gcs_protected_routing_precondition_and_retention_readback(self):
        from unittest.mock import Mock
        from google.api_core.exceptions import PreconditionFailed
        st = GcsStore.__new__(GcsStore)
        st.prefix = "flywheel"
        st._bucket_name, st._audit_bucket_name = "synthetic-main", "synthetic-audit"
        st._bucket, st._audit_bucket = Mock(), Mock()
        for k in ("receipts/promotion/x.json", "receipts/legacy_ratification.json", "audit.jsonl"):
            self.assertIs(st._target(k)[0], st._audit_bucket)
            with self.assertRaises(PermissionError): st.delete(k)
            with self.assertRaises(PermissionError): st.move(k, "other")
        self.assertIs(st._target("receiptsX/x.json")[0], st._bucket)
        blob = st._audit_bucket.blob.return_value
        data = b'{"synthetic":true}'
        blob.download_as_bytes.return_value = data
        self.assertTrue(st.put_json_immutable("receipts/test.json", {"synthetic": True}))
        self.assertEqual(blob.upload_from_string.call_args.kwargs,
                         {"content_type": "application/json", "if_generation_match": 0})
        blob.upload_from_string.side_effect = PreconditionFailed("synthetic conflict")
        self.assertFalse(st.put_json_immutable("receipts/test.json", {"synthetic": True}))
        with self.assertRaises(ImmutableConflict):
            st.put_json_immutable("receipts/test.json", {"synthetic": False})
        st._audit_bucket._properties = {"retentionPolicy": {"retentionPeriod": "220752000", "isLocked": False}}
        self.assertFalse(st.retention_info()["locked"])
        self.assertEqual(st.retention_info()["retention_seconds"], 220752000)
        self.assertNotIn("WORM", st.describe())
        st._audit_bucket = None
        with self.assertRaises(RuntimeError): st._target("receipts/test.json")

    def test_inventory_and_archive_are_list_only_with_pending_staging(self):
        self.stage()
        def snapshot():
            return {str(p.relative_to(self.tmp.name)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in Path(self.tmp.name).rglob("*") if p.is_file()}
        before = snapshot()
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "WOUNDAI_STORE": "local"}
        tool = ROOT / "engineering" / "phase2" / "p0_4_staging_tools.py"
        result = subprocess.run([sys.executable, str(tool), "inventory", "--root", self.tmp.name],
                                capture_output=True, text=True, encoding="utf-8", env=env, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        inventory = json.loads(result.stdout)
        self.assertEqual(inventory["objects"][0]["image_id"], self.iid)
        self.assertFalse({"code", "actor", "org"} & set(inventory["objects"][0]))
        archive = ROOT / "engineering" / "phase2" / "archive_flywheel_queue.py"
        result = subprocess.run([sys.executable, str(archive), "--flywheel-dir", self.tmp.name, "--dry-run"],
                                capture_output=True, text=True, encoding="utf-8", env=env, timeout=30)
        self.assertEqual(result.returncode, 2)
        self.assertIn("staging_not_empty", result.stdout)
        self.assertEqual(before, snapshot())

    def api(self):
        from flask import Flask
        from flask_jwt_extended import JWTManager, create_access_token
        import api_flywheel as fw
        import store
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.dict(os.environ, {"CARE_RECEIPT_SECRET": json.dumps(key_config())}))
        for name, child in (("FLYWHEEL_DIR", ""), ("QUEUE", "retrain_queue.jsonl"),
                            ("AUDIT", "audit.jsonl"), ("IMAGES_DIR", "images"),
                            ("WITHDRAWN", "withdrawn.jsonl"), ("RETRACTED", "retracted.jsonl"),
                            ("QUARANTINE_DIR", "quarantine")):
            stack.enter_context(patch.object(fw, name, str(Path(self.tmp.name) / child)))
        stack.enter_context(patch.object(store, "_ACTIVE", self.st))
        flask_app = Flask("synthetic-p0-4")
        flask_app.config.update(TESTING=True, JWT_SECRET_KEY="synthetic-test-only-" * 3)
        JWTManager(flask_app)
        flask_app.register_blueprint(fw.flywheel_bp)
        def headers(actor="default:doctor", role="physician", org="default"):
            with flask_app.app_context():
                tok = create_access_token(identity=actor, additional_claims={"role": role, "org": org})
            return {"Authorization": "Bearer " + tok}
        return flask_app.test_client(), headers, fw

    def test_runtime_attest_role_matrix(self):
        c, headers, _ = self.api()
        for role in ("physician", "nurse", "assistant", "admin", "engineer", "lite"):
            with self.subTest(role=role):
                res = c.post("/api/v1/consent/care/attest", json={"code": self.code}, headers=headers(role=role))
                self.assertEqual(res.status_code, 200 if role in ("physician", "nurse", "assistant") else 403)

    def test_runtime_annotation_duplicate_parallel_and_manifest(self):
        c, headers, fw = self.api()
        self.stage()
        for actor, expected in ((self.actor, "enqueued"), (self.actor, "duplicate_skipped"),
                                ("default:second", "enqueued")):
            res = c.post("/api/v1/annotation", json=self.d, headers=headers(actor=actor))
            self.assertEqual(res.status_code, 200, res.json)
            self.assertEqual(res.json["status"], expected)
        rows = fw.read_jsonl(fw.QUEUE)
        self.assertEqual(len(rows), 2)
        self.assertIsNone(rows[1]["supersedes"])
        self.assertFalse(self.st.exists("staging/" + self.iid + ".jpg"))
        bad = c.post("/api/v1/annotation", json={**self.d, "code": "WD-other"}, headers=headers())
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(bad.json["error"], "promotion_code_mismatch")
        statuses = {s for _, s in fw.classify_queue(rows)}
        self.assertEqual(statuses, {"trainable", "parallel_rater"})
        self.assertEqual(len(fw.effective_queue()[0]), 1)
        # Flags cannot launder a pre-hotfix image into ANY of the three consumers.
        self.st.put_blob("images/" + "f" * 16 + ".jpg", b"synthetic-legacy")
        legacy = {**rows[0], "image_id": "f" * 16, "legacy_image": False}
        self.assertEqual(fw.classify_queue([legacy])[0][1], "legacy_unratified")
        d = {k: v for k, v in self.d.items() if k != "source"}
        self.assertEqual(c.post("/api/v1/annotation", json=d, headers=headers()).status_code, 400)

    def test_runtime_classify_no_receipt_zero_writes_then_staging(self):
        _, _, fw = self.api()
        import importlib.util
        spec = importlib.util.spec_from_file_location("p0_4_backend_app", ROOT / "Backend" / "Flask" / "app.py")
        backend = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(backend)
        from flask_jwt_extended import create_access_token
        backend.app.config["TESTING"] = True
        tissue = {k: 0.0 for k in ("necrosis", "slough", "granulation", "epithelial", "other")}
        score = {k: 0 for k in ("tool", "area_subscore", "tissue_subscore", "exudate_subscore",
                                "total_partial_img", "total_full", "range_full")}
        with backend.app.app_context():
            token = create_access_token(identity=self.actor, additional_claims={"role": "physician", "org": self.org})
        h = {"Authorization": "Bearer " + token}
        with patch.object(backend, "_load_classify_mods", return_value=(lambda *a, **k: tissue, lambda *a: score, None)), \
             patch.object(backend, "segment_wound_ai", side_effect=lambda img: (np.ones(img.shape[:2]), 1.0)):
            c = backend.app.test_client()
            before = sorted(str(p) for p in Path(self.tmp.name).rglob("*"))
            res = c.post("/api/v1/classify", data={"image": (io.BytesIO(jpeg()), "test.jpg"), "escalate": "off"}, headers=h)
            self.assertEqual(res.status_code, 200, res.json)
            self.assertFalse(res.json["persisted"])
            self.assertIsNone(res.json["image_id"])
            self.assertEqual(before, sorted(str(p) for p in Path(self.tmp.name).rglob("*")))
            receipt = c.post("/api/v1/consent/care/attest", json={"code": self.code}, headers=h).json["care_receipt"]
            res = c.post("/api/v1/classify", data={"image": (io.BytesIO(jpeg()), "test.jpg"), "escalate": "off",
                                                 "care_receipt": receipt}, headers=h)
            self.assertEqual(res.status_code, 200, res.json)
            self.assertTrue(res.json["persisted"])
            self.assertEqual(res.json["image_id"], self.iid)
            self.assertTrue(self.st.exists("staging/" + self.iid + ".jpg"))
            self.assertFalse(self.st.exists("images/" + self.iid + ".jpg"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
