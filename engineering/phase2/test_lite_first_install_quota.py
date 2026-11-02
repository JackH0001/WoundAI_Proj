"""Cold-start the real service graph with empty commercial stores and synthetic provider signatures."""
import hashlib
import io
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from PIL import Image
from lite_attest_state import SQLiteStateStore, StateUnavailable
from lite_service_operations import ServiceAccounts
from lite_service_release import ServiceReleaseController, ServiceReleaseReader, make_manifest
from lite_subscription_executor import EvidenceContradiction, key
from lite_subscription_migration import StableSubscriptionLinks
from lite_subscription_runtime import SubscriptionRuntime
from lite_usage_cutover import FIRST_INSTALL, validate
from lite_usage_ledger import UsageLedger, UsageConflict, digest
from lite_usage_rollout import UsageRollout, validate_baselines, ObservationUnavailable, ObservationContradiction
from test_lite_signed_client import SignedServiceClient, b64
from test_lite_usage_rollout import SyntheticProvider
from test_lite_subscription_grace import rows
import test_lite_native_startup as startup

class FrozenInventory(SyntheticProvider):
    def __init__(self, manifest, inventory):
        super().__init__(manifest)
        self.inventory=inventory.copy()
    def observe(self, manifest):
        return {**super().observe(manifest), **self.inventory}

