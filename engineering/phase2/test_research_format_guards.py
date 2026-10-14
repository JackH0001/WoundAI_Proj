"""Adversarial tissue ingestion/readback and multi-component export regressions."""
import base64
import hashlib
import importlib.util
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'Backend/Flask'))
sys.path.insert(0, str(Path(__file__).parent))
from tissue_mask_contract import validate_tissue_mask
import test_medical_revision_assets as fixtures
png = fixtures.png
import api_flywheel as fw


def labels(mode='RGBA', size=(8, 8), value=(5, 0, 0, 255)):
    out = io.BytesIO(); Image.new(mode, size, value).save(out, 'PNG'); return out.getvalue()


class TissueContract(unittest.TestCase):
    def setUp(self):
        self.r = dict(rx0=2, ry0=3, mw=8, mh=8, m_scale=.5)
    def test_valid_mobile_and_grayscale(self):
        for raw in (labels(), png(0), png(5)):
            validate_tissue_mask(raw, self.r, 32, 32)
    def test_dimensions_and_labels_rejected(self):
        for raw in (labels(size=(7,8)), png(6), labels(value=(1,1,0,255)), labels(value=(1,0,0,0)), labels('P', value=1), labels()[:40]):
            with self.subTest(raw_len=len(raw)), self.assertRaises(ValueError):
                validate_tissue_mask(raw, self.r, 32, 32)
    def test_geometry_adversarial(self):
        for updates in ({'mw':True}, {'mh':0}, {'rx0':-20}, {'ry0':30}, {'m_scale':0}, {'m_scale':float('nan')}, {'m_scale':float('inf')}, {'rx0':10**500}):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                validate_tissue_mask(png(1), dict(self.r, **updates), 32, 32)
    def test_missing_geometry_not_guessed(self):
        for r in (None, {}, {'mw':8,'mh':8}, dict(self.r,mw=1000000,mh=1000000)):
            with self.subTest(r=r), self.assertRaises(ValueError):
                validate_tissue_mask(png(1), r,32,32)
    def test_huge_header_rejected_before_decoder(self):
        raw=bytearray(png(1));raw[16:20]=(100000).to_bytes(4,'big');raw[20:24]=(100000).to_bytes(4,'big')
        with patch('tissue_mask_contract.Image.open',side_effect=AssertionError('decoder must not run')):
            with self.assertRaises(ValueError):validate_tissue_mask(bytes(raw),self.r,32,32)
    def test_partial_roi_valid(self):
        validate_tissue_mask(png(1),dict(self.r,rx0=-2),32,32)
    def test_unknown_geometry_not_inferred(self):
        with self.assertRaises(ValueError):
            validate_tissue_mask(png(2),dict(x=2,y=2,w=18,h=18,mw=8,mh=8),32,32)


class TissueHTTP(unittest.TestCase):
    # Reuse the isolated HTTP fixture, not its inherited test cases.
    def setUp(self):
        fixtures.RevisionAssets.setUp(self)
        from _synthetic_receipts import ratify_synthetic
        ratify_synthetic(self.tmp.name, [self.iid])
        from consent_staging import image_is_ratified
        self.receipts = patch.object(fw, 'trainable_image_receipt', side_effect=lambda row,*a,**k:
                                    image_is_ratified(self.st, row['image_id'], row.get('code'), row.get('org')))
        self.receipts.start()
        self.addCleanup(self.receipts.stop)
    tearDown = fixtures.RevisionAssets.tearDown
    annotation = fixtures.RevisionAssets.annotation
    rows = fixtures.RevisionAssets.rows
    def payload(self):
        self.annotation()
        row=self.rows()[0]
        return {**{k:row[k] for k in fw.REQUIRED+fw.PROVENANCE if k in row}, 'tissue_mask_png':base64.b64encode(png(1)).decode()}
    def test_reject_before_storage(self):
        body=self.payload(); before=self.rows()
        for changes in ({'tissue_mask_png':base64.b64encode(png(255)).decode()}, {'tissue_raster':dict(mw=7,mh=8,x=2,y=2,w=18,h=18)}, {'tissue_mask_png':'bad!'}, {'tissue_edited':'yes'}, {'tissue_edit_ratio':1.5}, {'tissue_edit_px':65}, {'tissue_raster':None}):
            with patch.object(fw,'store_revision_asset', wraps=fw.store_revision_asset) as save, patch.object(fw,'audit_intent', wraps=fw.audit_intent) as intent:
                resp=self.client.post('/api/v1/annotation',headers=self.headers['dr1'],json={**body,**changes})
                self.assertEqual(resp.status_code,400,resp.json);save.assert_not_called();intent.assert_not_called()
                self.assertEqual(resp.json['error'],'tissue_mask_invalid')
                self.assertEqual(fw.read_jsonl(fw.AUDIT)[-1]['action'],'tissue_mask_rejected')
                self.assertEqual(self.rows(),before)
    def test_export_revalidates_even_matching_digest(self):
        self.annotation(); row=self.rows()[0]; raw=png(255)
        self.st.put_blob(row['tissue_mask_key'],raw)
        row['tissue_mask_sha256']=hashlib.sha256(raw).hexdigest()
        original=fw.read_jsonl
        with patch.object(fw,'read_jsonl',side_effect=lambda path,*a,**k:(([row],0) if k.get('with_bad') else [row]) if path==fw.QUEUE else original(path,*a,**k)):
            result=self.client.get('/api/v1/dataset/manifest?source=sample',headers=self.headers['admin'])
            self.assertEqual(result.status_code,200,result.json)
            self.assertEqual(result.json['count'],0)
            self.assertEqual(result.json['excluded']['tissue_geometry_or_revision_invalid'],1)
    def test_manifest_all_components(self):
        body=self.payload();body['gt_polygons']=[body['gt_polygon'],[[23,23],[30,23],[30,30],[23,30]]]
        result=self.client.post('/api/v1/annotation',headers=self.headers['dr1'],json=body)
        self.assertEqual(result.status_code,200,result.json)
        result=self.client.get('/api/v1/dataset/manifest?kind=wound&source=sample',headers=self.headers['admin'])
        self.assertEqual(result.json['items'][0]['gt_polygons'],body['gt_polygons'])
    def test_manifest_retains_contours_and_revision(self):
        self.annotation(); row=self.rows()[0]
        result=self.client.get('/api/v1/dataset/manifest?kind=wound&source=sample',headers=self.headers['admin'])
        self.assertEqual(result.status_code,200,result.json)
        item=result.json['items'][0]
        self.assertIsNone(item['gt_polygons'])
        self.assertEqual(item['gt_polygon'],row['gt_polygon'])
        self.assertEqual(item['annotation_receipt_id'],fw.revision_id(row))
        self.assertTrue(item['doctor_verified'])


class WoundExport(unittest.TestCase):
    def test_all_components_and_background(self):
        import export_flywheel_dataset as ex
        a=[[1,1],[4,1],[4,4],[1,4]]; b=[[10,10],[14,10],[14,14],[10,14]]
        rec=dict(gt_polygon=a,gt_polygons=[a,b])
        # Exercise both OpenCV and the Pillow fallback.
        for backend in (ex.cv2,None):
            with patch.object(ex,'cv2',backend):
                out=ex.rasterize_record(rec,20,20)
                self.assertEqual(int(out[2,2]),255);self.assertEqual(int(out[12,12]),255)
                self.assertEqual(int(out[7,7]),0)
                self.assertEqual(int(ex.rasterize_record(dict(gt_polygon=a),20,20)[12,12]),0)


if __name__=='__main__': unittest.main()
