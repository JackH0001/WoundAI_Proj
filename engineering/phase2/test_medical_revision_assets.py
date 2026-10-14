"""Synthetic HTTP regression: immutable revision assets and explicit RGB-D binding."""
import base64
import hashlib
import io
import json
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'Backend' / 'Flask'))
os.environ.setdefault('JWT_SECRET_KEY', 'synthetic-revision-tests-not-a-credential')
import api_flywheel as fw
from store import LocalStore
from flask import Flask
from flask_jwt_extended import JWTManager, create_access_token
from PIL import Image


def png(value):
    b = io.BytesIO(); Image.new('L', (8, 8), value).save(b, 'PNG'); return b.getvalue()


class RevisionAssets(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = LocalStore(self.tmp.name)
        self.mock = patch.object(fw, '_store', return_value=self.st); self.mock.start()
        self.app = Flask(__name__); self.app.config['JWT_SECRET_KEY'] = 'x' * 40
        JWTManager(self.app); self.app.register_blueprint(fw.flywheel_bp)
        self.client = self.app.test_client()
        with self.app.app_context():
            self.headers = {}
            for who, role in [('dr1', 'physician'), ('dr2', 'physician'), ('admin', 'admin')]:
                self.headers[who] = {'Authorization': 'Bearer ' + create_access_token(
                    identity='test:' + who, additional_claims={'role': role, 'org': 'test', 'user': who})}
        b = io.BytesIO(); Image.new('RGB', (32, 32), (35, 80, 90)).save(b, 'JPEG')
        self.rgb = b.getvalue(); self.iid = hashlib.sha1(self.rgb).hexdigest()[:16]
        self.st.put_blob('images/' + self.iid + '.jpg', self.rgb)

    def tearDown(self):
        self.mock.stop(); self.tmp.cleanup()

    def annotation(self, value=1, who='dr1'):
        d = dict(code='WD-SYNTH', image_id=self.iid, image_w=32, image_h=32,
                 gt_polygon=[[2, 2], [20, 2], [20, 20], [2, 20]], mm_per_px=.5,
                 exudate=0, doctor_verified=True, deidentified=True, consent_train=True,
                 source='sample', route='cloud', tissue_edited=True,
                 tissue_raster={'mw': 8, 'mh': 8, 'rx0': 2, 'ry0': 2, 'm_scale': 8/18},
                 tissue_mask_png=base64.b64encode(png(value)).decode())
        r = self.client.post('/api/v1/annotation', headers=self.headers[who], json=d)
        self.assertEqual(r.status_code, 200, r.json)
        return r.json['annotation_receipt_id']

    def depth(self, rid, value=.3, who='dr1'):
        raw = struct.pack('<256f', *([value] * 256))
        meta = dict(width=16, height=16, format='f32_le_meters',
                    camera_intrinsics=dict(fx=50., fy=50., cx=8., cy=8., ref_width=16, ref_height=16))
        data = dict(image_id=self.iid, meta=json.dumps(meta), depth_f32=(io.BytesIO(raw), 'd.f32'))
        if rid is not None: data['annotation_receipt_id'] = rid
        return self.client.post('/api/v1/depth', headers=self.headers[who], data=data)

    def rows(self): return fw.read_jsonl(fw.QUEUE)
    def depths(self): return fw.read_jsonl(fw.DEPTH_INDEX)
    def mask(self, rid, who='dr1'):
        return self.client.get('/api/v1/flywheel/record/' + self.iid + '/tissue_mask.png?revision=' + rid,
                               headers=self.headers[who])

    def test_legacy_2d_retry_returns_original_receipt_without_new_row(self):
        self.annotation()
        original = self.rows()[0]
        original.pop('revision_signature'); original.pop('asset_schema')
        original['depth_source'] = 'none'
        original['annotation_receipt_id'] = 'a' * 16
        # Reconstruct the exact Android-style payload from persisted metadata.
        payload = {k: original.get(k) for k in fw.REQUIRED + fw.PROVENANCE
                   if k in original and k not in ('tissue_mask_key', 'depth_map_key', 'depth_conf_key')}
        payload['tissue_mask_png'] = base64.b64encode(png(1)).decode()
        read_original = fw.read_jsonl
        with patch.object(fw, 'read_jsonl', side_effect=lambda path: [original] if path == fw.QUEUE else read_original(path)), \
             patch.object(self.st, 'append_record_once', wraps=self.st.append_record_once) as append:
            response = self.client.post('/api/v1/annotation', headers=self.headers['dr1'], json=payload)
            self.assertEqual(response.status_code, 200, response.json)
            self.assertEqual(response.json['status'], 'duplicate_skipped')
            self.assertEqual(response.json['annotation_receipt_id'], 'a' * 16)
            append.assert_not_called()

    def test_legacy_match_rejects_changed_score_depth_or_corrupt_mask(self):
        self.annotation(); rec = self.rows()[0]; rec.pop('revision_signature'); rec['depth_source']='none'
        sig = fw.legacy_2d_signature(rec); self.assertIsNotNone(sig)
        altered = dict(rec, exudate=2)
        self.assertNotEqual(sig, fw.legacy_2d_signature(altered))
        self.assertIsNone(fw.legacy_2d_signature(dict(rec, depth_source='lidar')))
        self.st.put_blob(rec['tissue_mask_key'], png(3))
        self.assertIsNone(fw.legacy_2d_signature(rec))

    def test_old_and_new_masks_remain_retrievable(self):
        a = self.annotation(1); b = self.annotation(2)
        rows = self.rows()
        self.assertNotEqual(rows[0]['tissue_mask_key'], rows[1]['tissue_mask_key'])
        self.assertEqual(rows[1]['supersedes_revision_ids'], [a])
        self.assertEqual(self.mask(a).data, png(1)); self.assertEqual(self.mask(b).data, png(2))
        self.assertTrue(all(r['image_sha256'] == hashlib.sha256(self.rgb).hexdigest() for r in rows))

    def test_duplicate_does_not_create_revision_or_replace_asset(self):
        a = self.annotation(); before = self.rows()
        self.assertEqual(a, self.annotation()); self.assertEqual(before, self.rows())
        self.assertEqual(self.mask(a).data, png(1))

    def test_parallel_actor_cannot_read_or_bind_other_revision(self):
        a = self.annotation(); b = self.annotation(2, 'dr2')
        self.assertEqual(self.mask(a, 'dr2').status_code, 404)
        self.assertEqual(self.depth(a, who='dr2').status_code, 409)
        self.assertEqual(self.depth(b, who='dr2').status_code, 200)

    def test_delayed_depth_binds_requested_revision_not_latest(self):
        a = self.annotation(1); b = self.annotation(2)
        self.assertEqual(self.depth(b, .4).status_code, 200)
        self.assertEqual(self.depth(a, .3).status_code, 200)
        index = fw.depth_index_map()
        for row, expected in zip(self.rows(), [.3, .4]):
            entry = fw.depth_for_record(row, index)
            self.assertEqual(entry['annotation_receipt_id'], row['annotation_receipt_id'])
            self.assertAlmostEqual(struct.unpack('<f', self.st.get_blob(entry['depth_key'])[:4])[0], expected)

    def test_new_mask_revision_reuses_same_depth_payload_without_losing_binding(self):
        a = self.annotation(1); first = self.depth(a)
        b = self.annotation(2); second = self.depth(b)
        self.assertEqual(second.json['comparison'], 'identical')
        self.assertEqual(first.json['payload_sha256'], second.json['payload_sha256'])
        self.assertNotEqual(first.json['annotation_receipt_id'], second.json['annotation_receipt_id'])
        entries = self.depths()
        self.assertEqual(entries[0]['depth_key'], entries[1]['depth_key'])
        self.assertEqual(len(fw.depth_index_map()), 2)

    def test_ambiguous_legacy_upload_is_rejected(self):
        self.annotation(1); self.annotation(2)
        self.assertEqual(self.depth(None).status_code, 409); self.assertFalse(self.depths())

    def test_depth_revisions_and_retry_keep_bytes_and_one_receipt(self):
        a = self.annotation(); self.assertEqual(self.depth(a).status_code, 200)
        old = self.depths()[0]; raw = self.st.get_blob(old['depth_key']); meta = self.st.get_blob(old['depth_meta_key'])
        self.assertEqual(self.depth(a).status_code, 200); self.assertEqual(len(self.depths()), 1)
        newer = self.depth(a, .4)
        self.assertEqual(newer.status_code, 200); self.assertFalse(newer.json['replaced_previous'])
        self.assertEqual(len(self.depths()), 2)
        self.assertEqual(self.st.get_blob(old['depth_key']), raw)
        self.assertEqual(self.st.get_blob(old['depth_meta_key']), meta)

    def test_wrong_rgb_rejects_depth(self):
        a = self.annotation(); self.st.put_blob('images/' + self.iid + '.jpg', b'changed')
        self.assertEqual(self.depth(a).status_code, 409); self.assertFalse(self.depths())

    def test_corrupt_existing_asset_cannot_be_overwritten_or_claimed_saved(self):
        a = self.annotation(); self.st.put_blob(self.rows()[0]['tissue_mask_key'], b'corrupt')
        self.assertEqual(self.mask(a).status_code, 409)
        with self.assertRaises(Exception): fw.store_revision_asset('tissue_masks', self.iid, png(1), '.png', 'image/png')
        self.assertEqual(self.st.get_blob(self.rows()[0]['tissue_mask_key']), b'corrupt')

    def test_legacy_mask_mismatch_fails_closed(self):
        a = self.annotation(); row = self.rows()[0]
        row.pop('tissue_mask_sha256'); row['tissue_mask_key'] = 'tissue_masks/' + self.iid + '.png'
        self.st.put_blob(row['tissue_mask_key'], png(2))
        with self.assertRaises(ValueError): fw.verified_tissue(row)

    def test_no_image_only_depth_join(self):
        a = self.annotation(); fw.append_jsonl(fw.DEPTH_INDEX, {'image_id': self.iid, 'depth_source':'lidar'})
        self.assertEqual(fw.depth_for_record(self.rows()[0], fw.depth_index_map()), {})

    def test_withdrawal_blocks_all_revision_read_and_depth(self):
        a = self.annotation(); b = self.annotation(2)
        fw.append_jsonl(fw.WITHDRAWN, {'code':'WD-SYNTH','image_id':self.iid})
        self.assertEqual(self.mask(a).status_code, 410); self.assertEqual(self.mask(b).status_code, 410)
        self.assertEqual(self.depth(a).status_code, 400)
        for rid in (a,b):
            r = self.client.get('/api/v1/flywheel/record/' + self.iid + '/preview.svg?revision=' + rid,
                                headers=self.headers['dr1'])
            self.assertEqual(r.status_code, 410)

    def test_scoring_or_calibration_change_is_a_distinct_revision(self):
        data = dict(code='WD-SYNTH', image_id=self.iid, image_w=32, image_h=32,
                    gt_polygon=[[2,2],[20,2],[20,20]], exudate=0,
                    doctor_verified=True, deidentified=True, consent_train=True, source='sample')
        first = self.client.post('/api/v1/annotation', headers=self.headers['dr1'], json=data)
        data['exudate'] = 2
        second = self.client.post('/api/v1/annotation', headers=self.headers['dr1'], json=data)
        self.assertEqual(first.status_code, 200); self.assertEqual(second.status_code, 200)
        self.assertNotEqual(first.json['annotation_receipt_id'], second.json['annotation_receipt_id'])
        self.assertEqual([r['exudate'] for r in self.rows()], [0,2])

    def test_digest_filename_order_cannot_reverse_revision_order(self):
        self.annotation(1); self.annotation(2)
        rows = self.rows()
        rows[0]['received_at'] = '2026-10-11T01:00:00Z'
        rows[1]['received_at'] = '2026-10-11T01:00:01Z'
        with patch.object(self.st, 'read_lines', return_value=[json.dumps(r) for r in reversed(rows)]):
            self.assertEqual(fw.read_jsonl(fw.QUEUE), rows)

    def test_immutable_depth_receipt_is_safe_under_concurrent_retries(self):
        from concurrent.futures import ThreadPoolExecutor
        digest = 'b' * 64
        record = {'depth_receipt_id':digest, 'annotation_receipt_id':'a' * 16}
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.st.append_record_once('depth_index.jsonl', digest,
                                  record, 'depth_receipt_id'), range(12)))
        self.assertEqual(sum(results), 1)
        self.assertEqual(len(self.st.read_lines('depth_index.jsonl')), 1)

    def test_gcs_assets_and_depth_receipts_use_create_only_preconditions(self):
        from store import GcsStore, ImmutableConflict
        from unittest.mock import Mock
        from google.api_core.exceptions import PreconditionFailed
        st = GcsStore.__new__(GcsStore)
        st.prefix = 'flywheel'; st._bucket_name = 'synthetic'; st._audit_bucket_name = 'synthetic-audit'
        st._bucket = Mock(); st._audit_bucket = Mock()
        blob = st._bucket.blob.return_value
        blob.download_as_bytes.return_value = b'raw'
        self.assertTrue(st.put_blob_immutable('depth_maps/synthetic/hash.f32', b'raw', 'application/octet-stream'))
        self.assertEqual(blob.upload_from_string.call_args.kwargs['if_generation_match'], 0)
        blob.upload_from_string.side_effect = PreconditionFailed('synthetic collision')
        self.assertFalse(st.put_blob_immutable('depth_maps/synthetic/hash.f32', b'raw'))
        with self.assertRaises(ImmutableConflict): st.put_blob_immutable('depth_maps/synthetic/hash.f32', b'other')
        record = {'depth_receipt_id':'a'*64, 'annotation_receipt_id':'b'*16}
        blob.upload_from_string.side_effect = None
        self.assertTrue(st.append_record_once('depth_index.jsonl', 'a'*64, record, 'depth_receipt_id'))
        self.assertEqual(blob.upload_from_string.call_args.kwargs['if_generation_match'], 0)
        blob.download_as_bytes.return_value = json.dumps(record).encode()
        blob.upload_from_string.side_effect = PreconditionFailed('synthetic retry')
        self.assertFalse(st.append_record_once('depth_index.jsonl', 'a'*64, record, 'depth_receipt_id'))

    def test_partial_write_failure_never_acknowledges_depth(self):
        a = self.annotation(); original = self.st.put_blob_immutable
        def failing(key, data, content_type='image/jpeg'):
            if key.endswith('.meta.json'): raise IOError('synthetic disk error')
            return original(key, data, content_type)
        with patch.object(self.st, 'put_blob_immutable', side_effect=failing):
            self.assertEqual(self.depth(a).status_code, 409)
        self.assertFalse(self.depths())
        self.assertEqual(self.depth(a).status_code, 200)

if __name__ == '__main__': unittest.main()
