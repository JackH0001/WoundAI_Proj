"""Local Flask HTTP/storage contract for additive medical depth provenance.

Synthetic files only; run in a separate process. No cloud clients or credentials.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class MedicalDepthOrientationStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='woundai-orientation-')
        self.addCleanup(self.tmp.cleanup)
        clean = {k:v for k,v in os.environ.items() if not k.startswith(('WOUNDAI_', 'GOOGLE_', 'GCLOUD_', 'CLOUDSDK_'))}
        clean.update(WOUNDAI_STORE='local',WOUNDAI_FLYWHEEL_DIR=self.tmp.name)
        env = patch.dict(os.environ,clean,clear=True);env.start();self.addCleanup(env.stop)
        sys.path.insert(0,str(ROOT/'Backend/Flask'))
        self.addCleanup(lambda:sys.path.remove(str(ROOT/'Backend/Flask')))
        import importlib
        import store
        active=patch.object(store,"_ACTIVE",store.LocalStore(self.tmp.name))
        active.start();self.addCleanup(active.stop)
        import api_flywheel
        self.fw = importlib.reload(api_flywheel)
        from flask import Flask
        from flask_jwt_extended import JWTManager, create_access_token
        from PIL import Image
        self.app = Flask(__name__)
        self.app.config['JWT_SECRET_KEY'] = os.urandom(32).hex()
        JWTManager(self.app);self.app.register_blueprint(self.fw.flywheel_bp)
        self.client=self.app.test_client()
        with self.app.app_context():
            self.headers={'Authorization':'Bearer '+create_access_token(identity='default:dr01',
                additional_claims={'role':'physician','org':'default','user':'dr01'})}
        for sub in ['images','quarantine','tissue_masks','depth_maps']:
            (Path(self.tmp.name)/sub).mkdir(exist_ok=True)
        buf=io.BytesIO();Image.new('RGB',(640,480),(40,100,180)).save(buf,'JPEG')
        jpg=buf.getvalue();self.iid=hashlib.sha1(jpg).hexdigest()[:16]
        (Path(self.tmp.name)/'images'/f'{self.iid}.jpg').write_bytes(jpg)
        r=self.client.post('/api/v1/annotation',headers=self.headers,json={
            'code':'WD-ORIENTATION','gt_polygon':[[100,100],[300,100],[300,400],[100,400]],
            'exudate':1,'image_id':self.iid,'image_w':640,'image_h':480,'mm_per_px':0.5,
            'doctor_verified':True,'deidentified':True,'consent_train':True,
            'route':'cloud','source':'sample','depth_source':'lidar_local'})
        self.assertEqual(r.status_code,200,r.json)

    def upload(self,extra):
        raw=struct.pack('<192f',*([.30]*192))
        meta={'width':16,'height':12,'format':'f32_le_meters',
              'camera_intrinsics':{'fx':13,'fy':17,'cx':4,'cy':3,'ref_width':16,'ref_height':12},**extra}
        r=self.client.post('/api/v1/depth',headers=self.headers,
            data={'image_id':self.iid,'meta':json.dumps(meta),'depth_f32':(io.BytesIO(raw),'depth.f32')},
            content_type='multipart/form-data')
        self.assertEqual(r.status_code,200,r.json)
        self.last_response = r.json
        folder=Path(self.tmp.name)/'depth_maps'
        self.assertEqual((folder/f'{self.iid}.f32').read_bytes(),raw)
        saved=json.loads((folder/f'{self.iid}.meta.json').read_text())
        self.assertEqual(saved['camera_intrinsics'],meta['camera_intrinsics'])
        return saved

    def test_all_eight_origins_preserved_by_actual_http_and_store(self):
        for orientation in range(1,9):
            extra={'source_exif_orientation':orientation,'normalized_exif_orientation':1,
                   'orientation_status':'normalized','registration':'not_verified'}
            with self.subTest(orientation=orientation):
                saved=self.upload(extra)
                for key,value in extra.items():self.assertEqual(saved[key],value)

    def test_legacy_absence_and_explicit_unknown_are_not_invented(self):
        for extra in [{},{'orientation_status':'unknown','registration':'not_verified'}]:
            saved=self.upload(extra)
            self.assertNotIn('source_exif_orientation',saved)
            self.assertNotIn('normalized_exif_orientation',saved)
            for key,value in extra.items():self.assertEqual(saved[key],value)

    def test_identical_retry_and_json_key_order_keep_revision(self):
        extra={'source_exif_orientation':6,'normalized_exif_orientation':1,
               'orientation_status':'normalized','registration':'not_verified'}
        self.upload(extra); first=self.last_response
        self.assertEqual(first['comparison'],'first_upload')
        self.upload(dict(reversed(list(extra.items())))); second=self.last_response
        self.assertFalse(second['replaced_previous'])
        self.assertEqual(second['comparison'],'identical')
        self.assertEqual(first['depth_revision_id'],second['depth_revision_id'])

    def test_metadata_only_changes_are_revisions(self):
        self.upload({'source_exif_orientation':1}); first=self.last_response
        for changed in [
            {'source_exif_orientation':8},
            {'source_exif_orientation':8,'camera_intrinsics':
                {'fx':19,'fy':17,'cx':4,'cy':3,'ref_width':16,'ref_height':12}},
        ]:
            self.upload(changed); second=self.last_response
            self.assertEqual(first['depth_id'],second['depth_id'])
            self.assertTrue(second['replaced_previous'])
            self.assertEqual(second['comparison'],'changed')
            self.assertNotEqual(first['depth_revision_id'],second['depth_revision_id'])
            first=second

    def test_hashes_bind_actual_stored_bytes_and_audit_intent(self):
        self.upload({'source_exif_orientation':7})
        folder=Path(self.tmp.name)/'depth_maps'
        raw=(folder/f'{self.iid}.f32').read_bytes()
        meta=(folder/f'{self.iid}.meta.json').read_bytes()
        raw_sha=hashlib.sha256(raw).hexdigest(); meta_sha=hashlib.sha256(meta).hexdigest()
        combined=hashlib.sha256(b'woundai-medical-depth-v1\0'+bytes.fromhex(raw_sha)+bytes.fromhex(meta_sha)).hexdigest()
        row=self.fw.read_jsonl(self.fw.DEPTH_INDEX)[-1]
        self.assertEqual(row['sha256'],raw_sha)
        for record in [row,self.last_response]:
            self.assertEqual(record['meta_sha256'],meta_sha)
            self.assertEqual(record['payload_sha256'],combined)
            self.assertEqual(record['payload_hash_version'],1)
        self.assertEqual(self.last_response['depth_revision_id'],combined[:16])
        # Intercept the audit intent boundary without replacing storage.
        with patch.object(self.fw,'audit_intent',wraps=self.fw.audit_intent) as intent:
            self.upload({'source_exif_orientation':7})
        evidence=intent.call_args.args[5]
        self.assertEqual(evidence['meta_sha256'],meta_sha)
        self.assertEqual(evidence['payload_sha256'],combined)

    def test_legacy_index_without_metadata_hash_is_not_identical_evidence(self):
        self.upload({})
        row=self.fw.read_jsonl(self.fw.DEPTH_INDEX)[-1]
        self.fw.append_jsonl(self.fw.DEPTH_INDEX,{'image_id':self.iid,'sha256':row['sha256']})
        self.upload({})
        self.assertTrue(self.last_response['replaced_previous'])
        self.assertEqual(self.last_response['comparison'],'legacy_unverifiable')

    def test_rejected_metadata_preserves_assets_and_index(self):
        self.upload({'source_exif_orientation':6})
        folder=Path(self.tmp.name)/'depth_maps'
        old_meta=(folder/f'{self.iid}.meta.json').read_bytes()
        old_index=self.fw.read_jsonl(self.fw.DEPTH_INDEX)
        bad={'width':16,'height':12,'format':'f32_le_meters','camera_intrinsics':
             {'fx':0,'fy':17,'cx':4,'cy':3,'ref_width':16,'ref_height':12}}
        r=self.client.post('/api/v1/depth',headers=self.headers,
            data={'image_id':self.iid,'meta':json.dumps(bad),
                  'depth_f32':(io.BytesIO(struct.pack('<192f',*([.3]*192))),'depth.f32')},
            content_type='multipart/form-data')
        self.assertEqual(r.status_code,400)
        self.assertEqual((folder/f'{self.iid}.meta.json').read_bytes(),old_meta)
        self.assertEqual(self.fw.read_jsonl(self.fw.DEPTH_INDEX),old_index)


if __name__=='__main__':unittest.main()
