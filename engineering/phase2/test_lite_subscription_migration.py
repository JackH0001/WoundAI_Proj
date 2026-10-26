"""Synthetic signed products, actual SQLite CAS, bounded fake-GCS transport."""
import copy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
import threading
import unittest
from unittest.mock import patch
import test_lite_subscription_authority as apple
import test_lite_service_subscription_links as legacy
from test_lite_subscription_grace import rows, enable_grace
from lite_attest_state import StateUnavailable, GCSStateStore
from lite_subscription_authority import AppleSubscriptionAuthority, SubscriptionRejected
from lite_service_operations import ServiceAccounts
from lite_service_subscription_links import ServiceSubscriptionLinks, SubscriptionMigrationRequired, SubscriptionLinkConflict, stable_subscription_key
from lite_subscription_migration import StableSubscriptionLinks
from lite_usage_ledger import digest, UsageConflict, UsageLimit
from test_lite_attest_state import FakeBucket

PRODUCT='com.woundai.lite.synthetic.next'  # no real App Store product is created
AUTH='a'*64
FENCE='f'*64


class MigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):legacy.LinksTests.setUpClass()

    def setUp(self):
        self.f=legacy.LinksTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.f.attach();self.a=self.f.a
        self.next=AppleSubscriptionAuthority(policy=replace(apple.POLICY,product=PRODUCT),verifier=self.a.verifier,
            client=self.a.client,proof_store=self.a.store,clock=lambda:self.a.now,
            allowed_products=(apple.POLICY.product,PRODUCT))
        self.old_id=digest(asdict(apple.POLICY));self.new_id=digest(asdict(self.next.policy))
        self.approved=set();self.fenced=True;self.control_allowed=True
        self.links=self.reader(self.old_id)
        self.accounts=ServiceAccounts(self.f.accounts.store,subscription_links=self.links)
        self.n=0

    def reader(self,active,authorities=None):
        return StableSubscriptionLinks(store=self.f.store,audit_store=self.f.audit,
            authorities=authorities or (self.a.a,self.next),active_policy=active,legacy_policies=(self.old_id,),
            authorize=lambda p,a:self.control_allowed and a==AUTH and digest(p) in self.approved,
            writer_fence=lambda proof:self.fenced and proof==FENCE,clock=lambda:self.a.now)

    def plan(self,device='first-device',original=None):
        self.n+=1
        p=self.links.plan(self.accounts,device,operation=f'{self.n:064x}',inventory_sha256='b'*64,
                          writer_fence_sha256=FENCE,original_transaction=original)
        self.approved.add(digest(p));return p

    def apply(self,p,device='first-device'):
        return self.links.execute(p,self.accounts,device,authorization_sha256=AUTH)

    def grant(self):return self.accounts.grant(self.accounts.resolve('first-device'),self.a.now)

    def target_new(self):
        self.links=self.reader(self.new_id);self.accounts=ServiceAccounts(self.f.accounts.store,subscription_links=self.links)
        self.a.payload['productId']=PRODUCT

    def test_baseline_new_policy_reader_loses_old_link_but_stable_reader_blocks_missing_bootstrap(self):
        old_style=ServiceSubscriptionLinks(store=self.f.store,audit_store=self.f.audit,authority=self.next,clock=lambda:self.a.now)
        self.assertIsNone(old_style.lookup(self.f.subject,self.f.token))
        with self.assertRaises(SubscriptionMigrationRequired):self.accounts.resolve('first-device')

    def test_import_preserves_legacy_registration_usage_and_proofs(self):
        before={r[0]:r for r in rows(self.f.store)};audit={r[0]:r for r in rows(self.f.audit)}
        protected=[rows(s) for s in (self.accounts.store,self.f.ledger.store)]
        receipt=self.apply(self.plan())
        self.assertEqual(self.grant().limit,10)
        self.assertNotIn('limit',receipt)
        self.assertEqual(protected,[rows(s) for s in (self.accounts.store,self.f.ledger.store)])
        self.assertTrue(before.items()<=dict((r[0],r) for r in rows(self.f.store)).items())
        self.assertTrue(audit.items()<=dict((r[0],r) for r in rows(self.f.audit)).items())

    def test_product_migration_keeps_identity_usage_and_old_completion(self):
        p=self.plan();r=self.apply(p);first_key=self.links.key(self.f.subject)
        before=rows(self.f.ledger.store);self.target_new();q=self.plan();self.apply(q)
        self.assertEqual(first_key,self.links.key(self.f.subject));self.assertEqual(self.grant().limit,10)
        self.assertEqual(before,rows(self.f.ledger.store));current=rows(self.f.store)
        with patch.object(self.a.client,'get_all_subscription_statuses',side_effect=TimeoutError()) as api:
            self.assertEqual(self.apply(p),r);self.assertEqual(api.call_count,0)
        self.assertEqual(current,rows(self.f.store))

    def test_two_devices_retain_same_original_and_shared_eight_remaining(self):
        self.apply(self.plan());self.target_new();self.apply(self.plan())
        for n in range(3,11):
            device='first-device' if n%2 else 'second-device'
            account=self.accounts.resolve(device);g=self.accounts.grant(account,self.a.now)
            t=self.f.ledger.reserve(self.f.subject,self.f.day,f'{n:032x}','c'*64,g)
            self.assertTrue(self.f.ledger.claim(t,f'{n:032x}'))
            self.f.ledger.settle(t,f'{n:032x}','succeeded','d'*64)
        with self.assertRaises(UsageLimit):self.f.ledger.reserve(self.f.subject,self.f.day,'f'*32,'c'*64,self.grant())
        self.assertEqual(self.f.ledger.snapshot(self.f.subject,self.f.day,self.grant())['used'],10)

    def test_legacy_reader_and_attach_block_after_stable_guard(self):
        self.apply(self.plan())
        with self.assertRaises(SubscriptionMigrationRequired):self.f.accounts.resolve('first-device')
        with self.assertRaises(SubscriptionMigrationRequired):self.f.attach()

    def test_rollback_reader_retains_binding_but_unknown_policy_never_grants_free(self):
        self.apply(self.plan());self.target_new();self.apply(self.plan())
        old=self.reader(self.old_id,(self.a.a,));accounts=ServiceAccounts(self.f.accounts.store,subscription_links=old)
        a=accounts.resolve('first-device');self.assertEqual(a['original_transaction'],self.f.original)
        with self.assertRaises(SubscriptionMigrationRequired):accounts.grant(a,self.a.now)

    def test_supported_legacy_product_still_verified_until_explicit_transition(self):
        self.apply(self.plan())
        self.links=self.reader(self.new_id);self.accounts=ServiceAccounts(self.f.accounts.store,subscription_links=self.links)
        self.assertEqual(self.grant().limit,10) # current Apple product is still old
        with self.assertRaises(SubscriptionRejected):self.apply(self.plan()) # not yet the new product
        self.assertEqual(self.grant().limit,10)

    def test_missing_or_bad_apple_proof_cannot_change_pointer(self):
        self.apply(self.plan());before=rows(self.f.store);self.target_new();p=self.plan()
        with patch.object(self.a.client,'get_all_subscription_statuses',side_effect=TimeoutError()):
            with self.assertRaises(StateUnavailable):self.apply(p)
        self.assertEqual(before,rows(self.f.store))
        self.a.payload['appAccountToken']='20000000-0000-4000-8000-000000000001'
        with self.assertRaises(SubscriptionRejected):self.apply(p)
        self.assertEqual(before,rows(self.f.store))

    def test_expired_refunded_or_retry_migration_does_not_restore_plus_or_usage(self):
        self.apply(self.plan());self.target_new();before=rows(self.f.ledger.store)
        self.a.status=3;self.a.payload['expiresDate']=(self.a.now-1)*1000
        self.apply(self.plan());self.assertEqual(self.grant().limit,2)
        self.a.status=5;self.assertEqual(self.grant().limit,2)
        self.assertEqual(before,rows(self.f.ledger.store))

    def test_new_product_valid_grace_is_bound_to_new_signed_renewal(self):
        self.apply(self.plan());self.target_new();renewal=enable_grace(self.a)
        renewal['productId']=PRODUCT;renewal['autoRenewProductId']=PRODUCT
        self.apply(self.plan());self.assertEqual(self.grant().limit,10)
        renewal['productId']=apple.POLICY.product
        with self.assertRaises(SubscriptionRejected):self.grant()

    def test_explicit_verified_unbound_bootstrap_does_not_mint_subscription(self):
        self.accounts.provision('free','new-free',apple.TOKEN,None,'a'*64)
        p=self.plan('free')
        with patch.object(self.a.client,'get_all_subscription_statuses',side_effect=TimeoutError()) as api:
            self.apply(p,'free');a=self.accounts.resolve('free')
            self.assertIsNone(a['original_transaction']);self.assertEqual(self.accounts.grant(a,self.a.now).limit,2)
            self.assertEqual(api.call_count,0)

    def test_unknown_account_not_enrolled_by_migration(self):
        before=rows(self.accounts.store)
        with self.assertRaises(StateUnavailable):self.plan('unknown')
        self.assertEqual(before,rows(self.accounts.store))

    def test_cross_account_token_or_original_replacement_is_rejected(self):
        p=self.plan()
        self.accounts.provision('other','other-account',apple.TOKEN,None,'a'*64)
        with self.assertRaises(SubscriptionLinkConflict):self.apply(p,'other')
        with self.assertRaises(SubscriptionLinkConflict):self.plan(original='999')
        self.accounts.provision('wrong-token',self.f.subject,'20000000-0000-4000-8000-000000000001',None,'a'*64)
        with self.assertRaises(StateUnavailable):self.plan('wrong-token')

    def test_scope_changes_forbidden_and_app_id_typo_does_not_select_empty_key(self):
        bad=copy.copy(self.next);bad.policy=replace(self.next.policy,app_id=123)
        with self.assertRaises(ValueError):self.reader(self.old_id,(self.a.a,bad))
        self.apply(self.plan())
        typo=StableSubscriptionLinks(store=self.f.store,audit_store=self.f.audit,authorities=(bad,),
            active_policy=digest(asdict(bad.policy)),legacy_policies=(),authorize=lambda *a:True,
            writer_fence=lambda *a:True,clock=lambda:self.a.now)
        self.assertEqual(typo.key(self.f.subject),self.links.key(self.f.subject))
        with self.assertRaises(StateUnavailable):typo.lookup(self.f.subject,self.f.token)

    def test_default_product_allowlist_not_widened(self):
        with self.assertRaises(ValueError):self.next.policy.validate()
        for products in ((),[PRODUCT],(PRODUCT,PRODUCT),(True,),([],),('other.app',)):
            with self.subTest(products=products),self.assertRaises(ValueError):self.next.policy.validate(products)

    def test_policy_catalog_cannot_partition_original_transaction_ownership(self):
        bad=copy.copy(self.next);bad.proofs=object()
        with self.assertRaises(ValueError):self.reader(self.old_id,(self.a.a,bad))

    def test_mutating_review_copy_does_not_mutate_reader_scope(self):
        p=self.plan();p['scope']['app_id']=123
        self.assertEqual(self.links.scope['app_id'],apple.POLICY.app_id)
        with self.assertRaises(StateUnavailable):self.apply(p)

    def test_provider_observation_cannot_mutate_approved_plan(self):
        p=self.plan();original=copy.deepcopy(p);authorize=self.links.authorize
        def observe(plan,proof):
            yes=authorize(plan,proof);plan['original_transaction']='999';return yes
        self.links.authorize=observe
        self.apply(p);self.assertEqual(p,original)
        self.assertEqual(self.accounts.resolve('first-device')['original_transaction'],self.f.original)

    def test_changed_legacy_snapshot_is_rejected_before_migration(self):
        p=self.plan();p['legacy_sources'][self.old_id]='c'*64;self.approved.add(digest(p))
        with self.assertRaises(SubscriptionLinkConflict):self.apply(p)
        self.assertIsNone(self.f.store.load(self.links.key(self.f.subject)))

    def test_new_migration_never_initializes_missing_next_day(self):
        self.apply(self.plan());self.a.now+=86400
        self.a.payload.update(purchaseDate=(self.a.now-100)*1000,expiresDate=(self.a.now+1000)*1000,signedDate=self.a.now*1000)
        before=rows(self.f.ledger.store);self.target_new();self.apply(self.plan())
        self.assertEqual(before,rows(self.f.ledger.store))
        with self.assertRaises(StateUnavailable):self.f.ledger.snapshot(self.f.subject,self.a.now//86400,self.grant())

    def test_authorization_fence_and_plan_mutation_block_before_writes(self):
        p=self.plan();before=[rows(s) for s in (self.f.store,self.f.audit)]
        self.control_allowed=False
        with self.assertRaises(StateUnavailable):self.apply(p)
        self.control_allowed=True;self.fenced=False
        with self.assertRaises(StateUnavailable):self.apply(p)
        self.fenced=True;p['inventory_sha256']='c'*64
        with self.assertRaises(StateUnavailable):self.apply(p)
        self.assertEqual(before,[rows(s) for s in (self.f.store,self.f.audit)])

    def test_stale_plan_cannot_overwrite_newer_migration(self):
        a=self.plan();b=self.plan();self.apply(a);before=rows(self.f.store)
        with self.assertRaises(SubscriptionLinkConflict):self.apply(b)
        self.assertEqual(before,rows(self.f.store))

    def test_same_plan_concurrent_retries_share_record_and_receipt(self):
        p=self.plan();barrier=threading.Barrier(4)
        def run(n):barrier.wait(10);return self.apply(p)
        with ThreadPoolExecutor(max_workers=4) as pool:out=list(pool.map(run,range(4)))
        self.assertTrue(all(x==out[0] for x in out))
        self.assertEqual(self.f.store.load(self.links.key(self.f.subject))[0],1)

    def test_competing_plans_use_one_pointer_cas_and_preserve_both_intents(self):
        plans=[self.plan(),self.plan()];barrier=threading.Barrier(2);real=self.f.store.compare_exchange
        def race(k,rev,value):
            if k==self.links.key(self.f.subject):barrier.wait(10)
            return real(k,rev,value)
        def run(p):
            try:return self.apply(p)
            except SubscriptionLinkConflict:return 'conflict'
        with patch.object(self.f.store,'compare_exchange',side_effect=race),ThreadPoolExecutor(max_workers=2) as pool:
            out=list(pool.map(run,plans))
        self.assertEqual(out.count('conflict'),1)
        self.assertTrue(all(self.links._intent(digest(p)) is not None for p in plans))

    def test_lost_response_at_each_write_resumes_same_plan_without_second_apple_query(self):
        for phase in ('intent','guard','pointer','complete'):
            with self.subTest(phase=phase):
                self.setUp();p=self.plan();seen=[]
                target=self.f.audit if phase in ('intent','complete') else self.f.store
                real=target.compare_exchange
                def fail(k,rev,value):
                    out=real(k,rev,value)
                    match=((phase=='intent' and value.get('kind')=='subscription-policy-transition-intent')
                        or (phase=='complete' and value.get('kind')=='subscription-policy-transition-confirmed')
                        or (phase=='pointer' and 'plan' in value and 'kind' not in value)
                        or (phase=='guard' and 'scope' in value and 'plan' not in value))
                    if match and not seen:seen.append(1);raise StateUnavailable('lost response')
                    return out
                with patch.object(target,'compare_exchange',side_effect=fail):
                    with self.assertRaises(StateUnavailable):self.apply(p)
                with patch.object(self.a.client,'get_all_subscription_statuses',side_effect=TimeoutError()) as api:
                    r=self.apply(p);self.assertEqual(r,self.apply(p));self.assertEqual(api.call_count,0)

    def test_guard_lost_before_pointer_blocks_both_old_and_new_admission(self):
        p=self.plan();real=self.f.store.compare_exchange
        def fail(k,r,v):
            out=real(k,r,v)
            if k==stable_subscription_key(self.links.policy,self.f.subject,'guard'):raise StateUnavailable('lost guard ack')
            return out
        with patch.object(self.f.store,'compare_exchange',side_effect=fail):
            with self.assertRaises(StateUnavailable):self.apply(p)
        with self.assertRaises(SubscriptionMigrationRequired):self.f.accounts.resolve('first-device')
        with self.assertRaises(SubscriptionMigrationRequired):self.accounts.resolve('first-device')
        self.apply(p);self.assertEqual(self.grant().limit,10)

    def test_fence_loss_after_audit_stops_pointer_and_preserves_intent(self):
        p=self.plan();real=self.f.audit.compare_exchange
        def fail(*args):out=real(*args);self.fenced=False;return out
        with patch.object(self.f.audit,'compare_exchange',side_effect=fail):
            with self.assertRaises(StateUnavailable):self.apply(p)
        self.assertIsNone(self.f.store.load(self.links.key(self.f.subject)))
        self.assertIsNotNone(self.links._intent(digest(p)))

    def test_grant_expired_during_control_io_requires_new_explicit_plan(self):
        p=self.plan();real=self.f.audit.compare_exchange
        def slow(*args):out=real(*args);self.a.now+=31;return out
        with patch.object(self.f.audit,'compare_exchange',side_effect=slow):
            with self.assertRaises(UsageConflict):self.apply(p)
        self.assertIsNone(self.f.store.load(self.links.key(self.f.subject)))
        with self.assertRaises(UsageConflict):self.apply(p)
        self.apply(self.plan());self.assertEqual(self.grant().limit,10)

    def test_corrupt_record_intent_and_completion_never_repaired_in_place(self):
        for kind in ('record','intent','complete','guard'):
            with self.subTest(kind=kind):
                self.setUp();p=self.plan();self.apply(p)
                store=self.f.store if kind in ('record','guard') else self.f.audit
                key=(self.links.key(self.f.subject) if kind=='record' else
                     stable_subscription_key(self.links.policy,self.f.subject,'guard') if kind=='guard' else
                     self.links.audit_key(kind,digest(p)))
                old=store.load(key);bad=copy.deepcopy(old[1]);bad['v']=True;store.compare_exchange(key,old[0],bad)
                before=rows(store)
                with self.assertRaises(StateUnavailable):self.accounts.resolve('first-device')
                self.assertEqual(before,rows(store))

    def test_incomplete_completion_requires_explicit_recovery_not_lookup_mutation(self):
        p=self.plan();real=self.f.audit.compare_exchange
        def fail(k,r,v):
            if v.get('kind')=='subscription-policy-transition-confirmed':raise StateUnavailable('offline')
            return real(k,r,v)
        with patch.object(self.f.audit,'compare_exchange',side_effect=fail):
            with self.assertRaises(StateUnavailable):self.apply(p)
        before=rows(self.f.audit)
        with self.assertRaises(StateUnavailable):self.accounts.resolve('first-device')
        self.assertEqual(before,rows(self.f.audit));self.apply(p)

    def test_bad_legacy_inventory_or_missing_completion_blocks_import(self):
        link=self.f.links._read(self.f.subject,self.f.token)
        key=self.f.links.audit_key('complete',link['intent']);row=self.f.audit.load(key)
        self.f.audit.compare_exchange(key,row[0],{'v':True})
        with self.assertRaises(StateUnavailable):self.plan()

    def test_fake_gcs_adapter_uses_expected_generation_and_preserves_old_objects(self):
        bucket=FakeBucket();self.f.store=GCSStateStore(bucket,prefix='synthetic_links')
        self.f.audit=GCSStateStore(bucket,prefix='synthetic_audit')
        old=ServiceSubscriptionLinks(store=self.f.store,audit_store=self.f.audit,authority=self.a.a,clock=lambda:self.a.now)
        reg=ServiceAccounts(self.accounts.store,subscription_links=old)
        old.attach(reg,'first-device',self.f.original,authentication_proof_sha256='b'*64)
        old_versions=copy.deepcopy(bucket.versions)
        self.links=self.reader(self.old_id);self.accounts=ServiceAccounts(reg.store,subscription_links=self.links)
        p=self.plan();self.apply(p);self.target_new();self.apply(self.plan())
        self.assertTrue(old_versions.items()<=bucket.versions.items())
        before=len(bucket.versions);self.apply(p);self.assertEqual(before,len(bucket.versions))
        writes=[c for c in bucket.calls if c[0]=='upload']
        self.assertTrue(all('if_generation_match' in c[2] for c in writes))
        self.assertTrue(any(c[2]['if_generation_match']!=0 for c in writes))


if __name__=='__main__':unittest.main()
