"""Same authenticated identity; no resurrection, replacement key or cloud calls."""
import json
import unittest
from unittest.mock import patch
from lite_reconsent import Reconsent, ReconsentRejected, CONSENT, DISCLOSURE
from lite_fenced_objects import ObjectRef
from lite_attest_state import StateUnavailable
import test_lite_fenced_http as fixture

class ReconsentTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.FencedHTTPTests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.h, self.privacy = self.f.h, self.f.privacy
        self.renew = Reconsent(self.privacy)
        self.owner, self.new = self.h.owner, 'a' * 32

    def body(self, **changes):
        return dict(dict(anon_id=self.owner, research_consent=True, previous_epoch='legacy',
            new_epoch=self.new, consent_version=CONSENT, disclosure_version=DISCLOSURE), **changes)

    def rejoin(self, **changes):
        return self.h.post_signed('/api/v1/lite/research/rejoin', json.dumps(self.body(**changes)).encode(), 'application/json')

    def clear(self):
        self.assertEqual(self.f.upload().status_code, 200)
        self.assertEqual(self.f.delete().status_code, 200)

    def new_upload(self, epoch=None):
        from werkzeug.test import EnvironBuilder
        import io
        fields = dict(anon_id=self.owner, research_consent='true', consent_version=CONSENT,
            consent_epoch=epoch or self.new, image=(io.BytesIO(self.h.f.jpeg), 'synthetic.jpg'))
        builder = EnvironBuilder(method='POST', data=fields)
        try:
            env = builder.get_environ(); raw, ctype = env['wsgi.input'].read(), env['CONTENT_TYPE']
        finally: builder.close()
        return self.h.post_signed('/api/v1/lite/segment', raw, ctype)

    def test_same_identity_new_namespace_old_receipts_and_fences_remain(self):
        self.clear(); before = dict(self.f.bucket.live)
        receipt = self.rejoin(); self.assertEqual(receipt.status_code, 200, receipt.json)
        self.assertEqual(receipt.json['anon_id'], self.owner)
        self.assertEqual(receipt.json['scope'], 'future_captures_only')
        self.assertFalse(receipt.json['historical_media_restored'])
        self.assertEqual(before, self.f.bucket.live)
        self.assertTrue(self.privacy.is_withdrawn(self.owner))
        response = self.new_upload(); self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['consent_epoch'], self.new)
        self.assertEqual({k:self.f.bucket.live[k] for k in before}, before)
        self.assertTrue(all(k in before or k.startswith('lite_epochs/v1/'+self.new+'/') for k in self.f.bucket.live))
        self.assertEqual(self.f.upload().status_code, 409)  # old client/queued request

    def test_idempotent_renewal_and_restart_returns_same_receipt(self):
        self.clear(); first = self.rejoin()
        self.assertEqual(first.status_code, 200)
        self.f.restart(); self.privacy = self.f.privacy
        second = self.rejoin(); self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json, second.json)

    def test_new_rgbd_and_revision_retries_preserve_exact_media_and_old_tombstones(self):
        from lite_raw_depth_store import unpack
        self.clear(); old = dict(self.f.bucket.live)
        self.assertEqual(self.rejoin().status_code, 200)
        response = self.f.f.upload(extra=[('consent_epoch', self.new)])
        self.assertEqual(response.status_code, 200, response.json)
        iid = response.json['image_id']; media = self.privacy.scoped(self.new).media(self.owner)
        packet = unpack(media.get_blob('lite_raw/'+self.owner+'/'+iid+'.rgbd'))
        self.assertEqual(packet['raw_depth'], self.f.f.f.raw)
        self.assertEqual(packet['jpeg'], self.f.f.f.jpeg)
        self.assertEqual(packet['metadata_bytes'], json.dumps(self.f.f.f.m).encode())
        before = {k:v for k,v in self.f.bucket.live.items() if '/image/' in k or '/rgbd/' in k}
        payload = dict(polygons=[[[0,0],[8,0],[8,8]]], image_w=self.f.f.f.m['rgb_width'],
            image_h=self.f.f.f.m['rgb_height'], surface_cm2=4, projected_cm2=4,
            source='manual', consent_version=CONSENT)
        body = dict(anon_id=self.owner, image_id=iid, revision=1, research_consent=True,
            consent_epoch=self.new, payload_json=json.dumps(payload))
        def post(): return self.h.post_signed('/api/v1/lite/annotation/revision', json.dumps(body).encode(), 'application/json')
        first = post(); self.assertEqual(first.status_code, 200, first.json)
        repeat = post(); self.assertEqual(repeat.status_code, 200); self.assertEqual(first.json, repeat.json)
        self.assertEqual(len(list(media.read_lines_fresh('lite_labels.jsonl'))), 1)
        self.assertEqual(first.json['consent_epoch'], self.new)
        self.assertEqual(before, {k:self.f.bucket.live[k] for k in before})
        self.assertEqual(old, {k:self.f.bucket.live[k] for k in old})
        body['consent_epoch'] = 'legacy'
        self.assertEqual(post().status_code, 409)
        self.assertEqual(len(list(media.read_lines_fresh('lite_labels.jsonl'))), 1)

    def test_no_renewal_before_withdrawal_or_without_explicit_current_consent(self):
        self.assertEqual(self.rejoin().status_code, 409)
        self.clear()
        for changes in (dict(research_consent=False),dict(consent_version='old'),dict(disclosure_version='old'),
                        dict(new_epoch='legacy'),dict(new_epoch='../other'),dict(anon_id='b'*32)):
            self.assertIn(self.rejoin(**changes).status_code, (403,409))
        self.assertEqual(self.renew.load(self.owner)[1], 'legacy')

    def test_partial_cleanup_or_pending_write_cannot_rejoin(self):
        self.assertEqual(self.f.upload().status_code, 200)
        self.privacy.manifest.withdraw(self.owner)
        self.assertEqual(self.rejoin().status_code, 409)
        self.assertEqual(self.renew.load(self.owner)[1], 'legacy')

    def test_unsigned_renewal_denied(self):
        response = self.h.app.test_client().post('/api/v1/lite/research/rejoin', json=self.body())
        self.assertEqual(response.status_code, 401)

    def test_competing_renewal_cannot_change_the_active_epoch(self):
        self.clear(); self.assertEqual(self.rejoin().status_code, 200)
        self.assertEqual(self.rejoin(new_epoch='b'*32).status_code, 409)
        self.assertEqual(self.renew.load(self.owner)[1], self.new)

    def test_lost_commit_response_can_retry_exact_request(self):
        self.clear(); original = self.renew.store.compare_exchange
        key = self.renew.key(self.owner)
        def lose(k, revision, value):
            result = original(k, revision, value)
            if k == key: raise StateUnavailable('response lost')
            return result
        with patch.object(self.renew.store, 'compare_exchange', side_effect=lose):
            with self.assertRaises(StateUnavailable): self.renew.resume(self.owner, self.body())
        self.assertEqual(self.renew.resume(self.owner, self.body())['consent_epoch'], self.new)

    def test_old_delayed_delete_and_upload_do_not_touch_new_data(self):
        self.clear(); self.assertEqual(self.rejoin().status_code, 200)
        self.assertEqual(self.new_upload().status_code, 200)
        new_before = {k:v for k,v in self.f.bucket.live.items() if k.startswith('lite_epochs/')}
        self.assertEqual(self.f.delete().status_code, 200)
        self.assertEqual(self.f.upload().status_code, 409)
        self.assertEqual(new_before, {k:v for k,v in self.f.bucket.live.items() if k.startswith('lite_epochs/')})

    def test_new_epoch_withdrawal_cannot_be_undone_by_old_rejoin_retry(self):
        self.clear(); self.assertEqual(self.rejoin().status_code, 200)
        self.assertEqual(self.new_upload().status_code, 200)
        path = '/api/v1/lite/data/' + self.owner + '/' + self.new
        headers = self.h.headers(path, b'', '', method='DELETE')
        response = self.h.app.test_client().delete(path, headers=headers)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(self.new_upload().status_code, 410)
        self.assertEqual(self.rejoin().status_code, 409)
        self.assertEqual(self.rejoin(previous_epoch=self.new, new_epoch='b'*32).status_code, 200)

    def test_old_pin_and_ticket_cannot_cross_epoch_namespace(self):
        self.clear(); self.rejoin()
        new = self.privacy.scoped(self.new)
        ref = ObjectRef(self.owner, 'image', 'c'*16)
        pin = self.privacy.objects.pin(ref)
        with self.assertRaises(ValueError): new.objects.write(pin, b'wrong epoch')
        ticket = new.begin_write(self.owner)
        with self.assertRaises(StateUnavailable): self.privacy.finish_write(ticket)
        new.finish_write(ticket)

    def test_missing_or_corrupt_audit_fails_closed(self):
        self.clear(); self.rejoin()
        key = self.renew.key(self.owner, self.new)
        generation, event = self.renew.store.load(key)
        event['owner'] = 'b'*32
        self.assertTrue(self.renew.store.compare_exchange(key, generation, event))
        with self.assertRaises(StateUnavailable): self.renew.resolve(self.owner, self.new)
        self.assertEqual(self.new_upload().status_code, 503)

if __name__ == '__main__': unittest.main()
