"""Synthetic deployment observations + real SQLite/GCS-adapter state, no cloud."""
import copy
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from lite_attest_state import SQLiteStateStore,GCSStateStore,StateUnavailable
from lite_usage_ledger import UsageLedger,UsageConflict,free_grant,digest
from lite_usage_rollout import UsageRollout
from test_lite_usage_recovery import manifest,NOW


class SyntheticProvider:
    def __init__(self, expected):
        self.expected=expected;self.legacy_disabled=False;self.new_enabled=False
        self.inflight=0;self.calls=[];self.wrong=None
    def freeze_legacy(self,m):
        self.calls.append('freeze');self.legacy_disabled=True
    def enable_new(self,m):
        self.calls.append('enable');self.new_enabled=True
    def disable_new(self,m):
        self.calls.append('disable');self.new_enabled=False
    def observe(self,m):
        self.calls.append('observe')
        v={k:self.expected[k] for k in ('namespace','writer_fence_sha256','baseline_sha256','account_map_sha256')}
        v.update(legacy_disabled=self.legacy_disabled,new_enabled=self.new_enabled,inflight_legacy=self.inflight)
        if self.wrong:v[self.wrong]='0'*64
        return v


class RolloutTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.now=NOW
        self.rows=[dict(subject='synthetic-a',successes=1,attempts=3),dict(subject='synthetic-b',successes=0,attempts=2)]
        self.m=manifest('rollout-test',self.now);self.m['baseline_sha256']=digest(self.rows)
        self.provider=SyntheticProvider(self.m)
        self.stores={k:SQLiteStateStore(self.root/(k+'.sqlite')) for k in ('control','audit','rollout','usage')}
        self.configure()
    def configure(self):
        self.ledger=UsageLedger(self.stores['usage'],namespace=self.m['namespace'],clock=lambda:self.now)
        self.executor=UsageRollout(store=self.stores['rollout'],audit_store=self.stores['audit'],
            control_store=self.stores['control'],ledger=self.ledger,provider=self.provider,manifest=self.m,baselines=self.rows)
        self.ledger.cutover=self.executor.policy()
    def snap(self,subject='synthetic-a'):
        return self.ledger.snapshot(subject,self.now//86400,free_grant(subject,self.now))
    def activate(self):
        self.assertEqual(self.executor.execute()['phase'],'active')

    def test_verified_cutover_preserves_nonzero_baseline_and_audit(self):
        self.activate();self.assertEqual((self.snap()['used'],self.snap()['attempts']),(1,3))
        self.assertTrue(self.provider.legacy_disabled);self.assertTrue(self.provider.new_enabled)
        t=self.ledger.reserve('synthetic-a',self.now//86400,'1'*32,'d'*64,free_grant('synthetic-a',self.now))
        self.assertTrue(self.ledger.claim(t,'a'*32));self.ledger.settle(t,'a'*32,'succeeded','f'*64)
        self.assertEqual(self.snap()['remaining'],0)
        before=copy.deepcopy(self.ledger._load('synthetic-a',self.now//86400))
        self.assertEqual(self.executor.execute()['phase'],'active')
        self.assertEqual(before,self.ledger._load('synthetic-a',self.now//86400))

    def test_rollback_keeps_usage_receipts_and_never_reopens_legacy(self):
        self.activate();t=self.ledger.reserve('synthetic-a',self.now//86400,'1'*32,'d'*64,free_grant('synthetic-a',self.now))
        self.assertTrue(self.ledger.claim(t,'a'*32))
        self.assertEqual(self.executor.rollback()['phase'],'paused')
        self.assertTrue(self.provider.legacy_disabled);self.assertFalse(self.provider.new_enabled)
        # Already-running work finishes on the original ledger, even while paused.
        self.ledger.settle(t,'a'*32,'succeeded','f'*64);self.assertEqual(self.snap()['used'],2)
        with self.assertRaises(StateUnavailable):self.ledger.reserve('synthetic-b',self.now//86400,'2'*32,'e'*64,free_grant('synthetic-b',self.now))
        with self.assertRaises(UsageConflict):self.executor.execute()
        self.assertEqual(self.ledger.receipt(t)['outcome'],'succeeded')

    def test_new_day_is_zero_only_after_verified_cutover(self):
        self.activate();day=self.now//86400;self.now+=86400
        self.assertEqual(self.snap()['remaining'],2)
        self.assertEqual(self.ledger._load('synthetic-a',day)[1]['baseline']['successes'],1)

    def test_claim_admitted_before_pause_can_finish_once_on_same_ledger(self):
        self.activate()
        ticket=self.ledger.reserve('synthetic-a',self.now//86400,'1'*32,'d'*64,free_grant('synthetic-a',self.now))
        cas=self.stores['usage'].compare_exchange;entered=threading.Event();release=threading.Event()
        def pause(key,rev,value):
            if value.get('entries',{}).get('1'*32,{}).get('history',[[]])[-1][1:] == ['running']:
                entered.set();self.assertTrue(release.wait(10))
            return cas(key,rev,value)
        with patch.object(self.stores['usage'],'compare_exchange',side_effect=pause),ThreadPoolExecutor(max_workers=1) as pool:
            pending=pool.submit(self.ledger.claim,ticket,'a'*32)
            self.assertTrue(entered.wait(5));self.executor.rollback();release.set()
            self.assertTrue(pending.result(5))
        self.assertFalse(self.ledger.claim(ticket,'a'*32))
        a=self.ledger.settle(ticket,'a'*32,'succeeded','f'*64)
        self.assertEqual(a,self.ledger.settle(ticket,'a'*32,'succeeded','f'*64))
        self.assertEqual((self.snap()['used'],self.snap()['attempts']),(2,4))
        self.assertFalse(self.executor.admission_allowed(self.m))

    def test_stale_writer_or_changed_evidence_stops_admission(self):
        self.activate()
        for field in ('legacy','baseline_sha256','account_map_sha256','writer_fence_sha256'):
            with self.subTest(field=field):
                self.provider.legacy_disabled=field!='legacy';self.provider.wrong=None if field=='legacy' else field
                self.assertFalse(self.executor.admission_allowed(self.m))
                with self.assertRaises(StateUnavailable):
                    self.ledger.reserve('synthetic-b',self.now//86400,'2'*32,'d'*64,free_grant('synthetic-b',self.now))
        self.provider.legacy_disabled=True;self.provider.wrong=None

    def test_inflight_legacy_blocks_seal_and_enable(self):
        self.provider.inflight=1
        with self.assertRaises(StateUnavailable):self.executor.execute()
        self.assertNotIn('enable',self.provider.calls);self.assertFalse(self.provider.new_enabled)

    def test_wrong_baseline_never_resets_existing_counts(self):
        self.ledger.initialize_day('synthetic-a',self.now//86400,baseline_successes=2,baseline_attempts=8,manifest_sha256='e'*64)
        before=copy.deepcopy(self.ledger._load('synthetic-a',self.now//86400))
        with self.assertRaises(UsageConflict):self.executor.execute()
        self.assertEqual(before,self.ledger._load('synthetic-a',self.now//86400))
        self.assertFalse(self.provider.new_enabled)

    def test_partial_baseline_failure_keeps_first_row_and_pauses(self):
        original=self.ledger.initialize_day
        def fail(subject,*a,**k):
            if subject=='synthetic-b':raise StateUnavailable('second row unavailable')
            return original(subject,*a,**k)
        with patch.object(self.ledger,'initialize_day',side_effect=fail),self.assertRaises(StateUnavailable):self.executor.execute()
        self.assertEqual(self.ledger._load('synthetic-a',self.now//86400)[1]['baseline']['successes'],1)
        self.assertIsNone(self.stores['usage'].load(self.ledger.key('synthetic-b',self.now//86400)))
        self.assertEqual(self.executor._read()[1]['phase'],'paused')

    def test_lost_seal_ack_keeps_control_and_blocks_new(self):
        cas=self.stores['control'].compare_exchange
        def lost(*a):cas(*a);raise StateUnavailable('seal reply lost')
        with patch.object(self.stores['control'],'compare_exchange',side_effect=lost),self.assertRaises(StateUnavailable):self.executor.execute()
        self.assertEqual(self.executor.policy().read(self.m['namespace'],self.now),self.m)
        self.assertFalse(self.provider.new_enabled);self.assertEqual(self.executor._read()[1]['phase'],'paused')

    def test_lost_enable_reply_rolls_back_without_erasing(self):
        enable=self.provider.enable_new
        def lost(m):enable(m);raise StateUnavailable('provider response lost')
        with patch.object(self.provider,'enable_new',side_effect=lost),self.assertRaises(StateUnavailable):self.executor.execute()
        self.assertFalse(self.provider.new_enabled);self.assertTrue(self.provider.legacy_disabled)
        self.assertEqual(self.executor._read()[1]['phase'],'paused')
        self.assertEqual(self.snap()['used'],1)

    def test_pause_storage_failure_still_closes_provider_and_reports_unconfirmed(self):
        self.activate()
        with patch.object(self.stores['rollout'],'compare_exchange',side_effect=StateUnavailable('store unavailable')):
            with self.assertRaisesRegex(StateUnavailable,'unconfirmed'):self.executor.rollback()
        self.assertFalse(self.provider.new_enabled);self.assertFalse(self.executor.admission_allowed(self.m))

    def test_provider_disable_failure_still_persists_pause_and_reports_unconfirmed(self):
        self.activate()
        with patch.object(self.provider,'disable_new',side_effect=StateUnavailable('provider unavailable')):
            with self.assertRaisesRegex(StateUnavailable,'unconfirmed'):self.executor.rollback()
        self.assertEqual(self.executor._read()[1]['phase'],'paused')
        self.assertFalse(self.executor.admission_allowed(self.m))

    def test_audit_failure_does_not_claim_success_or_delete(self):
        self.activate()
        with patch.object(self.stores['audit'],'compare_exchange',side_effect=StateUnavailable('audit unavailable')):
            with self.assertRaisesRegex(StateUnavailable,'unconfirmed'):self.executor.rollback()
        self.assertFalse(self.provider.new_enabled);self.assertFalse(self.executor.admission_allowed(self.m))

    def test_corrupt_control_blocks_admission_without_replacement(self):
        self.activate();rev,v=self.stores['rollout'].load(self.executor.key)
        self.stores['rollout'].compare_exchange(self.executor.key,rev,{'v':99})
        before=self.stores['rollout'].load(self.executor.key)
        self.assertFalse(self.executor.admission_allowed(self.m))
        with self.assertRaises(StateUnavailable):self.executor.rollback()
        self.assertEqual(before,self.stores['rollout'].load(self.executor.key));self.assertFalse(self.provider.new_enabled)

    def test_day_crossing_aborts_partial_cutover_no_new_zero_baseline(self):
        freeze=self.provider.freeze_legacy
        def midnight(m):freeze(m);self.now+=86400
        with patch.object(self.provider,'freeze_legacy',side_effect=midnight),self.assertRaises(UsageConflict):self.executor.execute()
        self.assertIsNone(self.stores['usage'].load(self.ledger.key('synthetic-a',self.m['day'])))
        self.assertFalse(self.provider.new_enabled)

    def test_process_restart_reads_same_active_state_and_baselines(self):
        self.activate();before=self.executor._read()
        self.configure();self.assertEqual(self.executor.execute(),before[1])
        self.assertEqual(self.snap()['used'],1)

    def test_changed_plan_cannot_replace_scope(self):
        self.activate();before=self.executor._read()
        self.m=copy.deepcopy(self.m);self.m['account_map_sha256']='e'*64
        self.configure()
        with self.assertRaises(StateUnavailable):self.executor.execute()
        self.assertEqual(before,self.stores['rollout'].load(self.executor.key))


class RolloutGCSTests(RolloutTests):
    def setUp(self):
        super().setUp()
        from test_lite_attest_state import FakeBucket
        self.bucket=FakeBucket()
        self.stores={k:GCSStateStore(self.bucket,prefix='synthetic/rollout/'+k) for k in self.stores}
        self.configure()
    def test_prior_generations_and_audits_survive_pause(self):
        self.activate();before=dict(self.bucket.versions)
        self.executor.rollback()
        for key,value in before.items():self.assertEqual(self.bucket.versions[key],value)
        self.assertTrue(all(call[0] in ('reload','download','upload') for call in self.bucket.calls))


if __name__=='__main__':unittest.main()

