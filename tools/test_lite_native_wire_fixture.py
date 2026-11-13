"""Safety boundary of the opt-in loopback fixture (no cloud or Xcode needed)."""
import json
import hashlib
import unittest
from werkzeug.test import Client
from werkzeug.wrappers import Response
from lite_native_wire_fixture import WireFixture


class NativeWireFixtureTests(unittest.TestCase):
    def setUp(self):
        self.fixture = WireFixture(); self.fixture.start()
        self.addCleanup(self.fixture.close)
        self.client = Client(self.fixture, Response)

    def post(self, path, raw=b'{}', remote='127.0.0.1', token=True):
        headers = {'X-Synthetic-Wire':self.fixture.token} if token else {}
        return self.client.post(path, data=raw, content_type='application/json', headers=headers,
                                environ_overrides={'REMOTE_ADDR':remote})

    def test_remote_or_missing_bearer_cannot_create_fixture_state(self):
        self.assertEqual(self.post('/fixture/reset', remote='192.0.2.1').status_code, 403)
        self.assertEqual(self.post('/fixture/reset', token=False).status_code, 403)
        self.assertIsNone(self.fixture.h)

    def test_bad_method_or_oversized_payload_rejected_before_state(self):
        self.assertEqual(self.client.get('/fixture/reset', headers={'X-Synthetic-Wire':self.fixture.token},
                         environ_overrides={'REMOTE_ADDR':'127.0.0.1'}).status_code, 400)
        self.assertEqual(self.post('/fixture/reset', b'x' * (2 * 1024 * 1024 + 1)).status_code, 400)
        self.assertIsNone(self.fixture.h)

    def test_reset_is_synthetic_and_snapshot_excludes_keys_and_images(self):
        seed = self.post('/fixture/reset'); self.assertEqual(seed.status_code, 200)
        snapshot = self.post('/fixture/snapshot')
        self.assertEqual(snapshot.status_code, 200)
        value = snapshot.json
        self.assertEqual((value['model_calls'], value['used'], value['attempts']), (0, 0, 0))
        self.assertTrue(value['research_unchanged']); self.assertTrue(value['image_unchanged'])
        for forbidden in ('jpeg', 'private', 'refresh_token', 'authorizationCode', self.fixture.token):
            self.assertNotIn(forbidden, snapshot.get_data(as_text=True))
        old = self.fixture.h.h.clock()
        invalid = self.post('/fixture/advance', json.dumps({'seconds':1801}).encode())
        self.assertEqual(invalid.status_code, 500)
        self.assertEqual(self.fixture.h.h.clock(), old)

    def test_two_registered_keys_resolve_same_human_without_patched_accounts(self):
        self.post('/fixture/reset')
        h = self.fixture.h; h.login()
        peer_seed = self.post('/fixture/second-installation')
        self.assertEqual(peer_seed.status_code, 200, peer_seed.json)
        peer = self.fixture.clients[peer_seed.json['owner']]; peer.login()
        self.assertNotEqual(peer.key, h.key); self.assertNotEqual(peer.owner, h.owner)
        self.assertEqual(peer.login_result['account_sha256'], h.login_result['account_sha256'])
        self.assertEqual(peer.login_result['app_account_token'], h.login_result['app_account_token'])
        account = h.login_result['account_sha256']
        first = h.post('service/wounds', account_sha256=account, action='open', wound_id='a'*32, revision=0)
        seen = peer.post('service/wounds', account_sha256=account, action='status')
        self.assertEqual(seen.json['active'], first.json['active'])
        self.assertEqual(self.post('/fixture/second-installation').status_code, 500)
        self.assertEqual(len(self.fixture.clients), 2)

    def test_second_signing_key_cannot_name_first_installation_in_body(self):
        self.post('/fixture/reset'); self.fixture.h.login()
        seed = self.post('/fixture/second-installation').json
        peer = self.fixture.clients[seed['owner']]; peer.login()
        reply = peer.post('service/wounds', anon_id=self.fixture.h.owner,
                          account_sha256=peer.login_result['account_sha256'], action='status')
        self.assertEqual(reply.status_code, 403)

    def test_research_adapter_requires_distinct_registered_owner_and_service(self):
        seed = self.post('/fixture/reset-research')
        self.assertEqual(seed.status_code, 200, seed.json)
        self.assertNotEqual(seed.json['owner'], seed.json['research_owner'])
        for service, owner in [('research', seed.json['owner']), ('unknown', seed.json['owner'])]:
            reply = self.client.post('/api/v1/lite/research/capture/status', data=b'{}',
                content_type='application/json', headers={'X-Synthetic-Wire':self.fixture.token,
                    'X-Synthetic-Service':service,'X-Synthetic-Installation':owner},
                environ_overrides={'REMOTE_ADDR':'127.0.0.1'})
            self.assertEqual(reply.status_code,403)
        self.assertEqual(self.fixture.flow.bucket.live,{})
        self.assertEqual(self.post('/fixture/research-readback').status_code,500)
        self.assertEqual(self.post('/fixture/reset').status_code,200)
        self.assertIsNone(self.fixture.flow)

    def test_native_request_cannot_reach_research_receiver(self):
        self.post('/fixture/reset-research')
        reply = self.post('/api/v1/lite/research/capture/status')
        self.assertEqual(reply.status_code,404)
        self.assertEqual(self.fixture.flow.bucket.live,{})

    def test_readback_decodes_stored_packet_not_request_and_preserves_generation(self):
        self.post('/fixture/reset-research')
        f = self.fixture.flow
        f.n.login(); self.assertEqual(f.n.complete().status_code,200)
        ticket = f.n.post('service/research-source',**f.fields)
        self.assertEqual(f.upload(ticket.json).status_code,200)
        before = dict(f.bucket.live)
        reply = self.post('/fixture/research-readback')
        self.assertEqual(reply.status_code,200,reply.json)
        self.assertEqual(reply.json['rgb_sha256'],hashlib.sha256(f.n.f.jpeg).hexdigest())
        self.assertEqual(reply.json['depth_sha256'],hashlib.sha256(f.r.f.raw).hexdigest())
        self.assertEqual(reply.json['index_rows'],1)
        self.assertEqual(reply.json['labels'],[])
        self.assertEqual(before,f.bucket.live)

    def test_privacy_readback_has_bounded_epochs_and_never_mutates_media(self):
        self.post('/fixture/reset-research')
        f = self.fixture.flow; f.n.login(); f.n.complete()
        ticket = f.n.post('service/research-source',**f.fields)
        self.assertEqual(f.upload(ticket.json).status_code,200)
        before = dict(f.bucket.live)
        read = lambda values:self.post('/fixture/research-privacy',json.dumps({'epochs':values}).encode())
        first = read(['legacy']); self.assertEqual(first.status_code,200,first.json)
        self.assertEqual(first.json['current'],'legacy')
        self.assertTrue(any(x['kind']=='rgbd' and x['bytes']>0 for x in first.json['epochs'][0]['objects']))
        self.assertEqual(before,f.bucket.live)
        for invalid in ([],['legacy']*4,['../escape'],None):
            self.assertEqual(read(invalid).status_code,500)
            self.assertEqual(before,f.bucket.live)
        self.assertEqual(f.r.delete().status_code,200)
        sealed = read(['legacy']).json['epochs'][0]
        self.assertTrue(sealed['withdrawn'])
        self.assertTrue(all(x['state']=='sealed' and x['bytes']==0 for x in sealed['objects']))
        self.assertEqual(len(sealed['objects']),len(first.json['epochs'][0]['objects']))

    def test_delete_requires_exact_privacy_path_empty_body_and_matching_owner(self):
        seed = self.post('/fixture/reset-research').json
        headers = {'X-Synthetic-Wire':self.fixture.token,'X-Synthetic-Service':'research',
                   'X-Synthetic-Installation':seed['research_owner'],'Content-Length':'0'}
        path = '/api/v1/lite/data/'+seed['research_owner']
        def request(url, data=b'', extra=None):
            return self.client.delete(url,data=data,headers=headers | (extra or {}),
                                      environ_overrides={'REMOTE_ADDR':'127.0.0.1',
                                                         'CONTENT_LENGTH':str(len(data))})
        self.assertEqual(request('/fixture/reset').status_code,400)
        self.assertEqual(request(path,b'nonempty').status_code,400)
        self.assertEqual(request(path,extra={'X-Synthetic-Installation':seed['owner']}).status_code,403)
        self.assertFalse(self.fixture.flow.privacy.is_withdrawn(seed['research_owner']))
        reply = request(path)
        self.assertEqual(reply.status_code,200,reply.json)
        self.assertTrue(self.fixture.flow.privacy.is_withdrawn(seed['research_owner']))
        self.assertEqual(reply.json['live_payloads_remaining'],0)

    def test_deletion_readback_requires_accepted_exact_request_and_never_dispatches_worker(self):
        self.post('/fixture/reset'); h = self.fixture.h; h.login(); h.complete()
        operation = 'd'*32
        for bad in (operation, None, '../escape'):
            self.assertEqual(self.post('/fixture/deletion-readback',json.dumps({'operation':bad}).encode()).status_code,500)
        accepted = h.post('account/deletion/request', operation=operation,
                          confirmation='delete_account_and_withdraw_linked_research_v1')
        self.assertEqual(accepted.status_code,202,accepted.json)
        raw = json.dumps({'operation':operation}).encode()
        first = self.post('/fixture/deletion-readback',raw)
        self.assertEqual(first.status_code,200,first.json)
        self.assertEqual(first.json['phase'],'closing')
        self.assertTrue(first.json['research_requested'])
        self.assertEqual(first.json['apple_revoke_calls'],0)
        self.assertEqual(self.post('/fixture/deletion-readback',raw).json,first.json)
        self.assertEqual(self.post('/fixture/snapshot').json['used'],1)
        for forbidden in ('refresh_token','private_key','authorizationCode',self.fixture.token):
            self.assertNotIn(forbidden,first.get_data(as_text=True))

    def test_synthetic_worker_is_explicit_bounded_and_replay_does_not_revoke_twice(self):
        self.post('/fixture/reset'); h = self.fixture.h; h.login(); h.complete()
        operation = 'd'*32
        self.assertEqual(h.post('account/deletion/request',operation=operation,
            confirmation='delete_account_and_withdraw_linked_research_v1').status_code,202)
        original = self.fixture.deletion_readback(operation)
        for limit in (0,2,True,None):
            raw = json.dumps({'operation':operation,'limit':limit}).encode()
            self.assertEqual(self.post('/fixture/deletion-worker',raw).status_code,500)
            self.assertEqual(self.fixture.deletion_readback(operation),original)
        wrong = json.dumps({'operation':'f'*32,'limit':1}).encode()
        self.assertEqual(self.post('/fixture/deletion-worker',wrong).status_code,500)
        raw = json.dumps({'operation':operation,'limit':1}).encode()
        first = self.post('/fixture/deletion-worker',raw)
        self.assertEqual(first.status_code,200,first.json)
        self.assertEqual((first.json['revoked'],first.json['apple_revoke_calls']),(1,1))
        self.assertEqual(first.json['service_store_sha256'],original['service_store_sha256'])
        self.assertEqual(self.post('/fixture/deletion-worker',raw).json,first.json)
        status = h.post('account/deletion/status',operation=operation)
        self.assertFalse(status.json['account_deleted']); self.assertFalse(status.json['subscription_cancelled'])


if __name__ == '__main__': unittest.main()
