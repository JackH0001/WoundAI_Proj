import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from PIL import Image
from lite_rgbd_preflight import inspect_record

class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        jpeg=io.BytesIO();Image.new('RGB',(8,6),1).save(jpeg,'JPEG')
        self.id=hashlib.sha1(jpeg.getvalue()).hexdigest()[:16]
        self.record=dict(image_id=self.id,assets={},pending_revision=1,acknowledged_revision=1)
        for suffix,mode,size in [('jpg','RGB',(8,6)),('depth.png','I;16',(8,6)),('conf.png','L',(8,6))]:
            out=io.BytesIO();Image.new(mode,size,255 if suffix=='conf.png' else 1).save(out,'JPEG' if suffix=='jpg' else 'PNG')
            self.put(suffix,out.getvalue())
        self.meta=dict(image_id=self.id,image_w=8,image_h=6,depth_format='png16_mm',depth_scale='0.001',
                       camera_intrinsics=dict(fx=10,fy=10,cx=4,cy=3))
        self.metadata()

    def put(self,suffix,raw):
        (self.root/(self.id+'.'+suffix)).write_bytes(raw)
        self.record['assets'][suffix]={'sha256':hashlib.sha256(raw).hexdigest()}

    def metadata(self): self.put('json',json.dumps(self.meta).encode())
    def check(self): return inspect_record(self.record,self.root)

    def test_valid_structure_never_authorizes_training(self):
        r=self.check();self.assertTrue(r['structural_checks_passed'])
        self.assertEqual(r['training_admission'],'not_evaluated')
        self.assertEqual(r['registration'],'not_verified')
        self.assertEqual(r['confidence_kind'],'validity_only')

    def test_asset_hash_change_detected(self):
        self.record['assets']['jpg']['sha256']='0'*64
        self.assertIn('hash_mismatch:jpg',self.check()['issues'])

    def test_actual_metadata_checked_not_report_copy(self):
        self.record['camera_intrinsics']=dict(fx=10,fy=10,cx=4,cy=3)
        self.meta['camera_intrinsics']['fx']=False;self.metadata()
        self.assertIn('invalid_intrinsics',self.check()['issues'])

    def test_pending_revision_rejected(self):
        self.record['acknowledged_revision']=0
        self.assertIn('latest_annotation_not_confirmed',self.check()['issues'])

    def test_unknown_units_rejected(self):
        self.meta['depth_scale']=1;self.metadata()
        self.assertIn('unknown_depth_encoding',self.check()['issues'])

    def test_dimension_and_mask_mismatch_detected(self):
        out=io.BytesIO();Image.new('I;16',(6,8),1).save(out,'PNG');self.put('depth.png',out.getvalue())
        self.assertIn('rgb_depth_aspect_mismatch',self.check()['issues'])
        self.assertIn('depth_validity_size_mismatch',self.check()['issues'])

    def test_undecodable_file_never_passes(self):
        self.put('depth.png',b'not png')
        with self.assertRaises(Exception):self.check()

    def test_path_escape_rejected(self):
        self.record['image_id']='../0123456789abcdef'
        with self.assertRaises(ValueError):self.check()

    def test_metadata_identity_mismatch(self):
        self.meta['image_id']='fedcba9876543210';self.metadata()
        self.assertIn('metadata_image_identity_mismatch',self.check()['issues'])

    def test_equal_invalid_revisions_do_not_mean_synchronized(self):
        for value in (-1,0,True,'1',None):
            with self.subTest(value=value):
                self.record.update(pending_revision=value,acknowledged_revision=value)
                self.assertIn('latest_annotation_not_confirmed',self.check()['issues'])

    def test_replacing_jpeg_and_manifest_hash_cannot_change_bound_identity(self):
        out=io.BytesIO();Image.new('RGB',(8,6),'red').save(out,'JPEG')
        self.put('jpg',out.getvalue())
        self.assertIn('rgb_image_identity_mismatch',self.check()['issues'])
        self.assertNotIn('hash_mismatch:jpg',self.check()['issues'])

    def test_renamed_png_is_not_jpeg(self):
        out=io.BytesIO();Image.new('RGB',(8,6),1).save(out,'PNG')
        self.put('jpg',out.getvalue())
        self.assertIn('rgb_not_jpeg',self.check()['issues'])

    def test_validity_mask_is_not_sensor_confidence(self):
        out=io.BytesIO();Image.new('L',(8,6),128).save(out,'PNG')
        self.put('conf.png',out.getvalue())
        self.assertIn('validity_mask_not_binary',self.check()['issues'])

if __name__=='__main__':unittest.main()
