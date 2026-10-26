"""G3 signed HTTP, immutable recovery and original-day CAS races. Synthetic only."""
import copy
import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from lite_attest_state import SQLiteStateStore, GCSStateStore, StateUnavailable
from lite_usage_ledger import free_grant, UsageConflict
from lite_service_recovery import ServiceRecovery
import test_lite_usage_recovery_http as recovery_fixture


class CrossDayTests(unittest.TestCase):
    def setUp(self):
        self.base=recovery_fixture.RecoveryHTTPTests();self.base.setUp();self.addCleanup(self.base.doCleanups)
        self.h=self.base.h
        self.recovery_store=SQLiteStateStore(self.h.root/'recovery.sqlite')
        self.h.ops.recovery=ServiceRecovery(self.h.ops,self.recovery_store)
        self.h.e.now=(self.h.e.now//86400+1)*86400-5
        self.old_day=self.h.e.now//86400;self.op=f'{1:032x}'

    def stranded(self):
        h=self.h
        with patch.object(h.ledger,'reserve',side_effect=StateUnavailable('before reservation CAS')):
            self.assertEqual(h.json_request('prepare').status_code,503)
        self.original=copy.deepcopy(h.ops.load(h.subject,self.op)[1])
        h.e.now+=6

    def recover(self):
        return self.h.json_request('recover',image_sha256=self.h.image_hash)

    def new_id(self,response):return int(response.json['recovery']['successor_operation'],16)

    def test_cross_day_recovery_then_one_execution_and_charge(self):
        self.stranded();h=self.h
        self.assertEqual(h.json_request('status').json['state'],'recovery_available')
        reply=self.recover();self.assertEqual(reply.status_code,200,reply.json)
        new=self.new_id(reply)
        self.assertEqual(self.recover().json,reply.json)
        self.assertEqual(h.json_request('prepare',new).json['state'],'ready')
        result=h.run_request(new);self.assertEqual(result.json['state'],'completed')
        self.assertEqual(h.run_request(new).json,result.json)
        self.assertEqual((h.model_calls,h.quota()['used'],h.quota()['attempts']),(1,1,1))
        self.assertEqual(h.ops.load(h.subject,self.op)[1],self.original)
        self.assertEqual(h.json_request('status').json,reply.json)
        self.assertEqual(h.run_request().json,reply.json)
        self.assertFalse(result.json['research_stored'])

    def test_reservation_commit_reply_lost_recovers_original_no_new_day_charge(self):
        h=self.h;reserve=h.ledger.reserve
        def lost(*a,**k):reserve(*a,**k);raise StateUnavailable('reply lost')
        with patch.object(h.ledger,'reserve',side_effect=lost):self.assertEqual(h.json_request('prepare').status_code,503)
        h.e.now+=6
        self.assertEqual(self.recover().json['state'],'ready')
        self.assertEqual(h.run_request().json['state'],'completed')
        self.assertEqual(h.model_calls,1);self.assertEqual(h.quota()['used'],0)
        self.assertEqual(h.ledger.receipt(h.ops.ticket(h.subject,h.ops.load(h.subject,self.op)[1]))['outcome'],'succeeded')
        self.assertIsNone(self.recovery_store.load(h.ops.recovery.key(h.subject,self.op)))

    def test_auth_reply_lost_reads_same_successor(self):
        self.stranded();h=self.h;cas=self.recovery_store.compare_exchange
        def lost(*a):cas(*a);raise StateUnavailable('lost authorization reply')
        with patch.object(self.recovery_store,'compare_exchange',side_effect=lost):
            self.assertEqual(self.recover().status_code,503)
        stored=copy.deepcopy(self.recovery_store.load(h.ops.recovery.key(h.subject,self.op)))
        a=self.recover();b=self.recover()
        self.assertEqual(a.json,b.json);self.assertEqual(stored,self.recovery_store.load(h.ops.recovery.key(h.subject,self.op)))
        self.assertEqual(h.model_calls,0)

    def test_successor_create_reply_lost_does_not_rotate(self):
        self.stranded();h=self.h;cas=h.ops.store.compare_exchange
        def lost(*a):cas(*a);raise StateUnavailable('lost successor reply')
        with patch.object(h.ops.store,'compare_exchange',side_effect=lost):
            self.assertEqual(self.recover().status_code,503)
        a=self.recover();new=self.new_id(a)
        self.assertEqual(h.json_request('prepare',new).status_code,200)
        self.assertEqual(h.run_request(new).status_code,200)
        self.assertEqual((h.model_calls,h.quota()['used']),(1,1))

    def test_concurrent_recoveries_pin_single_authorization(self):
        self.stranded();h=self.h;barrier=threading.Barrier(8)
        def recover(_):
            barrier.wait(5)
            return h.ops.recovery.recover(h.owner,self.op,h.image_hash)
        with ThreadPoolExecutor(max_workers=8) as pool:replies=list(pool.map(recover,range(8)))
        self.assertTrue(all(r==replies[0] for r in replies))
        self.assertEqual(h.model_calls,0)
        new=int(replies[0]['recovery']['successor_operation'],16)
        h.json_request('prepare',new)
        barrier=threading.Barrier(4)
        def run(_):
            barrier.wait(5);return h.ops.run(h.owner,f'{new:032x}',h.f.jpeg)
        with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(run,range(4)))
        self.assertEqual((h.model_calls,h.quota()['used'],h.quota()['attempts']),(1,1,1))

    def test_parent_paused_reservation_loses_to_fence(self):
        h=self.h;cas=h.ledger.store.compare_exchange
        entered=threading.Event();release=threading.Event();armed=[True]
        def pause(key,revision,value):
            if armed[0] and self.op in value.get('entries',{}):
                armed[0]=False;entered.set();self.assertTrue(release.wait(10))
            return cas(key,revision,value)
        with patch.object(h.ledger.store,'compare_exchange',side_effect=pause), ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(h.ops.prepare,h.owner,self.op,h.image_hash)
            self.assertTrue(entered.wait(5));h.e.now+=6
            reply=self.recover();self.assertEqual(reply.status_code,200,reply.json)
            release.set()
            with self.assertRaises(UsageConflict):future.result(5)
        new=self.new_id(reply);h.json_request('prepare',new);h.run_request(new)
        self.assertEqual(h.model_calls,1)
        self.assertEqual(h.run_request().json['state'],'new_operation_required')

    def test_fence_commit_reply_lost_keeps_both_audits(self):
        self.stranded();h=self.h;cas=h.ledger.store.compare_exchange
        def lost(*a):cas(*a);raise StateUnavailable('lost fence reply')
        with patch.object(h.ledger.store,'compare_exchange',side_effect=lost):
            self.assertEqual(self.recover().status_code,503)
        before=copy.deepcopy(h.ledger.store.load(h.ledger.key(h.subject,self.old_day)))
        self.assertEqual(self.recover().status_code,200)
        self.assertEqual(before,h.ledger.store.load(h.ledger.key(h.subject,self.old_day)))

    def test_running_after_midnight_cannot_create_successor(self):
        h=self.h;h.json_request('prepare')
        def fail(_):h.model_calls+=1;raise StateUnavailable('unknown model completion')
        with patch.object(h.inference,'infer',side_effect=fail):self.assertEqual(h.run_request().status_code,503)
        h.e.now+=6
        for _ in range(3):self.assertEqual(self.recover().status_code,202)
        self.assertEqual(h.model_calls,1)
        self.assertIsNone(self.recovery_store.load(h.ops.recovery.key(h.subject,self.op)))

    def test_completed_and_settlement_uncertain_never_reexecute(self):
        h=self.h;h.json_request('prepare');settle=h.ledger.settle
        with patch.object(h.ledger,'settle',side_effect=StateUnavailable('result saved, no settlement reply')):
            self.assertEqual(h.run_request().status_code,503)
        h.e.now+=6
        self.assertEqual(self.recover().json['state'],'completed')
        self.assertEqual(self.recover().json['state'],'completed')
        self.assertEqual(h.model_calls,1);self.assertEqual(h.quota()['used'],0)

    def test_expired_parent_never_recovers(self):
        self.stranded();h=self.h;h.e.now=self.original['expires']
        self.assertEqual(self.recover().status_code,410)
        self.assertEqual(h.model_calls,0)

    def test_cancelled_original_is_not_replaced(self):
        h=self.h;h.json_request('prepare');h.e.now+=901;h.run_request()
        self.assertEqual(self.recover().json['state'],'cancelled')
        self.assertIsNone(self.recovery_store.load(h.ops.recovery.key(h.subject,self.op)))
        self.assertEqual(h.model_calls,0)

    def test_wrong_image_owner_and_client_successor_fields_rejected(self):
        self.stranded();h=self.h
        self.assertEqual(h.json_request('recover',image_sha256='b'*64).status_code,409)
        self.assertEqual(h.json_request('recover',image_sha256=h.image_hash,anon_id='other').status_code,403)
        for field in ('day','successor_operation','paid','research_consent'):
            self.assertEqual(h.json_request('recover',image_sha256=h.image_hash,**{field:True}).status_code,409)
        self.assertEqual(h.model_calls,0)

    def test_corrupt_authorization_is_not_replaced(self):
        self.stranded();h=self.h
        self.recovery_store.compare_exchange(h.ops.recovery.key(h.subject,self.op),0,{'v':999})
        before=self.recovery_store.load(h.ops.recovery.key(h.subject,self.op))
        self.assertEqual(self.recover().status_code,503)
        self.assertEqual(before,self.recovery_store.load(h.ops.recovery.key(h.subject,self.op)))

    def test_fence_unavailable_blocks_recovery_without_charging(self):
        self.stranded();self.base.frozen=False
        self.assertEqual(self.recover().status_code,503)
        self.assertIsNone(self.recovery_store.load(self.h.ops.recovery.key(self.h.subject,self.op)))
        self.assertEqual(self.h.model_calls,0)

    def test_recovered_prepare_quota_keeps_auth_and_original(self):
        self.stranded();h=self.h;a=self.recover();new=self.new_id(a)
        h.complete(10);h.complete(11)
        self.assertEqual(h.json_request('prepare',new).status_code,429)
        self.assertEqual(self.recover().json,a.json)
        self.assertEqual(h.model_calls,2);self.assertEqual(h.ops.load(h.subject,self.op)[1],self.original)

    def test_model_change_cannot_authorize_new_inference(self):
        self.stranded();h=self.h;h.ops.model_version='e'*64
        self.assertEqual(self.recover().status_code,409)
        self.assertIsNone(self.recovery_store.load(h.ops.recovery.key(h.subject,self.op)))
        self.assertEqual(h.model_calls,0)

    def test_successor_collision_keeps_existing_data_and_blocks(self):
        self.stranded();h=self.h
        h.json_request('prepare',2)
        before=copy.deepcopy(h.ops.load(h.subject,f'{2:032x}'))
        with patch('lite_service_recovery._new_id',return_value=f'{2:032x}'):
            self.assertEqual(self.recover().status_code,503)
        self.assertEqual(h.ops.load(h.subject,f'{2:032x}'),before)
        self.assertEqual(self.recover().status_code,503)
        self.assertEqual(h.model_calls,0)

    def test_expired_successor_never_rotates_parent_authority(self):
        self.stranded();h=self.h;a=self.recover();new=self.new_id(a)
        h.json_request('prepare',new);h.run_request(new)
        # Expiry remains a stop, not another recovery or billable operation.
        h.e.now=h.ops.load(h.subject,f'{new:032x}')[1]['expires']
        self.assertEqual(self.recover().status_code,410)
        self.assertEqual(h.run_request(new).status_code,410)
        self.assertEqual(h.model_calls,1)
        self.assertEqual(h.ops.load(h.subject,self.op)[1],self.original)

    def test_paused_rollout_replays_authority_but_cannot_reserve_successor(self):
        self.stranded();h=self.h;a=self.recover();new=self.new_id(a)
        self.base.frozen=False
        self.assertEqual(self.recover().json,a.json)
        self.assertEqual(h.json_request('prepare',new).status_code,503)
        self.assertEqual(h.model_calls,0)

    def test_corrupt_old_daily_row_cannot_be_interpreted_as_absence(self):
        self.stranded();h=self.h;key=h.ledger.key(h.subject,self.old_day)
        revision,_=h.ledger.store.load(key)
        h.ledger.store.compare_exchange(key,revision,{'v':999})
        before=h.ledger.store.load(key)
        self.assertEqual(self.recover().status_code,503)
        self.assertEqual(h.ledger.store.load(key),before)
        self.assertIsNone(self.recovery_store.load(h.ops.recovery.key(h.subject,self.op)))
        self.assertEqual(h.model_calls,0)


class CrossDayGCSTests(CrossDayTests):
    def setUp(self):
        super().setUp()
        from test_lite_attest_state import FakeBucket
        self.bucket=FakeBucket()
        self.h.ledger.store=GCSStateStore(self.bucket,prefix='synthetic/g3/usage')
        self.h.ops.store=GCSStateStore(self.bucket,prefix='synthetic/g3/operations')
        self.recovery_store=GCSStateStore(self.bucket,prefix='synthetic/g3/recovery')
        self.h.ops.recovery=ServiceRecovery(self.h.ops,self.recovery_store)

    def test_generations_preserved(self):
        self.stranded();before=dict(self.bucket.versions)
        reply=self.recover();new=self.new_id(reply)
        self.h.json_request('prepare',new);self.h.run_request(new)
        for key,value in before.items():self.assertEqual(self.bucket.versions[key],value)
        self.assertTrue(all(call[0] in ('reload','download','upload') for call in self.bucket.calls))


if __name__=='__main__':unittest.main()

