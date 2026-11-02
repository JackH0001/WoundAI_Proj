import copy,unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from lite_account_identity import IdentityRejected
from lite_attest_state import StateUnavailable
from lite_disposition_worker import AccountDispositionWorker,LANES
from lite_apple_oauth import REVOKE_URL
from lite_subscription_executor import key
from lite_usage_ledger import digest
from lite_disposition_worker_probe import WorkerFixture,run
from test_lite_subscription_grace import rows
import test_lite_native_purchase as native


class WorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):native.legacy.LinksTests.setUpClass()
    def setUp(self):self.f=WorkerFixture(self);self.w=self.f.w;self.person=self.f.person
    def ready(self):self.f.ready()
    def work(self,**kw):return self.w.run(self.person,apply=True,**kw)
    def test_end_to_end_actual_components_preserve_originals_and_quota(self):
        r=run();self.assertTrue(r['composition_available'])
        self.assertEqual(r['model_calls'],1);self.assertTrue(r['quota_and_identity_unchanged'])
        self.assertEqual(r['research_old_active_remaining'],0);self.assertEqual(r['service_old_active_remaining'],0)
        self.assertTrue(all(s=='observed' for s in r['worker_status']['states'].values()))
        self.assertEqual(r['worker_status']['observations']['research_history']['unresolved_grants'],1)
        self.assertFalse(r['account_deleted']);self.assertEqual(r['live_gcs_apple_kms_calls'],0)
    def test_default_status_is_read_only(self):
        self.ready();before=self.snap();r=self.w.run(self.person)
        self.assertEqual(r['stop_reason'],'read_only');self.assertEqual(r['dispatches'],0);self.assertEqual(before,self.snap())
    def snap(self):
        return (rows(self.f.store),rows(self.f.audit),copy.deepcopy(self.f.h.bucket.history),
                copy.deepcopy(self.f.service_bucket.versions),self.f.f.f.g.calls.copy(),self.f.f.f.kms.calls.copy())
    def test_active_account_rejected(self):
        with self.assertRaises(IdentityRejected):self.work()
        self.assertEqual(rows(self.f.audit),[])
    def test_one_dispatch_has_no_unrelated_lane_work(self):
        self.ready();r=self.work(max_steps=1,limit=1)
        self.assertEqual(r['dispatches'],1);self.assertEqual(r['states']['service_current'],'observed')
        self.assertEqual(r['states']['apple_revocation'],'not_started');self.assertEqual(self.f.f.f.g.calls.count(REVOKE_URL),0)
    def test_legacy_closure_can_revoke_apple_while_both_research_lanes_wait(self):
        f = self.f.f
        f.complete()
        self.f.h.seed(3)
        f.d.begin('device-one','d'*32)
        original = copy.deepcopy(self.f.h.bucket.history)
        progress = self.work(max_steps=20)
        self.assertEqual(progress['states']['service_current'], 'observed')
        self.assertEqual(progress['states']['apple_revocation'], 'observed')
        for lane in ('research_current', 'research_history'):
            self.assertEqual(progress['states'][lane], 'blocked')
        self.assertEqual(original, self.f.h.bucket.history)
        original_plan = progress['plan_sha256']
        f.close()
        final, _ = self.f.finish()
        self.assertEqual(final['plan_sha256'], original_plan)
        self.assertEqual(final['states']['research_current'], 'observed')
        self.assertEqual(final['states']['research_history'], 'observed')
        self.assertFalse(final['account_deleted'])
    def test_research_failure_does_not_prevent_apple_results_or_keys(self):
        self.ready()
        with patch.object(self.f.h.m.w,'advance',side_effect=StateUnavailable('private media unavailable')):
            r=self.work(max_steps=12)
        self.assertEqual(r['states']['research_current'],'blocked')
        self.assertEqual(r['observations']['apple_revocation']['apple_revocation'],'confirmed')
        self.assertEqual(r['observations']['service_current']['service_result_payloads'],'current_sealed')
        self.assertEqual(r['observations']['grant_key']['kms_state'],'DESTROY_SCHEDULED')
        final,_=self.f.finish();self.assertEqual(final['states']['research_history'],'observed')
    def test_apple_outage_keeps_key_but_other_lanes_advance(self):
        self.ready();self.f.f.f.g.revoke_error=True;r=self.work(max_steps=30)
        self.assertEqual(r['observations']['service_current']['service_result_payloads'],'current_sealed')
        self.assertIsNone(r['observations']['grant_key']);self.assertEqual(r['states']['grant_key'],'blocked')
        self.assertNotIn('destroy',[c[0] for c in self.f.f.f.kms.calls])
        self.f.f.f.g.revoke_error=False;self.f.finish()
    def test_failed_lane_is_attempted_only_once_per_run(self):
        self.ready()
        with patch.object(self.f.h.m.w,'advance',side_effect=StateUnavailable('offline')) as call:
            self.work(max_steps=40)
        self.assertEqual(call.call_count,1)

    def test_public_receipt_preserves_blocked_lane_when_another_lane_succeeds(self):
        self.ready()
        with patch.object(self.f.f.results, 'advance', side_effect=StateUnavailable('temporary')):
            self.work(max_steps=1)
        self.assertEqual(self.f.f.status()['disposition']['blocked_lanes'],['service_results'])
        self.work(max_steps=1,limit=20)
        self.assertEqual(self.f.f.status()['apple_revocation'],'confirmed')
        self.assertEqual(self.f.f.status()['disposition']['blocked_lanes'],['service_results'])
        with patch.object(self.f.f.f.provider,'describe',side_effect=StateUnavailable('temporary')):
            self.work(max_steps=1)
        before=self.f.f.status()
        self.assertEqual(before['disposition']['blocked_lanes'],['grant_key','service_results'])
        self.f.finish()
        after=self.f.f.status()
        self.assertEqual(after['operation'],before['operation'])
        self.assertEqual(after['disposition']['blocked_lanes'],[])
        self.assertEqual(after['disposition']['grant_key_state'],'unrecoverable')
        self.assertFalse(after['account_deleted'])

    def test_restart_publishes_saved_checkpoint_without_rerunning_provider(self):
        self.ready()
        with patch.object(self.w,'_publish_progress',side_effect=StateUnavailable('lost publication')):
            with self.assertRaisesRegex(StateUnavailable,'lost publication'):
                self.work(max_steps=1)
        self.assertEqual(self.w.status(self.person)['states']['service_current'],'observed')
        self.assertEqual(self.f.f.status()['disposition']['service_state'],'not_started')
        self.w=self.f.worker()
        self.w.set_paused(self.person,paused=True,expected_revision=0)
        provider_calls=self.f.f.f.kms.calls.copy(),self.f.f.f.g.calls.copy()
        result=self.work(max_steps=1)
        self.assertEqual(result['dispatches'],0)
        self.assertEqual(self.f.f.status()['disposition']['service_state'],'current_sealed')
        revision=self.f.f.status()['disposition']['revision']
        self.work(max_steps=1)
        self.assertEqual(self.f.f.status()['disposition']['revision'],revision)
        self.assertEqual(provider_calls,(self.f.f.f.kms.calls,self.f.f.f.g.calls))

    def test_late_publisher_cannot_reinstate_a_cleared_block(self):
        self.ready()
        with patch.object(self.w,'_publish_progress'), patch.object(self.f.f.results,'advance',
                side_effect=StateUnavailable('temporary')):
            self.work(max_steps=1)
        public=self.f.f.progress.store
        compare_exchange=public.compare_exchange
        competed=False
        def compete(key,revision,value):
            nonlocal competed
            if not competed:
                competed=True
                self.f.finish()
            return compare_exchange(key,revision,value)
        with patch.object(public,'compare_exchange',side_effect=compete):
            self.w._publish_progress(self.w._plan(self.person))
        self.assertTrue(competed)
        final=self.f.f.status()['disposition']
        self.assertEqual(final['blocked_lanes'],[])
        self.assertEqual(final['service_state'],'current_sealed')
        self.assertEqual(final['grant_key_state'],'unrecoverable')

    def test_shared_key_in_dedicated_row_is_blocked_without_key_destruction(self):
        self.ready()
        self.work(max_steps=2,limit=20)
        cipher=self.f.f.f.cipher
        key=cipher._key(self.person)
        generation,row=cipher.store.load(key)
        shared=cipher.provider.keyring+'/cryptoKeys/shared/cryptoKeyVersions/1'
        self.assertTrue(cipher.store.compare_exchange(key,generation,dict(row,version=shared)))
        before=self.f.f.f.kms.calls.copy()
        self.work(max_steps=1)
        public=self.f.f.status()
        self.assertEqual(public['apple_revocation'],'confirmed')
        self.assertEqual(public['disposition']['blocked_lanes'],['grant_key'])
        self.assertFalse(public['account_deleted'])
        self.assertEqual(before,self.f.f.f.kms.calls)
    def test_service_history_outage_preserves_research_progress(self):
        self.ready()
        with patch.object(self.f.service,'advance_account',side_effect=StateUnavailable('offline')):
            for _ in range(8):r=self.work(max_steps=20)
        self.assertEqual(r['states']['research_history'],'observed');self.assertEqual(r['states']['service_history'],'blocked')
        self.f.finish();self.assertEqual(len(self.f.service_bucket.versions),len(self.f.service_bucket.live))
    def test_pause_survives_restart_and_does_not_reopen_account(self):
        self.ready();self.work(max_steps=1)
        c=self.w.set_paused(self.person,paused=True,expected_revision=0);before=self.snap()
        r=self.f.worker().run(self.person,apply=True);self.assertEqual(r['stop_reason'],'paused');self.assertEqual(before,self.snap())
        self.assertEqual(self.f.f.f.lifecycle._read(self.person)[1]['phase'],'closing')
        self.w.set_paused(self.person,paused=False,expected_revision=c['revision']);self.f.finish()
    def test_stale_pause_revision_cannot_resume(self):
        self.ready();self.w.set_paused(self.person,paused=True,expected_revision=0)
        with self.assertRaises(StateUnavailable):self.w.set_paused(self.person,paused=False,expected_revision=0)
        self.assertTrue(self.w.status(self.person)['paused'])
    def test_missing_pause_control_cannot_default_to_active(self):
        self.ready();self.w.set_paused(self.person,paused=True,expected_revision=0)
        p=self.w._plan(self.person);k=key('disposition-worker-control',digest(p));real=self.f.store.load
        with patch.object(self.f.store,'load',side_effect=lambda name:None if name==k else real(name)):
            with self.assertRaises(StateUnavailable):self.work()
        self.assertEqual(self.f.f.f.g.calls.count(REVOKE_URL),0)
    def test_missing_control_anchor_cannot_be_reconstructed(self):
        self.ready();self.work(max_steps=1);p=self.w._plan(self.person)
        k=key('disposition-worker-control-anchor',digest(p));real=self.f.audit.load;before=self.snap()
        with patch.object(self.f.audit,'load',side_effect=lambda name:None if name==k else real(name)):
            with self.assertRaises(StateUnavailable):self.work()
        self.assertEqual(before,self.snap())
    def test_pending_component_has_three_dispatch_budget(self):
        self.ready();real=self.f.f.d.advance
        with patch.object(self.f.f.d,'advance',wraps=real) as calls:
            self.f.f.f.g.revoke_error=True;self.work(max_steps=100,limit=1)
        self.assertLessEqual(calls.call_count,3)
    def test_lost_pause_ack_reads_same_control(self):
        self.ready();real=self.f.store.compare_exchange
        def lost(*a):real(*a);raise StateUnavailable('lost response')
        with patch.object(self.f.store,'compare_exchange',side_effect=lost):c=self.w.set_paused(self.person,paused=True,expected_revision=0)
        self.assertEqual(c['revision'],1);self.assertTrue(c['paused'])
    def test_cancellation_before_start_performs_no_writes(self):
        self.ready();before=self.snap();r=self.work(cancelled=lambda:True)
        self.assertEqual(r['stop_reason'],'cancelled');self.assertEqual(before,self.snap())
    def test_deadline_after_evidence_prevents_dispatch(self):
        self.ready();values=iter([0,0,2]);self.w=self.f.worker(clock=lambda:next(values))
        r=self.work(max_seconds=1)
        self.assertEqual(r['dispatches'],0);self.assertEqual(r['stop_reason'],'deadline')
        self.assertEqual(self.f.f.f.g.calls.count(REVOKE_URL),0)
        self.assertNotEqual(len(self.f.service_bucket.versions),len(self.f.service_bucket.live))
    def test_slow_component_stops_next_dispatch(self):
        self.ready();t=[0];self.w=self.f.worker(clock=lambda:t[0]);real=self.w._invoke
        def slow(*a):r=real(*a);t[0]=10;return r
        with patch.object(self.w,'_invoke',side_effect=slow):r=self.work(max_seconds=1)
        self.assertEqual(r['dispatches'],1);self.assertEqual(r['stop_reason'],'deadline');self.assertTrue(r['deadline_is_cooperative'])
    def test_pause_during_intent_write_stops_dispatch(self):
        self.ready();real=self.f.audit.compare_exchange
        def pause(k,g,v):
            r=real(k,g,v)
            if v.get('lane')=='service_current':self.w.set_paused(self.person,paused=True,expected_revision=0)
            return r
        with patch.object(self.f.audit,'compare_exchange',side_effect=pause):r=self.work()
        self.assertEqual(r['dispatches'],0);self.assertEqual(r['stop_reason'],'paused')
    def test_lost_checkpoint_acknowledgment_and_restart(self):
        self.ready();real=self.f.store.compare_exchange
        def lost(*a):real(*a);raise StateUnavailable('lost response')
        with patch.object(self.f.store,'compare_exchange',side_effect=lost):self.work(max_steps=1)
        r,_=self.f.finish();self.assertTrue(all(v=='observed' for v in r['states'].values()))
    def test_observation_audit_loss_resumes_component_without_reinference(self):
        self.ready();real=self.f.audit.compare_exchange
        def fail(k,g,v):
            if v.get('step')==1 and 'receipts' in v:raise StateUnavailable('observation lost')
            return real(k,g,v)
        with patch.object(self.f.audit,'compare_exchange',side_effect=fail):
            with self.assertRaises(StateUnavailable):self.work(max_steps=1)
        self.f.finish();self.assertEqual(self.f.f.calls,1)
    def test_missing_original_audit_never_recreated(self):
        self.ready();self.work(max_steps=1);real=self.f.audit.load
        k=key('disposition-worker-plan',digest(self.w._plan(self.person)));before=self.snap()
        with patch.object(self.f.audit,'load',side_effect=lambda name:None if name==k else real(name)):
            with self.assertRaises(StateUnavailable):self.work()
        self.assertEqual(before,self.snap())
    def test_corrupt_checkpoint_never_reset(self):
        self.ready();self.work(max_steps=1);p=self.w._plan(self.person);k=key('account-disposition-worker',digest(p))
        rev,j=self.f.store.load(k);self.f.store.compare_exchange(k,rev,dict(j,next=True));before=self.snap()
        with self.assertRaises(StateUnavailable):self.work()
        self.assertEqual(before,self.snap())
    def test_two_workers_preserve_original_plan_and_single_model(self):
        self.ready()
        def work(_):
            try:return self.f.worker().run(self.person,apply=True,max_steps=6,limit=2)
            except StateUnavailable:return None
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(work,range(2)))
        r,_=self.f.finish();self.assertEqual(self.f.f.calls,1)
        self.assertEqual(self.f.f.f.g.calls.count(REVOKE_URL),2);self.assertFalse(r['account_deleted'])
    def test_idle_observations_create_no_more_audit_or_provider_calls(self):
        self.ready();self.f.finish();before=self.snap();r=self.work()
        self.assertEqual(r['dispatches'],0);self.assertEqual(r['stop_reason'],'observations_complete');self.assertEqual(before,self.snap())
    def test_status_never_calls_media_apple_or_kms(self):
        self.ready();self.f.finish();before=self.snap()
        with patch.object(self.f.service_bucket,'list_blobs',side_effect=AssertionError('status listing')):
            self.w.status(self.person)
        self.assertEqual(before,self.snap())
    def test_soft_versions_stay_pending_and_explicit_rescan(self):
        self.ready()
        self.f.service_bucket._properties['softDeletePolicy']={'retentionDurationSeconds':'604800'}
        self.f.h.bucket._properties['softDeletePolicy']={'retentionDurationSeconds':'604800'}
        r,_=self.f.finish();self.assertGreater(r['observations']['research_history']['soft_pending_slots'],0)
        self.assertGreater(r['observations']['service_history']['soft_pending_operations'],0)
        self.f.h.bucket.soft.clear();self.f.service_bucket.soft.clear()
        self.assertGreater(self.w.status(self.person)['observations']['research_history']['soft_pending_slots'],0)
        for lane in ('research_history','service_history'):self.w.request_rescan(self.person,lane=lane)
        r,_=self.f.finish()
        self.assertEqual(r['observations']['research_history']['round'],2);self.assertEqual(r['observations']['research_history']['soft_pending_slots'],0)
        self.assertEqual(r['observations']['service_history']['round'],2);self.assertEqual(r['observations']['service_history']['soft_pending_operations'],0)
    def test_rescan_request_is_idempotent_until_dispatched(self):
        self.ready();self.f.finish();r=self.w.request_rescan(self.person,lane='service_history')
        before=rows(self.f.audit);self.assertEqual(self.w.request_rescan(self.person,lane='service_history'),r)
        self.assertEqual(before,rows(self.f.audit))
    def test_rescan_response_lost_does_not_start_third_round(self):
        self.ready();self.f.finish();self.w.request_rescan(self.person,lane='service_history')
        real=self.f.audit.compare_exchange
        def lost(k,g,v):
            if 'round_targets' in v and v['round_targets']['service_history'] is None and v['receipts']['service_history']['round']==2:
                raise StateUnavailable('lost worker checkpoint')
            return real(k,g,v)
        with patch.object(self.f.audit,'compare_exchange',side_effect=lost):
            with self.assertRaises(StateUnavailable):self.work()
        r,_=self.f.finish();self.assertEqual(r['observations']['service_history']['round'],2)
    def test_two_history_scopes_cannot_alias(self):
        self.f.service.jobs=self.f.h.jobs
        with self.assertRaises(ValueError):self.f.worker()
    def test_missing_component_proof_blocks_only_that_lane(self):
        self.ready();self.work(max_steps=1);self.f.service.d.audit=self.f.f.store('missing-seal-proof')
        r=self.work(max_steps=20);self.assertEqual(r['states']['service_history'],'blocked')
        self.assertEqual(r['observations']['apple_revocation']['apple_revocation'],'confirmed')
    def test_invalid_limits_and_flags_have_no_effect(self):
        self.ready();before=self.snap()
        for kw in ({'max_steps':True},{'max_steps':101},{'limit':0},{'max_seconds':float('nan')},{'max_seconds':False},{'apply':1}):
            with self.assertRaises(ValueError):self.w.run(self.person,**kw)
        self.assertEqual(before,self.snap())
    def test_independent_scopes_and_real_components_required(self):
        for store in (self.f.store,self.f.f.progress.store,self.f.h.jobs):
            with self.assertRaises(ValueError):AccountDispositionWorker(progress=self.f.f.progress,
                service_history=self.f.service,research_history=self.f.h.h,store=store,audit_store=store)
        with self.assertRaises(ValueError):AccountDispositionWorker(progress=object(),service_history=self.f.service,
            research_history=self.f.h.h,store=self.f.store,audit_store=self.f.audit)
    def test_unknown_error_stops_instead_of_swallowing_programming_bug(self):
        self.ready()
        with patch.object(self.w,'_invoke',side_effect=RuntimeError('bug')):
            with self.assertRaises(RuntimeError):self.work()
        self.assertEqual(self.f.f.f.g.calls.count(REVOKE_URL),0)


class ServiceScanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):native.legacy.LinksTests.setUpClass()
    def setUp(self):self.f=WorkerFixture(self);self.s=self.f.service
    def ready(self):self.f.ready();self.f.f.results.advance(self.f.person)
    def test_account_cursor_waits_for_all_versions_then_moves(self):
        for i in range(8):self.f.f.f.lifecycle.services.reserve(self.f.person,f'{100+i:032x}')
        self.ready();r=self.s.advance_account(self.f.person,versions=1,apply=True)
        self.assertEqual(r['operations_checked'],0)
        for _ in range(60):
            r=self.s.advance_account(self.f.person,versions=1,apply=True)
            if r['scan_finished']:break
        self.assertTrue(r['scan_finished']);self.assertEqual(r['operations_checked'],9)
    def test_read_only_and_idle_account_scans_preserve_audit(self):
        self.ready();before=rows(self.s.jobs),rows(self.s.audit)
        self.s.advance_account(self.f.person);self.assertEqual(before,(rows(self.s.jobs),rows(self.s.audit)))
        for _ in range(10):self.s.advance_account(self.f.person,apply=True)
        before=rows(self.s.jobs),rows(self.s.audit);self.s.advance_account(self.f.person,apply=True)
        self.assertEqual(before,(rows(self.s.jobs),rows(self.s.audit)))
    def test_missing_scan_checkpoint_evidence_stops(self):
        self.ready();self.s.advance_account(self.f.person,apply=True);self.s.audit=self.f.f.store('missing-scan-audit')
        with self.assertRaises(StateUnavailable):self.s.advance_account(self.f.person,apply=True)
    def test_active_scan_cannot_reset(self):
        self.ready();self.s.advance_account(self.f.person,apply=True)
        with self.assertRaises(StateUnavailable):self.s.advance_account(self.f.person,apply=True,rescan=True)
    def test_corrupt_scan_counts_not_repaired(self):
        self.ready();self.s.advance_account(self.f.person,apply=True);p=self.s._scan_plan(self.f.person)
        k=key('service-history-scan',digest(p));rev,j=self.s.jobs.load(k)
        self.s.jobs.compare_exchange(k,rev,dict(j,checked=True))
        with self.assertRaises(StateUnavailable):self.s.advance_account(self.f.person,apply=True)


if __name__=='__main__':unittest.main()
