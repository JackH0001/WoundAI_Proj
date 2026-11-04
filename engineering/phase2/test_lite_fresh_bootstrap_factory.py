"""Private factory, read-only data lanes, genuine core and synthetic transports."""
import copy
import hashlib
import io
import socket
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from PIL import Image
from lite_attest_state import SQLiteStateStore, StateUnavailable
from lite_usage_ledger import digest, UsageConflict
from lite_subscription_executor import canonical, key
from lite_fresh_bootstrap_factory import build, GCSFactory, NAMES, READ, CREATE, OPERATIONS
from test_lite_fresh_bootstrap import Fixture, b64
from test_lite_subscription_grace import rows
from test_lite_attest_state import FakeBucket
import test_lite_native_startup as startup


class FactoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):startup.StartupPreflightTests.setUpClass()
    def setUp(self):
        for name in ('connect','connect_ex'):
            p=patch.object(socket.socket,name,side_effect=AssertionError('no network'));p.start();self.addCleanup(p.stop)
        self.f=f=Fixture(self);self.config=f.h.config;self.raw=canonical(self.config)
        self.assertEqual(hashlib.sha256(self.raw).hexdigest(),f.plan['config'])
        self.locations={name:dict(bucket='synthetic-ops-bucket',prefix='lite-bootstrap/'+f.plan['namespace']+'/'+name)
                        if name in OPERATIONS else self.config['stores'][name] for name in NAMES}
        self.states=dict(f.stores,bootstrap=f.control,bootstrap_audit=f.audit,bootstrap_observer=f.observer,
                         gate=SQLiteStateStore(f.h.release.n.f.root/'gate.db'),gate_audit=SQLiteStateStore(f.h.release.n.f.root/'gate-audit.db'))
        self.calls=[]
        def factory(name,loc):self.calls.append((name,loc));return self.states[name]
        self.factory=factory
    def make(self,**changes):
        f=self.f
        args=dict(raw=self.raw,expected_config=digest(self.config),plan=f.plan,expected_plan=digest(f.plan),
            sources=f.sources,writers=f.writers,locations=self.locations,expected_locations=digest(self.locations),
            state_factory=self.factory,clock=f.h.clock)
        args.update(changes);return build(**args)
    def wire(self):
        f=self.f;f.x=self.make();f.runtime=f.x.runtime;f.rollout=f.x.rollout;f.reader=f.x.reader;f.provider=f.rollout.provider
        return f
    def test_composes_and_initializes_without_secret_loading_or_provider_network(self):
        old={k:rows(v) for k,v in self.states.items() if k in READ}
        with patch('cryptography.hazmat.primitives.serialization.load_pem_private_key',side_effect=AssertionError('no Apple key needed')):
            f=self.wire();f.finish()
        self.assertEqual({k:rows(v) for k,v in self.states.items() if k in READ},old)
        self.assertEqual(len(self.calls),len(NAMES));self.assertEqual(f.reader.require_active()['phase'],'active')
    def test_constructor_does_not_write_any_state(self):
        before={k:rows(v) for k,v in self.states.items()};self.make()
        self.assertEqual({k:rows(v) for k,v in self.states.items()},before)
    def test_wrong_hashes_never_connect_store_factory(self):
        for name in ('expected_config','expected_plan','expected_locations'):
            with self.subTest(name=name),self.assertRaises(ValueError):self.make(**{name:'0'*64})
        self.assertEqual(self.calls,[])
    def test_wrong_public_app_or_mode_never_connects(self):
        for mutate in (lambda c:c.update(schema='lite-native-startup/2'),lambda c:c['app'].update(bundle='com.other.app'),
                       lambda c:c['apple'].update(environment='Production')):
            c=copy.deepcopy(self.config);mutate(c);raw=canonical(c);p=dict(self.f.plan,config=digest(c))
            with self.assertRaises(ValueError):self.make(raw=raw,expected_config=digest(c),plan=p,expected_plan=digest(p))
        self.assertEqual(self.calls,[])
    def test_rehashed_but_inconsistent_manifests_never_connect(self):
        changes=(lambda c,p:c['deployment'].update(source='a'*40),
                 lambda c,p:c['deployment'].update(image='a'*64),
                 lambda c,p:p['subscription'].update(fence='a'*64),
                 lambda c,p:p['subscription'].update(entries=[{}]),
                 lambda c,p:p.update(unreviewed=True))
        for mutate in changes:
            c=copy.deepcopy(self.config);p=copy.deepcopy(self.f.plan);mutate(c,p)
            p['config']=digest(c)
            with self.subTest(mutate=mutate),self.assertRaises(ValueError):
                self.make(raw=canonical(c),expected_config=digest(c),plan=p,expected_plan=digest(p))
        self.assertEqual(self.calls,[])
    def test_missing_or_unexpected_operational_lane_refused(self):
        for loc in ({k:v for k,v in self.locations.items() if k!='gate'},dict(self.locations,unreviewed=self.locations['gate'])):
            with self.assertRaises(ValueError):self.make(locations=loc,expected_locations=digest(loc))
        self.assertEqual(self.calls,[])
    def test_data_prefix_cannot_be_remapped_even_with_matching_location_hash(self):
        loc=copy.deepcopy(self.locations);loc['accounts']['prefix']='lite-native/other/accounts'
        with self.assertRaises(ValueError):self.make(locations=loc,expected_locations=digest(loc))
        self.assertEqual(self.calls,[])
    def test_operation_prefix_cannot_alias_any_data_lane(self):
        loc=copy.deepcopy(self.locations);loc['gate']=self.config['stores']['grants']
        with self.assertRaises(ValueError):self.make(locations=loc,expected_locations=digest(loc))
        self.assertEqual(self.calls,[])
    def test_two_operation_lanes_cannot_share_a_parent_prefix(self):
        loc=copy.deepcopy(self.locations);loc['gate_audit']=dict(loc['gate'],prefix=loc['gate']['prefix']+'/audit')
        with self.assertRaises(ValueError):self.make(locations=loc,expected_locations=digest(loc))
        self.assertEqual(self.calls,[])
    def test_read_only_data_stores_reject_writes_before_transport(self):
        f=self.wire();stores=[f.runtime.registry.store,f.runtime.links.store,f.runtime.links.audit,
                             f.runtime.links.catalog[f.runtime.links.active].proofs,f.rollout.ledger.store,
                             f.runtime.observations,f.x.observer]
        before={k:rows(v) for k,v in self.states.items()}
        for store in stores:
            with self.subTest(store=store),self.assertRaises(StateUnavailable):store.compare_exchange(b'x'*32,0,{'v':1})
        self.assertEqual(before,{k:rows(v) for k,v in self.states.items()})
    def test_audit_and_sealed_cutover_never_replace(self):
        f=self.wire();stores=[f.runtime.audit,f.rollout.audit,f.reader.audit,f.x.audit,f.rollout.control,f.provider.audit]
        for store in stores:
            with self.assertRaises(StateUnavailable):store.compare_exchange(b'x'*32,1,{'v':1})
    def test_apple_client_and_verifier_cannot_be_used(self):
        f=self.wire();a=f.runtime.links.catalog[f.runtime.links.active]
        for obj in (a.client,a.verifier):
            with self.assertRaises(StateUnavailable):getattr(obj,'get_all_subscription_statuses')
    def test_gate_write_requires_claimed_quota_step(self):
        f=self.wire();before=rows(self.states['gate']),rows(self.states['gate_audit'])
        for op in ('freeze_legacy','enable_new','disable_new'):
            with self.subTest(op=op),self.assertRaises(StateUnavailable):getattr(f.provider,op)(f.usage)
        self.assertEqual((rows(self.states['gate']),rows(self.states['gate_audit'])),before)
    def test_corrupt_gate_retained_and_rollout_not_activated(self):
        f=self.wire();f.step('subscription');self.states['gate'].compare_exchange(f.provider.key,0,{'wrong':True})
        with self.assertRaises(StateUnavailable):f.step('quota')
        self.assertEqual(self.states['gate'].load(f.provider.key)[1],{'wrong':True})
        self.assertEqual(f.rollout._read()[1]['phase'],'paused')
        with self.assertRaises(StateUnavailable):f.reader.require_active()
    def test_stale_fence_cannot_authorize_preparation(self):
        f=self.wire();f.step('subscription');f.refresh(route_closed=False)
        with self.assertRaises(StateUnavailable):f.step('quota')
        self.assertEqual(rows(self.states['gate']),[])
    def test_gate_confirmation_lost_then_recovery_preserves_all_old_audit(self):
        f=self.wire();f.step('subscription');write=f.provider._immutable
        def fail(kind,v):
            if kind=='confirmed' and v['enabled']:raise StateUnavailable('lost readiness audit')
            return write(kind,v)
        with patch.object(f.provider,'_immutable',side_effect=fail),self.assertRaises(StateUnavailable):f.step('quota')
        self.assertEqual(f.rollout._read()[1]['phase'],'paused');old=rows(self.states['gate_audit']);pause=f.rollout._read()[1]
        f.tick();f.step('quota',recover=True,inner=dict(action='resume-paused',expected_pause_sha256=digest(pause)));f.step('release')
        self.assertTrue(all(row in rows(self.states['gate_audit']) for row in old))
        self.assertEqual(f.reader.require_active()['phase'],'active')
    def test_failed_provider_containment_can_be_explicitly_reclosed_before_resume(self):
        f=self.wire();f.step('subscription');move=f.rollout._move
        def fail(phase):
            if phase=='active':raise StateUnavailable('active phase unavailable')
            return move(phase)
        with patch.object(f.rollout,'_move',side_effect=fail),patch.object(f.provider,'disable_new',side_effect=StateUnavailable('containment unavailable')),self.assertRaises(StateUnavailable):f.step('quota')
        self.assertTrue(f.provider._read()[1]['enabled']);pause=f.rollout._read()[1]
        self.assertEqual(pause['phase'],'paused');f.tick()
        f.step('quota',recover=True,inner=dict(action='resume-paused',expected_pause_sha256=digest(pause)));f.step('release')
        self.assertEqual(f.reader.require_active()['phase'],'active')
    def test_expired_claim_cannot_be_reused_to_move_gate(self):
        f=self.wire();f.step('subscription')
        with patch.object(f.provider,'freeze_legacy',side_effect=StateUnavailable('offline')),self.assertRaises(StateUnavailable):f.step('quota')
        f.tick(901)
        with self.assertRaises(StateUnavailable):f.provider.disable_new(f.usage)
    def test_completed_bootstrap_is_not_an_operation_authority(self):
        f=self.wire();f.finish()
        with self.assertRaises(StateUnavailable):f.provider.disable_new(f.usage)

    def test_native_web_factory_reads_bootstrap_receipts_and_replay_is_one_charge(self):
        from test_lite_fresh_bootstrap import BootstrapTests
        f=self.wire();f.finish();host=BootstrapTests();h=f.h
        host.h=h;host.backend=h.compose();host.client=host.backend.create_app().test_client()
        host.e=h.e;host.key=b64(h.e.key_id);host.counter=0;host.login_serial=450
        host.register();host.login();buf=io.BytesIO();Image.new('RGB',(64,64),(190,100,120)).save(buf,format='JPEG')
        host.f=SimpleNamespace(jpeg=buf.getvalue());host.image_hash=hashlib.sha256(host.f.jpeg).hexdigest()
        first=host.complete(1);again=host.run_request(1)
        self.assertEqual(first.json,again.json);self.assertEqual((h.model_calls,host.quota()['used']),(1,1))
        # Restart the CUSTOMER factory, not the operator. It neither replays
        # initialization nor needs a continuously refreshed bootstrap observer.
        saved=rows(self.states['bootstrap']),rows(self.states['gate_audit'])
        f.tick(31);host.backend=h.compose();host.client=host.backend.create_app().test_client()
        self.assertEqual(host.run_request(1).json,first.json)
        self.assertEqual((h.model_calls,host.quota()['used']),(1,1))
        self.assertEqual((rows(self.states['bootstrap']),rows(self.states['gate_audit'])),saved)


