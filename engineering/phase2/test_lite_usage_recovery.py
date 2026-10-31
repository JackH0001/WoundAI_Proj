"""Synthetic opt-in cutover/lease/fence tests, real durable SQLite CAS.

G3 here tests the exclusion primitive, not a native retry/successor workflow.
Legacy characterization probes intentionally remain unchanged/default-off.
"""
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from lite_attest_state import SQLiteStateStore, GCSStateStore, StateUnavailable, _encode, MAX_STATE
from lite_usage_ledger import UsageLedger, UsageConflict, UsageLimit, Ticket, digest, free_grant
from lite_usage_cutover import CutoverPolicy, seal_cutover, validate

NOW = 1792886400 + 100
SUBJECT = 'synthetic-recovery-subject'


def manifest(namespace, now):
    return dict(schema='lite-usage-cutover/1', namespace=namespace, day=now//86400,
        sealed_at=now, writer_fence_sha256='a'*64, baseline_sha256='b'*64,
        account_map_sha256='c'*64, policy='free2-plus10-attempt30/v1', reservation_seconds=900)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.now = NOW; self.namespace = 'recovery-test'; self.frozen = True
        self.control = SQLiteStateStore(Path(self.tmp.name)/'control.sqlite')
        self.store = SQLiteStateStore(Path(self.tmp.name)/'usage.sqlite')
        self.manifest = manifest(self.namespace, self.now)
        self.proof = seal_cutover(self.control, self.manifest, verify_frozen_inputs=lambda m: True)
        self.policy = CutoverPolicy(self.control, namespace=self.namespace,
            expected_sha256=self.proof, verify_writer_fence=lambda m: self.frozen)
        self.l = UsageLedger(self.store, namespace=self.namespace, clock=lambda:self.now, cutover=self.policy)
        self.now += 86400; self.day = self.now//86400

    def snap(self, subject=SUBJECT, day=None):
        return self.l.snapshot(subject, self.day if day is None else day, free_grant(subject, self.now))

    def reserve(self, n=1):
        self.snap()
        return self.l.reserve(SUBJECT, self.day, f'{n:032x}', 'd'*64, free_grant(SUBJECT,self.now))

    def ticket(self, n=1): return Ticket(SUBJECT, self.day, f'{n:032x}', 'd'*64)

    def test_cutover_seal_replay_exact_and_conflict_preserved(self):
        self.assertEqual(self.proof, seal_cutover(self.control, self.manifest, verify_frozen_inputs=lambda m: True))
        changed = dict(self.manifest, account_map_sha256='e'*64)
        with self.assertRaises(UsageConflict):seal_cutover(self.control,changed,verify_frozen_inputs=lambda m:True)
        self.assertEqual(self.policy.read(self.namespace,self.now),self.manifest)

    def test_seal_requires_trusted_verified_frozen_inputs(self):
        other=SQLiteStateStore(Path(self.tmp.name)/'other.sqlite')
        for value in (False, None, 1, 'verified'):
            with self.assertRaises(StateUnavailable):seal_cutover(other,self.manifest,verify_frozen_inputs=lambda m:value)
        self.assertEqual(other.load(next(iter_key(self.control))), None)

    def test_malformed_seal_rejected(self):
        for key, bad in [('day',True),('sealed_at',-1),('reservation_seconds',1),
                         ('namespace','../other'),('writer_fence_sha256',''),('policy','free5')]:
            with self.subTest(key=key), self.assertRaises(ValueError):validate(dict(self.manifest,**{key:bad}))

    def test_uncertain_seal_can_read_back_but_never_reset(self):
        other=SQLiteStateStore(Path(self.tmp.name)/'uncertain.sqlite');cas=other.compare_exchange
        def lost(*args):cas(*args);raise StateUnavailable('response lost')
        with patch.object(other,'compare_exchange',side_effect=lost),self.assertRaises(StateUnavailable):
            seal_cutover(other,self.manifest,verify_frozen_inputs=lambda m:True)
        self.assertEqual(seal_cutover(other,self.manifest,verify_frozen_inputs=lambda m:True),self.proof)

    def test_cutover_day_requires_verified_baseline(self):
        self.now=NOW;self.day=self.now//86400
        with self.assertRaises(StateUnavailable):self.snap()
        self.l.initialize_day(SUBJECT,self.day,baseline_successes=2,baseline_attempts=5,manifest_sha256='f'*64)
        self.assertEqual(self.snap()['remaining'],0)
        self.assertEqual(self.l._load(SUBJECT,self.day)[1]['v'],1)

    def test_missing_old_day_never_created(self):
        with self.assertRaises(StateUnavailable):self.snap(day=self.day-1)
        self.assertIsNone(self.store.load(self.l.key(SUBJECT,self.day-1)))

    def test_post_cutover_day_create_once_concurrent(self):
        barrier=threading.Barrier(8)
        def read(_):barrier.wait(5);return self.snap()
        with ThreadPoolExecutor(max_workers=8) as p:rows=list(p.map(read,range(8)))
        self.assertTrue(all(r==rows[0] for r in rows));self.assertEqual(rows[0]['remaining'],2)
        before=self.store.load(self.l.key(SUBJECT,self.day));self.snap()
        self.assertEqual(before,self.store.load(self.l.key(SUBJECT,self.day)))

    def test_after_expiry_eight_new_requests_admit_only_two(self):
        self.reserve(1);self.reserve(2);self.now+=900;barrier=threading.Barrier(8)
        def attempt(n):
            barrier.wait(5)
            try:
                t=self.reserve(n);return self.l.claim(t,f'{n:032x}')
            except UsageLimit:return False
        with ThreadPoolExecutor(max_workers=8) as p:outcomes=list(p.map(attempt,range(3,11)))
        self.assertEqual(outcomes.count(True),2)
        self.assertEqual((self.snap()['pending'],self.snap()['attempts']),(2,4))

    def test_uncertain_auto_initialize_retry_preserves_existing_row(self):
        cas=self.store.compare_exchange
        def lost(*args):cas(*args);raise StateUnavailable('daily row response lost')
        with patch.object(self.store,'compare_exchange',side_effect=lost),self.assertRaises(StateUnavailable):self.snap()
        before=self.store.load(self.l.key(SUBJECT,self.day))
        self.assertEqual(self.snap()['remaining'],2)
        self.assertEqual(before,self.store.load(self.l.key(SUBJECT,self.day)))

    def test_missing_fence_blocks_new_day_without_writes(self):
        self.frozen=False
        with self.assertRaises(StateUnavailable):self.snap()
        self.assertIsNone(self.store.load(self.l.key(SUBJECT,self.day)))

    def test_corrupt_day_not_reset(self):
        key=self.l.key(SUBJECT,self.day);self.store.compare_exchange(key,0,{'v':999})
        before=self.store.load(key)
        with self.assertRaises(StateUnavailable):self.snap()
        self.assertEqual(self.store.load(key),before)

    def test_control_hash_change_rejected(self):
        self.snap();key=next(iter_key(self.control));rev,row=self.control.load(key)
        row['baseline_sha256']='e'*64;self.control.compare_exchange(key,rev,row)
        with self.assertRaises(StateUnavailable):self.snap()

    def test_next_day_never_resets_previous_success(self):
        t=self.reserve();self.assertTrue(self.l.claim(t,'e'*32));self.l.settle(t,'e'*32,'succeeded','f'*64)
        original=self.l.receipt(t);self.now+=86400
        self.assertEqual(self.snap(day=self.day+1)['remaining'],2)
        self.assertEqual(self.snap()['used'],1);self.assertEqual(self.l.receipt(t),original)

    def test_same_subject_new_policy_or_namespace_not_silent_reset(self):
        wrong=CutoverPolicy(self.control,namespace=self.namespace,expected_sha256='0'*64,verify_writer_fence=lambda m:True)
        other=UsageLedger(self.store,namespace=self.namespace,cutover=wrong,clock=lambda:self.now)
        with self.assertRaises(StateUnavailable):other.snapshot(SUBJECT,self.day,free_grant(SUBJECT,self.now))

    def test_expire_two_unstarted_releases_capacity_keeps_attempts(self):
        t1=self.reserve(1);t2=self.reserve(2)
        with self.assertRaises(UsageLimit):self.reserve(3)
        self.now+=900;self.assertEqual(self.snap()['remaining'],2)
        self.assertEqual(self.snap()['attempts'],2)
        for t in (t1,t2):
            self.assertEqual(self.l.receipt(t)['outcome'],'cancelled');self.assertFalse(self.l.claim(t,'e'*32))
        self.reserve(3);self.assertEqual(self.snap()['attempts'],3)

    def test_before_deadline_still_reserved(self):
        t=self.reserve();self.now+=899
        self.assertEqual(self.snap()['pending'],1);self.assertEqual(self.l.receipt(t)['outcome'],'reserved')

    def test_claim_itself_checks_deadline_without_sweeper(self):
        t=self.reserve();self.now+=900
        self.assertFalse(self.l.claim(t,'e'*32));self.assertEqual(self.l.receipt(t)['outcome'],'cancelled')

    def test_delayed_fence_check_does_not_claim_after_deadline(self):
        t=self.reserve()
        def delayed(m):self.now+=901;return True
        with patch.object(self.policy,'verify_writer_fence',side_effect=delayed):self.assertFalse(self.l.claim(t,'e'*32))

    def test_delayed_admission_rechecks_grant_before_reserve(self):
        self.snap()
        def delayed(m):self.now+=31;return True
        with patch.object(self.policy,'verify_writer_fence',side_effect=delayed),self.assertRaises(UsageConflict):
            self.l.reserve(SUBJECT,self.day,self.ticket().operation,'d'*64,free_grant(SUBJECT,self.now))
        self.assertEqual(self.l._load(SUBJECT,self.day)[1]['entries'],{})

    def test_clock_rollback_during_claim_never_authorizes_execution(self):
        t=self.reserve()
        def rollback(m):self.now-=1;return True
        with patch.object(self.policy,'verify_writer_fence',side_effect=rollback),self.assertRaises(StateUnavailable):
            self.l.claim(t,'e'*32)
        self.now+=1;self.assertEqual(self.l.receipt(t)['outcome'],'reserved')

    def test_unknown_running_never_expires(self):
        t=self.reserve();self.assertTrue(self.l.claim(t,'e'*32));self.now+=86400*3
        self.assertEqual(self.snap()['pending'],1);self.assertEqual(self.l.receipt(t)['outcome'],'running')
        self.assertFalse(self.l.claim(t,'f'*32))

    def test_no_legacy_lease_reinterpretation(self):
        self.now=NOW;self.day=self.now//86400
        self.l.initialize_day(SUBJECT,self.day,baseline_successes=0,baseline_attempts=0,manifest_sha256='f'*64)
        t=self.reserve();self.now+=10000
        self.assertEqual(self.snap()['pending'],1);self.assertTrue(self.l.claim(t,'e'*32))

    def test_expiry_audit_replay_and_changed_input_rejected(self):
        t=self.reserve();self.now+=900;self.snap();before=self.l.receipt(t)
        self.assertEqual(self.reserve(),t);self.assertEqual(self.l.receipt(t),before)
        with self.assertRaises(UsageConflict):self.l.reserve(SUBJECT,self.day,t.operation,'e'*64,free_grant(SUBJECT,self.now))

    def test_expiry_cas_response_lost_no_inference_authorized(self):
        t=self.reserve();self.now+=900;cas=self.store.compare_exchange
        def lost(*args):cas(*args);raise StateUnavailable('expiry response lost')
        with patch.object(self.store,'compare_exchange',side_effect=lost),self.assertRaises(StateUnavailable):self.l.claim(t,'e'*32)
        self.assertFalse(self.l.claim(t,'e'*32));self.assertEqual(self.snap()['remaining'],2)

    def test_expiry_wins_against_paused_claim(self):
        t=self.reserve();entered=threading.Event();release=threading.Event();cas=self.store.compare_exchange
        def paused(key,rev,row):
            if row['entries'].get(t.operation,{}).get('worker')=='e'*32:
                entered.set();self.assertTrue(release.wait(5))
            return cas(key,rev,row)
        with patch.object(self.store,'compare_exchange',side_effect=paused),ThreadPoolExecutor(max_workers=1) as p:
            task=p.submit(self.l.claim,t,'e'*32)
            try:
                self.assertTrue(entered.wait(5));self.now+=900;self.snap()
            finally:release.set()
            self.assertFalse(task.result(5))
        self.assertEqual(self.l.receipt(t)['outcome'],'cancelled')

    def test_claim_wins_then_expiry_cannot_refund(self):
        t=self.reserve();self.assertTrue(self.l.claim(t,'e'*32));self.now+=900
        self.snap();self.assertEqual(self.l.receipt(t)['outcome'],'running')
        self.l.settle(t,'e'*32,'succeeded','f'*64);self.assertEqual(self.snap()['used'],1)

    def test_writer_fence_failure_blocks_new_claim_but_not_receipts_or_settlement(self):
        running=self.reserve();self.l.claim(running,'e'*32);waiting=self.reserve(2);self.frozen=False
        with self.assertRaises(StateUnavailable):self.l.claim(waiting,'f'*32)
        self.assertEqual(self.l.receipt(running)['outcome'],'running')
        self.l.settle(running,'e'*32,'succeeded','f'*64)
        self.assertEqual(self.l.receipt(running)['outcome'],'succeeded')

    def test_thirty_cancelled_attempts_cannot_bypass_daily_bound(self):
        for n in range(1,31):self.reserve(n);self.now+=900;self.snap()
        with self.assertRaises(UsageLimit):self.reserve(31)
        self.assertEqual(self.snap()['attempts'],30);self.assertEqual(self.snap()['used'],0)
        self.assertLessEqual(len(_encode(self.l._load(SUBJECT,self.day)[1])),MAX_STATE)

    def test_fence_missing_replay_blocks_old_operation(self):
        self.snap();t=self.ticket();self.now+=86400
        proof=self.l.fence_missing(t);self.assertEqual(self.l.fence_missing(t),proof)
        with self.assertRaises(UsageConflict):self.l.reserve(SUBJECT,self.day,t.operation,t.request_digest,free_grant(SUBJECT,self.now))
        with self.assertRaises(UsageConflict):self.l.fence_missing(replace(t,request_digest='e'*64))
        self.assertEqual(self.snap()['attempts'],1)

    def test_fence_does_not_create_missing_old_day(self):
        t=self.ticket();self.now+=86400
        with self.assertRaises(StateUnavailable):self.l.fence_missing(t)
        self.assertIsNone(self.store.load(self.l.key(SUBJECT,self.day)))

    def test_fence_cannot_cancel_existing_reserved_running_or_completed(self):
        t=self.reserve();self.now+=86400
        self.assertIsNone(self.l.fence_missing(t))
        self.assertEqual(self.l.receipt(t)['outcome'],'reserved')
        # G3 checks the original receipt; it does not run G2 reclamation.

    def test_fence_current_day_refused(self):
        self.snap()
        with self.assertRaises(UsageConflict):self.l.fence_missing(self.ticket())

    def test_fence_old_v1_requires_explicit_reconciliation(self):
        self.now=NOW;self.day=self.now//86400
        self.l.initialize_day(SUBJECT,self.day,baseline_successes=0,baseline_attempts=0,manifest_sha256='f'*64)
        self.now+=86400
        with self.assertRaises(StateUnavailable):self.l.fence_missing(self.ticket())

    def test_fence_wins_against_pre_midnight_paused_reservation(self):
        self.snap();t=self.ticket();entered=threading.Event();release=threading.Event();cas=self.store.compare_exchange
        self.now=(self.day+1)*86400-1
        def paused(key,rev,row):
            if t.operation in row['entries']:
                entered.set();self.assertTrue(release.wait(5))
            return cas(key,rev,row)
        def reserve():
            try:self.l.reserve(SUBJECT,self.day,t.operation,t.request_digest,free_grant(SUBJECT,self.now));return 'reserved'
            except UsageConflict:return 'fenced'
        with patch.object(self.store,'compare_exchange',side_effect=paused),ThreadPoolExecutor(max_workers=1) as p:
            task=p.submit(reserve)
            try:
                self.assertTrue(entered.wait(5));self.now+=2;proof=self.l.fence_missing(t)
            finally:release.set()
            self.assertEqual(task.result(5),'fenced')
        self.assertEqual(proof,self.l.fence_missing(t));self.assertEqual(self.l._load(SUBJECT,self.day)[1]['entries'],{})

    def test_reservation_wins_then_fence_refuses_successor(self):
        t=self.reserve();self.now+=86400
        self.assertIsNone(self.l.fence_missing(t));self.assertEqual(self.l._load(SUBJECT,self.day)[1]['fences'],{})

    def test_fence_lost_response_recovers_same_proof(self):
        self.snap();t=self.ticket();self.now+=86400;cas=self.store.compare_exchange
        def lost(*args):cas(*args);raise StateUnavailable('fence response lost')
        with patch.object(self.store,'compare_exchange',side_effect=lost),self.assertRaises(StateUnavailable):self.l.fence_missing(t)
        self.assertEqual(self.l.fence_missing(t),self.l._fence_proof(t))

    def test_fence_corruption_detected(self):
        self.snap();t=self.ticket();self.now+=86400;self.l.fence_missing(t)
        key=self.l.key(SUBJECT,self.day);rev,row=self.store.load(key);row['fences'][t.operation]['proof']='0'*64
        self.store.compare_exchange(key,rev,row)
        with self.assertRaises(StateUnavailable):self.l.fence_missing(t)

    def test_fence_bound_and_real_storage_size(self):
        self.snap();self.now+=86400
        for n in range(1,31):self.l.fence_missing(self.ticket(n))
        with self.assertRaises(StateUnavailable):self.l.fence_missing(self.ticket(31))
        self.assertLessEqual(len(_encode(self.l._load(SUBJECT,self.day)[1])),MAX_STATE)

    def test_disabled_default_retains_original_missing_day_failure(self):
        legacy=UsageLedger(self.store,namespace='disabled-default',clock=lambda:self.now)
        with self.assertRaises(StateUnavailable):legacy.snapshot(SUBJECT,self.day,free_grant(SUBJECT,self.now))


def iter_key(store):
    # Only the synthetic control DB; no credentials or application data.
    from lite_usage_cutover import _key
    yield _key('recovery-test')


class GCSRecoveryTests(RecoveryTests):
    """Same scenarios through the real generation-CAS adapter, fake SDK transport."""
    def setUp(self):
        super().setUp()
        from test_lite_attest_state import FakeBucket
        self.bucket=FakeBucket()
        self.store=GCSStateStore(self.bucket,prefix='synthetic_recovery/v2')
        self.l=UsageLedger(self.store,namespace=self.namespace,clock=lambda:self.now,cutover=self.policy)

    def test_real_adapter_preserves_prior_versions_no_delete(self):
        t=self.reserve();before=dict(self.bucket.versions)
        self.now+=900;self.snap();self.now+=86400;self.l.fence_missing(self.ticket(2))
        for key,data in before.items():self.assertEqual(self.bucket.versions[key],data)
        for method,_,kw in self.bucket.calls:
            self.assertIn(method,('reload','download','upload'))
            if method=='upload':self.assertIn('if_generation_match',kw)
        self.assertEqual(self.l.receipt(t)['outcome'],'cancelled')


if __name__=='__main__':unittest.main()
