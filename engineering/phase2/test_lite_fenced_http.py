"""Signed Flask upload/revision/withdrawal using the public fenced media adapter.

Synthetic device attestation + SQLite journal + injected GCS transport. No cloud.
"""
import hashlib
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch
import test_lite_raw_depth_http as raw_fixture
import test_lite_fenced_objects as object_fixture
from lite_attest_state import SQLiteStateStore, StateUnavailable
from lite_fenced_store import FencedPrivacy
from lite_fenced_objects import ObjectRef, PinnedWrite, GenerationConflict


class FencedHTTPTests(unittest.TestCase):
    def setUp(self):
        self.f = raw_fixture.RawDepthHTTPTests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.h = self.f.h; self.bucket = object_fixture.Bucket()
        self.path = Path(self.h.e.temp.name)/'fenced.sqlite'
        self.restart()
    def restart(self):
        self.privacy = FencedPrivacy(SQLiteStateStore(self.path), self.bucket)
        self.h.app.extensions['lite_attest_http']['privacy'] = self.privacy
        self.h.privacy = self.privacy
    def upload(self): return self.f.upload()
    def delete(self): return self.f.delete()
    def rows(self, name): return [json.loads(line) for line in self.privacy.media(self.h.owner).read_lines_fresh(name)]
    def revision(self, image_id, source='ai', area=4):
        p=dict(polygons=[[[0,0],[8,0],[8,8]]],image_w=self.f.f.m['rgb_width'],image_h=self.f.f.m['rgb_height'],
               surface_cm2=area,projected_cm2=area,source=source,consent_version='2026-10-04.1')
        raw=json.dumps(dict(anon_id=self.h.owner,image_id=image_id,revision=1,research_consent=True,payload_json=json.dumps(p))).encode()
        return self.h.post_signed('/api/v1/lite/annotation/revision',raw,'application/json')
    def assert_sealed(self):
        self.assertTrue(self.bucket.live)
        for name, (generation, data, metadata) in self.bucket.live.items():
            if '/'+self.h.owner+'/' in name:
                self.assertEqual(data,b''); self.assertEqual(metadata['state'],'sealed')
        self.assertEqual(self.privacy.manifest.withdraw(self.h.owner),0)

    def test_signed_rgbd_writes_only_fenced_media_and_preserves_exact_bundle(self):
        response=self.upload();self.assertEqual(response.status_code,200,response.json)
        iid=response.json['image_id'];media=self.privacy.media(self.h.owner)
        from lite_raw_depth_store import unpack
        bundle=unpack(media.get_blob('lite_raw/'+self.h.owner+'/'+iid+'.rgbd'))
        self.assertEqual(bundle['raw_depth'],self.f.f.raw);self.assertEqual(bundle['jpeg'],self.f.f.jpeg)
        self.assertEqual(media.get_blob('lite/'+self.h.owner+'/'+iid+'.jpg'),self.f.f.jpeg)
        self.assertEqual(media.get_json('lite/'+self.h.owner+'/'+iid+'.json')['raw_depth_receipt'],response.json['storage_receipt']['raw_depth_receipt'])
        self.assertEqual(len(self.rows('lite_index.jsonl')),1)
        self.assertEqual(list(self.h.f.store.list_keys('lite/'+self.h.owner+'/')),[])
        self.assertEqual(list(self.h.f.store.list_keys('lite_raw/'+self.h.owner+'/')),[])
        self.assertTrue(all(name.startswith('lite_fenced/v1/'+self.h.owner+'/') for name in self.bucket.live))

    def test_legacy_depth_and_validity_png_are_fenced_and_withdrawn(self):
        import base64
        import io
        from PIL import Image
        from werkzeug.test import EnvironBuilder
        from test_lite_segment import png16
        depth = png16(); buffer = io.BytesIO()
        Image.new('L', (8, 6), 255).save(buffer, 'PNG'); confidence = buffer.getvalue()
        builder = EnvironBuilder(method='POST', data=dict(
            anon_id=self.h.owner, research_consent='true', consent_version='2026-10-04.1',
            image=(io.BytesIO(self.h.f.jpeg), 'synthetic.jpg'),
            depth_map_png=base64.b64encode(depth).decode(), depth_conf_png=base64.b64encode(confidence).decode()))
        try:
            env = builder.get_environ(); raw, ctype = env['wsgi.input'].read(), env['CONTENT_TYPE']
        finally: builder.close()
        response = self.h.post_signed('/api/v1/lite/segment', raw, ctype)
        self.assertEqual(response.status_code, 200, response.json)
        receipt = response.json['storage_receipt']
        self.assertEqual(receipt['depth'], 'stored'); self.assertEqual(receipt['validity_mask'], 'stored')
        prefix = 'lite/'+self.h.owner+'/'+response.json['image_id']
        media = self.privacy.media(self.h.owner)
        self.assertEqual(media.get_blob(prefix+'.depth.png'), depth)
        self.assertEqual(media.get_blob(prefix+'.conf.png'), confidence)
        self.assertEqual(self.delete().status_code, 200); self.assert_sealed()

    def test_no_payload_write_when_durable_plan_cannot_be_registered(self):
        with patch.object(self.privacy.manifest, 'plan', side_effect=StateUnavailable('journal unavailable')):
            response = self.upload()
            self.assertEqual(response.status_code, 503, response.json)
            self.assertEqual(self.bucket.live, {})
            # The immutable RGB-D path and ordinary JPEG path register through
            # different adapter methods. Exercise both before asserting no write.
            raw, ctype = self.h.segment_bytes()
            response = self.h.post_signed('/api/v1/lite/segment', raw, ctype)
            self.assertEqual(response.status_code, 503, response.json)
            self.assertEqual(self.bucket.live, {})
        self.assertEqual(self.delete().status_code, 200)

    def test_revision_retry_retains_one_label_without_reuploading_media(self):
        image=self.upload().json['image_id']
        before={k:v for k,v in self.bucket.live.items() if '/image/' in k or '/rgbd/' in k}
        first=self.revision(image);self.assertEqual(first.status_code,200,first.json)
        second=self.revision(image);self.assertEqual(second.status_code,200,second.json)
        self.assertEqual(first.json,second.json);self.assertEqual(len(self.rows('lite_labels.jsonl')),1)
        self.assertEqual(self.rows('lite_labels.jsonl')[0]['label_grade'],'ai_unverified')
        self.assertEqual(before,{k:v for k,v in self.bucket.live.items() if k in before})
        conflict=self.revision(image,area=5);self.assertEqual(conflict.status_code,409,conflict.json)

    def test_delete_clears_payloads_and_returns_distinct_fence_receipt(self):
        image=self.upload().json['image_id'];self.assertEqual(self.revision(image).status_code,200)
        response=self.delete();self.assertEqual(response.status_code,200,response.json)
        self.assertFalse(response.json['writers_drained']);self.assertEqual(response.json['write_fence'],'gcs-generation-v1')
        self.assertEqual(response.json['live_payloads_remaining'],0)
        self.assertEqual(response.json['retained_markers'],len(self.bucket.live))
        self.assert_sealed();self.assertEqual(self.rows('lite_labels.jsonl'),[])
        self.assertEqual(self.upload().status_code,410)
        self.assertEqual(self.delete().status_code,200)

    def test_paused_real_upload_cannot_resurrect_after_successful_delete(self):
        entered=threading.Event();resume=threading.Event();result={}
        def pause(blob,data,kwargs):
            if data and '/rgbd/' in blob.name:
                entered.set()
                if not resume.wait(5):raise RuntimeError('test wait timeout')
        raw,ctype=self.f.body();headers=self.h.headers('/api/v1/lite/segment',raw,ctype)
        def upload():
            with self.h.app.test_client() as client:
                result['response']=client.post('/api/v1/lite/segment',data=raw,content_type=ctype,headers=headers)
        self.bucket.before_upload=pause;thread=threading.Thread(target=upload);thread.start()
        try:
            self.assertTrue(entered.wait(5));response=self.delete()
            self.assertEqual(response.status_code,200,response.json);self.assert_sealed()
        finally:resume.set();thread.join(5);self.bucket.before_upload=None
        self.assertFalse(thread.is_alive());self.assertNotEqual(result['response'].status_code,200)
        self.assert_sealed();self.assertEqual(self.upload().status_code,410)

    def test_write_response_lost_recovers_from_persistent_plan_after_restart(self):
        self.bucket.lose_response=True
        self.assertEqual(self.upload().status_code,503)
        self.bucket.lose_response=False;self.restart()
        response=self.delete();self.assertEqual(response.status_code,200,response.json);self.assert_sealed()

    def test_process_dies_after_acknowledged_payload_before_ticket_release(self):
        with patch.object(self.privacy,'finish_write',side_effect=StateUnavailable('process unavailable')):
            self.assertEqual(self.upload().status_code,503)
        self.restart();response=self.delete();self.assertEqual(response.status_code,200,response.json);self.assert_sealed()

    def test_seal_failure_never_claims_deleted_or_reopens_uploads(self):
        self.assertEqual(self.upload().status_code,200)
        self.bucket.upload_error=TimeoutError('seal unavailable')
        response=self.delete();self.assertEqual(response.status_code,503,response.json)
        self.assertEqual(self.upload().status_code,410)
        self.bucket.upload_error=None;self.restart()
        self.assertEqual(self.delete().status_code,200);self.assert_sealed()

    def test_partial_inventory_failure_cannot_report_success(self):
        self.upload()
        def broken(**kwargs):
            yield object_fixture.SimpleNamespace(name=next(iter(self.bucket.live)))
            raise OSError('listing interrupted')
        with patch.object(self.bucket,'list_blobs',side_effect=broken):
            self.assertEqual(self.delete().status_code,503)
        self.assertEqual(self.delete().status_code,200);self.assert_sealed()

    def test_withdraw_during_inference_finishes_without_waiting_for_stopped_model(self):
        lite=raw_fixture.http_fixture.media_fixture.lite;original=lite._SEGMENT
        def inference(*args):
            response=self.delete();self.assertEqual(response.status_code,200,response.json)
            return original(*args)
        with patch.object(lite,'_SEGMENT',side_effect=inference):
            self.assertEqual(self.upload().status_code,410)
        self.assertEqual(self.bucket.live,{})

    def test_legacy_annotation_route_is_fenced_too(self):
        image=self.upload().json['image_id']
        raw=json.dumps(dict(anon_id=self.h.owner,image_id=image,research_consent=True,polygons=[[[0,0],[8,0],[8,8]]],image_w=16,image_h=16)).encode()
        response=self.h.post_signed('/api/v1/lite/annotation',raw,'application/json')
        self.assertEqual(response.status_code,200,response.json);self.assertEqual(len(self.rows('lite_labels.jsonl')),1)
        self.assertEqual(self.delete().status_code,200);self.assert_sealed()

    def test_other_owner_survives_withdrawal_and_no_plan_mutates_them(self):
        other='f'*32;ticket=self.privacy.begin_write(other)
        store=self.privacy.media(other,ticket);store.put_blob('lite/'+other+'/'+'1'*16+'.jpg',b'synthetic other')
        self.privacy.finish_write(ticket);self.upload()
        self.assertEqual(self.delete().status_code,200)
        self.assertEqual(self.privacy.media(other).get_blob('lite/'+other+'/'+'1'*16+'.jpg'),b'synthetic other')
        self.assert_sealed()

    def test_raw_duplicate_is_immutable_and_changed_depth_cannot_replace_it(self):
        first=self.upload();self.assertEqual(first.status_code,200,first.json)
        second=self.upload();self.assertEqual(second.status_code,200,second.json)
        self.assertEqual(first.json['storage_receipt']['raw_depth_receipt'],second.json['storage_receipt']['raw_depth_receipt'])
        changed=dict(self.f.f.m,filtered=True)
        response=self.f.upload(metadata=changed);self.assertEqual(response.status_code,409,response.json)
        self.assertEqual(len(self.privacy.objects.list_refs(self.h.owner,'rgbd')),1)
        self.assertEqual(self.delete().status_code,200)

if __name__=='__main__':unittest.main()
