"""Durable-release integration with real signatures/CAS, synthetic providers only."""
import copy
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from lite_attest_state import SQLiteStateStore, GCSStateStore, StateUnavailable
from lite_usage_ledger import UsageLedger, UsageConflict, digest, free_grant
from lite_service_operations import ServiceAccounts
from lite_subscription_runtime import SubscriptionRuntime
from lite_subscription_executor import key, EvidenceContradiction, EvidenceUnavailable
from lite_usage_rollout import UsageRollout
from lite_service_release import ServiceReleaseReader, ServiceReleaseController, make_manifest, marker_key
from test_lite_attest_state import FakeBucket
from test_lite_usage_rollout import SyntheticProvider
from test_lite_usage_recovery import manifest
from test_lite_subscription_grace import rows
import test_lite_native_purchase as native


class Fixture:
    def __init__(self, host, gcs=False):
        self.n = native.Fixture(host); n = self.n; self.serial = 0
        self.buckets = []
        def store(name):
            if gcs:
                bucket = FakeBucket(); self.buckets.append(bucket)
                return GCSStateStore(bucket, prefix='synthetic-' + name)
            return SQLiteStateStore(n.f.root / ('release-' + name + '.sqlite'))
        self.store, self.audit = store('control'), store('audit')
        self.quota_stores = {k:store(k) for k in ('rollout','quota-audit','cutover','usage')}
        self.ledger = UsageLedger(self.quota_stores['usage'], namespace='release-usage', clock=lambda:n.a.now)
        self.baselines = [dict(subject=n.f.subject, successes=0, attempts=0)]
        m = manifest('release-usage', n.a.now); m['baseline_sha256'] = digest(self.baselines)
        m['writer_fence_sha256'] = n.m['fence']
        self.provider = SyntheticProvider(m)
        self.rollout = UsageRollout(store=self.quota_stores['rollout'], audit_store=self.quota_stores['quota-audit'],
            control_store=self.quota_stores['cutover'], ledger=self.ledger, provider=self.provider, manifest=m, baselines=self.baselines)
        self.ledger.cutover = self.rollout.policy(); self.rollout.execute()
        self.manifest = make_manifest(n.r, self.rollout, namespace='synthetic-service')
        self.reader = ServiceReleaseReader(store=self.store, audit_store=self.audit, manifest=self.manifest)
        self.controller = ServiceReleaseController(reader=self.reader, runtime=n.r, rollout=self.rollout, evidence=n.evidence)
        n.r.service_release = self.reader; self.rollout.service_release = self.reader

    def approval(self, action='publish', **changes):
        self.serial += 1
        row = self.reader.snapshot()
        p = dict(kind='lite-migration-approval/1', manifest=self.reader.identity, action='service-release-' + action,
            expected_state=digest(row[1] if row else None), issued_at=self.n.a.now, expires_at=self.n.a.now+900,
            nonce=f'{self.serial:064x}')
        p.update(changes); return self.n.sign(p)

    def active(self): return self.controller.apply(self.approval())
    def grant(self): return self.n.accounts.grant(self.n.accounts.resolve('first-device'), self.n.a.now)
    def fresh(self): self.n.publish()
    def reserve(self, op='1'*32):
        a = self.n.accounts.resolve('first-device')
        self.ledger._ensure_current_day(a['subject'], self.n.a.now//86400)
        return self.ledger.reserve(a['subject'], self.n.a.now//86400, op, 'c'*64, self.grant())
    def use(self, op='1'*32):
        t = self.reserve(op)
        ran = self.ledger.claim(t,'b'*32)
        if ran: self.ledger.settle(t,'b'*32,'succeeded','d'*64)
        return ran
    def snapshot(self): return self.ledger.snapshot(self.n.f.subject, self.n.a.now//86400, free_grant(self.n.f.subject,self.n.a.now))
    def restart(self):
        n=self.n
        registry=ServiceAccounts(n.accounts.store,subscription_links=n.links)
        reader=ServiceReleaseReader(store=self.store,audit_store=self.audit,manifest=self.manifest)
        r=SubscriptionRuntime(links=n.links,registry=registry,state_store=n.state,audit_store=n.audit,
            observation_store=n.observer,evidence=n.evidence,expected_source=n.r.source,expected_image=n.r.image,
            service_release=reader)
        return r,registry


class ReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): native.NativePurchaseTests.setUpClass()
    def setUp(self): self.f=Fixture(self)
    def assertBlocked(self):
        with self.assertRaises(StateUnavailable):self.f.grant()
        self.assertFalse(self.f.rollout.admission_allowed(self.f.rollout.manifest))

    def test_unpublished_reader_cannot_serve_even_with_fresh_observer(self): self.assertBlocked()

    def test_fresh_publication_does_not_mutate_account_or_usage(self):
        f=self.f; before=[rows(s) for s in (f.n.accounts.store,f.n.links.store)]
        quota=f.ledger._load(f.n.f.subject,f.n.a.now//86400)
        f.active();self.assertEqual(f.grant().limit,2)
        self.assertEqual(before,[rows(s) for s in (f.n.accounts.store,f.n.links.store)])
        self.assertEqual(quota,f.ledger._load(f.n.f.subject,f.n.a.now//86400))

    def test_stale_observer_48_hours_does_not_block_new_day_or_repeat_model(self):
        f=self.f;f.active();f.n.a.now+=172800
        with (patch.object(f.n.observer,'load',side_effect=AssertionError('no daily observer reads')),
              patch.object(f.provider,'observe',side_effect=AssertionError('no daily provider polling'))):
            self.assertTrue(f.use());self.assertFalse(f.use())
            self.assertEqual((f.snapshot()['used'],f.snapshot()['attempts']),(1,1))
            f.n.accounts.provision('new','fresh-human','40000000-0000-4000-8000-000000000001',None,'a'*64)
            self.assertEqual(f.n.accounts.grant(f.n.accounts.resolve('new'),f.n.a.now).limit,2)

    def test_native_purchase_restore_after_observer_expiry(self):
        f=self.f;f.active();f.n.a.now+=31
        before=f.ledger._load(f.n.f.subject,f.n.a.now//86400)
        first=f.n.confirm(); self.assertEqual(f.grant().limit,10)
        with patch.object(f.n.a.client,'get_all_subscription_statuses',side_effect=AssertionError('no repeated Apple')):
            self.assertEqual(first,f.n.confirm(device='second-device'))
        self.assertEqual(before,f.ledger._load(f.n.f.subject,f.n.a.now//86400))

    def test_other_instance_immediately_sees_pause_no_allow_cache(self):
        f=self.f;f.active();r,registry=f.restart();a=registry.resolve('first-device')
        self.assertTrue(r.allowed(a));f.controller.apply(f.approval('pause'))
        self.assertFalse(r.allowed(a));self.assertBlocked()

    def test_pause_without_observer_and_resume_requires_fresh_proof(self):
        f=self.f;f.active();f.n.a.now+=31;f.controller.apply(f.approval('pause'))
        with self.assertRaises(EvidenceUnavailable):f.controller.apply(f.approval('resume'))
        self.assertBlocked();f.fresh();f.controller.apply(f.approval('resume'));self.assertTrue(f.use())

    def test_old_publication_cannot_resume_a_later_pause(self):
        f=self.f;a=f.approval();f.controller.apply(a);f.controller.apply(f.approval('pause'))
        with self.assertRaises(EvidenceContradiction):f.controller.apply(a)
        self.assertBlocked()

    def test_duplicate_publication_is_read_only(self):
        f=self.f;a=f.approval();first=f.controller.apply(a)
        before=f.reader.snapshot();self.assertEqual(f.controller.apply(a),first)
        self.assertEqual(before,f.reader.snapshot())

    def test_expired_tampered_observer_signed_approvals_rejected(self):
        f=self.f;a=f.approval();a['payload']['nonce']='0'*64
        for value in (a, f.approval(expires_at=f.n.a.now), f.n.sign(f.approval()['payload'],'observer')):
            with self.subTest(value=value['key_id']),self.assertRaises(EvidenceUnavailable):f.controller.apply(value)
        self.assertIsNone(f.reader.snapshot())

    def test_wrong_manifest_or_state_approval_cannot_publish(self):
        for kw in ({'manifest':'0'*64},{'expected_state':'1'*64}):
            with self.subTest(kw=kw),self.assertRaises(EvidenceContradiction):self.f.controller.apply(self.f.approval(**kw))
        self.assertIsNone(self.f.reader.snapshot())

    def test_provider_observation_unavailable_never_publishes(self):
        f=self.f
        with patch.object(f.provider,'observe',side_effect=TimeoutError()),self.assertRaises(StateUnavailable):f.active()
        self.assertIsNone(f.reader.snapshot());self.assertBlocked()

    def test_provider_contradiction_never_publishes(self):
        f=self.f;f.provider.wrong='baseline_sha256'
        with self.assertRaises(StateUnavailable):f.active()
        self.assertIsNone(f.reader.snapshot())

    def test_health_outage_does_not_revoke_or_write_control(self):
        f=self.f;f.active();before=f.reader.snapshot();f.n.a.now+=31
        self.assertEqual(f.controller.health()['status'],'observation-unavailable')
        self.assertEqual(f.reader.snapshot(),before);self.assertTrue(f.use())

    def test_confirmed_contradiction_pauses_and_does_not_auto_resume(self):
        f=self.f;f.active();f.provider.legacy_disabled=False
        self.assertEqual(f.controller.health()['status'],'paused-contradiction');self.assertBlocked()
        state=f.reader.snapshot()[1];intent=f.audit.load(key('service-release-intent',state['intent']))[1]
        self.assertIs(intent['proof']['observation']['legacy_disabled'],False)
        f.provider.legacy_disabled=True
        self.assertEqual(f.controller.health()['status'],'inactive');self.assertBlocked()
        f.controller.apply(f.approval('resume'));self.assertTrue(f.use())

    def test_cas_reply_lost_reuses_intent_epoch_and_confirmation(self):
        f=self.f;a=f.approval();real=f.store.compare_exchange
        def lost(*args): real(*args);raise StateUnavailable('reply lost')
        with patch.object(f.store,'compare_exchange',side_effect=lost),self.assertRaises(StateUnavailable):f.controller.apply(a)
        self.assertBlocked();state=f.reader.snapshot()
        f.controller.apply(a);self.assertEqual(state,f.reader.snapshot());self.assertTrue(f.use())

    def test_lost_confirmation_can_be_repaired_after_approval_expired(self):
        f=self.f;a=f.approval();real=f.audit.compare_exchange
        def lost(k,rev,v):
            if set(v)=={'v','release','epoch','phase','intent'}:raise StateUnavailable('audit outage')
            return real(k,rev,v)
        with patch.object(f.audit,'compare_exchange',side_effect=lost),self.assertRaises(StateUnavailable):f.controller.apply(a)
        state=f.reader.snapshot();self.assertBlocked()
        with self.assertRaises(StateUnavailable):f.controller.health()
        f.n.a.now+=901;f.fresh()
        with self.assertRaises(EvidenceUnavailable):f.controller.apply(a)
        f.controller.apply(f.approval('confirm'));self.assertEqual(state,f.reader.snapshot());self.assertTrue(f.use())

    def test_control_unavailable_has_no_cached_allow(self):
        f=self.f;f.active();self.assertEqual(f.grant().limit,2)
        with patch.object(f.store,'load',side_effect=StateUnavailable('offline')):self.assertBlocked()
        self.assertEqual(f.grant().limit,2)

    def test_audit_unavailable_has_no_cached_allow(self):
        f=self.f;f.active()
        with patch.object(f.audit,'load',side_effect=StateUnavailable('offline')):self.assertBlocked()

    def test_corrupt_state_denies_without_replacing_data(self):
        f=self.f;f.active();old=f.reader.snapshot();f.store.compare_exchange(f.reader.key,old[0],{'v':1})
        before=f.store.load(f.reader.key);self.assertBlocked();self.assertEqual(before,f.store.load(f.reader.key))

    def test_markers_prevent_injection_removal_falling_back_to_old_observer(self):
        f=self.f;f.active();f.n.r.service_release=None;f.rollout.service_release=None
        f.fresh();self.assertBlocked()

    def test_partial_marker_failure_blocks_then_same_signed_publication_recovers(self):
        f=self.f;a=f.approval();real=f.rollout.store.compare_exchange
        def fail(k,rev,v):
            if k==marker_key('usage',f.rollout.manifest['namespace']):raise StateUnavailable('second marker')
            return real(k,rev,v)
        with patch.object(f.rollout.store,'compare_exchange',side_effect=fail),self.assertRaises(StateUnavailable):f.controller.apply(a)
        self.assertBlocked();f.controller.apply(a);self.assertTrue(f.use())

    def test_missing_marker_or_wrong_namespace_denies(self):
        f=self.f;f.active();k=marker_key('subscription',f.n.r.scope);v=f.n.state.load(k)
        f.n.state.compare_exchange(k,v[0],dict(v=1,namespace='other'))
        with self.assertRaises(StateUnavailable):f.grant()

    def test_usage_rollout_pause_still_denies_with_active_release(self):
        f=self.f;f.active();f.rollout.rollback()
        self.assertFalse(f.rollout.admission_allowed(f.rollout.manifest))
        with self.assertRaises(StateUnavailable):f.reserve()

    def test_inflight_result_settles_once_after_pause_without_reset(self):
        f=self.f;f.active();t=f.reserve();self.assertTrue(f.ledger.claim(t,'b'*32))
        f.controller.apply(f.approval('pause'))
        first=f.ledger.settle(t,'b'*32,'succeeded','d'*64)
        self.assertEqual(first,f.ledger.settle(t,'b'*32,'succeeded','d'*64))
        self.assertEqual(f.snapshot()['used'],1);self.assertBlocked()

    def test_concurrent_same_publication_never_creates_two_epochs(self):
        f=self.f;a=f.approval()
        def apply(_):
            try:return f.controller.apply(a)
            except UsageConflict:return None
        with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(apply,range(4)))
        self.assertTrue(any(results));self.assertEqual(f.reader.snapshot()[1]['epoch'],1)
        self.assertEqual(f.grant().limit,2)

    def test_policy_catalog_or_source_drift_denies_existing_release(self):
        f=self.f;f.active();old=f.n.r.source;f.n.r.source='0'*40
        with self.assertRaises(StateUnavailable):f.grant()
        f.n.r.source=old;self.assertEqual(f.grant().limit,2)

    def test_subscription_fence_must_match_usage_fence(self):
        f=self.f;f.n.m['fence']='b'*64
        # Signed observer claims different fence but immutable activation origin remains pinned.
        f.n.publish()
        with self.assertRaises(EvidenceContradiction):f.active()

    def test_retained_audit_is_append_only_through_pause_resume(self):
        f=self.f;f.active();before=f.reader.snapshot()[1];f.controller.apply(f.approval('pause'))
        f.controller.apply(f.approval('resume'));f.reader.confirmed(before)
        self.assertEqual(f.reader.snapshot()[1]['epoch'],3)


    def test_quota_contradiction_is_not_hidden_by_expired_subscription_observer(self):
        f=self.f;f.active();f.n.a.now+=31;f.provider.legacy_disabled=False
        self.assertEqual(f.controller.health()['status'],'paused-contradiction');self.assertBlocked()

    def test_subscription_drift_contained_while_quota_observer_unavailable(self):
        f=self.f;f.active();f.n.m['fence']='e'*64;f.n.publish()
        with patch.object(f.provider,'observe',side_effect=TimeoutError()):
            self.assertEqual(f.controller.health()['status'],'paused-contradiction')
        self.assertBlocked()
        state=f.reader.snapshot()[1];intent=f.audit.load(key('service-release-intent',state['intent']))[1]
        observed=intent['proof']['observation']
        self.assertEqual(f.n.evidence.verify(observed,'observation')['fence'],'e'*64)

    def test_missing_quota_completion_audit_blocks_both_new_work_and_publication(self):
        f=self.f
        with patch.object(f.rollout.audit,'load',return_value=None),self.assertRaises(StateUnavailable):f.active()
        self.assertBlocked()

    def test_missing_quota_seal_blocks_publication(self):
        f=self.f
        with patch.object(f.rollout.control,'load',return_value=None),self.assertRaises(StateUnavailable):f.active()
        self.assertBlocked()

    def test_read_only_admission_store_handles_are_sufficient(self):
        f=self.f;f.active();f.n.a.now+=31
        class GET:
            def __init__(self,store):self.store=store
            def load(self,key):return self.store.load(key)
        f.n.r.service_release=ServiceReleaseReader(store=GET(f.store),audit_store=GET(f.audit),manifest=f.manifest)
        f.rollout.service_release=f.n.r.service_release
        self.assertTrue(f.use())

    def test_corrupt_confirmed_audit_denies_without_changing_account(self):
        f=self.f;f.active();r=f.reader.snapshot()[1];k=key('service-release-confirmed',digest(r))
        old=f.audit.load(k);f.audit.compare_exchange(k,old[0],dict(r,epoch=r['epoch']+1))
        self.assertBlocked()

    def test_approval_loses_pause_resume_race_cannot_overwrite_new_state(self):
        f=self.f;f.active();pause=f.approval('pause');f.controller.apply(pause)
        resume=f.approval('resume');f.controller.apply(resume);f.controller.apply(f.approval('pause'))
        with self.assertRaises(EvidenceContradiction):f.controller.apply(resume)
        self.assertEqual(f.reader.snapshot()[1]['epoch'],4);self.assertBlocked()

    def test_unchanged_release_can_be_confirmed_without_duplicating_epoch(self):
        f=self.f;f.active();old=f.reader.snapshot()
        f.controller.apply(f.approval('confirm'));self.assertEqual(old,f.reader.snapshot())

    def test_paused_release_cannot_be_resumed_if_previous_audit_is_missing(self):
        f=self.f;f.active();f.controller.apply(f.approval('pause'));real=f.audit.load
        state=f.reader.snapshot()[1];missing=key('service-release-confirmed',digest(state))
        with patch.object(f.audit,'load',side_effect=lambda k:None if k==missing else real(k)),self.assertRaises(StateUnavailable):
            f.controller.apply(f.approval('resume'))
        self.assertBlocked()

    def test_upgrade_requires_explicit_pause_and_pins_new_source(self):
        f=self.f;f.active();old_reader=f.reader
        f.controller.apply(f.approval('pause'))
        # A different reviewed image/source must have its own activated policy.
        new_source='1'*40;new_image='2'*64
        registry=ServiceAccounts(f.n.accounts.store,subscription_links=f.n.links)
        r=SubscriptionRuntime(links=f.n.links,registry=registry,state_store=f.n.state,audit_store=f.n.audit,
            observation_store=f.n.observer,evidence=f.n.evidence,expected_source=new_source,expected_image=new_image)
        m=dict(f.n.m,source=new_source,image=new_image,namespace='fresh-next')
        p=dict(kind='lite-migration-observation/1',namespace=m['namespace'],manifest=digest(m),source=m['source'],image=m['image'],
            inventory=m['inventory'],fence=m['fence'],legacy_disabled=True,legacy_inflight=0,migration_inflight=0,
            stable_reader=True,issued_at=f.n.a.now,expires_at=f.n.a.now+30)
        f.n.observer.compare_exchange(key('observation',m['namespace']),0,f.n.sign(p,'observer'))
        r.activate_fresh(m,f.n.sign(dict(kind='lite-migration-approval/1',manifest=digest(m),action='activate-fresh',
            expected_state=digest(None),issued_at=f.n.a.now,expires_at=f.n.a.now+900,nonce='b'*64)))
        proposed=make_manifest(r,f.rollout,namespace=f.manifest['namespace'])
        reader=ServiceReleaseReader(store=f.store,audit_store=f.audit,manifest=proposed)
        controller=ServiceReleaseController(reader=reader,runtime=r,rollout=f.rollout,evidence=f.n.evidence)
        r.service_release=reader
        with self.assertRaises(StateUnavailable):reader.require_active()
        before=reader.snapshot()[1]
        signed=f.n.sign(dict(kind='lite-migration-approval/1',manifest=reader.identity,action='service-release-upgrade',
            expected_state=digest(before),issued_at=f.n.a.now,expires_at=f.n.a.now+900,nonce='c'*64))
        controller.apply(signed);self.assertEqual(reader.require_active()['epoch'],3)
        self.assertEqual(registry.grant(registry.resolve('first-device'),f.n.a.now).limit,2)
        with self.assertRaises(StateUnavailable):old_reader.require_active()
        self.assertFalse(f.rollout.admission_allowed(f.rollout.manifest))
        f.rollout.service_release=reader
        self.assertTrue(f.rollout.admission_allowed(f.rollout.manifest))


    def test_ongoing_batch_still_blocks_only_its_accounts_with_fresh_transition_evidence(self):
        from lite_subscription_executor import make_manifest as batch_manifest, SubscriptionMigrationExecutor, StoredObservation
        f=self.f;f.active();n=f.n
        n.accounts.provision('new','other-human','40000000-0000-4000-8000-000000000002',None,'a'*64)
        m=batch_manifest(n.links,n.accounts,['new'],namespace='next-batch',source=n.r.source,image=n.r.image,
            inventory=n.m['inventory'],fence=n.m['fence'],operation='b'*64)
        executor=SubscriptionMigrationExecutor(manifest=m,links=n.links,registry=n.accounts,state_store=n.state,
            audit_store=n.audit,observation=StoredObservation(n.observer,n.evidence,m['namespace']),evidence=n.evidence,
            expected_source=n.r.source,expected_image=n.r.image)
        p=dict(kind='lite-migration-observation/1',namespace=m['namespace'],manifest=digest(m),source=m['source'],image=m['image'],
            inventory=m['inventory'],fence=m['fence'],legacy_disabled=True,legacy_inflight=0,migration_inflight=0,
            stable_reader=True,issued_at=n.a.now,expires_at=n.a.now+30)
        n.observer.compare_exchange(key('observation',m['namespace']),0,n.sign(p,'observer'))
        def approval(action):
            return n.sign(dict(kind='lite-migration-approval/1',manifest=digest(m),action=action,
                expected_state=executor.state_digest(),issued_at=n.a.now,expires_at=n.a.now+900,nonce='d'*64))
        with patch.object(executor.links,'execute',side_effect=StateUnavailable('batch interrupted')),self.assertRaises(StateUnavailable):
            executor.advance(approval('start'))
        self.assertEqual(f.grant().limit,2)
        with self.assertRaises(StateUnavailable):n.accounts.grant(n.accounts.resolve('new'),n.a.now)
        executor.advance(approval('resume'),resume=True)
        n.a.now+=31
        self.assertEqual(n.accounts.grant(n.accounts.resolve('new'),n.a.now).limit,2)
        self.assertEqual(f.grant().limit,2)


class ReleaseGCSTests(ReleaseTests):
    def setUp(self):self.f=Fixture(self,gcs=True)

if __name__=='__main__':unittest.main()