class CloudAdapterTests(unittest.TestCase):
    def setUp(self):
        self.meta=dict(kind='storage#bucket',name='synthetic-bucket',projectNumber='123456789',location='ASIA-EAST1',
            metageneration='3',softDeletePolicy={'retentionDurationSeconds':'0'},
            iamConfiguration={'publicAccessPrevention':'enforced','uniformBucketLevelAccess':{'enabled':True}})
        self.bucket=FakeBucket();self.bucket._properties=self.meta;self.calls=[]
        self.bucket.reload=lambda **kw:self.calls.append(kw)
        self.client=SimpleNamespace(bucket=lambda n:self.bucket)
        self.f=GCSFactory(client=self.client,project_number='123456789',buckets={'synthetic-bucket':{'metageneration':'3'}})
        self.loc=dict(bucket='synthetic-bucket',prefix='lite-bootstrap/test/gate')
    def test_actual_gcs_adapter_uses_pinned_bucket_and_generation_cas(self):
        s=self.f('gate',self.loc);s.compare_exchange(b'x'*32,0,{'v':1});row=s.load(b'x'*32)
        self.assertFalse(s.compare_exchange(b'x'*32,0,{'v':2}));self.assertEqual(s.load(b'x'*32),row)
        self.f('gate_audit',dict(self.loc,prefix='lite-bootstrap/test/audit'))
        self.assertEqual(self.calls,[{'timeout':10,'retry':None}])
    def test_wrong_bucket_policy_is_not_inferred_from_config(self):
        for change in ({'projectNumber':'999999999'},{'metageneration':'4'},{'softDeletePolicy':{'retentionDurationSeconds':'604800'}},
                       {'versioning':{'enabled':True}},{'retentionPolicy':{}},{'location':'US'}):
            with self.subTest(change=change):
                self.bucket._properties=dict(self.meta,**change)
                with self.assertRaises(ValueError):self.f('gate',self.loc)
        self.assertEqual(self.bucket.calls,[])
    def test_unlisted_bucket_rejected_without_provider_get(self):
        with self.assertRaises(ValueError):self.f('gate',dict(self.loc,bucket='other-bucket'))
        self.assertEqual(self.calls,[])
    def test_bucket_get_unavailable_does_not_create_state(self):
        with patch.object(self.bucket,'reload',side_effect=StateUnavailable('denied')),self.assertRaises(StateUnavailable):self.f('gate',self.loc)
        self.assertEqual(self.bucket.calls,[])


if __name__=='__main__':unittest.main()
