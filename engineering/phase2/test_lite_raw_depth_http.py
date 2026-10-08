"""Signed RGB-D business upload, immutable readback and drained withdrawal."""
import io
import json
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from werkzeug.datastructures import MultiDict
from werkzeug.test import EnvironBuilder
import test_lite_attest_http as http_fixture
import test_lite_raw_depth_contract as raw_fixture
from lite_raw_depth_store import capture_key, unpack


class RawDepthHTTPTests(unittest.TestCase):
    def setUp(self):
        self.h=http_fixture.HTTPTests();self.h.setUp();self.addCleanup(self.h.doCleanups);self.h.register()
        self.f=raw_fixture.RawDepthTests();self.f.setUp()
    def body(self,metadata=None,depth=None,consent='true',extra=None):
        fields=MultiDict([('anon_id',self.h.owner),('research_consent',consent),('consent_version','2026-10-04.1'),
                         ('raw_depth_metadata',json.dumps(self.f.m if metadata is None else metadata)),
                         ('image',(io.BytesIO(self.f.jpeg),'synthetic.jpg')),
                         ('raw_depth',(io.BytesIO(self.f.raw if depth is None else depth),'depth.f32le'))])
        for key,value in extra or []:fields.add(key,value)
        builder=EnvironBuilder(method='POST',data=fields)
        try:
            env=builder.get_environ();return env['wsgi.input'].read(),env['CONTENT_TYPE']
        finally:builder.close()
    def upload(self,**kwargs):
        raw,ctype=self.body(**kwargs);return self.h.post_signed('/api/v1/lite/segment',raw,ctype)
    def delete(self):
        path='/api/v1/lite/data/'+self.h.owner
        return self.h.client.delete(path,headers=self.h.headers(path,b'','',method='DELETE'))
    def test_signed_upload_persists_exact_bits_metadata_reference_and_receipt(self):
        response=self.upload();self.assertEqual(response.status_code,200,response.json)
        body=response.json;iid=body['image_id'];receipt=body['storage_receipt']['raw_depth_receipt']
        self.assertEqual(receipt['installation_id'],self.h.owner);self.assertEqual(receipt['capture_id'],iid)
        packet=unpack(self.h.f.store.get_blob(capture_key(self.h.owner,iid)))
        self.assertEqual(packet['raw_depth'],self.f.raw);self.assertEqual(packet['jpeg'],self.f.jpeg)
        self.assertEqual(packet['metadata_bytes'],json.dumps(self.f.m).encode())
        meta=self.h.f.store.get_json('lite/'+self.h.owner+'/'+iid+'.json')
        self.assertEqual(meta['raw_depth_receipt'],receipt)
        self.assertEqual(meta['camera_intrinsics'],self.f.m['intrinsics'])
        self.assertEqual(body['storage_receipt']['validity_mask'],'not_provided')
        self.assertEqual(receipt['registration'],'not_verified');self.assertEqual(receipt['training_admission'],'not_evaluated')
    def test_identical_retry_retains_one_bundle_and_conflict_cannot_rebind_image(self):
        first=self.upload();self.assertEqual(first.status_code,200,first.json)
        repeat=self.upload();self.assertEqual(repeat.status_code,200,repeat.json)
        self.assertEqual(first.json['storage_receipt']['raw_depth_receipt'],repeat.json['storage_receipt']['raw_depth_receipt'])
        before=self.h.f.store.get_json('lite/'+self.h.owner+'/'+first.json['image_id']+'.json')
        changed=dict(self.f.m,filtered=True)
        response=self.upload(metadata=changed);self.assertEqual(response.status_code,409,response.json)
        self.assertEqual(len(list(self.h.f.store.list_keys('lite_raw/'+self.h.owner+'/'))),1)
        self.assertEqual(self.h.f.store.get_json('lite/'+self.h.owner+'/'+first.json['image_id']+'.json'),before)
    def test_bad_hash_duplicate_part_and_mixed_png_rejected_before_model(self):
        for kwargs in [dict(depth=self.f.raw[:-1]),dict(metadata=dict(self.f.m,rgb_sha256='0'*64)),
                       dict(extra=[('raw_depth_metadata',json.dumps(self.f.m))]),dict(extra=[('depth_map_png','legacy')]),
                       dict(extra=[('raw_depth',(io.BytesIO(self.f.raw),'duplicate.f32'))])]:
            with self.subTest(kwargs=list(kwargs)),patch.object(http_fixture.media_fixture.lite,'_SEGMENT') as model:
                response=self.upload(**kwargs);self.assertEqual(response.status_code,400,response.json)
                model.assert_not_called()
        self.assertEqual(list(self.h.f.store.list_keys('lite_raw/')),[])
    def test_unsigned_request_cannot_write_or_invoke_model(self):
        raw,ctype=self.body()
        with patch.object(http_fixture.media_fixture.lite,'_SEGMENT') as model:
            self.assertEqual(self.h.client.post('/api/v1/lite/segment',data=raw,content_type=ctype).status_code,401)
            model.assert_not_called()
        self.assertEqual(list(self.h.f.store.list_keys('lite_raw/')),[])
    def test_raw_capture_requires_consent_even_with_valid_signature(self):
        with patch.object(http_fixture.media_fixture.lite,'_SEGMENT') as model:
            response=self.upload(consent='false');self.assertEqual(response.status_code,403,response.json);model.assert_not_called()
        self.assertEqual(list(self.h.f.store.list_keys('lite_raw/')),[])
    def test_write_or_readback_failure_never_returns_success_and_orphan_is_withdrawn(self):
        store=self.h.f.store;original=store.get_blob
        def fail_raw(key):return None if key.startswith('lite_raw/') else original(key)
        with patch.object(store,'get_blob',side_effect=fail_raw):
            response=self.upload();self.assertEqual(response.status_code,503,response.json)
        self.assertTrue(list(store.list_keys('lite_raw/')))
        self.assertEqual(list(store.list_keys('lite/'+self.h.owner+'/')),[])
        response=self.delete();self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(response.json['raw_objects_removed'],1)
        self.assertEqual(list(store.list_keys('lite_raw/'+self.h.owner+'/')),[])
    def test_withdrawal_clears_raw_and_metadata_and_rejects_new_uploads(self):
        self.assertEqual(self.upload().status_code,200)
        store=self.h.f.store
        store.put_blob('lite_raw/other/keep.rgbd',b'other owner')
        response=self.delete();self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(response.json['raw_objects_removed'],1)
        self.assertEqual(list(store.list_keys('lite_raw/'+self.h.owner+'/')),[])
        self.assertTrue(store.exists('lite_raw/other/keep.rgbd'))
        self.assertEqual(self.upload().status_code,410)
    def test_failed_raw_delete_returns_incomplete_and_retry_finishes(self):
        self.assertEqual(self.upload().status_code,200)
        original=self.h.f.store.delete
        def fail_raw(key):
            if key.startswith('lite_raw/'):raise OSError('injected')
            return original(key)
        with patch.object(self.h.f.store,'delete',side_effect=fail_raw):
            response=self.delete();self.assertEqual(response.status_code,503,response.json)
        self.assertEqual(self.upload().status_code,410)
        self.assertEqual(self.delete().status_code,200)
    def test_inflight_raw_writer_delays_deletion_and_cannot_resurrect_data(self):
        h=self.h;entered=threading.Event();resume=threading.Event();result={}
        original=h.f.store.put_blob_immutable
        def pause(key,*args,**kwargs):
            if key.startswith('lite_raw/'):
                entered.set()
                if not resume.wait(5):raise RuntimeError('test timeout')
            return original(key,*args,**kwargs)
        raw,ctype=self.body();headers=h.headers('/api/v1/lite/segment',raw,ctype)
        def upload():
            with h.app.test_client() as client:result['response']=client.post('/api/v1/lite/segment',data=raw,content_type=ctype,headers=headers)
        with patch.object(h.f.store,'put_blob_immutable',side_effect=pause):
            thread=threading.Thread(target=upload);thread.start()
            try:
                self.assertTrue(entered.wait(5));self.assertEqual(self.delete().status_code,202)
            finally:resume.set();thread.join(5)
        self.assertFalse(thread.is_alive());self.assertEqual(result['response'].status_code,200)
        self.assertEqual(self.upload().status_code,410)
        self.assertEqual(self.delete().status_code,200)
        self.assertEqual(list(h.f.store.list_keys('lite_raw/'+h.owner+'/')),[])


if __name__=='__main__':unittest.main()
