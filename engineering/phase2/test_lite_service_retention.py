"""Synthetic persistent CAS tests; no cloud, real model, purge or user data."""
import copy
import json
import sqlite3
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import test_lite_service_http as fixture
from lite_attest_state import SQLiteStateStore, StateUnavailable
from lite_usage_ledger import UsageConflict, digest, free_grant
from lite_service_operations import OperationExpired, ServiceOperations, RESULT_TTL
from lite_service_retention import ServiceResultRetention, RetentionBlocked


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.h=fixture.ServiceHTTPTests();self.h.setUp();self.addCleanup(self.h.doCleanups)
        self.ops=self.h.ops;self.op=f'{1:032x}';self.subject=self.h.subject
        self.audit=SQLiteStateStore(self.h.root/'retention-audit.sqlite')
        self.job=ServiceResultRetention(self.ops,audit_store=self.audit,scope='b'*64)

    def prepare(self):return self.ops.prepare(self.h.owner,self.op,self.h.image_hash)
    def complete(self):self.prepare();return self.ops.run(self.h.owner,self.op,self.h.f.jpeg)
    def expire(self):self.h.e.now+=RESULT_TTL+1
    def plan(self):return self.job.plan(self.subject,self.op)
    def row(self):return self.ops.load(self.subject,self.op)
    def ticket(self):return self.ops.ticket(self.subject,self.row()[1])
    def receipt(self):return self.ops.ledger.receipt(self.ticket())
    def entries(self,store):
        with sqlite3.connect(store.path) as c:return c.execute('select key_id,revision,data from attest_state order by key_id').fetchall()

    def test_plan_is_read_only_and_pins_exact_source(self):
        self.complete();self.expire();before=[self.entries(s) for s in (self.ops.store,self.ops.ledger.store,self.audit)]
        plan=self.plan();self.assertEqual(plan['expected_revision'],self.row()[0]);self.assertEqual(plan['before_sha256'],digest(self.row()[1]))
        self.assertEqual(before,[self.entries(s) for s in (self.ops.store,self.ops.ledger.store,self.audit)])

    def test_complete_result_compacts_payload_preserving_receipt_usage_and_original_hashes(self):
        self.complete();before=copy.deepcopy(self.row()[1]);receipt=self.receipt();ledger=self.entries(self.ops.ledger.store)
        self.expire();plan=self.plan();done=self.job.execute(plan);row=self.row()[1]
        self.assertEqual(row['v'],2);self.assertIsNone(row['result']);self.assertEqual(row['retention']['receipt'],receipt)
        for k in ('operation','day','image_sha256','model','digest','worker','result_sha256'):self.assertEqual(row[k],before[k])
        self.assertEqual(self.entries(self.ops.ledger.store),ledger);self.assertEqual(done['receipt'],receipt)
        self.assertEqual(len(self.entries(self.audit)),2);self.assertEqual(self.h.model_calls,1)
        self.assertNotIn(b'wound_polygons',b''.join(x[2] for x in self.entries(self.audit)))

    def test_same_plan_replay_is_readback_without_new_writes(self):
        self.complete();self.expire();p=self.plan();first=self.job.execute(p)
        before=[self.entries(s) for s in (self.ops.store,self.ops.ledger.store,self.audit)]
        self.assertEqual(self.job.execute(p),first)
        self.assertEqual(before,[self.entries(s) for s in (self.ops.store,self.ops.ledger.store,self.audit)])

    def test_old_operation_cannot_be_recreated_after_compaction_on_new_day(self):
        self.complete();self.expire();self.job.execute(self.plan());self.h.initialize_day()
        with self.assertRaises(OperationExpired):self.prepare()
        with self.assertRaises(OperationExpired):self.ops.run(self.h.owner,self.op,self.h.f.jpeg)
        with self.assertRaises(OperationExpired):self.ops.status(self.h.owner,self.op)
        self.assertEqual(self.h.model_calls,1);self.assertEqual(self.h.quota()['attempts'],0)

    def test_signed_status_after_compaction_is_410_not_unknown(self):
        self.h.complete();self.expire();self.job.execute(self.plan())
        response=self.h.json_request('status');self.assertEqual(response.status_code,410,response.json)
        self.assertEqual(response.json['error'],'lite_operation_expired');self.assertTrue(response.json['keep_local'])

    def test_empty_result_compacts_without_counting_success(self):
        self.h.inference.model=lambda _:None;self.complete();r=self.receipt();self.assertEqual(r['outcome'],'empty')
        self.expire();self.job.execute(self.plan());self.assertEqual(self.receipt(),r)

    def test_saved_unsettled_result_is_settled_before_compaction_without_model(self):
        self.prepare()
        with patch.object(self.ops.ledger,'settle',side_effect=StateUnavailable('lost')):
            with self.assertRaises(StateUnavailable):self.ops.run(self.h.owner,self.op,self.h.f.jpeg)
        self.expire();p=self.plan();self.assertEqual(p['action'],'settle_and_compact');self.job.execute(p)
        self.assertEqual(self.receipt()['outcome'],'succeeded');self.assertEqual(self.h.model_calls,1)

    def test_unstarted_reservation_cancelled_but_attempt_and_ticket_retained(self):
        self.prepare();before=self.row()[1];self.expire();p=self.plan();self.job.execute(p)
        r=self.receipt();self.assertEqual(r['outcome'],'cancelled');self.assertEqual(r['request_digest'],before['digest'])
        _,ledger=self.ops.ledger._load(self.subject,before['day'])
        self.assertEqual(self.ops.ledger._counts(ledger),(0,0,1));self.assertEqual(self.h.model_calls,0)

    def test_already_cancelled_preflight_preserves_cancellation(self):
        self.prepare();self.ops.ledger.cancel_unstarted(self.ticket(),'c'*64);r=self.receipt();self.expire()
        p=self.plan();self.assertEqual(p['action'],'compact_cancelled');self.job.execute(p)
        self.assertEqual(self.receipt(),r)

    def test_running_without_result_blocks_without_mutation(self):
        self.prepare();self.ops.ledger.claim(self.ticket(),self.row()[1]['worker']);self.expire()
        before=self.row()
        with self.assertRaises(RetentionBlocked):self.plan()
        self.assertEqual(self.row(),before);self.assertEqual(self.entries(self.audit),[])

    def test_missing_quota_mapping_blocks_without_guessing_zero(self):
        real=self.ops.ledger.reserve
        with patch.object(self.ops.ledger,'reserve',side_effect=StateUnavailable('no response')):
            with self.assertRaises(StateUnavailable):self.prepare()
        self.expire()
        with self.assertRaises(RetentionBlocked):self.plan()
        self.assertEqual(self.entries(self.audit),[]);self.assertEqual(self.row()[1]['v'],1)

    def test_missing_operation_is_not_created(self):
        with self.assertRaises(RetentionBlocked):self.plan()
        self.assertIsNone(self.row());self.assertEqual(self.entries(self.audit),[])

    def test_unexpired_result_is_preserved(self):
        self.complete();before=self.row()
        with self.assertRaises(RetentionBlocked):self.plan()
        self.assertEqual(self.row(),before)

    def test_plan_deadline_and_wrong_scope_block_before_writes(self):
        self.complete();self.expire();p=self.plan();before=self.row()
        wrong=copy.deepcopy(p);wrong['scope']='e'*64
        with self.assertRaises(RetentionBlocked):self.job.execute(wrong)
        self.h.e.now=p['deadline']
        with self.assertRaises(RetentionBlocked):self.job.execute(p)
        self.assertEqual(self.row(),before);self.assertEqual(self.entries(self.audit),[])

    def test_stale_generation_no_rebase_or_overwrite(self):
        self.complete();self.expire();p=self.plan();rev,v=self.row();self.ops.store.compare_exchange(self.ops.key(self.subject,self.op),rev,v)
        with self.assertRaises(RetentionBlocked):self.job.execute(p)
        self.assertEqual(self.row()[1]['v'],1);self.assertEqual(self.entries(self.audit),[])

    def test_changed_image_and_changed_receipt_plan_are_rejected(self):
        self.complete();self.expire();p=self.plan()
        for field,value in [('before_sha256','e'*64),('receipt',dict(p['receipt'],grant_sha256='e'*64))]:
            with self.subTest(field=field):
                wrong=copy.deepcopy(p);wrong[field]=value
                with self.assertRaises(RetentionBlocked):self.job.execute(wrong)
        self.assertEqual(self.row()[1]['v'],1);self.assertEqual(self.entries(self.audit),[])

    def test_claim_between_plan_and_execute_cannot_be_cancelled(self):
        self.prepare();self.expire();p=self.plan();self.ops.ledger.claim(self.ticket(),self.row()[1]['worker'])
        with self.assertRaises(RetentionBlocked):self.job.execute(p)
        self.assertEqual(self.receipt()['outcome'],'running');self.assertEqual(self.row()[1]['v'],1)

    def test_claim_wins_inside_cancel_race_preserves_work(self):
        self.prepare();self.expire();p=self.plan();real=self.ops.ledger.cancel_unstarted
        def raced(ticket,proof):
            self.ops.ledger.claim(ticket,self.row()[1]['worker']);return real(ticket,proof)
        with patch.object(self.ops.ledger,'cancel_unstarted',side_effect=raced):
            with self.assertRaises(UsageConflict):self.job.execute(p)
        self.assertEqual(self.receipt()['outcome'],'running');self.assertEqual(self.row()[1]['v'],1)
        self.assertEqual(len(self.entries(self.audit)),1)

    def test_cancel_wins_inside_claim_race_no_executor(self):
        self.prepare();self.expire();p=self.plan();ticket=self.ticket();worker=self.row()[1]['worker']
        self.job.execute(p);self.assertFalse(self.ops.ledger.claim(ticket,worker));self.assertEqual(self.h.model_calls,0)

    def test_lost_intent_write_stops_then_same_plan_reads_back(self):
        self.complete();self.expire();p=self.plan();real=self.audit.compare_exchange
        def lost(*args):real(*args);raise StateUnavailable('lost audit ack')
        with patch.object(self.audit,'compare_exchange',side_effect=lost):
            with self.assertRaises(StateUnavailable):self.job.execute(p)
        self.assertEqual(self.row()[1]['v'],1);self.job.execute(p);self.assertEqual(self.row()[1]['v'],2)

    def test_lost_cancel_ack_preserves_attempt_and_resume_finishes(self):
        self.prepare();self.expire();p=self.plan();real=self.ops.ledger.cancel_unstarted
        def lost(*args):real(*args);raise StateUnavailable('lost cancel ack')
        with patch.object(self.ops.ledger,'cancel_unstarted',side_effect=lost):
            with self.assertRaises(StateUnavailable):self.job.execute(p)
        self.assertEqual(self.row()[1]['v'],1);self.assertEqual(self.receipt()['outcome'],'cancelled')
        self.job.execute(p);self.assertEqual(self.h.model_calls,0)

    def test_lost_settle_ack_is_reconciled_without_second_model(self):
        self.prepare()
        with patch.object(self.ops.ledger,'settle',side_effect=StateUnavailable('hold')):
            with self.assertRaises(StateUnavailable):self.ops.run(self.h.owner,self.op,self.h.f.jpeg)
        self.expire();p=self.plan();real=self.ops.ledger.settle
        def lost(*args):real(*args);raise StateUnavailable('lost settle ack')
        with patch.object(self.ops.ledger,'settle',side_effect=lost):
            with self.assertRaises(StateUnavailable):self.job.execute(p)
        self.job.execute(p);self.assertEqual(self.h.model_calls,1)

    def test_lost_compaction_ack_reads_terminal_and_completes_audit(self):
        self.complete();self.expire();p=self.plan();real=self.ops.store.compare_exchange
        def lost(*args):real(*args);raise StateUnavailable('lost replacement ack')
        with patch.object(self.ops.store,'compare_exchange',side_effect=lost):
            with self.assertRaises(StateUnavailable):self.job.execute(p)
        self.assertEqual(self.row()[1]['v'],2);self.assertEqual(len(self.entries(self.audit)),1)
        self.job.execute(p);self.assertEqual(len(self.entries(self.audit)),2)

    def test_lost_completion_audit_ack_is_idempotent(self):
        self.complete();self.expire();p=self.plan();real=self.audit.compare_exchange
        def lost(key,rev,value):
            answer=real(key,rev,value)
            if value['kind'].endswith('complete'):raise StateUnavailable('lost completion ack')
            return answer
        with patch.object(self.audit,'compare_exchange',side_effect=lost):
            with self.assertRaises(StateUnavailable):self.job.execute(p)
        before=self.entries(self.audit);self.job.execute(p);self.assertEqual(before,self.entries(self.audit))

    def test_conflicting_audit_is_not_overwritten(self):
        self.complete();self.expire();p=self.plan();key=self.job.audit_key(p,'intent')
        self.audit.compare_exchange(key,0,{'unrelated':'keep'})
        with self.assertRaises(StateUnavailable):self.job.execute(p)
        self.assertEqual(self.row()[1]['v'],1);self.assertEqual(self.audit.load(key)[1],{'unrelated':'keep'})

    def test_same_plan_concurrent_executors_keep_one_terminal_and_two_audits(self):
        self.complete();self.expire();p=self.plan();start=threading.Barrier(6)
        def execute(_):start.wait(5);return self.job.execute(p)
        with ThreadPoolExecutor(max_workers=6) as pool:values=list(pool.map(execute,range(6)))
        self.assertTrue(all(x==values[0] for x in values));self.assertEqual(len(self.entries(self.audit)),2)
        self.assertEqual(self.h.model_calls,1)

    def test_different_plan_cannot_recompact_or_resurrect_payload(self):
        self.complete();self.expire();p=self.plan();self.h.e.now+=1;other=self.plan()
        self.job.execute(p)
        with self.assertRaises(RetentionBlocked):self.job.execute(other)
        self.assertEqual(len(self.entries(self.audit)),2)

    def test_result_cas_rejected_does_not_rebase(self):
        self.complete();self.expire();p=self.plan()
        with patch.object(self.ops.store,'compare_exchange',return_value=False):
            with self.assertRaises(StateUnavailable):self.job.execute(p)
        self.assertEqual(self.row()[1]['v'],1);self.assertEqual(len(self.entries(self.audit)),1)

    def test_terminal_receipt_tamper_fails_closed(self):
        self.complete();self.expire();p=self.plan();self.job.execute(p);rev,v=self.row()
        v['retention']['receipt']['result_sha256']='f'*64
        self.ops.store.compare_exchange(self.ops.key(self.subject,self.op),rev,v)
        with self.assertRaises(StateUnavailable):self.row()

    def test_restart_reads_terminal_without_quota_reset(self):
        self.complete();self.expire();p=self.plan();self.job.execute(p)
        other=ServiceOperations(ledger=self.ops.ledger,accounts=self.ops.accounts,
            store=SQLiteStateStore(self.h.root/'results.sqlite'),inference=self.h.inference,
            model_version='a'*64,clock=lambda:self.h.e.now)
        recovered=ServiceResultRetention(other,audit_store=SQLiteStateStore(self.h.root/'retention-audit.sqlite'),scope='b'*64)
        self.assertEqual(recovered.execute(p)['receipt']['outcome'],'succeeded')

    def test_research_privacy_and_old_storage_never_touched(self):
        self.complete();self.expire();p=self.plan();before=self.entries(self.h.privacy.store)
        with patch.object(self.h.privacy,'withdraw',side_effect=AssertionError('not a research cleanup')), \
             patch.object(self.ops.accounts,'resolve',side_effect=AssertionError('no account change')):
            self.job.execute(p)
        self.assertEqual(self.entries(self.h.privacy.store),before)
        self.assertEqual(list(__import__('pathlib').Path(self.h.f.tmp.name).rglob('*')),[])

    def test_withdrawal_interleaved_with_compaction_keeps_research_withdrawn(self):
        self.complete();self.expire();p=self.plan();real=self.ops.store.compare_exchange
        def concurrent(key,rev,value):
            self.h.privacy.withdraw(self.h.owner);return real(key,rev,value)
        with patch.object(self.ops.store,'compare_exchange',side_effect=concurrent):self.job.execute(p)
        self.assertTrue(self.h.privacy.is_withdrawn(self.h.owner));self.assertEqual(self.receipt()['outcome'],'succeeded')

    def test_deadline_crossed_during_audit_stops_before_ledger_and_payload(self):
        self.prepare();self.expire();p=self.plan();real=self.audit.compare_exchange
        def delayed(*args):
            answer=real(*args);self.h.e.now=p['deadline'];return answer
        with patch.object(self.audit,'compare_exchange',side_effect=delayed):
            with self.assertRaises(RetentionBlocked):self.job.execute(p)
        self.assertEqual(self.row()[1]['v'],1);self.assertEqual(self.receipt()['outcome'],'reserved')

    def test_malformed_plan_never_writes(self):
        self.complete();self.expire();p=self.plan()
        for field,value in [('v',True),('expected_revision',False),('deadline',True),('receipt',{}),('operation','../other')]:
            with self.subTest(field=field):
                wrong=copy.deepcopy(p);wrong[field]=value
                with self.assertRaises((UsageConflict,StateUnavailable)):self.job.execute(wrong)
        self.assertEqual(self.entries(self.audit),[]);self.assertEqual(self.row()[1]['v'],1)

    def test_corrupt_terminal_schema_and_clock_are_not_read_as_complete(self):
        self.complete();self.expire();p=self.plan();self.job.execute(p);_,base=self.row()
        for mutation in ('has_payload','before_expiry','boolean_limit','changed_day','cancel_with_hash'):
            v=copy.deepcopy(base)
            if mutation=='has_payload':v['result']={'secret':'do not accept'}
            if mutation=='before_expiry':v['retention']['compacted_at']=v['expires']-1
            if mutation=='boolean_limit':v['retention']['receipt']['admitted_limit']=True
            if mutation=='changed_day':v['retention']['receipt']['day']+=1
            if mutation=='cancel_with_hash':v['retention']['receipt']['outcome']='cancelled'
            with self.subTest(mutation=mutation), patch.object(self.ops.store,'load',return_value=(100,v)):
                with self.assertRaises(StateUnavailable):self.row()

    def test_terminal_without_intent_does_not_claim_audit_complete(self):
        self.complete();self.expire();p=self.plan();self.job.execute(p)
        missing=ServiceResultRetention(self.ops,audit_store=SQLiteStateStore(self.h.root/'missing-audit.sqlite'),scope='b'*64)
        with self.assertRaises(StateUnavailable):missing.execute(p)

    def test_real_parallel_claim_cancel_has_one_winner(self):
        self.prepare();self.expire();p=self.plan();ticket=self.ticket();worker=self.row()[1]['worker'];barrier=threading.Barrier(2)
        def claim():barrier.wait(5);return self.ops.ledger.claim(ticket,worker)
        def compact():
            barrier.wait(5)
            try:self.job.execute(p);return True
            except UsageConflict:return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            a=pool.submit(claim);b=pool.submit(compact);claimed,compacted=a.result(5),b.result(5)
        self.assertNotEqual(claimed,compacted)
        self.assertEqual(self.receipt()['outcome'],'running' if claimed else 'cancelled')
        self.assertEqual(self.row()[1]['v'],1 if claimed else 2)

    def test_actual_gcs_adapter_compacts_current_only_and_preserves_old_generations(self):
        from lite_attest_state import GCSStateStore
        from test_lite_attest_state import FakeBucket
        result,audit=FakeBucket(),FakeBucket()
        self.ops.store=GCSStateStore(result,prefix='synthetic-retention/results')
        self.audit=GCSStateStore(audit,prefix='synthetic-retention/audit')
        self.job=ServiceResultRetention(self.ops,audit_store=self.audit,scope='b'*64)
        self.complete();before=self.row();key=self.ops.store._name(self.ops.key(self.subject,self.op))
        original=result.versions[(key,before[0])];self.expire();self.job.execute(self.plan())
        self.assertIsNone(json.loads(result.live[key][1])['result'])
        self.assertEqual(result.versions[(key,before[0])],original)
        self.assertIsNotNone(json.loads(original)['result'])
        uploads=[c for bucket in (result,audit) for c in bucket.calls if c[0]=='upload']
        self.assertTrue(all('if_generation_match' in c[2] for c in uploads))
        self.assertEqual(len(audit.live),2)


if __name__=='__main__':unittest.main()
