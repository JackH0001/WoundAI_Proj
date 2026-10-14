"""Actual iOS grid conventions and failure-before-assets for RGB-D."""
import base64
import io
import json
import struct
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'Backend/Flask'))
sys.path.insert(0,str(Path(__file__).parent))
import api_flywheel as fw
from medical_depth_geometry import validate_png_depth, validate_rgb_depth
import test_medical_revision_assets as fixtures


def png(size=(576,768),mode='I;16',value=300):
    out=io.BytesIO();Image.new(mode,size,value).save(out,'PNG');return out.getvalue()


class Geometry(unittest.TestCase):
    def meta(self):
        return dict(width=576,height=768,format='f32_le_meters',
                    camera_intrinsics=dict(fx=2800.,fy=2800.,cx=1512.,cy=2016.,ref_width=3024,ref_height=4032))
    def test_actual_ios_raw_grid(self):
        meta=self.meta();raw=struct.pack('<f',.3)*(576*768)
        self.assertTrue(fw.validate_depth_payload(raw,meta)[0])
        validate_rgb_depth(meta,1536,2048)
    def test_reference_aspect_rejected(self):
        meta=self.meta();meta['camera_intrinsics']['ref_width']=4032
        self.assertFalse(fw.validate_depth_payload(struct.pack('<f',.3)*(576*768),meta)[0])
    def test_rotated_and_wrong_rgb_rejected(self):
        for changes in ({'width':768,'height':576},{'rgb_w':1535,'rgb_h':2048},{'rgb_width':True,'rgb_height':2048}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                validate_rgb_depth({**self.meta(),**changes},1536,2048)
        with self.assertRaises(ValueError):validate_rgb_depth(self.meta(),2048,1536)
    def test_actual_ios_png_grid_and_reference(self):
        raw=png();ci=dict(fx=500.,fy=500.,cx=288.,cy=384.)
        self.assertEqual(validate_png_depth(raw,ci,1536,2048),'depth_png')
        self.assertEqual(validate_png_depth(raw,self.meta()['camera_intrinsics'],1536,2048),'reference')
    def test_png_rgb_aspect_mismatch(self):
        with self.assertRaises(ValueError):
            validate_png_depth(png(),dict(fx=500.,fy=500.,cx=288.,cy=384.),2048,1536)
    def test_invalid_png_intrinsics_and_confidence(self):
        ci=dict(fx=500.,fy=500.,cx=288.,cy=384.);raw=png()
        for changes in ({'fx':0},{'fy':-1},{'cx':float('nan')},{'cy':768},{'fx':True},{'ref_width':576}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                validate_png_depth(raw,{**ci,**changes},1536,2048)
        for bad in (png((575,768),'L',1),png(mode='RGB',value=(1,1,1)),raw[:40]):
            with self.subTest(size=len(bad)),self.assertRaises(ValueError):
                validate_png_depth(raw,ci,1536,2048,bad)
        validate_png_depth(raw,ci,1536,2048,png(mode='L',value=1))


class HTTP(unittest.TestCase):
    setUp=fixtures.RevisionAssets.setUp
    tearDown=fixtures.RevisionAssets.tearDown
    annotation=fixtures.RevisionAssets.annotation
    rows=fixtures.RevisionAssets.rows
    def payload(self):
        self.annotation();row=self.rows()[0]
        return {**{k:row[k] for k in fw.REQUIRED+fw.PROVENANCE if k in row},
                'tissue_mask_png':base64.b64encode(fixtures.png(1)).decode()}
    def test_raw_mismatch_no_write(self):
        rid=self.annotation();raw=struct.pack('<192f',*([.3]*192))
        meta=dict(width=16,height=12,format='f32_le_meters',camera_intrinsics=dict(fx=20,fy=20,cx=8,cy=6,ref_width=16,ref_height=12))
        with patch.object(fw,'store_revision_asset',wraps=fw.store_revision_asset) as save:
            resp=self.client.post('/api/v1/depth',headers=self.headers['dr1'],data=dict(image_id=self.iid,annotation_receipt_id=rid,meta=json.dumps(meta),depth_f32=(io.BytesIO(raw),'d.f32')))
            self.assertEqual(resp.status_code,400,resp.json);save.assert_not_called()
            self.assertEqual(fw.read_jsonl(fw.DEPTH_INDEX),[])
    def test_png_grid_persisted_and_bad_confidence_no_depth_assets(self):
        body=self.payload();body.update(depth_source='lidar',depth_map_png=base64.b64encode(png((16,16))).decode(),camera_intrinsics=dict(fx=20,fy=20,cx=8,cy=8))
        result=self.client.post('/api/v1/annotation',headers=self.headers['dr1'],json=body)
        self.assertEqual(result.status_code,200,result.json)
        self.assertEqual(self.rows()[-1]['camera_intrinsics_grid'],'depth_png')
        for bad in (png((16,12),'L',1),png((16,16),'RGB',(1,1,1))):
            body['depth_conf_png']=base64.b64encode(bad).decode()
            with patch.object(fw,'store_revision_asset',wraps=fw.store_revision_asset) as save:
                result=self.client.post('/api/v1/annotation',headers=self.headers['dr1'],json=body)
                self.assertEqual(result.status_code,200,result.json)
                self.assertFalse(any(c.args[0]=='depth_maps' for c in save.call_args_list))
                self.assertEqual(self.rows()[-1]['depth_source'],'rejected')
                self.assertNotIn('depth_map_key',self.rows()[-1])


if __name__=='__main__':unittest.main()
