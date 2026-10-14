"""Synthetic end-to-end proof of hole-preserving, revision-bound paired masks."""
import base64, hashlib, io, os, sys, unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'Backend/Flask'))
import test_research_format_guards as fixtures
import api_flywheel as fw
from annotation_masks import decode_wound, CONTRACT

def encode(values):
    im=Image.new('L',(8,8)); im.putdata(values); out=io.BytesIO(); im.save(out,'PNG'); return out.getvalue()

class NativeMasks(unittest.TestCase):
    setUp=fixtures.TissueHTTP.setUp
    tearDown=fixtures.TissueHTTP.tearDown
    rows=fixtures.TissueHTTP.rows
    depth=fixtures.fixtures.RevisionAssets.depth
    depths=fixtures.fixtures.RevisionAssets.depths
    def payload(self, change=False):
        mask=[int(1<=i%8<=6 and 1<=i//8<=6 and not(3<=i%8<=4 and 3<=i//8<=4)) for i in range(64)]
        mask[0]=1 # disconnected component
        if change: mask[1]=1 # class remains unknown; polygons and tissue unchanged
        tissue=[3*v for v in mask];tissue[0]=0;tissue[1]=0;tissue[9]=0
        raw=encode(mask)
        return dict(code='WD-SYNTH',image_id=self.iid,image_w=32,image_h=32,
                    gt_polygon=[[0,0],[31,0],[31,31],[0,31]],mm_per_px=.5,exudate=0,
                    doctor_verified=True,deidentified=True,consent_train=True,source='sample',
                    tissue_edited=True,tissue_raster=dict(rx0=0,ry0=0,mw=8,mh=8,m_scale=.25),
                    tissue_mask_png=base64.b64encode(encode(tissue)).decode(),mask_contract=CONTRACT,
                    wound_mask_png=base64.b64encode(raw).decode(),wound_mask_sha256=hashlib.sha256(raw).hexdigest())
    def post(self,body,who='dr1'):
        return self.client.post('/api/v1/annotation',headers=self.headers[who],json=body)
    def getmask(self,rid,who='dr1'):
        return self.client.get('/api/v1/flywheel/record/'+self.iid+'/wound_mask.png?revision='+rid,headers=self.headers[who])
    def test_roundtrip_hole_island_unknown_and_receipt(self):
        body=self.payload(); response=self.post(body);self.assertEqual(response.status_code,200,response.json)
        receipt=response.json;raw=base64.b64decode(body['wound_mask_png'])
        self.assertEqual(receipt['wound_mask_sha256'],hashlib.sha256(raw).hexdigest())
        self.assertEqual(receipt['tissue_mask_sha256'],hashlib.sha256(base64.b64decode(body['tissue_mask_png'])).hexdigest())
        self.assertEqual(receipt['mask_contract'],CONTRACT)
        retrieved=self.getmask(receipt['annotation_receipt_id']);self.assertEqual(retrieved.data,raw)
        self.assertIn('no-store',retrieved.headers['Cache-Control'])
        import export_flywheel_dataset as ex
        projected=ex.rasterize_record(self.rows()[0],32,32)
        self.assertEqual(int(projected[14,14]),0);self.assertEqual(int(projected[6,6]),255)
        self.assertEqual(int(projected[1,1]),255);self.assertEqual(int(projected[1,30]),0)
    def test_native_invalid_never_falls_back_to_polygons(self):
        self.post(self.payload());row=self.rows()[0]
        self.st.put_blob(fw._key(os.path.join(fw.FLYWHEEL_DIR,row['wound_mask_key'])),b'bad')
        import export_flywheel_dataset as ex
        with self.assertRaises(ValueError):ex.rasterize_record(row,32,32)
    def test_same_payload_dedup_and_changed_hole_membership_new_revision(self):
        body=self.payload(); first=self.post(body);second=self.post(body)
        self.assertEqual(second.json['status'],'duplicate_skipped');self.assertEqual(first.json['annotation_receipt_id'],second.json['annotation_receipt_id'])
        self.assertEqual(len(self.rows()),1)
        changed=self.payload(True);third=self.post(changed);self.assertEqual(third.status_code,200,third.json)
        self.assertNotEqual(first.json['annotation_receipt_id'],third.json['annotation_receipt_id'])
        self.assertEqual(len(self.rows()),2)
        for resp,payload in ((first,body),(third,changed)):
            self.assertEqual(self.getmask(resp.json['annotation_receipt_id']).data,base64.b64decode(payload['wound_mask_png']))
    def test_invalid_masks_reject_before_asset_or_intent(self):
        good=self.payload()
        invalid=[]
        for name in ('mask_contract','wound_mask_sha256','wound_mask_png','tissue_mask_png'):
            b=dict(good);b.pop(name);invalid.append(b)
        for key,value in [('mask_contract','unknown'),('wound_mask_sha256','0'*64),('wound_mask_png','bad!')]:
            invalid.append(dict(good,**{key:value}))
        for v in (0,2,255):
            raw=encode([v]*64);invalid.append(dict(good,wound_mask_png=base64.b64encode(raw).decode(),wound_mask_sha256=hashlib.sha256(raw).hexdigest()))
        invalid.append(dict(good,tissue_mask_png=base64.b64encode(encode([1]*64)).decode()))
        for body in invalid:
            with self.subTest(keys=sorted(body)),patch.object(fw,'store_revision_asset') as save,patch.object(fw,'audit_intent') as intent:
                result=self.post(body);self.assertEqual(result.status_code,400,result.json)
                save.assert_not_called();intent.assert_not_called();self.assertFalse(self.rows())
    def test_withdrawal_and_actor_guards_remain(self):
        response=self.post(self.payload());rid=response.json['annotation_receipt_id']
        self.assertEqual(self.getmask(rid,'dr2').status_code,404)
        result=self.client.post('/api/v1/consent/withdraw',headers=self.headers['dr1'],json={'code':'WD-SYNTH'})
        self.assertEqual(result.status_code,200,result.json)
        self.assertEqual(self.getmask(rid).status_code,410)
    def test_tamper_blocks_duplicate_and_manifest(self):
        body=self.payload();res=self.post(body);row=self.rows()[0]
        self.st.put_blob(fw._key(os.path.join(fw.FLYWHEEL_DIR,row['wound_mask_key'])),b'corrupt')
        self.assertEqual(self.post(body).status_code,409)
        self.assertEqual(self.getmask(res.json['annotation_receipt_id']).status_code,409)
        result=self.client.get('/api/v1/dataset/manifest?kind=wound&source=sample',headers=self.headers['admin'])
        self.assertEqual(result.json['count'],0);self.assertEqual(result.json['excluded']['wound_revision_unverifiable'],1)
    def test_valid_png_replacement_still_fails_revision_hash(self):
        body=self.payload();response=self.post(body);row=self.rows()[0]
        replacement=base64.b64decode(self.payload(True)['wound_mask_png'])
        self.st.put_blob(fw._key(os.path.join(fw.FLYWHEEL_DIR,row['wound_mask_key'])),replacement)
        self.assertEqual(self.getmask(response.json['annotation_receipt_id']).status_code,409)
        self.assertEqual(self.post(body).status_code,409)

    def test_manifest_and_delayed_depth_bind_exact_mask_revision(self):
        a=self.post(self.payload()).json['annotation_receipt_id'];b=self.post(self.payload(True)).json['annotation_receipt_id']
        self.assertEqual(self.depth(b,.4).status_code,200);self.assertEqual(self.depth(a,.3).status_code,200)
        self.assertEqual({x['annotation_receipt_id'] for x in self.depths()},{a,b})
        result=self.client.get('/api/v1/dataset/manifest?kind=wound&source=sample',headers=self.headers['admin'])
        item=result.json['items'][0];self.assertEqual(item['annotation_receipt_id'],b)
        self.assertEqual(item['raw_depth']['annotation_receipt_id'],b)
        self.assertEqual(item['wound_mask_sha256'],self.payload(True)['wound_mask_sha256'])
    def test_write_failure_not_acknowledged(self):
        original=fw.store_revision_asset
        def fail(folder,*args,**kwargs):
            if folder=='wound_masks':raise IOError('synthetic write failure')
            return original(folder,*args,**kwargs)
        with patch.object(fw,'store_revision_asset',side_effect=fail):
            result=self.post(self.payload());self.assertEqual(result.status_code,409,result.json);self.assertFalse(self.rows())
        self.assertEqual(self.post(self.payload()).status_code,200);self.assertEqual(len(self.rows()),1)
    def test_legacy_request_is_still_supported_without_claiming_native(self):
        body=self.payload()
        for field in ('mask_contract','wound_mask_sha256','wound_mask_png'):body.pop(field)
        response=self.post(body);self.assertEqual(response.status_code,200,response.json)
        self.assertNotIn('wound_mask_sha256',response.json)
        self.assertEqual(self.getmask(response.json['annotation_receipt_id']).status_code,404)

if __name__=='__main__':unittest.main()
