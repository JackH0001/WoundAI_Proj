"""Real Flask endpoint and temporary LocalStore; no cloud credentials or network."""
import base64
import io
import json
import unittest
from unittest.mock import patch
import test_lite_quota as fixture
from test_lite_segment import png16
from PIL import Image


class StorageReceiptTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.LiteQuotaTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.store = self.fixture.store

    def post(self, depth=None, mask=None, consent=True):
        data = dict(anon_id="receipt-install", research_consent=str(consent).lower(),
                    image=(io.BytesIO(self.fixture.jpeg), "synthetic.jpg"))
        if depth is not None: data['depth_map_png'] = base64.b64encode(depth).decode()
        if mask is not None: data['depth_conf_png'] = base64.b64encode(mask).decode()
        return self.fixture.client.post('/api/v1/lite/segment', data=data)

    def mask(self, size=(8,6)):
        out=io.BytesIO();Image.new('L',size,255).save(out,'PNG');return out.getvalue()

    def test_valid_pair_has_explicit_storage_but_never_geometry_claim(self):
        r=self.post(png16(),self.mask());self.assertEqual(r.status_code,200)
        receipt=r.json['storage_receipt'];self.assertEqual(receipt['depth'],'stored')
        self.assertEqual(receipt['validity_mask'],'stored');self.assertEqual(receipt['rgbd_validation'],'not_performed')
        m=json.loads(self.store.get_blob('lite/receipt-install/'+r.json['image_id']+'.json'))
        self.assertEqual(m['storage_receipt'],receipt)

    def test_missing_depth_is_not_a_complete_rgbd_receipt(self):
        r=self.post();self.assertTrue(r.json['stored'])
        self.assertEqual(r.json['storage_receipt']['depth'],'not_provided')

    def test_bad_depth_still_allows_inference_but_discloses_rejection(self):
        r=self.post(b'not png');self.assertEqual(r.status_code,200)
        self.assertTrue(r.json['stored'])  # Legacy field means image storage only.
        self.assertEqual(r.json['storage_receipt']['depth'],'rejected')
        self.assertFalse(self.store.exists('lite/receipt-install/'+r.json['image_id']+'.depth.png'))

    def test_valid_header_with_truncated_image_is_rejected(self):
        r=self.post(png16()[:40]);self.assertEqual(r.json['storage_receipt']['depth'],'rejected')

    def test_corrupt_crc_is_rejected(self):
        raw=bytearray(png16());raw[29]^=1
        r=self.post(bytes(raw));self.assertEqual(r.json['storage_receipt']['depth'],'rejected')

    def test_wrong_mask_size_rejects_pair_before_either_depth_asset_is_written(self):
        r=self.post(png16(),self.mask((7,6)));self.assertEqual(r.json['storage_receipt']['depth'],'rejected')
        pre='lite/receipt-install/'+r.json['image_id']
        self.assertFalse(self.store.exists(pre+'.depth.png'));self.assertFalse(self.store.exists(pre+'.conf.png'))

    def test_invalid_mask_is_not_silently_ignored(self):
        r=self.post(png16(),b'bad');self.assertEqual(r.json['storage_receipt']['validity_mask'],'rejected')

    def test_store_failure_never_returns_success_receipt(self):
        original=self.store.put_blob
        def fail(key,data,*args,**kwargs):
            if key.endswith('.conf.png'):raise OSError('injected storage failure')
            return original(key,data,*args,**kwargs)
        with patch.object(self.store,'put_blob',side_effect=fail):r=self.post(png16(),self.mask())
        self.assertEqual(r.status_code,500)
        self.assertNotIn('storage_receipt',r.json)

    def test_no_consent_does_not_store_either_asset(self):
        r=self.post(png16(),self.mask(),False);self.assertFalse(r.json['stored'])
        self.assertEqual(r.json['storage_receipt']['depth'],'not_requested')
        self.assertIsNone(r.json['image_id'])

if __name__=='__main__':unittest.main()
