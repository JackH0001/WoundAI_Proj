"""Durable account admission, new enrollment and >100-account synthetic rehearsal."""
import copy
import unittest
from unittest.mock import patch
from dataclasses import asdict
from lite_attest_state import StateUnavailable
from lite_usage_ledger import UsageConflict,digest
from lite_service_operations import ServiceAccounts
from lite_subscription_executor import SubscriptionMigrationExecutor,StoredObservation,make_manifest,key
from lite_subscription_runtime import SubscriptionRuntime
import test_lite_subscription_executor as fixtures
from test_lite_subscription_grace import rows


class RuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):fixtures.ExecutorTests.setUpClass()
    def setUp(self):self.f=fixtures.Fixture(self);self.f.active();self.r=self.f.executor.runtime
    def new(self,n):
        self.f.accounts.provision('new-'+str(n),'new-subject-'+str(n),f'30000000-0000-4000-8000-{n:012d}',None,'9'*64)
        return 'new-'+str(n)
    def grant(self,d):return self.f.accounts.grant(self.f.accounts.resolve(d),self.f.a.now)
    def batch(self,devices,name):
        f=self.f;m=make_manifest(f.accounts.subscription_links,f.accounts,devices,namespace=name,source=fixtures.SOURCE,
            image=fixtures.IMAGE,inventory='b'*64,fence='f'*64,operation=digest(name))
        e=SubscriptionMigrationExecutor(manifest=m,links=f.accounts.subscription_links,registry=f.accounts,
            state_store=f.control,audit_store=f.audit,observation=StoredObservation(f.observations,f.evidence,name),
            evidence=f.evidence,expected_source=fixtures.SOURCE,expected_image=fixtures.IMAGE)
        p=dict(kind='lite-migration-observation/1',namespace=name,manifest=digest(m),source=m['source'],
            image=m['image'],inventory=m['inventory'],fence=m['fence'],legacy_disabled=True,legacy_inflight=0,
            migration_inflight=0,stable_reader=True,issued_at=f.a.now,expires_at=f.a.now+30)
        f.observations.compare_exchange(key('observation',name),0,f.sign(p,'observation'))
        a=f.sign(dict(kind='lite-migration-approval/1',manifest=digest(m),action='start',expected_state=digest(None),
            issued_at=f.a.now,expires_at=f.a.now+900,nonce=digest(name)))
        return e,a

    def test_new_verified_account_after_activation_uses_free_without_apple(self):
        with patch.object(self.f.a.client,'get_all_subscription_statuses',side_effect=AssertionError('no purchase')):
            device=self.new(1);self.assertEqual(self.grant(device).limit,2)
        self.assertTrue(self.r.allowed(self.f.accounts.resolve(device)))

    def test_second_verified_device_uses_same_stable_account_and_usage_not_reset(self):
        device=self.new(2);a=self.f.accounts.registration(device);before=rows(self.f.base.f.ledger.store)
        self.f.accounts.provision('new-second',a['subject'],a['token'],None,'8'*64)
        self.assertEqual(self.grant('new-second').subject,self.grant(device).subject)
        self.assertEqual(before,rows(self.f.base.f.ledger.store))

    def test_second_batch_does_not_block_first_while_running_or_paused(self):
        d=self.new(3);e,a=self.batch([d],'second');real=e.links.execute
        def interrupted(*args,**kwargs):
            self.assertEqual(self.grant('first-device').limit,10)
            real(*args,**kwargs);raise StateUnavailable('response lost')
        with patch.object(e.links,'execute',side_effect=interrupted),self.assertRaises(StateUnavailable):e.advance(a)
        self.assertEqual(self.grant('first-device').limit,10)
        with self.assertRaises(StateUnavailable):self.grant(d)
        p=self.f.sign(dict(kind='lite-migration-approval/1',manifest=e.identity,action='resume',expected_state=e.state_digest(),
            issued_at=self.f.a.now,expires_at=self.f.a.now+900,nonce='a'*64))
        e.advance(p,resume=True);self.assertEqual(self.grant(d).limit,2)

    def test_lost_claim_audit_resumes_same_batch_without_losing_account(self):
        d=self.new(30);e,a=self.batch([d],'lost-claim');real=self.f.audit.compare_exchange
        def fail(k,rev,v):
            if set(v)=={'v','subject_hash','token_hash','manifest','namespace'}:raise StateUnavailable('audit offline')
            return real(k,rev,v)
        with patch.object(self.f.audit,'compare_exchange',side_effect=fail),self.assertRaises(StateUnavailable):e.advance(a)
        with self.assertRaises(StateUnavailable):self.grant(d)
        self.assertEqual(self.grant('first-device').limit,10)
        resume=self.f.sign(dict(kind='lite-migration-approval/1',manifest=e.identity,action='resume',expected_state=e.state_digest(),
            issued_at=self.f.a.now,expires_at=self.f.a.now+900,nonce='a'*64))
        e.advance(resume,resume=True);self.assertEqual(self.grant(d).limit,2)

    def test_205_accounts_in_three_batches_and_later_account_are_available(self):
        devices=[self.new(n) for n in range(100,305)]
        for i in range(0,len(devices),100):
            e,a=self.batch(devices[i:i+100],'many-'+str(i));e.advance(a)
            self.assertEqual(self.grant('first-device').limit,10)
        for d in devices:self.assertEqual(self.grant(d).limit,2)
        self.assertEqual(self.grant(self.new(999)).limit,2)

    def test_restart_does_not_need_in_memory_batch_membership(self):
        d=self.new(4);f=self.f
        registry=ServiceAccounts(f.accounts.store,subscription_links=f.accounts.subscription_links)
        r=SubscriptionRuntime(links=registry.subscription_links,registry=registry,state_store=f.control,
            audit_store=f.audit,observation_store=f.observations,evidence=f.evidence,
            expected_source=fixtures.SOURCE,expected_image=fixtures.IMAGE)
        self.assertTrue(r.allowed(registry.resolve(d)));self.assertTrue(r.allowed(registry.resolve('first-device')))

    def test_existing_completed_outside_batch_can_use_activated_policy(self):
        d=self.new(5);self.assertNotIn(d,[x['installation'] for x in self.f.manifest['entries']])
        self.assertEqual(self.grant(d).limit,2)

    def test_no_activation_blocks_free_bootstrap_and_preserves_registration(self):
        f=fixtures.Fixture(self);before=rows(f.accounts.store)
        with self.assertRaises(StateUnavailable):f.accounts.provision('new','subject','40000000-0000-4000-8000-000000000001',None,'a'*64)
        self.assertEqual(len(rows(f.accounts.store)),len(before)+1)
        f.active();f.accounts.provision('new','subject','40000000-0000-4000-8000-000000000001',None,'a'*64)
        self.assertEqual(f.accounts.grant(f.accounts.resolve('new'),f.a.now).limit,2)

    def test_bootstrap_lost_completion_reuses_plan(self):
        f=self.f;real=f.links.audit.compare_exchange
        def lost(k,rev,v):
            result=real(k,rev,v)
            if v.get('kind')=='subscription-policy-transition-confirmed':raise StateUnavailable('lost complete')
            return result
        with patch.object(f.links.audit,'compare_exchange',side_effect=lost),self.assertRaises(StateUnavailable):self.new(6)
        before=rows(f.links.store);self.new(6)
        self.assertEqual(self.grant('new-6').limit,2);self.assertEqual(before,rows(f.links.store))

    def test_bootstrap_does_not_downgrade_paid_or_replace_account(self):
        before=rows(self.f.links.store);self.r.bootstrap('first-device')
        self.assertEqual(before,rows(self.f.links.store));self.assertEqual(self.grant('first-device').limit,10)
        with self.assertRaises(UsageConflict):self.f.accounts.provision('first-device','attacker',
            '40000000-0000-4000-8000-000000000001',None,'a'*64)

    def test_expired_observer_denies_new_and_recovers_without_state_write(self):
        d=self.new(7);before=rows(self.f.control);self.f.a.now+=30
        with self.assertRaises(StateUnavailable):self.grant(d)
        self.f.publish();self.assertEqual(self.grant(d).limit,2);self.assertEqual(before,rows(self.f.control))

    def test_policy_missing_or_corrupt_does_not_fall_back_free(self):
        k=self.r._policy_key(self.r.links.active);old=self.r.store.load(k)
        self.r.store.compare_exchange(k,old[0],dict(v=1))
        with self.assertRaises(StateUnavailable):self.grant('first-device')

    def test_unknown_installation_never_created_by_bootstrap(self):
        before=rows(self.f.accounts.store)
        with self.assertRaises(StateUnavailable):self.r.bootstrap('unknown')
        self.assertEqual(before,rows(self.f.accounts.store))

    def test_same_account_concurrent_batches_cannot_steal_pause(self):
        d=self.new(8);e,a=self.batch([d],'race-a');other,b=self.batch([d],'race-b')
        with patch.object(e.links,'execute',side_effect=StateUnavailable('stop')),self.assertRaises(StateUnavailable):e.advance(a)
        with self.assertRaises(StateUnavailable):other.advance(b)
        with self.assertRaises(StateUnavailable):self.grant(d)
        self.assertEqual(self.grant('first-device').limit,10)

    def test_batch_runtime_does_not_replace_core_callbacks(self):
        before=self.f.accounts.subscription_links.authorize;d=self.new(9)
        self.batch([d],'callback');self.assertIs(self.f.accounts.subscription_links.authorize,before)

    def test_fresh_stable_launch_requires_signed_empty_inventory(self):
        f=fixtures.Fixture(self);r=f.executor.runtime;m=copy.deepcopy(f.manifest);m['entries']=[];m['namespace']='fresh'
        p=dict(kind='lite-migration-observation/1',namespace='fresh',manifest=digest(m),source=m['source'],image=m['image'],
            inventory=m['inventory'],fence=m['fence'],legacy_disabled=True,legacy_inflight=0,migration_inflight=0,
            stable_reader=True,issued_at=f.a.now,expires_at=f.a.now+30)
        f.observations.compare_exchange(key('observation','fresh'),0,f.sign(p,'observation'))
        a=f.sign(dict(kind='lite-migration-approval/1',manifest=digest(m),action='activate-fresh',expected_state=digest(None),
            issued_at=f.a.now,expires_at=f.a.now+900,nonce='a'*64))
        with self.assertRaises(Exception):r.activate_fresh(m,{})
        r.activate_fresh(m,a)
        f.accounts.provision('fresh-new','fresh-subject','50000000-0000-4000-8000-000000000001',None,'8'*64)
        self.assertEqual(f.accounts.grant(f.accounts.resolve('fresh-new'),f.a.now).limit,2)
        # Existing v1 subject must not be silently imported by free enrollment.
        with self.assertRaises(UsageConflict):r.bootstrap('first-device')

if __name__=='__main__':unittest.main()
