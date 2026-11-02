"""Signed synthetic research uploads, original service receipt and GCS CAS fake.

No remote service or real data. These exercise actual HTTP admission, immutable
media readback, existing revision routes and withdrawal during an in-flight write.
"""
import copy
import hashlib
import io
import json
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from werkzeug.datastructures import MultiDict
from werkzeug.test import EnvironBuilder
from lite_attest_http import install_lite_attest
from lite_attest_state import SQLiteStateStore, StateUnavailable
from lite_fenced_store import FencedPrivacy, FencedMedia
from lite_raw_depth_store import pack, unpack
from lite_research_capture import ResearchCaptureReceiver, CaptureAdmissionBudget, PATH
from lite_research_source import TTL, sha
from lite_research_source_issuer import ResearchSourceIssuer
import test_lite_service_http as native_fixture
import test_lite_raw_depth_http as raw_fixture
import test_lite_fenced_objects as object_fixture


class ResearchCaptureTests(unittest.TestCase):
    def setUp(self):
        self.native = native_fixture.ServiceHTTPTests(); self.native.setUp()
        self.addCleanup(self.native.doCleanups)
        self.original = self.native.complete().json
        self.quota = copy.deepcopy(self.native.quota())
        self.key = Ed25519PrivateKey.generate(); self.bucket = object_fixture.Bucket()
        self.issuer = ResearchSourceIssuer(scope='d'*64, bucket=self.bucket.name,
            private_key=self.key.private_bytes_raw(), operations=self.native.ops)
        self.f = raw_fixture.RawDepthHTTPTests()
        def install(app, **kwargs):
            root = Path(self.native.e.temp.name)
            self.privacy = FencedPrivacy(SQLiteStateStore(root/'research.sqlite'), self.bucket)
            self.budget = CaptureAdmissionBudget(SQLiteStateStore(root/'capture-budget.sqlite'),
                day_limit=30, clock=lambda:self.native.e.now)
            self.receiver = ResearchCaptureReceiver(verifier=self.issuer.verifier, budget=self.budget)
            return install_lite_attest(app, **(kwargs | dict(privacy=self.privacy, research_capture=self.receiver)))
        with patch.object(raw_fixture.http_fixture, 'install_lite_attest', side_effect=install): self.f.setUp()
        self.addCleanup(self.f.doCleanups); self.h = self.f.h
        self.jpeg = self.native.f.jpeg; self.depth = self.f.f.raw
        self.capture_id = 'fe000000-0000-4000-8000-000000000001'
        self.metadata = dict(self.f.f.m, schema='lite.rgbd/2', capture_time='host_wall_clock',
            rgb_sha256=hashlib.sha256(self.jpeg).hexdigest(), capture_id=self.capture_id,
            captured_at='2023-11-14T22:13:20.000Z', capture_time_source='photo_callback_wall_clock')
        self.ticket = self.proof()

    def proof(self, **changes):
        return self.issuer.issue(self.native.owner, **(dict(research=self.h.owner, consent_epoch='legacy',
            consent_version='2026-10-16.1', capture_id=self.capture_id,
            image_sha256=hashlib.sha256(self.jpeg).hexdigest(), operation=f'{1:032x}') | changes))

    def body(self, *, ticket=None, metadata=None, jpeg=None, depth=None, raw=True, consent='true', extra=()):
        fields = MultiDict([('anon_id',self.h.owner), ('research_consent',consent),
            ('consent_version','2026-10-16.1'), ('consent_epoch',(self.ticket if ticket is None else ticket)['claim']['consent_epoch']),
            ('source_ticket',json.dumps(self.ticket if ticket is None else ticket)),
            ('image',(io.BytesIO(self.jpeg if jpeg is None else jpeg),'capture.jpg'))])
        if raw:
            fields.add('raw_depth_metadata',json.dumps(self.metadata if metadata is None else metadata))
            fields.add('raw_depth',(io.BytesIO(self.depth if depth is None else depth),'depth.f32'))
        for k,v in extra: fields.add(k,v)
        builder=EnvironBuilder(method='POST',data=fields)
        try:
            env=builder.get_environ(); return env['wsgi.input'].read(),env['CONTENT_TYPE']
        finally: builder.close()

    def upload(self, **kwargs):
        body,ct=self.body(**kwargs); return self.h.post_signed(PATH,body,ct)

    def rows(self, key):
        return [json.loads(x) for x in self.privacy.media(self.h.owner).read_lines_fresh(key)]

    def unchanged_usage(self):
        self.assertEqual(self.native.model_calls,1)
        self.assertEqual(self.native.quota(),self.quota)

    def lookup(self, **changes):
        bundle = pack(json.dumps(self.metadata).encode(), self.depth, self.jpeg)
        fields = dict(anon_id=self.h.owner, consent_epoch='legacy', consent_version='2026-10-16.1',
                      research_consent=True, image_bytes=len(self.jpeg), source_ticket=json.dumps(self.ticket),
                      bundle_sha256=hashlib.sha256(bundle).hexdigest())
        return self.h.post_signed(PATH+'/status', json.dumps(fields | changes).encode())

    def test_lookup_after_lost_receipt_works_without_media_model_or_writer_even_after_expiry(self):
        with patch.object(FencedPrivacy, 'begin_write', side_effect=AssertionError('lookup started a write')):
            missing = self.lookup()
        self.assertEqual(missing.status_code, 200, missing.json)
        self.assertEqual(missing.json['status'], 'upload_required')
        self.assertEqual(self.bucket.live, {})
        first = self.upload()
        frozen = copy.deepcopy((self.bucket.live,self.bucket.history))
        self.native.e.now += TTL+1
        with patch.object(FencedPrivacy, 'begin_write', side_effect=AssertionError('lookup wrote')), \
             patch.object(FencedMedia, 'get_blob', autospec=True, side_effect=FencedMedia.get_blob) as reads:
            receipt = self.lookup()
        self.assertEqual(receipt.status_code, 200, receipt.json)
        self.assertEqual(receipt.json, first.json)
        self.assertTrue(all(args.args[1].endswith('.json') for args in reads.call_args_list))
        self.assertEqual((self.bucket.live,self.bucket.history), frozen)
        self.unchanged_usage()

    def test_lookup_conflicting_depth_and_corrupt_publication_never_instruct_upload(self):
        self.assertEqual(self.upload().status_code, 200)
        self.assertEqual(self.lookup(bundle_sha256='f'*64).status_code, 409)
        self.assertEqual(self.lookup(image_bytes=len(self.jpeg)+1).status_code, 409)
        with patch.object(FencedMedia, 'capture_intent', return_value=None):
            self.assertEqual(self.lookup().status_code, 503)
        with patch.object(FencedMedia, 'capture_index', side_effect=StateUnavailable('unavailable')):
            self.assertEqual(self.lookup().status_code, 503)
        with patch.object(FencedMedia, 'capture_index', return_value={}):
            self.assertEqual(self.lookup().status_code, 503)
        self.unchanged_usage()

    def test_missing_capture_with_expired_proof_requests_renewal_before_any_media(self):
        self.native.e.now += TTL+1
        expired = self.lookup()
        self.assertEqual(expired.status_code, 409)
        self.assertIs(expired.json['renew_source_only'], True)
        fresh = self.proof()
        self.assertEqual(fresh['signature'], self.ticket['signature'])
        self.assertEqual(self.lookup(source_ticket=json.dumps(fresh)).json['status'], 'upload_required')
        self.assertFalse(self.bucket.live)
        self.unchanged_usage()

    def test_lookup_needs_owner_epoch_consent_and_signature_and_stops_after_withdrawal(self):
        self.assertEqual(self.lookup(anon_id='f'*32).status_code, 403)
        self.assertEqual(self.lookup(consent_epoch='e'*32).status_code, 409)
        self.assertEqual(self.lookup(research_consent=False).status_code, 400)
        bad = copy.deepcopy(self.ticket)
        bad['claim']['operation'] = '1'*32
        self.assertEqual(self.lookup(source_ticket=json.dumps(bad)).status_code, 403)
        self.assertEqual(self.h.client.post(PATH+'/status',json={}).status_code, 401)
        self.assertEqual(self.upload().status_code, 200)
        self.assertEqual(self.f.delete().status_code, 200)
        self.assertEqual(self.lookup().status_code, 410)
        self.unchanged_usage()

    def test_complete_exact_rgbd_and_provenance_without_model_or_personal_quota(self):
        with patch.object(raw_fixture.http_fixture.media_fixture.lite,'_SEGMENT',side_effect=AssertionError('no inference')):
            r=self.upload(); self.assertEqual(r.status_code,200,r.json)
        iid=r.json['image_id']; store=self.privacy.media(self.h.owner)
        decoded=unpack(store.get_blob('lite_raw/'+self.h.owner+'/'+iid+'.rgbd'))
        self.assertEqual(decoded['jpeg'],self.jpeg); self.assertEqual(decoded['raw_depth'],self.depth)
        self.assertEqual(decoded['metadata_bytes'],json.dumps(self.metadata).encode())
        meta=store.get_json('lite/'+self.h.owner+'/'+iid+'.json')
        self.assertEqual(meta['ai_polygons'],self.original['result']['wound_polygons'])
        self.assertEqual(meta['raw_depth_receipt'],r.json['raw_depth_receipt'])
        self.assertEqual(meta['source_provenance']['source'],self.ticket['claim'])
        self.assertEqual(r.json['annotation_status'],'not_confirmed')
        self.assertEqual(r.json['training_admission'],'not_evaluated')
        self.assertEqual(len(self.rows('lite_index.jsonl')),1); self.unchanged_usage()

    def test_repeat_after_lost_client_reply_retains_one_index_and_no_media_versions(self):
        a=self.upload(); self.assertEqual(a.status_code,200,a.json)
        original=copy.deepcopy((self.bucket.live,self.bucket.history))
        b=self.upload(); self.assertEqual(a.json,b.json)
        self.assertEqual(original,(self.bucket.live,self.bucket.history)); self.unchanged_usage()

    def test_manual_no_depth_capture_does_not_fabricate_ai_or_depth(self):
        ticket=self.proof(operation=None)
        r=self.upload(ticket=ticket,raw=False); self.assertEqual(r.status_code,200,r.json)
        meta=self.privacy.media(self.h.owner).get_json('lite/'+self.h.owner+'/'+r.json['image_id']+'.json')
        self.assertEqual(meta['ai_polygons'],[]); self.assertIsNone(meta['route'])
        self.assertIsNone(r.json['raw_depth_receipt']); self.unchanged_usage()

    def test_expired_ticket_only_reads_already_complete_capture(self):
        r=self.upload(); self.assertEqual(r.status_code,200,r.json)
        original=copy.deepcopy((self.bucket.live,self.bucket.history))
        self.native.e.now+=TTL+1
        self.assertEqual(self.upload().json,r.json)
        self.assertEqual(original,(self.bucket.live,self.bucket.history))

    def test_expired_new_ticket_cannot_create_any_media(self):
        self.native.e.now+=TTL+1
        r=self.upload(); self.assertEqual(r.status_code,409,r.json)
        self.assertTrue(r.json['renew_source_only']); self.assertEqual(self.bucket.live,{})
        self.assertEqual(self.upload(ticket=self.proof()).status_code,200); self.unchanged_usage()

    def test_partial_write_needs_fresh_ticket_then_resumes_without_new_recognition(self):
        self.bucket.lose_response=True
        self.assertEqual(self.upload().status_code,503)
        self.bucket.lose_response=False; self.native.e.now+=TTL+1
        self.assertEqual(self.upload().status_code,409)
        r=self.upload(ticket=self.proof()); self.assertEqual(r.status_code,200,r.json)
        self.assertEqual(len(self.rows('lite_index.jsonl')),1); self.unchanged_usage()

    def test_source_photo_or_consent_mismatch_rejected_before_payload(self):
        for kwargs in [dict(jpeg=self.jpeg+b'x'),dict(ticket=self.proof(research='f'*32)),
                       dict(consent='false')]:
            with self.subTest(kwargs=list(kwargs)):
                self.assertEqual(self.upload(**kwargs).status_code,403)
                self.assertEqual(self.bucket.live,{})

    def test_unapproved_epoch_and_privacy_preflight_never_save_payload(self):
        self.assertEqual(self.upload(ticket=self.proof(consent_epoch='e'*32)).status_code,409)
        for result,status in [((True,'1'),400),((None,'unknown'),503)]:
            with patch('lite_research_capture._has_face',return_value=result):
                self.assertEqual(self.upload().status_code,status)
            self.assertEqual(self.bucket.live,{})

    def test_new_consent_epoch_isolated_from_late_old_upload_and_old_delete(self):
        self.assertEqual(self.upload().status_code,200); self.assertEqual(self.f.delete().status_code,200)
        from lite_reconsent import CONSENT, DISCLOSURE
        new='c'*32
        request=dict(anon_id=self.h.owner,research_consent=True,previous_epoch='legacy',new_epoch=new,
                     consent_version=CONSENT,disclosure_version=DISCLOSURE)
        r=self.h.post_signed('/api/v1/lite/research/rejoin',json.dumps(request).encode())
        self.assertEqual(r.status_code,200,r.json)
        r=self.upload(ticket=self.proof(consent_epoch=new)); self.assertEqual(r.status_code,200,r.json)
        new_rows={k:v for k,v in self.bucket.live.items() if k.startswith('lite_epochs/v1/'+new+'/')}
        self.assertTrue(new_rows)
        self.assertEqual(self.upload().status_code,409)
        self.assertEqual(self.f.delete().status_code,200)
        self.assertEqual(new_rows,{k:v for k,v in self.bucket.live.items() if k in new_rows})
        self.unchanged_usage()

    def test_concurrent_identical_capture_publish_is_idempotent(self):
        from concurrent.futures import ThreadPoolExecutor
        barrier=threading.Barrier(2)
        def receive(_):
            writer=self.privacy.begin_write(self.h.owner)
            try:
                barrier.wait(timeout=5)
                return self.receiver.receive(privacy=self.privacy,writer=writer,owner=self.h.owner,
                    consent_version='2026-10-16.1',ticket=self.ticket,jpeg=self.jpeg,
                    metadata=json.dumps(self.metadata).encode(),depth=self.depth)
            finally: self.privacy.finish_write(writer)
        with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(receive,range(2)))
        self.assertEqual(results[0],results[1]); self.assertEqual(len(self.rows('lite_index.jsonl')),1)
        self.assertEqual(len(self.bucket.live),len(self.bucket.history)); self.unchanged_usage()

    def test_tampered_signed_result_cannot_be_saved(self):
        ticket=copy.deepcopy(self.ticket); ticket['claim']['result']['route']='edited'
        ticket['claim']['result_sha256']=sha(ticket['claim']['result'])
        self.assertEqual(self.upload(ticket=ticket).status_code,403); self.assertEqual(self.bucket.live,{})

    def test_raw_capture_identity_and_hash_mismatch_preserve_empty_storage(self):
        for kwargs in [dict(metadata=dict(self.metadata,capture_id='ff000000-0000-4000-8000-000000000001')),
                       dict(metadata=dict(self.metadata,depth_sha256='f'*64)),dict(depth=self.depth[:-1])]:
            with self.subTest(kwargs=list(kwargs)):
                self.assertIn(self.upload(**kwargs).status_code,(400,403)); self.assertEqual(self.bucket.live,{})

    def test_same_capture_cannot_rebind_depth_missing_depth_or_source(self):
        first=self.upload(); self.assertEqual(first.status_code,200,first.json)
        original=copy.deepcopy((self.bucket.live,self.bucket.history))
        for kwargs in [dict(metadata=dict(self.metadata,filtered=True)),dict(raw=False),dict(ticket=self.proof(operation=None))]:
            with self.subTest(kwargs=list(kwargs)): self.assertEqual(self.upload(**kwargs).status_code,409)
        self.assertEqual(original,(self.bucket.live,self.bucket.history)); self.unchanged_usage()

    def test_duplicate_and_unknown_multipart_fields_are_rejected(self):
        for extra in [[('source_ticket',json.dumps(self.ticket))],[('unknown','value')],
                      [('image',(io.BytesIO(self.jpeg),'duplicate.jpg'))]]:
            self.assertEqual(self.upload(extra=extra).status_code,400); self.assertEqual(self.bucket.live,{})

    def test_unsigned_request_is_rejected_before_storage(self):
        b,ct=self.body(); self.assertEqual(self.h.client.post(PATH,data=b,content_type=ct).status_code,401)
        self.assertEqual(self.bucket.live,{})

    def test_failed_image_readback_never_publishes_index(self):
        original=FencedMedia.get_blob
        def fail(store,key): return None if key.endswith('.jpg') else original(store,key)
        with patch.object(FencedMedia,'get_blob',fail): self.assertEqual(self.upload().status_code,503)
        self.assertEqual(self.rows('lite_index.jsonl'),[])
        self.assertEqual(self.upload().status_code,200); self.unchanged_usage()

    def test_lost_index_write_response_recovers_exact_receipt(self):
        def lose(blob,data,kwargs):
            if '/index/' in blob.name: self.bucket.lose_response=True
        self.bucket.before_upload=lose
        self.assertEqual(self.upload().status_code,503)
        self.bucket.before_upload=None; self.bucket.lose_response=False
        before=copy.deepcopy(self.bucket.live)
        r=self.upload(); self.assertEqual(r.status_code,200,r.json)
        self.assertEqual(before,self.bucket.live); self.assertEqual(len(self.rows('lite_index.jsonl')),1)

    def test_no_annotation_before_capture_publish_and_revisions_reuse_original_media(self):
        with patch.object(FencedMedia,'publish_capture_once',side_effect=StateUnavailable('index unavailable')):
            self.assertEqual(self.upload().status_code,503)
        iid=sha(['lite.research-capture/1',self.h.owner,'legacy',self.capture_id])[:16]
        def revision(rev,area):
            payload=dict(polygons=[[[2,2],[20,2],[20,20]]],image_w=64,image_h=64,surface_cm2=area,
                projected_cm2=area,source='manual',consent_version='2026-10-16.1')
            body=json.dumps(dict(anon_id=self.h.owner,research_consent=True,image_id=iid,revision=rev,
                payload_json=json.dumps(payload))).encode()
            return self.h.post_signed('/api/v1/lite/annotation/revision',body)
        self.assertEqual(revision(1,4).status_code,503)
        self.assertEqual(self.upload().status_code,200)
        before=copy.deepcopy(self.bucket.live)
        for rev,area in [(1,4),(1,4),(2,5),(2,5)]: self.assertEqual(revision(rev,area).status_code,200)
        self.assertEqual(len(self.rows('lite_labels.jsonl')),2)
        self.assertEqual(before,{k:v for k,v in self.bucket.live.items() if k in before}); self.unchanged_usage()

    def test_admission_limit_bounds_new_captures_but_allows_same_capture_readback(self):
        self.budget.limit=1
        first=self.upload(); self.assertEqual(first.status_code,200,first.json)
        other='ff000000-0000-4000-8000-000000000001'
        r=self.upload(ticket=self.proof(capture_id=other),metadata=dict(self.metadata,capture_id=other))
        self.assertEqual(r.status_code,429,r.json); self.assertEqual(self.upload().json,first.json)
        self.unchanged_usage()

    def test_withdrawal_blocks_replay_and_clears_all_capture_objects(self):
        self.assertEqual(self.upload().status_code,200)
        response=self.f.delete(); self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(self.upload().status_code,410)
        self.assertTrue(self.bucket.live)
        self.assertTrue(all(v[1]==b'' and v[2]['state']=='sealed' for v in self.bucket.live.values()))
        self.unchanged_usage()

    def test_withdrawal_seals_paused_rgbd_writer_before_late_upload(self):
        entered=threading.Event(); resume=threading.Event(); output={}
        def pause(blob,data,kwargs):
            if data and '/rgbd/' in blob.name:
                entered.set()
                if not resume.wait(5): raise RuntimeError('timeout')
        raw,ct=self.body(); headers=self.h.headers(PATH,raw,ct)
        def run():
            with self.h.app.test_client() as client: output['response']=client.post(PATH,data=raw,headers=headers,content_type=ct)
        self.bucket.before_upload=pause; t=threading.Thread(target=run); t.start()
        try:
            self.assertTrue(entered.wait(5)); r=self.f.delete(); self.assertEqual(r.status_code,200,r.json)
        finally: resume.set(); t.join(5); self.bucket.before_upload=None
        self.assertFalse(t.is_alive()); self.assertNotEqual(output['response'].status_code,200)
        self.assertTrue(all(v[1]==b'' and v[2]['state']=='sealed' for v in self.bucket.live.values()))
        self.assertEqual(self.upload().status_code,410); self.unchanged_usage()


if __name__ == '__main__': unittest.main()
