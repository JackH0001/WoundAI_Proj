"""Authenticated reviewer routes must honor withdrawal even when files survive."""
import json
import secrets
import unittest
from unittest.mock import patch
from flask_jwt_extended import JWTManager, create_access_token
import test_lite_quota as fixture

class WithdrawalReaderTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.LiteQuotaTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        app=self.f.client.application;app.config['JWT_SECRET_KEY']=secrets.token_hex(32);JWTManager(app)
        with app.app_context():
            self.headers={'Authorization':'Bearer '+create_access_token(identity='test-reviewer',additional_claims={'role':'engineer'})}
        self.image=self.f.post().json['image_id']
        self.base='/api/v1/lite/record/test-installation/'+self.image
        self.f.store.append_line('lite_labels.jsonl',json.dumps(dict(anon_id='test-installation',image_id=self.image,revision=1,polygons=[[[0,0],[10,0],[10,10]]],iou_vs_ai=.5,corrected=True)))

    def revoke(self,ledger,action):
        self.f.store.append_line(ledger,json.dumps(dict(anon_id='test-installation',action=action)))

    def get(self,path): return self.f.client.get(path,headers=self.headers)

    def test_active_image_preview_and_statistics_work(self):
        for suffix in ('/image.jpg','/preview.svg'):
            self.assertEqual(self.get(self.base+suffix).status_code,200)
        r=self.get('/api/v1/lite/records');self.assertEqual(r.status_code,200)
        self.assertEqual(r.json['total'],1);self.assertEqual(r.json['labels'],1)

    def test_surviving_blobs_are_hidden_for_each_revocation_marker(self):
        for ledger in ('lite_index.jsonl','lite_labels.jsonl'):
            original=self.f.store.get_blob(ledger)
            for action in ('deleted','withdrawal_requested','delete_incomplete'):
                with self.subTest(ledger=ledger,action=action):
                    self.f.store.put_blob(ledger,original)
                    self.revoke(ledger,action)
                    for suffix in ('/image.jpg','/preview.svg'):
                        self.assertEqual(self.get(self.base+suffix).status_code,410)
                    r=self.get('/api/v1/lite/records')
                    self.assertEqual(r.status_code,200)
                    self.assertEqual(r.json['records'],[])
                    self.assertEqual(r.json['total'],0)
                    self.assertEqual(r.json['labels'],0)
                    self.assertEqual(r.json['lay_with_ai'],0)
            self.f.store.put_blob(ledger,original)

    def test_unreadable_withdrawal_state_never_returns_data(self):
        with patch.object(self.f.store,'read_lines_fresh',side_effect=OSError('injected')):
            for path in (self.base+'/image.jpg',self.base+'/preview.svg','/api/v1/lite/records'):
                self.assertEqual(self.get(path).status_code,503)

    def test_revocation_does_not_hide_another_installation(self):
        other=self.f.post('another-installation').json['image_id']
        self.revoke('lite_labels.jsonl','delete_incomplete')
        r=self.get('/api/v1/lite/records')
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.json['total'],1)
        self.assertEqual([e['anon_id'] for e in r.json['records']],['another-installation'])
        self.assertEqual(self.get('/api/v1/lite/record/another-installation/'+other+'/image.jpg').status_code,200)

    def test_corrupt_index_never_returns_partial_statistics(self):
        self.f.store.append_line('lite_index.jsonl','not json')
        r=self.get('/api/v1/lite/records')
        self.assertEqual(r.status_code,503)
        self.assertNotIn('records',r.json)

    def test_anonymous_cannot_probe_revocation_state(self):
        self.revoke('lite_index.jsonl','deleted')
        for path in (self.base+'/image.jpg',self.base+'/preview.svg','/api/v1/lite/records'):
            self.assertEqual(self.f.client.get(path).status_code,401)

if __name__=='__main__':unittest.main()
