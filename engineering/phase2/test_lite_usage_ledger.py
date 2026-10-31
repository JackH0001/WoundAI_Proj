"""Actual SQLite thread/process CAS and actual GCS adapter with fake transport.

No network, original research data, cloud bucket, store purchase or production
route changes. Pending jobs intentionally remain pending after an unknown crash.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import multiprocessing
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'Backend/Flask'))
from lite_usage_ledger import *
from lite_attest_state import SQLiteStateStore, GCSStateStore, _encode, MAX_STATE
from test_lite_attest_state import FakeBucket

NOW = 1792886400 + 100
SUBJECT = 'synthetic-service-subject'
BASELINE = digest({'manifest': 'synthetic-zero-baseline'})


def ledger(path, now=NOW):
    return UsageLedger(SQLiteStateStore(path), namespace='synthetic-usage', clock=lambda: now)


def worker_process(path, operation, start, out):
    start.wait(10)
    l = ledger(path)
    try:
        ticket = l.reserve(SUBJECT, NOW//86400, operation, 'b'*64, free_grant(SUBJECT, NOW))
        out.put(l.claim(ticket, operation))
    except UsageLimit:
        out.put(False)
    except Exception as e:
        out.put(type(e).__name__)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'usage.sqlite'; self.now = NOW
        self.l = UsageLedger(SQLiteStateStore(self.path), namespace='synthetic-usage', clock=lambda:self.now)
        self.day = self.now//86400
        self.l.initialize_day(SUBJECT, self.day, baseline_successes=0, baseline_attempts=0, manifest_sha256=BASELINE)

    def grant(self, limit=2):
        return DailyGrant(SUBJECT, limit, digest(['synthetic-verification',limit]), self.now, self.now+30)

    def reserve(self, n=1, grant=None, day=None):
        return self.l.reserve(SUBJECT, self.day if day is None else day, f'{n:032x}', 'b'*64, grant or self.grant())

    def complete(self, n=1, outcome='succeeded', limit=2):
        t=self.reserve(n,self.grant(limit)); self.assertTrue(self.l.claim(t,f'{n:032x}'))
        return self.l.settle(t,f'{n:032x}',outcome,digest(['synthetic-result',n,outcome]))

    def snapshot(self, limit=2):
        return self.l.snapshot(SUBJECT,self.day,self.grant(limit))

    def test_eight_threads_admit_exactly_two_not_eight(self):
        barrier=threading.Barrier(8)
        def run(n):
            barrier.wait(10)
            try:
                t=self.reserve(n);return self.l.claim(t,f'{n:032x}')
            except UsageLimit:return False
        with ThreadPoolExecutor(max_workers=8) as pool:r=list(pool.map(run,range(1,9)))
        self.assertEqual(r.count(True),2);self.assertEqual(self.snapshot()['pending'],2)
        self.assertEqual(self.snapshot()['attempts'],2)

    def test_processes_share_durable_capacity(self):
        ctx=multiprocessing.get_context('spawn');start=ctx.Event();out=ctx.Queue()
        jobs=[ctx.Process(target=worker_process,args=(str(self.path),f'{i:032x}',start,out)) for i in range(1,5)]
        try:
            for p in jobs:p.start()
            start.set();r=[out.get(timeout=20) for _ in jobs]
            for p in jobs:p.join(10);self.assertEqual(p.exitcode,0)
            self.assertEqual(r.count(True),2);self.assertEqual(r.count(False),2)
        finally:
            for p in jobs:
                if p.is_alive():p.terminate();p.join(5)
            out.close();out.join_thread()

    def test_same_id_concurrent_execution_only_once(self):
        barrier=threading.Barrier(8)
        def run(n):
            barrier.wait(10);return self.l.claim(self.reserve(1),f'{n:032x}')
        with ThreadPoolExecutor(max_workers=8) as pool:r=list(pool.map(run,range(1,9)))
        self.assertEqual(r.count(True),1);self.assertEqual(self.snapshot()['attempts'],1)

    def test_known_success_retry_returns_identical_receipt_no_new_charge(self):
        a=self.complete();before=self.snapshot();t=self.reserve()
        self.assertFalse(self.l.claim(t,'1'*32))
        self.assertEqual(a,self.l.receipt(t));self.assertEqual(before,self.snapshot())
        self.assertEqual(a,self.l.settle(t,f'{1:032x}','succeeded',digest(['synthetic-result',1,'succeeded'])))

    def test_changed_input_same_operation_rejected(self):
        self.reserve()
        with self.assertRaises(UsageConflict):self.l.reserve(SUBJECT,self.day,f'{1:032x}','c'*64,self.grant())
        self.assertEqual(self.snapshot()['attempts'],1)

    def test_upgrade_total_ten_not_twelve_and_no_reset(self):
        self.complete(1);self.complete(2)
        with self.assertRaises(UsageLimit):self.reserve(3)
        for n in range(3,11):self.complete(n,limit=10)
        with self.assertRaises(UsageLimit):self.reserve(11,self.grant(10))
        self.assertEqual(self.snapshot(10)['used'],10)

    def test_downgrade_preserves_usage_and_old_receipts(self):
        a=self.complete(1,limit=10)
        for n in range(2,5):self.complete(n,limit=10)
        with self.assertRaises(UsageLimit):self.reserve(5)
        self.assertEqual(self.l.receipt(self.reserve(1)),a)
        self.assertEqual(self.snapshot()['used'],4)

    def test_empty_failure_release_success_capacity_but_keep_attempts(self):
        self.complete(1,'empty');self.complete(2,'failed');self.complete(3)
        self.assertEqual(self.snapshot()['used'],1);self.assertEqual(self.snapshot()['attempts'],3)
        self.assertEqual(self.snapshot()['remaining'],1)

    def test_thirty_empty_attempts_hit_independent_bound_and_fit_real_storage(self):
        for n in range(1,31):self.complete(n,'empty')
        with self.assertRaises(UsageLimit) as e:self.reserve(31)
        self.assertEqual(e.exception.reason,'attempt_quota')
        _,state=self.l._load(SUBJECT,self.day)
        self.assertLessEqual(len(_encode(state)),MAX_STATE)
        self.assertEqual(self.snapshot()['used'],0)

    def test_unknown_execution_is_not_timed_out_or_automatically_repeated(self):
        t=self.reserve();self.assertTrue(self.l.claim(t,'d'*32));self.now+=3000
        t2=self.reserve();self.assertFalse(self.l.claim(t2,'d'*32));self.assertFalse(self.l.claim(t2,'e'*32))
        self.assertEqual(self.l.receipt(t2)['outcome'],'running')
        self.assertEqual(self.snapshot()['pending'],1)

    def test_cancel_only_unstarted_preserves_attempt_audit(self):
        t=self.reserve();a=self.l.cancel_unstarted(t,'f'*64)
        self.assertEqual(a,self.l.cancel_unstarted(t,'f'*64));self.assertFalse(self.l.claim(t,'d'*32))
        self.assertEqual(self.snapshot()['remaining'],2);self.assertEqual(self.snapshot()['attempts'],1)
        t=self.reserve(2);self.l.claim(t,'d'*32)
        with self.assertRaises(UsageConflict):self.l.cancel_unstarted(t,'f'*64)

    def test_finish_after_midnight_charges_original_day(self):
        t=self.reserve();self.l.claim(t,'d'*32);self.now=(self.day+1)*86400+3
        self.l.initialize_day(SUBJECT,self.day+1,baseline_successes=0,baseline_attempts=0,manifest_sha256='c'*64)
        self.l.settle(t,'d'*32,'succeeded','e'*64)
        self.assertEqual(self.snapshot()['used'],1)
        self.assertEqual(self.l.snapshot(SUBJECT,self.day+1,self.grant())['used'],0)
        with self.assertRaises(UsageConflict):self.reserve(2)
        self.assertEqual(t,self.reserve(1))

    def test_retired_day_cannot_be_reinitialized(self):
        self.now+=86400
        with self.assertRaises(UsageConflict):self.l.initialize_day(SUBJECT,self.day,baseline_successes=0,baseline_attempts=0,manifest_sha256=BASELINE)

    def test_legacy_usage_not_erased_and_missing_baseline_blocks(self):
        with self.assertRaises(StateUnavailable):self.l.reserve('new',self.day,'a'*32,'b'*64,free_grant('new',self.now))
        with self.assertRaises(UsageConflict):self.l.initialize_day(SUBJECT,self.day,baseline_successes=2,baseline_attempts=2,manifest_sha256='d'*64)
        self.l.initialize_day('legacy',self.day,baseline_successes=5,baseline_attempts=7,manifest_sha256='d'*64)
        with self.assertRaises(UsageLimit):self.l.reserve('legacy',self.day,'a'*32,'b'*64,free_grant('legacy',self.now))

    def test_baseline_replay_does_not_clear_entries(self):
        self.complete();self.l.initialize_day(SUBJECT,self.day,baseline_successes=0,baseline_attempts=0,manifest_sha256=BASELINE)
        self.assertEqual(self.snapshot()['used'],1)

    def test_service_namespace_and_owner_isolation(self):
        self.reserve()
        other=UsageLedger(self.l.store,namespace='other',clock=lambda:self.now)
        with self.assertRaises(StateUnavailable):other.receipt(Ticket(SUBJECT,self.day,f'{1:032x}','b'*64))
        with self.assertRaises(StateUnavailable):self.l.receipt(Ticket('other',self.day,f'{1:032x}','b'*64))

    def test_no_research_epoch_or_subscription_price_reset_parameter(self):
        self.complete()
        with self.assertRaises(TypeError):self.l.reserve(SUBJECT,self.day,'a'*32,'b'*64,self.grant(),consent_epoch='new')
        self.assertEqual(self.snapshot()['used'],1)

    def test_invalid_client_grants_or_expiry_do_not_write(self):
        for grant in ({'paid':True},replace(self.grant(),limit=12),replace(self.grant(),subject='other'),
                      replace(self.grant(),valid_until=self.now),replace(self.grant(),issued_at=self.now+1),
                      replace(self.grant(),valid_until=self.now+100)):
            with self.assertRaises(UsageConflict):self.reserve(grant=grant)
        self.assertEqual(self.snapshot()['attempts'],0)

    def test_wrong_worker_and_conflicting_terminal_cannot_change_history(self):
        self.complete();t=self.reserve()
        with self.assertRaises(UsageConflict):self.l.settle(t,'d'*32,'succeeded','e'*64)
        with self.assertRaises(UsageConflict):self.l.settle(t,f'{1:032x}','failed','e'*64)
        self.assertEqual(self.snapshot()['used'],1)

    def test_clock_rollback_corruption_and_read_failure_fail_closed(self):
        self.reserve();self.now-=1
        with self.assertRaises(StateUnavailable):self.snapshot()
        self.now+=1;key=self.l.key(SUBJECT,self.day);rev,state=self.l.store.load(key)
        state['entries'][f'{1:032x}']['history']=[[self.now,'succeeded']]
        self.l.store.compare_exchange(key,rev,state)
        with self.assertRaises(StateUnavailable):self.snapshot()
        with patch.object(self.l.store,'load',side_effect=StateUnavailable('unavailable')):
            with self.assertRaises(StateUnavailable):self.reserve(2)

    def test_contention_does_not_execute_or_reset(self):
        with patch.object(self.l.store,'compare_exchange',return_value=False):
            with self.assertRaises(StateUnavailable):self.reserve()
        self.assertEqual(self.snapshot()['attempts'],0)


class GCSLedgerTests(unittest.TestCase):
    def setUp(self):
        self.bucket=FakeBucket();self.store=GCSStateStore(self.bucket,prefix='synthetic_usage/v1')
        self.l=UsageLedger(self.store,namespace='synthetic',clock=lambda:NOW);self.day=NOW//86400
        self.l.initialize_day(SUBJECT,self.day,baseline_successes=0,baseline_attempts=0,manifest_sha256=BASELINE)

    def reserve(self):return self.l.reserve(SUBJECT,self.day,'a'*32,'b'*64,free_grant(SUBJECT,NOW))

    def test_lost_reserve_response_same_ticket_no_second_slot(self):
        self.bucket.lose_response=True
        with self.assertRaises(StateUnavailable):self.reserve()
        self.bucket.lose_response=False;t=self.reserve()
        self.assertEqual(self.l.snapshot(SUBJECT,self.day,free_grant(SUBJECT,NOW))['pending'],1)
        self.assertTrue(self.l.claim(t,'c'*32))

    def test_lost_claim_response_never_authorizes_rerun(self):
        t=self.reserve();self.bucket.lose_response=True
        with self.assertRaises(StateUnavailable):self.l.claim(t,'c'*32)
        self.bucket.lose_response=False
        self.assertFalse(self.l.claim(t,'c'*32));self.assertFalse(self.l.claim(t,'d'*32))
        self.assertEqual(self.l.receipt(t)['outcome'],'running')

    def test_lost_settle_response_reads_same_success_and_preserves_versions(self):
        t=self.reserve();self.l.claim(t,'c'*32);before=dict(self.bucket.versions)
        self.bucket.lose_response=True
        with self.assertRaises(StateUnavailable):self.l.settle(t,'c'*32,'succeeded','d'*64)
        self.bucket.lose_response=False;a=self.l.settle(t,'c'*32,'succeeded','d'*64)
        self.assertEqual(a['outcome'],'succeeded')
        self.assertEqual(self.l.snapshot(SUBJECT,self.day,free_grant(SUBJECT,NOW))['used'],1)
        for k,v in before.items():self.assertEqual(self.bucket.versions[k],v)
        for method,_,kw in self.bucket.calls:
            self.assertIn(method,('reload','download','upload'))
            if method=='upload':self.assertIn('if_generation_match',kw)

    def test_lost_initialization_does_not_replace_existing_day(self):
        self.bucket.lose_response=True
        with self.assertRaises(StateUnavailable):self.l.initialize_day('new',self.day,baseline_successes=1,baseline_attempts=1,manifest_sha256=BASELINE)
        self.bucket.lose_response=False
        self.l.initialize_day('new',self.day,baseline_successes=1,baseline_attempts=1,manifest_sha256=BASELINE)
        self.assertEqual(self.l.snapshot('new',self.day,free_grant('new',NOW))['used'],1)


if __name__=='__main__':unittest.main()
