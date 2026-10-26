"""Expired reservation is distinct from privacy rejection. Signed synthetic HTTP only."""
import copy
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from lite_attest_state import GCSStateStore, StateUnavailable
from lite_service_operations import ImageRejected
from lite_service_recovery import ServiceRecovery
from lite_usage_ledger import UsageConflict, digest
import test_lite_service_recovery as fixture

class ResubmitTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.CrossDayTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.h=self.f.h;self.h.e.now=(self.h.e.now//86400)*86400+100
        self.op=self.f.op
    def expired(self):
        h=self.h
        self.assertEqual(h.json_request('prepare').status_code,200)
        self.original=copy.deepcopy(h.ops.load(h.subject,self.op)[1])
        h.e.now+=900
        r=h.json_request('status');self.assertEqual(r.status_code,200,r.json)
        self.assertEqual((r.json['state'],r.json['cancel_reason']),('cancelled','reservation_expired'))
        return r.json
    def retry(self):
        return self.h.json_request('resubmit',image_sha256=self.h.image_hash)
    def test_expiry_status_does_not_create_successor_or_run(self):
        self.expired();h=self.h
        for _ in range(3):
            self.assertEqual(h.json_request('status').json['retry_action'],'none')
            self.assertEqual(h.run_request().json['cancel_reason'],'reservation_expired')
        self.assertIsNone(h.ops.recovery.store.load(h.ops.recovery.key(h.subject,self.op)))
        self.assertEqual((h.model_calls,h.quota()['used'],h.quota()['pending']),(0,0,0))
    def test_explicit_retry_replay_executes_and_charges_once(self):
        parent=self.expired();h=self.h;a=self.retry()
        self.assertEqual(a.status_code,200,a.json)
        self.assertEqual(a.json['recovery']['version'],2)
        self.assertEqual(a.json['recovery']['reason'],'reservation_expired')
        new=int(a.json['recovery']['successor_operation'],16)
        self.assertEqual(self.retry().json,a.json)
        self.assertEqual(h.json_request('prepare',new).json['state'],'ready')
        done=h.run_request(new);self.assertEqual(done.json['state'],'completed')
        self.assertEqual(h.run_request(new).json,done.json)
        self.assertEqual(self.retry().json,a.json)
        self.assertEqual((h.model_calls,h.quota()['used'],h.quota()['attempts']),(1,1,2))
        self.assertEqual(h.json_request('status').json,parent)
        self.assertEqual(h.ops.load(h.subject,self.op)[1],self.original)
    def test_preflight_rejection_cannot_resubmit_even_after_deadline(self):
        h=self.h;h.json_request('prepare')
        with patch.object(h.inference,'decode',side_effect=ImageRejected('face')):
            self.assertEqual(h.run_request().status_code,422)
        h.e.now+=901
        self.assertEqual(h.json_request('status').json['cancel_reason'],'preflight_rejected')
        self.assertEqual(self.retry().status_code,409)
        self.assertEqual(h.model_calls,0)
    def test_unknown_legacy_cancel_is_not_guessed_expiry(self):
        h=self.h;h.json_request('prepare')
        row=h.ops.load(h.subject,self.op)[1]
        h.ledger.cancel_unstarted(h.ops.ticket(h.subject,row),'e'*64);h.e.now+=901
        self.assertEqual(h.json_request('status').json['cancel_reason'],'cancelled_unknown')
        self.assertEqual(self.retry().status_code,409)
    def test_running_cannot_resubmit_or_be_refunded(self):
        h=self.h;h.json_request('prepare')
        def fail(_):h.model_calls+=1;raise StateUnavailable('unknown model')
        with patch.object(h.inference,'infer',side_effect=fail):self.assertEqual(h.run_request().status_code,503)
        h.e.now+=901
        self.assertEqual(self.retry().status_code,409)
        self.assertEqual(h.run_request().status_code,202)
        self.assertEqual((h.model_calls,h.quota()['pending']),(1,1))
    def test_completed_cannot_resubmit(self):
        h=self.h;h.json_request('prepare');h.run_request();h.e.now+=901
        self.assertEqual(self.retry().status_code,409)
        self.assertEqual((h.model_calls,h.quota()['used']),(1,1))
    def test_missing_reservation_cannot_use_expiry_endpoint(self):
        h=self.h
        with patch.object(h.ledger,'reserve',side_effect=StateUnavailable('no reservation')):
            self.assertEqual(h.json_request('prepare').status_code,503)
        h.e.now+=901
        self.assertEqual(self.retry().status_code,409)
        self.assertEqual(h.model_calls,0)
    def test_successor_prepare_rechecks_quota(self):
        self.expired();h=self.h;a=self.retry().json
        new=int(a['recovery']['successor_operation'],16)
        h.complete(10);h.complete(11)
        self.assertEqual(h.json_request('prepare',new).status_code,429)
        self.assertEqual(self.retry().json,a)
        self.assertEqual(h.model_calls,2)
    def test_lost_authorization_response_reuses_single_id(self):
        self.expired();h=self.h;cas=h.ops.recovery.store.compare_exchange
        def lost(*a):cas(*a);raise StateUnavailable('lost auth ack')
        with patch.object(h.ops.recovery.store,'compare_exchange',side_effect=lost):
            self.assertEqual(self.retry().status_code,503)
        a=self.retry().json;self.assertEqual(self.retry().json,a)
        new=int(a['recovery']['successor_operation'],16)
        h.json_request('prepare',new);h.run_request(new)
        self.assertEqual((h.model_calls,h.quota()['used']),(1,1))
    def test_concurrent_explicit_retries_share_one_authorization(self):
        self.expired();h=self.h;b=threading.Barrier(6)
        def run(_):
            b.wait(5);return h.ops.recovery.resubmit(h.owner,self.op,h.image_hash)
        with ThreadPoolExecutor(max_workers=6) as pool:rows=list(pool.map(run,range(6)))
        self.assertTrue(all(r==rows[0] for r in rows))
        self.assertEqual(h.model_calls,0)
    def test_claim_inflight_cas_loses_to_expiration(self):
        h=self.h;h.json_request('prepare');cas=h.ledger.store.compare_exchange
        entered=threading.Event();release=threading.Event();armed=[True]
        def pause(key,rev,value):
            e=value.get('entries',{}).get(self.op)
            if armed[0] and e and e['history'][-1][1]=='running':
                armed[0]=False;entered.set();self.assertTrue(release.wait(10))
            return cas(key,rev,value)
        with patch.object(h.ledger.store,'compare_exchange',side_effect=pause),ThreadPoolExecutor(max_workers=1) as pool:
            pending=pool.submit(h.ops.run,h.owner,self.op,h.f.jpeg)
            self.assertTrue(entered.wait(5));h.e.now+=900
            a=self.retry();self.assertEqual(a.status_code,200,a.json);release.set()
            self.assertEqual(pending.result(5)['state'],'cancelled')
        new=int(a.json['recovery']['successor_operation'],16)
        h.json_request('prepare',new);h.run_request(new)
        self.assertEqual((h.model_calls,h.quota()['used']),(1,1))
    def test_identity_input_and_extra_fields_rejected(self):
        self.expired();h=self.h
        self.assertEqual(h.json_request('resubmit',image_sha256='a'*64).status_code,409)
        self.assertEqual(h.json_request('resubmit',image_sha256=h.image_hash,anon_id='other').status_code,403)
        self.assertEqual(h.json_request('resubmit',image_sha256=h.image_hash,cancel_reason='reservation_expired').status_code,409)
        self.assertEqual(h.model_calls,0)
    def test_disabled_or_corrupt_authority_never_creates_retry(self):
        self.expired();h=self.h;recovery=h.ops.recovery
        h.ops.recovery=None;self.assertEqual(self.retry().status_code,409);h.ops.recovery=recovery
        recovery.store.compare_exchange(recovery.key(h.subject,self.op),0,{'v':999})
        before=recovery.store.load(recovery.key(h.subject,self.op))
        self.assertEqual(self.retry().status_code,503)
        self.assertEqual(recovery.store.load(recovery.key(h.subject,self.op)),before)
    def test_read_window_expiry_stops_old_and_successor_rotation(self):
        self.expired();h=self.h;h.e.now=self.original['expires']
        self.assertEqual(self.retry().status_code,410)
        self.assertEqual(h.model_calls,0)
    def test_model_change_does_not_guess_new_binding(self):
        self.expired();h=self.h;h.ops.model_version='d'*64
        self.assertEqual(self.retry().status_code,409)
        self.assertEqual(h.model_calls,0)
    def test_pause_prevents_new_authorization(self):
        self.expired();self.f.base.frozen=False
        self.assertEqual(self.retry().status_code,503)
        self.assertEqual(self.h.model_calls,0)

class ResubmitGCSTests(ResubmitTests):
    def setUp(self):
        super().setUp()
        from test_lite_attest_state import FakeBucket
        self.bucket=FakeBucket()
        self.h.ledger.store=GCSStateStore(self.bucket,prefix='synthetic/resubmit/usage')
        self.h.ops.store=GCSStateStore(self.bucket,prefix='synthetic/resubmit/ops')
        self.h.ops.recovery=ServiceRecovery(self.h.ops,GCSStateStore(self.bucket,prefix='synthetic/resubmit/auth'))
    def test_old_versions_preserved(self):
        self.expired();before=dict(self.bucket.versions)
        a=self.retry().json;new=int(a['recovery']['successor_operation'],16)
        self.h.json_request('prepare',new);self.h.run_request(new)
        for key,value in before.items():self.assertEqual(self.bucket.versions[key],value)
        self.assertTrue(all(c[0] in ('reload','download','upload') for c in self.bucket.calls))

if __name__=='__main__':unittest.main()
