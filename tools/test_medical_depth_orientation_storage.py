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


if __name__=='__main__':unittest.main()