class FirstInstallTests(SignedServiceClient, unittest.TestCase):
    post=startup.StartupHTTPTests.post
    login=startup.StartupHTTPTests.login
    quota=startup.StartupHTTPTests.quota

    @classmethod
    def setUpClass(cls):startup.StartupHTTPTests.setUpClass()

    def setUp(self):
        self.h=h=startup.Fixture(self,day_offset=0)
        n=h.release.n
        # Use the fixture's Apple trust roots and enrollment challenge only.
        # Replace all commercial stores with NEW files; do not clear any old rows.
        for lane in h.all_stores:
            if lane!='security':
                h.all_stores[lane]=SQLiteStateStore(n.f.root/('first-install-'+lane+'.db'))
        self.stores=st=h.all_stores
        for lane in ('accounts','links','quota','subscription','release'):
            self.assertEqual(rows(st[lane]),[])
        links=StableSubscriptionLinks(store=st['links'],audit_store=st['links_audit'],authorities=(n.a.a,),
            active_policy=n.links.active,legacy_policies=(),authorize=lambda *_:False,writer_fence=lambda _:False,clock=h.clock)
        accounts=ServiceAccounts(st['accounts'],subscription_links=links)
        self.runtime=SubscriptionRuntime(links=links,registry=accounts,state_store=st['subscription'],
            audit_store=st['subscription_audit'],observation_store=st['observer'],evidence=n.evidence,
            expected_source=n.r.source,expected_image=n.r.image)
        evidence=dict(scope='synthetic-first-install',prior_usage_subjects=[],prior_accounts=[],prior_links=[])
        self.inventory=digest(evidence)
        self.sub={**n.m,'namespace':'synthetic-fresh-subscriptions','inventory':self.inventory,'entries':[]}
        self.publish_observation()
        self.runtime.activate_fresh(self.sub,n.sign(dict(kind='lite-migration-approval/1',
            manifest=digest(self.sub),action='activate-fresh',expected_state=digest(None),
            issued_at=h.clock(),expires_at=h.clock()+900,nonce='1'*64)))
        self.usage={**h.release.rollout.manifest,'schema':FIRST_INSTALL,'namespace':'synthetic-empty-ledger',
            'baseline_sha256':digest([]),'account_map_sha256':digest([]),'fresh_inventory_sha256':self.inventory}
        self.observed=dict(fresh_inventory_sha256=self.inventory, prior_quota_accounts=0, prior_account_mappings=0)
        self.operator=FrozenInventory(self.usage,self.observed)
        self.ledger=UsageLedger(st['quota'],namespace=self.usage['namespace'],clock=h.clock)
        self.rollout=UsageRollout(store=st['rollout'],audit_store=st['rollout_audit'],control_store=st['cutover'],
            ledger=self.ledger,provider=self.operator,manifest=self.usage,baselines=[])
        self.ledger.cutover=self.rollout.policy()
        self.assertEqual(self.rollout.advance()['phase'],'active')
        self.assertEqual(rows(st['quota']),[])
        release=make_manifest(self.runtime,self.rollout,namespace='fresh-release')
        self.reader=ServiceReleaseReader(store=st['release'],audit_store=st['release_audit'],manifest=release)
        self.controller=ServiceReleaseController(reader=self.reader,runtime=self.runtime,rollout=self.rollout,evidence=n.evidence)
        self.runtime.service_release=self.rollout.service_release=self.reader
        h.config['controls'].update(release=release,usage=self.usage,baselines=[])
        self.serial=0

    def publish_observation(self):
        h=self.h;n=h.release.n;m=self.sub
        body=dict(kind='lite-migration-observation/1',namespace=m['namespace'],manifest=digest(m),
            source=m['source'],image=m['image'],inventory=m['inventory'],fence=m['fence'],legacy_disabled=True,
            legacy_inflight=0,migration_inflight=0,stable_reader=True,issued_at=h.clock(),expires_at=h.clock()+30)
        k=key('observation',m['namespace']);prior=self.stores['observer'].load(k)
        self.assertTrue(self.stores['observer'].compare_exchange(k,prior[0] if prior else 0,n.sign(body,'observer')))

    def approval(self,action):
        self.serial+=1
        row=self.reader.snapshot()
        return self.h.release.n.sign(dict(kind='lite-migration-approval/1',manifest=self.reader.identity,
            action='service-release-'+action,expected_state=digest(row[1] if row else None),
            issued_at=self.h.clock(),expires_at=self.h.clock()+900,nonce=f'{self.serial:064x}'))

    def signed_client(self):
        self.controller.apply(self.approval('publish'))
        self.backend=self.h.compose()
        self.client=self.backend.create_app().test_client()
        self.e=self.h.e; self.key=b64(self.e.key_id);self.counter=0;self.login_serial=100
        self.register();self.login()
        b=io.BytesIO();Image.new('RGB',(64,64),(200,100,120)).save(b,format='JPEG')
        self.f=SimpleNamespace(jpeg=b.getvalue());self.image_hash=hashlib.sha256(self.f.jpeg).hexdigest()

    def test_first_person_can_use_two_on_opening_day_and_no_extra_on_replay(self):
        self.signed_client()
        self.assertEqual(rows(self.stores['quota']),[])
        first=self.complete(1)
        self.assertEqual(first.json['receipt']['outcome'],'succeeded')
        self.assertEqual(self.run_request(1).json,first.json)
        self.assertEqual(self.complete(2).json['receipt']['outcome'],'succeeded')
        self.assertEqual(self.json_request('prepare',3).status_code,429)
        self.assertEqual((self.h.model_calls,self.quota()['used'],self.quota()['attempts']),(2,2,2))
        account=self.backend.operations.accounts.registration(self.owner)
        values=[json.loads(row[2]) for row in rows(self.stores['accounts'])]
        registered=[value for value in values if 'installation_hash' in value and 'subject' in value]
        self.assertEqual(registered,[account])
        self.assertEqual(len(rows(self.stores['quota'])),1)
        self.assertEqual(rows(self.stores['privacy']),[])

    def test_release_is_required_before_first_daily_row_can_be_written(self):
        with self.assertRaises(StateUnavailable):self.ledger._ensure_current_day('not-admitted',self.h.clock()//86400)
        self.rollout.service_release=None
        self.assertFalse(self.rollout.admission_allowed(self.usage))
        self.assertEqual(rows(self.stores['quota']),[])

    def test_paid_restore_on_first_day_preserves_free_usage(self):
        self.signed_client()
        self.complete(1); self.complete(2)
        context=self.post('purchase/context',operation='c'*32)
        self.assertEqual(context.status_code,200)
        apple=self.h.release.n.a
        apple.payload['appAccountToken']=context.json['app_account_token']
        signed=apple.sign(apple.payload)
        first=self.post('purchase/confirm',operation='c'*32,signed_transaction=signed)
        self.assertEqual(first.status_code,200,first.json)
        with patch.object(apple.client,'get_all_subscription_statuses',side_effect=AssertionError('duplicate verification')):
            self.assertEqual(self.post('purchase/confirm',operation='c'*32,signed_transaction=signed).json,first.json)
        account=self.backend.operations.accounts.resolve(self.owner)
        grant=self.backend.operations.accounts.grant(account,self.h.clock())
        balance=self.ledger.snapshot(account['subject'],self.h.clock()//86400,grant)
        self.assertEqual((balance['limit'],balance['used'],balance['remaining']),(10,2,8))

    def test_concurrent_first_reservations_create_one_row_and_one_attempt(self):
        self.signed_client();barrier=Barrier(4)
        def reserve(_):
            barrier.wait(10)
            return self.backend.operations.prepare(self.owner,'a'*32,self.image_hash)
        with ThreadPoolExecutor(max_workers=4) as pool:replies=list(pool.map(reserve,range(4)))
        self.assertTrue(all(r==replies[0] for r in replies))
        self.assertEqual(len(rows(self.stores['quota'])),1)
        self.assertEqual((self.quota()['attempts'],self.quota()['pending'],self.quota()['used']),(1,1,0))

    def test_health_distinguishes_unobserved_inventory_from_confirmed_prior_usage(self):
        self.signed_client();self.complete(1)
        before=rows(self.stores['quota'])
        self.operator.inventory={**self.observed,'prior_quota_accounts':False}
        self.assertEqual(self.controller.health()['status'],'observation-unavailable')
        self.assertEqual(self.reader.snapshot()[1]['phase'],'active')
        self.operator.inventory={**self.observed,'prior_quota_accounts':1}
        self.assertEqual(self.controller.health()['status'],'paused-contradiction')
        self.assertEqual(self.json_request('prepare',2).status_code,503)
        self.assertEqual(rows(self.stores['quota']),before)

    def test_legacy_and_fresh_rows_cannot_be_exchanged(self):
        legacy={k:v for k,v in self.usage.items() if k!='fresh_inventory_sha256'}
        legacy['schema']='lite-usage-cutover/1'
        with self.assertRaises(UsageConflict):validate_baselines([],legacy)
        with self.assertRaises(UsageConflict):validate_baselines([dict(subject='a',successes=0,attempts=0)],self.usage)
        for field in ['baseline_sha256','account_map_sha256']:
            with self.subTest(field=field),self.assertRaises(UsageConflict):validate({**self.usage,field:'a'*64})
        with self.assertRaises(UsageConflict):
            self.ledger.initialize_day('a',self.h.clock()//86400,baseline_successes=0,baseline_attempts=0,manifest_sha256='b'*64)
        self.assertEqual(rows(self.stores['quota']),[])

    def test_previous_usage_or_account_mapping_blocks_release(self):
        for k in ['prior_quota_accounts','prior_account_mappings','fresh_inventory_sha256']:
            with self.subTest(k=k):
                self.operator.inventory={**self.observed,k:'a'*64 if k.endswith('sha256') else 1}
                with self.assertRaises(ObservationContradiction):self.controller.apply(self.approval('publish'))
                self.assertIsNone(self.reader.snapshot());self.assertEqual(rows(self.stores['quota']),[])

    def test_missing_inventory_is_not_a_verified_empty_inventory(self):
        self.operator.inventory.pop('prior_quota_accounts')
        with self.assertRaises(ObservationUnavailable):self.controller.apply(self.approval('publish'))
        self.assertIsNone(self.reader.snapshot())

    def test_different_signed_subscription_inventory_does_not_authorize_quota(self):
        original=self.runtime._policy
        def different(*a,**kw):
            return {**original(*a,**kw),'inventory':'a'*64}
        with patch.object(self.runtime,'_policy',side_effect=different),self.assertRaises(EvidenceContradiction):
            self.controller.apply(self.approval('publish'))
        self.assertIsNone(self.reader.snapshot())

    def test_existing_legacy_usage_is_preserved_and_refused(self):
        self.signed_client();self.complete(1)
        original_rows=rows(self.stores['quota'])
        account=self.backend.operations.accounts.resolve(self.owner)
        k=self.ledger.key(account['subject'],self.h.clock()//86400)
        revision,v=self.stores['quota'].load(k)
        legacy={a:b for a,b in v.items() if a not in ('policy','fences')}
        legacy['v']=1
        self.stores['quota'].compare_exchange(k,revision,legacy)
        before=rows(self.stores['quota'])
        with self.assertRaises(StateUnavailable):self.quota()
        self.assertEqual(before,rows(self.stores['quota']))
        self.assertNotEqual(before,original_rows)

    def test_pause_restart_and_next_day_preserve_receipts(self):
        self.signed_client();first=self.complete(1).json
        original=rows(self.stores['quota'])
        self.controller.apply(self.approval('pause'))
        self.assertEqual(self.json_request('prepare',2).status_code,503)
        self.assertEqual(rows(self.stores['quota']),original)
        self.controller.apply(self.approval('resume'))
        self.backend=self.h.compose();self.client=self.backend.create_app().test_client()
        self.assertEqual(self.run_request(1).json,first)
        person=self.backend.operations.accounts.resolve(self.owner)['subject']
        day=self.h.clock()//86400
        self.h.release.n.a.now+=86400
        self.backend.operations.ledger._ensure_current_day(person,day+1)
        self.assertEqual(self.quota()['used'],0)
        self.assertEqual(self.backend.operations.ledger._load(person,day)[1]['entries'][f'{1:032x}']['history'][-1][1],'succeeded')
