"""Real Flask/crypto/write path concurrency with only synthetic test media."""
import json
import secrets
from concurrent.futures import ThreadPoolExecutor
import threading
import unittest
from unittest.mock import patch
from flask_jwt_extended import JWTManager, create_access_token
import test_lite_attest_http as fixture
from lite_attest_http import install_lite_attest
from lite_attest_state import StateUnavailable
from flask import Flask


class FenceHTTPTests(unittest.TestCase):
    def setUp(self):
        self.h=fixture.HTTPTests();self.h.setUp();self.addCleanup(self.h.doCleanups)
        self.h.app.config['JWT_SECRET_KEY']=secrets.token_hex(32);JWTManager(self.h.app)
        self.h.register();self.owner=self.h.owner

    def delete(self):
        h=self.h;path='/api/v1/lite/data/'+self.owner
        return h.client.delete(path,headers=h.headers(path,b'','',method='DELETE'))

    def upload(self):
        h=self.h;raw,ctype=h.segment_bytes()
        return h.post_signed('/api/v1/lite/segment',raw,ctype)

    def test_late_blob_writer_blocks_cleanup_until_it_finishes(self):
        h=self.h;entered=threading.Event();resume=threading.Event();result={}
        original=h.f.store.put_blob
        def pause(key,*args,**kwargs):
            if key.endswith('.jpg'):
                entered.set()
                if not resume.wait(5):raise RuntimeError('test writer resume timeout')
            return original(key,*args,**kwargs)
        raw,ctype=h.segment_bytes();headers=h.headers('/api/v1/lite/segment',raw,ctype)
        def upload():
            try:
                with h.app.test_client() as client:
                    result['response']=client.post('/api/v1/lite/segment',data=raw,content_type=ctype,headers=headers)
            except Exception as exc:result['error']=exc
        with patch.object(h.f.store,'put_blob',side_effect=pause):
            thread=threading.Thread(target=upload);thread.start()
            try:
                self.assertTrue(entered.wait(5),'did not reach actual blob write')
                response=self.delete();self.assertEqual(response.status_code,202,response.json)
                self.assertEqual(response.json['status'],'withdrawal_pending')
                self.assertEqual(response.json['anon_id'],self.owner)
                self.assertEqual(self.upload().status_code,410)
            finally:resume.set();thread.join(5)
        self.assertFalse(thread.is_alive());self.assertNotIn('error',result)
        self.assertEqual(result['response'].status_code,200,result['response'].json)
        self.assertTrue(list(h.f.store.list_keys('lite/'+self.owner+'/')))
        # Check before another DELETE can accidentally re-close a reopened gate.
        self.assertEqual(self.upload().status_code,410)
        response=self.delete();self.assertEqual(response.status_code,200,response.json)
        self.assertTrue(response.json['writers_drained'])
        self.assertEqual(response.json['deletion_scope'],'live_media')
        self.assertEqual(list(h.f.store.list_keys('lite/'+self.owner+'/')),[])
        self.assertEqual(self.upload().status_code,410)

    def test_revocation_during_model_inference_denies_writes_and_releases_ticket(self):
        def inference(*args,**kwargs):
            with ThreadPoolExecutor(max_workers=1) as pool:
                response=pool.submit(self.delete).result(5)
            self.assertEqual(response.status_code,202,response.json)
            return original(*args,**kwargs)
        original=fixture.media_fixture.lite._SEGMENT
        with patch.object(fixture.media_fixture.lite,'_SEGMENT',side_effect=inference):
            response=self.upload()
        self.assertEqual(response.status_code,410,response.json)
        self.assertEqual(self.delete().status_code,200)
        self.assertEqual(list(self.h.f.store.list_keys('lite/'+self.owner+'/')),[])

    def test_ledger_failure_cannot_reenable_writes_or_reviewer_reads(self):
        h=self.h;image=self.upload().json['image_id']
        with patch.object(fixture.media_fixture.lite._fw,'append_jsonl',side_effect=OSError('injected')):
            response=self.delete();self.assertEqual(response.status_code,503)
        self.assertTrue(h.privacy.is_withdrawn(self.owner))
        self.assertEqual(self.upload().status_code,410)
        app=h.app
        with app.app_context():
            token=create_access_token(identity='reviewer',additional_claims={'role':'engineer'})
        auth={'Authorization':'Bearer '+token}
        for suffix in ('image.jpg','preview.svg'):
            r=h.client.get('/api/v1/lite/record/'+self.owner+'/'+image+'/'+suffix,headers=auth)
            self.assertEqual(r.status_code,410)
        r=h.client.get('/api/v1/lite/records',headers=auth)
        self.assertEqual(r.status_code,200,r.json);self.assertEqual(r.json['records'],[])

    def test_unreleased_ticket_survives_http_errors_and_cannot_be_cleaned_early(self):
        h=self.h
        with patch.object(h.privacy,'finish_write',side_effect=StateUnavailable('injected')):
            r=self.upload();self.assertEqual(r.status_code,503)
        self.assertEqual(self.delete().status_code,202)

    def test_exception_path_releases_only_after_synchronous_writer_stops(self):
        h=self.h
        def crash(*args,**kwargs):raise RuntimeError('injected model failure')
        with patch.object(fixture.media_fixture.lite,'_SEGMENT',side_effect=crash):
            self.upload()
        self.assertEqual(self.delete().status_code,200)

    def test_nested_control_request_cannot_release_another_requests_writer(self):
        original=fixture.media_fixture.lite._SEGMENT
        def inference(*args,**kwargs):
            response=self.delete()  # Nested test client shares Flask app g, but not request.environ.
            self.assertEqual(response.status_code,202,response.json)
            return original(*args,**kwargs)
        with patch.object(fixture.media_fixture.lite,'_SEGMENT',side_effect=inference):
            response=self.upload()
        self.assertEqual(response.status_code,410,response.json)
        self.assertEqual(self.delete().status_code,200)

    def test_cleanup_removes_research_payloads_but_preserves_other_owners(self):
        h=self.h;self.upload()
        for ledger in ('lite_index.jsonl','lite_labels.jsonl'):
            h.f.store.append_line(ledger,json.dumps(dict(anon_id=self.owner,payload_json='sensitive',polygons=[[1,2]])))
            h.f.store.append_line(ledger,json.dumps(dict(anon_id='other',payload_json='must survive')))
        response=self.delete();self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(response.json['research_ledger_cleanup'],'live_rows_removed')
        for ledger in ('lite_index.jsonl','lite_labels.jsonl'):
            rows=[json.loads(line) for line in h.f.store.read_lines_fresh(ledger) if line.strip()]
            self.assertTrue(any(row.get('payload_json')=='must survive' for row in rows))
            target=[row for row in rows if row.get('anon_id')==self.owner]
            self.assertTrue(target)
            self.assertTrue(all(row.get('action') in ('withdrawal_requested','deleted') for row in target))
            self.assertTrue(all('payload_json' not in row and 'polygons' not in row for row in target))
        self.assertEqual(self.upload().status_code,410)
        self.assertEqual(self.delete().status_code,200)

    def test_research_cleanup_failure_cannot_return_a_completion_receipt(self):
        h=self.h;self.upload()
        h.f.store.append_line('lite_labels.jsonl',json.dumps(dict(anon_id=self.owner,payload_json='pending')))
        with patch('lite_ledger_purge.purge_live_research_rows',side_effect=OSError('injected')):
            response=self.delete()
        self.assertEqual(response.status_code,503,response.json)
        self.assertEqual(response.json['error'],'delete_incomplete')
        self.assertEqual(self.upload().status_code,410)
        self.assertTrue(any('pending' in line for line in h.f.store.read_lines_fresh('lite_labels.jsonl')))
        self.assertEqual(self.delete().status_code,200)
        self.assertFalse(any('pending' in line for line in h.f.store.read_lines_fresh('lite_labels.jsonl')))

    def test_missing_privacy_configuration_cannot_use_otherwise_valid_security(self):
        h=self.h;app=Flask('no-privacy')
        install_lite_attest(app,service=h.e.service,budget=h.budget)
        self.assertEqual(app.test_client().post('/api/v1/lite/attest/challenge',json={}).status_code,503)


if __name__=='__main__':unittest.main()
