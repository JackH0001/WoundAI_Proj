"""Synthetic JPEG/mask, signed Apple identity and real SQLite CAS race tests."""
import copy
import hashlib
import io
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from PIL import Image
import numpy as np
from lite_account_lifecycle import AccountLifecycle, AccountClosing
from lite_account_identity import IdentityRejected
from lite_attest_state import SQLiteStateStore, StateUnavailable
from lite_service_operations import ServiceOperations, RasterInference, OperationExpired
from lite_service_disposition import ServiceResultDisposition, validate_seal
from lite_service_retention import ServiceResultRetention, RetentionBlocked
from lite_subscription_executor import key
from lite_usage_ledger import digest, UsageLimit
from test_lite_account_lifecycle import Fixture
from test_lite_account_deletion import coordinator
from test_lite_subscription_grace import rows
import test_lite_native_purchase as native


class ServiceDispositionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): native.legacy.LinksTests.setUpClass()
    def setUp(self):
        self.f=Fixture(self,service_catalog=True);self.f.login()
        self.life=self.f.lifecycle;self.person=self.f.person;self.subject='apple-'+self.person
        self.root=self.f.f.h.f.root;self.ledger=self.f.f.h.f.ledger;self.calls=0;self.op='1'*32
        out=io.BytesIO();Image.new('RGB',(32,32),(180,30,30)).save(out,format='JPEG')
        self.raw=out.getvalue();self.image_hash=hashlib.sha256(self.raw).hexdigest()
        def model(rgb):
            self.calls+=1;mask=np.zeros(rgb.shape[:2],dtype=np.uint8);mask[5:25,5:25]=1;return mask
        self.ops=ServiceOperations(ledger=self.ledger,accounts=self.f.b.registry,
            store=self.store('service-results'),inference=RasterInference(model,lambda _: (False,None)),
            model_version='a'*64,clock=self.f.f.now)
        self.ledger.initialize_day(self.subject,self.f.f.now()//86400,
            baseline_successes=0,baseline_attempts=0,manifest_sha256='b'*64)
        self.d=self.worker()
    def store(self,name): return SQLiteStateStore(self.root/(name+'.sqlite'))
    def worker(self): return ServiceResultDisposition(operations=self.ops,lifecycle=self.life,
        store=self.store('result-disposition'),audit_store=self.store('result-disposition-audit'))
    def prepare(self,op=None,device='device-one'):return self.ops.prepare(device,op or self.op,self.image_hash)
    def complete(self,op=None,device='device-one'):
        self.prepare(op,device);return self.ops.run(device,op or self.op,self.raw)
    def close(self):return self.f.close()
    def advance(self,**kw):return self.d.advance(self.person,**kw)
    def row(self,op=None):return self.ops.load(self.subject,op or self.op)
    def unchanged(self):return rows(self.ledger.store),rows(self.f.b.registry.store),rows(self.f.b.store)

    def test_current_mask_removed_receipts_and_identity_unchanged(self):
        done=self.complete();before=self.row();ledger=self.unchanged();ticket=self.ops.ticket(self.subject,before[1])
        self.close();r=self.advance();seal=self.row()[1]
        self.assertEqual(seal['v'],3);self.assertNotIn('result',seal);self.assertNotIn('wound_polygons',repr(seal))
        self.assertEqual(seal['prior'],dict(revision=before[0],sha256=digest(before[1])))
        self.assertEqual(self.unchanged(),ledger);self.assertEqual(self.ledger.receipt(ticket),done['receipt'])
        self.assertEqual(r['service_result_payloads'],'current_sealed');self.assertFalse(r['account_deleted'])
        self.assertFalse(r['historical_versions_erased']);self.assertFalse(r['physical_backups_erased'])
        self.assertEqual(self.calls,1)

    def test_active_account_cannot_be_cleaned(self):
        self.complete();before=rows(self.ops.store)
        with self.assertRaises(IdentityRejected):self.advance()
        self.assertEqual(rows(self.ops.store),before);self.assertEqual(rows(self.d.audit),[])

    def test_closed_without_operations_completes_empty_lane_only(self):
        self.close();r=self.advance();self.assertEqual((r['sealed'],r['total']),(0,0));self.assertFalse(r['account_deleted'])

    def test_missing_slot_is_permanently_sealed_before_late_create(self):
        self.life.services.reserve(self.person,self.op);self.close();r=self.advance()
        self.assertEqual(r['sealed'],1);self.assertEqual(self.row()[1]['prior'],dict(revision=0,sha256=None))
        self.assertFalse(self.ops.store.compare_exchange(self.ops.key(self.subject,self.op),0,{'late':'create'}))
        self.assertEqual(self.row()[1]['v'],3)

    def test_close_between_index_and_create_prevents_late_operation_write(self):
        original=self.ops.store.compare_exchange
        def late(k,rev,value):
            if value.get('v')==1:
                self.close();self.advance()
            return original(k,rev,value)
        with patch.object(self.ops.store,'compare_exchange',side_effect=late),self.assertRaises(OperationExpired):self.prepare()
        self.assertEqual(self.row()[1]['prior']['revision'],0);self.assertEqual(self.calls,0)
        self.assertEqual(self.life._read(self.person)[1]['service_count'],1)

    def test_close_and_seal_at_result_commit_defeats_stale_cas(self):
        self.prepare();original=self.ops.store.compare_exchange
        def late(k,rev,value):
            if value.get('result') is not None:
                self.close();self.advance()
            return original(k,rev,value)
        with patch.object(self.ops.store,'compare_exchange',side_effect=late),self.assertRaises(OperationExpired):
            self.ops.run('device-one',self.op,self.raw)
        self.assertEqual(self.row()[1]['v'],3);self.assertEqual(self.calls,1)
        self.assertFalse(self.advance()['account_deleted'])

    def test_close_during_model_does_not_persist_returned_mask(self):
        self.prepare();real=self.ops.inference.infer
        def infer(rgb):
            result=real(rgb);self.close();self.advance();return result
        with patch.object(self.ops.inference,'infer',side_effect=infer),self.assertRaises(AccountClosing):
            self.ops.run('device-one',self.op,self.raw)
        self.assertEqual(self.calls,1);self.assertEqual(self.row()[1]['v'],3)

    def test_close_during_cached_status_never_returns_mask(self):
        self.complete();real=self.ledger.active_receipt
        def receipt(*args):
            r=real(*args);self.close();self.advance();return r
        with patch.object(self.ledger,'active_receipt',side_effect=receipt),self.assertRaises(AccountClosing):
            self.ops.status('device-one',self.op)
        self.assertEqual(self.calls,1)

    def test_same_account_two_devices_share_one_inventory(self):
        self.f.login('device-two');self.complete();self.complete('2'*32,'device-two');self.close()
        self.assertEqual(self.advance()['sealed'],2)
        for device in ('device-one','device-two'):
            with self.assertRaises(AccountClosing):self.ops.status(device,self.op)

    def test_other_account_and_result_are_not_changed(self):
        self.complete();self.f.login('other','apple-human-2');other=self.f.person
        other_subject='apple-'+other;self.ledger.initialize_day(other_subject,self.f.f.now()//86400,
            baseline_successes=0,baseline_attempts=0,manifest_sha256='b'*64)
        result=self.complete('2'*32,'other');before=self.ops.load(other_subject,'2'*32)
        self.close();self.advance()
        self.assertEqual(self.ops.load(other_subject,'2'*32),before)
        self.assertEqual(self.ops.status('other','2'*32),result)

    def test_repeated_prepare_does_not_duplicate_catalog(self):
        self.prepare();self.prepare();self.assertEqual(self.life._read(self.person)[1]['service_count'],1)

    def test_catalog_commit_response_loss_recovers_without_duplicate(self):
        real=self.life.store.compare_exchange
        def lost(k,rev,value):
            r=real(k,rev,value)
            if value.get('service_count')==1:raise StateUnavailable('lost root acknowledgment')
            return r
        with patch.object(self.life.store,'compare_exchange',side_effect=lost):self.prepare()
        self.assertEqual(self.life._read(self.person)[1]['service_count'],1)

    def test_close_wins_catalog_cas_prevents_prepare(self):
        real=self.life.store.compare_exchange;once=[False]
        def raced(k,rev,value):
            if value.get('phase')=='active' and value.get('service_count')==1 and not once[0]:
                once[0]=True;self.close()
            return real(k,rev,value)
        with patch.object(self.life.store,'compare_exchange',side_effect=raced),self.assertRaises(AccountClosing):self.prepare()
        self.assertIsNone(self.row());self.assertEqual(self.advance()['total'],0)

    def test_admission_audit_outage_prevents_result_create_and_still_inventoried(self):
        with patch.object(self.life.audit,'compare_exchange',side_effect=StateUnavailable('audit offline')):
            with self.assertRaises(StateUnavailable):self.prepare()
        self.assertIsNone(self.row());self.close();self.assertEqual(self.advance()['sealed'],1)

    def test_lost_seal_write_reply_reads_back_and_no_model_or_quota_replay(self):
        self.complete();self.close();real=self.ops.store.compare_exchange;quota=rows(self.ledger.store)
        def lost(*args): real(*args);raise StateUnavailable('lost seal acknowledgment')
        with patch.object(self.ops.store,'compare_exchange',side_effect=lost):self.assertEqual(self.advance()['sealed'],1)
        self.assertEqual(rows(self.ledger.store),quota);self.assertEqual(self.calls,1)

    def test_failed_seal_write_does_not_claim_complete(self):
        self.complete();self.close()
        with patch.object(self.ops.store,'compare_exchange',side_effect=StateUnavailable('write down')):
            with self.assertRaises(StateUnavailable):self.advance()
        self.assertEqual(self.row()[1]['v'],1)
        self.assertEqual(self.worker().advance(self.person)['sealed'],1)

    def test_intent_audit_outage_preserves_payload(self):
        self.complete();self.close();before=self.row()
        with patch.object(self.d.audit,'compare_exchange',side_effect=StateUnavailable('audit down')):
            with self.assertRaises(StateUnavailable):self.advance()
        self.assertEqual(self.row(),before)

    def test_receipt_outage_keeps_seal_and_resumes_without_duplication(self):
        self.complete();self.close();real=self.d.audit.compare_exchange
        def fail(k,rev,value):
            if 'seal_sha256' in value:raise StateUnavailable('receipt down')
            return real(k,rev,value)
        with patch.object(self.d.audit,'compare_exchange',side_effect=fail),self.assertRaises(StateUnavailable):self.advance()
        self.assertEqual(self.row()[1]['v'],3)
        self.assertEqual(self.worker().advance(self.person)['sealed'],1)

    def test_lost_checkpoint_reply_reconciles_on_restart(self):
        self.complete();self.close();real=self.d.store.compare_exchange
        def lost(k,rev,value):
            r=real(k,rev,value)
            if value.get('processed',0)>0:raise StateUnavailable('lost progress')
            return r
        with patch.object(self.d.store,'compare_exchange',side_effect=lost),self.assertRaises(StateUnavailable):self.advance()
        self.assertEqual(self.worker().advance(self.person)['sealed'],1)

    def test_replay_is_no_new_state_or_audit(self):
        self.complete();self.close();r=self.advance();before=[rows(s) for s in (self.ops.store,self.d.store,self.d.audit)]
        self.assertEqual(self.worker().advance(self.person),r)
        self.assertEqual([rows(s) for s in (self.ops.store,self.d.store,self.d.audit)],before)

    def test_two_workers_converge_without_double_count_or_reset(self):
        for i in range(15):self.life.services.reserve(self.person,f'{i+100:032x}')
        self.close()
        with ThreadPoolExecutor(2) as pool:
            results=list(pool.map(lambda _:self.worker().advance(self.person,limit=10),range(2)))
        r=self.advance(limit=10);self.assertEqual(r['sealed'],15)
        self.assertTrue(all(x['sealed']<=15 for x in results));self.assertEqual(self.calls,0)

    def test_more_than_one_hundred_operations_use_bounded_pages(self):
        for i in range(125):self.life.services.reserve(self.person,f'{i+100:032x}')
        self.close();a=self.advance(limit=100);self.assertEqual((a['sealed'],a['total']),(100,125))
        b=self.worker().advance(self.person,limit=100);self.assertEqual(b['sealed'],125)
        self.assertEqual(len(rows(self.ops.store)),125)

    def test_corrupt_result_never_silently_overwritten(self):
        self.prepare();rev,v=self.row();self.ops.store.compare_exchange(self.ops.key(self.subject,self.op),rev,dict(v,extra='bad'))
        self.close()
        with self.assertRaises(StateUnavailable):self.advance()
        self.assertIn('extra',self.ops.store.load(self.ops.key(self.subject,self.op))[1])

    def test_corrupt_index_never_claims_empty(self):
        self.prepare();self.close();root=self.life._read(self.person)[1];k=key('account-service-node',root['service_head'])
        rev,v=self.life.store.load(k);self.life.store.compare_exchange(k,rev,dict(v,position=2))
        with self.assertRaises(StateUnavailable):self.advance()
        self.assertEqual(self.row()[1]['v'],1)

    def test_corrupt_checkpoint_never_skips_unsealed_operations(self):
        self.prepare();self.close();plan=self.d.plan(self.person);k,(rev,j)=self.d._job(plan)
        self.d.store.compare_exchange(k,rev,dict(j,processed=1))
        with self.assertRaises(StateUnavailable):self.advance()
        self.assertEqual(self.row()[1]['v'],1)

    def test_marker_cannot_be_relabelled_for_other_account_or_operation(self):
        self.complete();self.close();self.advance();seal=self.row()[1]
        for subject,op in [('apple-'+'e'*64,self.op),(self.subject,'2'*32)]:
            with self.assertRaises(StateUnavailable):validate_seal(seal,subject,op)

    def test_legacy_inventory_cannot_enable_catalog_or_downgrade_implicitly(self):
        args=dict(store=self.life.store,audit_store=self.life.audit,binding=self.life.binding,clock=self.life.clock)
        with self.assertRaises(StateUnavailable):AccountLifecycle(**args)
        old=AccountLifecycle(**dict(args,store=self.store('legacy'),audit_store=self.store('legacy-audit')))
        with self.assertRaises(StateUnavailable):AccountLifecycle(**dict(args,store=old.store,audit_store=old.audit),service_catalog=True)

    def test_shared_worker_stores_rejected(self):
        with self.assertRaises(ValueError):ServiceResultDisposition(operations=self.ops,lifecycle=self.life,
            store=self.ops.store,audit_store=self.d.audit)

    def test_bad_limit_makes_no_mutations(self):
        self.complete();self.close();before=rows(self.ops.store)
        for value in (0,101,True,1.5):
            with self.assertRaises(ValueError):self.advance(limit=value)
        self.assertEqual(rows(self.ops.store),before);self.assertEqual(rows(self.d.audit),[])

    def test_old_reader_and_new_reader_cannot_rerun_sealed_operation(self):
        self.complete();self.close();self.advance()
        # Even bypassing account checks, the permanent operation slot is terminal.
        with self.assertRaises(OperationExpired):self.ops._require(self.subject,self.op)
        self.f.f.h.a.now+=86401
        with self.assertRaises(OperationExpired):self.ops._require(self.subject,self.op)
        self.assertEqual(self.calls,1)

    def test_reference_inventory_digest_binds_both_grants_and_operations(self):
        self.prepare();root=self.life._read(self.person)[1];old=digest([self.life.scope,self.person,root['head'],root['count']])
        receipt=self.close();self.assertNotEqual(receipt['inventory_sha256'],old)
        self.assertEqual(receipt['inventory_sha256'],self.life.inventory_digest(root))

    def test_grant_revoke_coordinator_still_works_with_service_catalog(self):
        self.complete();d=coordinator(self.f);d.begin('device-one','d'*32)
        self.assertEqual(d.advance(self.person)['apple_revocation'],'confirmed')
        self.assertEqual(self.advance()['sealed'],1)
        self.assertFalse(d.status('device-one','d'*32)['account_deleted'])

    def test_compacted_operation_can_be_sealed_without_ledger_change(self):
        self.complete();self.f.f.h.a.now+=86401
        retention=ServiceResultRetention(self.ops,audit_store=self.store('retention-audit'),scope='b'*64)
        retention.execute(retention.plan(self.subject,self.op));self.assertEqual(self.row()[1]['v'],2)
        self.life.close(self.person,'device-one','d'*32);quota=rows(self.ledger.store)
        self.assertEqual(self.advance()['sealed'],1);self.assertEqual(rows(self.ledger.store),quota)

    def test_stale_retention_plan_cannot_replace_account_seal(self):
        self.complete();self.f.f.h.a.now+=86401
        retention=ServiceResultRetention(self.ops,audit_store=self.store('retention-audit'),scope='b'*64)
        plan=retention.plan(self.subject,self.op);self.life.close(self.person,'device-one','d'*32);self.advance()
        with self.assertRaises((RetentionBlocked,StateUnavailable)):retention.execute(plan)
        self.assertEqual(self.row()[1]['v'],3)

    def setup_recovery(self):
        from lite_usage_cutover import CutoverPolicy,seal_cutover
        from lite_usage_ledger import UsageLedger
        from lite_service_recovery import ServiceRecovery
        from test_lite_usage_recovery import manifest
        control=self.store('cutover');ns=self.ledger.namespace;now=self.f.f.now()
        proof=seal_cutover(control,manifest(ns,now),verify_frozen_inputs=lambda _:True)
        policy=CutoverPolicy(control,namespace=ns,expected_sha256=proof,verify_writer_fence=lambda _:True)
        self.ledger=UsageLedger(self.store('cutover-ledger'),namespace=ns,clock=self.f.f.now,cutover=policy)
        self.ops.ledger=self.ledger
        self.ops.recovery=ServiceRecovery(self.ops,self.store('crossday-recovery'))
        self.f.f.h.a.now=(now//86400+2)*86400-5;self.f.f.h.publish()

    def stranded(self):
        self.setup_recovery()
        with patch.object(self.ledger,'reserve',side_effect=StateUnavailable('old day before CAS')):
            with self.assertRaisesRegex(StateUnavailable,'old day before CAS'):self.prepare()
        self.assertIsNotNone(self.row())
        self.f.f.h.a.now+=6;self.f.f.h.publish()

    def test_crossday_successor_is_catalogued_and_removed_with_parent(self):
        self.stranded();r=self.ops.recovery.recover('device-one',self.op,self.image_hash)
        successor=r['recovery']['successor_operation'];self.complete(successor)
        self.life.close(self.person,'device-one','d'*32);self.assertEqual(self.advance()['sealed'],2)
        self.assertEqual(self.row(successor)[1]['v'],3);self.assertEqual(self.calls,1)

    def test_close_during_crossday_successor_write_seals_missing_slot(self):
        self.stranded();original=self.ops.store.compare_exchange;created=[]
        def late(k,rev,value):
            if value.get('v')==1:
                created.append(value['operation']);self.life.close(self.person,'device-one','d'*32);self.advance()
            return original(k,rev,value)
        with patch.object(self.ops.store,'compare_exchange',side_effect=late),self.assertRaises(OperationExpired):
            self.ops.recovery.recover('device-one',self.op,self.image_hash)
        self.assertEqual(len(created),1);self.assertEqual(self.row(created[0])[1]['prior']['revision'],0)
        self.assertEqual(self.calls,0);self.assertEqual(self.advance()['sealed'],2)

    def test_corrupt_seal_blocks_without_reset(self):
        self.complete();self.close();self.advance();rev,seal=self.row()
        self.ops.store.compare_exchange(self.ops.key(self.subject,self.op),rev,dict(seal,prior={'revision':0,'sha256':'e'*64}))
        with self.assertRaises(StateUnavailable):self.row()

    def test_membership_receipt_mismatch_prevents_new_work(self):
        self.prepare();k=self.life.services.member_key(self.person,self.op);rev,m=self.life.audit.load(k)
        self.life.audit.compare_exchange(k,rev,dict(m,node='e'*64))
        with self.assertRaises(StateUnavailable):self.prepare()
        self.assertEqual(self.calls,0)

    def test_grant_appended_after_service_does_not_lose_catalog(self):
        self.prepare();self.f.login('device-two');root=self.life._read(self.person)[1]
        self.assertEqual((root['count'],root['service_count']),(2,1))
        self.close();self.assertEqual(self.advance()['sealed'],1)

    def test_missing_original_audit_after_seal_cannot_be_reconstructed_as_success(self):
        self.complete();self.close();self.advance()
        self.d.audit=self.store('missing-original-audit')
        with self.assertRaises(StateUnavailable):self.advance()
        self.assertEqual(rows(self.d.audit),[])

    def test_actual_gcs_adapter_seals_current_generation_preserving_history(self):
        import json
        from lite_attest_state import GCSStateStore
        from test_lite_attest_state import FakeBucket
        bucket=FakeBucket();self.ops.store=GCSStateStore(bucket,prefix='synthetic/disposition/results')
        self.d=self.worker();self.complete();rev,before=self.row();name=self.ops.store._name(self.ops.key(self.subject,self.op))
        historical=bucket.versions[(name,rev)];self.close();r=self.advance()
        self.assertEqual(json.loads(bucket.live[name][1])['v'],3)
        self.assertEqual(bucket.versions[(name,rev)],historical)
        self.assertIn('wound_polygons',json.loads(historical)['result'])
        self.assertFalse(r['historical_versions_erased'])
        self.assertTrue(all('if_generation_match' in c[2] for c in bucket.calls if c[0]=='upload'))
        self.assertFalse(self.ops.store.compare_exchange(self.ops.key(self.subject,self.op),rev,before))
        self.assertFalse(self.ops.store.compare_exchange(self.ops.key(self.subject,self.op),0,before))

    def test_quota_denied_prepare_does_not_append_catalog_slots(self):
        self.prepare();self.prepare('2'*32)
        with self.assertRaises(UsageLimit):self.prepare('3'*32)
        self.assertEqual(self.life._read(self.person)[1]['service_count'],2)

    def test_unindexed_existing_operation_is_not_silently_adopted(self):
        self.prepare();_,v=self.row();other=dict(v,operation='2'*32)
        self.ops.store.compare_exchange(self.ops.key(self.subject,'2'*32),0,other)
        with self.assertRaises(StateUnavailable):self.prepare('2'*32)
        self.assertEqual(self.life._read(self.person)[1]['service_count'],1)

    def test_aliased_gcs_store_rejected(self):
        from lite_attest_state import GCSStateStore
        from test_lite_attest_state import FakeBucket
        bucket=FakeBucket();self.ops.store=GCSStateStore(bucket,prefix='synthetic/result')
        with self.assertRaises(ValueError):ServiceResultDisposition(operations=self.ops,lifecycle=self.life,
            store=GCSStateStore(bucket,prefix='synthetic/result'),audit_store=self.d.audit)


class ServiceDispositionHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):native.legacy.LinksTests.setUpClass()
    def setUp(self):
        from test_lite_account_lifecycle import ClosureHTTPTests
        self.f=ClosureHTTPTests();self.f.service_catalog=True;self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.h=self.f.h.h;self.person=self.f.account['subject'][6:]
        self.d=ServiceResultDisposition(operations=self.h.ops,lifecycle=self.f.lifecycle,
            store=SQLiteStateStore(self.h.root/'disposition.sqlite'),audit_store=SQLiteStateStore(self.h.root/'disposition-audit.sqlite'))
    def test_signed_complete_close_seal_and_all_retries_blocked(self):
        self.assertEqual(self.h.complete(77).json['state'],'completed')
        before=rows(self.h.ledger.store),rows(self.h.privacy.store),rows(self.h.accounts.store)
        self.f.close();r=self.d.advance(self.person);self.assertEqual(r['sealed'],1)
        for reply in (self.h.json_request('prepare',78),self.h.run_request(77),self.h.json_request('status',77)):
            self.assertEqual(reply.status_code,401)
        self.assertEqual(before,(rows(self.h.ledger.store),rows(self.h.privacy.store),rows(self.h.accounts.store)))
        self.assertEqual(self.h.model_calls,1);self.assertFalse(r['account_deleted'])
    def test_worker_not_exposed_as_public_route(self):
        reply=self.h.client.post('/api/lite/v1/service/disposition',json={'person':self.person})
        self.assertEqual(reply.status_code,404);self.assertEqual(rows(self.d.audit),[])


if __name__=='__main__':unittest.main()
