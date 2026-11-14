"""Actual activation/cutover/release and signed HTTP; no cloud or copied seed rows."""
import copy
import hashlib
import io
import socket
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from PIL import Image
from lite_attest_state import SQLiteStateStore, GCSStateStore, StateUnavailable
from lite_usage_ledger import UsageLedger, UsageConflict, digest
from lite_subscription_migration import StableSubscriptionLinks
from lite_subscription_runtime import SubscriptionRuntime
from lite_service_operations import ServiceAccounts
from lite_service_release import ServiceReleaseReader, ServiceReleaseController
from lite_usage_rollout import UsageRollout
from lite_usage_cutover import FIRST_INSTALL
from lite_subscription_executor import key
from lite_fresh_bootstrap import FreshBootstrapExecutor, inventory, STAGES
from test_lite_first_install_quota import FrozenInventory
from test_lite_shared_runtime import SharedNativeFixture
from test_lite_signed_client import SignedServiceClient, b64
from test_lite_subscription_grace import rows
from test_lite_attest_state import FakeBucket
import test_lite_native_startup as startup
import test_lite_native_backend as native


class Fixture:
    def __init__(self, host):
        self.h = h = SharedNativeFixture(host); n = h.release.n; self.serial = 100
        self.original_stores = dict(h.all_stores)
        # Retain only generated Apple trust roots and the synthetic enrollment.
        # No old records are cleared or imported into these empty stores.
        for lane in h.all_stores:
            if lane != 'security': h.all_stores[lane] = SQLiteStateStore(n.f.root/('fresh-'+lane+'.db'))
        st = self.stores = h.all_stores
        n.a.a.proofs = st['apple_proofs']
        links = StableSubscriptionLinks(store=st['links'],audit_store=st['links_audit'],authorities=(n.a.a,),
            active_policy=n.links.active,legacy_policies=(),authorize=lambda *_:False,writer_fence=lambda _:False,clock=h.clock)
        accounts = ServiceAccounts(st['accounts'],subscription_links=links)
        self.runtime = SubscriptionRuntime(links=links,registry=accounts,state_store=st['subscription'],
            audit_store=st['subscription_audit'],observation_store=st['observer'],evidence=n.evidence,
            expected_source=n.r.source,expected_image=n.r.image)
        self.sources = [dict(id='frozen-'+role,role=role,location=digest(h.config['stores'][role]))
                        for role in ('accounts','links','quota')]
        self.writers = ['synthetic-fenced-native']
        self.inventory = dict(schema='lite-fresh-inventory/1',writers=self.writers,
            sources=[dict(v,count=0,snapshot=digest(['empty',v])) for v in self.sources])
        self.sub = dict(n.m,namespace='fresh-native-subscriptions',entries=[],inventory=digest(self.inventory))
        self.usage = dict(h.release.rollout.manifest,schema=FIRST_INSTALL,namespace='fresh-native-usage',
            day=h.clock()//86400,sealed_at=h.clock(),baseline_sha256=digest([]),account_map_sha256=digest([]),fresh_inventory_sha256=digest(self.inventory))
        self.provider = FrozenInventory(self.usage,dict(fresh_inventory_sha256=digest(self.inventory),
                                                       prior_quota_accounts=0,prior_account_mappings=0))
        self.ledger = UsageLedger(st['quota'],namespace=self.usage['namespace'],clock=h.clock)
        self.rollout = UsageRollout(store=st['rollout'],audit_store=st['rollout_audit'],control_store=st['cutover'],
            ledger=self.ledger,provider=self.provider,manifest=self.usage,baselines=[])
        self.ledger.cutover = self.rollout.policy()
        release = dict(schema='lite-service-release/1',namespace='fresh-native-release',source=n.r.source,image=n.r.image,
            scope=self.runtime.scope,active_policy=links.active,policies={links.active:digest(self.sub)},
            usage_namespace=self.usage['namespace'],usage_plan=digest(self.usage),fence=self.sub['fence'])
        self.reader = ServiceReleaseReader(store=st['release'],audit_store=st['release_audit'],manifest=release)
        self.runtime.service_release = self.rollout.service_release = self.reader
        h.config['controls'].update(release=release,usage=self.usage,baselines=[])
        self.plan = dict(schema='lite-fresh-bootstrap/1',namespace='fresh-native-bootstrap',config=digest(h.config),
                         inventory=self.inventory,subscription=self.sub,usage=self.usage,release=release)
        self.control = SQLiteStateStore(n.f.root/'bootstrap-control.db')
        self.audit = SQLiteStateStore(n.f.root/'bootstrap-audit.db')
        self.observer = SQLiteStateStore(n.f.root/'bootstrap-observer.db')
        self.evidence = n.evidence; self.refresh()
        self.x = self.make()

    def make(self, **changes):
        args = dict(plan=self.plan,expected_plan=digest(self.plan),expected_config=digest(self.h.config),
            sources=self.sources,writers=self.writers,runtime=self.runtime,rollout=self.rollout,reader=self.reader,
            evidence=self.evidence,observer=self.observer,store=self.control,audit=self.audit)
        args.update(changes); return FreshBootstrapExecutor(**args)

    def sign(self, payload, role='operator'): return self.h.release.n.sign(payload,role)

    def refresh(self, **changes):
        now = self.h.clock(); m = self.sub
        sub = dict(kind='lite-migration-observation/1',namespace=m['namespace'],manifest=digest(m),source=m['source'],
            image=m['image'],inventory=m['inventory'],fence=m['fence'],legacy_disabled=True,
            legacy_inflight=0,migration_inflight=0,stable_reader=True,issued_at=now,expires_at=now+30)
        k=key('observation',m['namespace']);old=self.stores['observer'].load(k)
        self.stores['observer'].compare_exchange(k,old[0] if old else 0,self.sign(sub,'observer'))
        body=dict(kind='lite-migration-observation/1',namespace=self.plan['namespace'],manifest=digest(self.plan),
            inventory=digest(self.inventory),writer_fence=m['fence'],route_closed=True,legacy_disabled=True,
            legacy_inflight=0,bootstrap_inflight=0,issued_at=now,expires_at=now+30)
        body.update(changes);k=key('bootstrap-observation',self.plan['namespace']);old=self.observer.load(k)
        self.observer.compare_exchange(k,old[0] if old else 0,self.sign(body,'observer'))

    def tick(self, seconds=1):
        self.h.release.n.a.now += seconds; self.h.e.now=self.h.clock();self.refresh()

    def inner(self, stage, confirm=False):
        if stage == 'quota': return None
        self.serial+=1;now=self.h.clock();state=self.reader.snapshot()
        return self.sign(dict(kind='lite-migration-approval/1',manifest=digest(self.sub) if stage=='subscription' else self.reader.identity,
            action='activate-fresh' if stage=='subscription' else 'service-release-confirm' if confirm else 'service-release-publish',
            expected_state=digest(state[1] if state else None),issued_at=now,expires_at=now+900,nonce=f'{self.serial:064x}'))

    def command(self, stage, inner, recover=False, **changes):
        self.serial+=1;now=self.h.clock()
        body=dict(kind='lite-migration-approval/1',manifest=digest(self.plan),stage=stage,
            action='bootstrap-recover' if recover else 'bootstrap-step',expected_state=digest(self.x.inspect()),
            inner_sha256=digest(inner),issued_at=now,expires_at=now+900,nonce=f'{self.serial:064x}')
        body.update(changes);return self.sign(body)

    def step(self, stage, recover=False, inner='auto'):
        inner=self.inner(stage) if inner=='auto' else inner
        command=self.command(stage,inner,recover);result=self.x.execute(command,inner=inner)
        return command,inner,result

    def finish(self):
        for stage in STAGES:self.step(stage)
        return self.x.inspect()

    def untouched(self):
        return {k:rows(self.stores[k]) for k in ('accounts','links','quota','operations','identity','grants','privacy')}


class BootstrapTests(SignedServiceClient, unittest.TestCase):
    post=native.NativeBackendTests.post
    login=native.NativeBackendTests.login
    quota=native.NativeBackendTests.quota

    @classmethod
    def setUpClass(cls): startup.StartupPreflightTests.setUpClass()

    def setUp(self):
        for method in ('connect','connect_ex'):
            p=patch.object(socket.socket,method,side_effect=AssertionError('no network in bootstrap tests'))
            p.start();self.addCleanup(p.stop)
        self.f=Fixture(self)

    def test_three_steps_cold_start_actual_shared_backend_and_replay_once(self):
        f=self.f;before=f.untouched();self.assertEqual(f.finish()['status'],'complete')
        self.assertEqual(f.untouched(),before)
        self.h=h=f.h;self.backend=h.compose();self.client=self.backend.create_app().test_client()
        self.e=h.e;self.key=b64(self.e.key_id);self.counter=0;self.login_serial=200;self.register();self.login()
        buf=io.BytesIO();Image.new('RGB',(64,64),(200,100,120)).save(buf,format='JPEG')
        self.f=SimpleNamespace(jpeg=buf.getvalue());self.image_hash=hashlib.sha256(self.f.jpeg).hexdigest()
        first=self.complete(1);self.assertEqual(self.run_request(1).json,first.json)
        self.assertEqual((h.model_calls,self.quota()['used']),(1,1))
        self.complete(2);self.assertEqual(self.json_request('prepare',3).status_code,429)
        self.assertEqual((h.model_calls,self.quota()['used']),(2,2))

    def test_wrong_pins_or_config_never_write(self):
        f=self.f;before=rows(f.control),rows(f.audit),f.untouched()
        for change in ({'expected_plan':'0'*64},{'expected_config':'0'*64},{'sources':f.sources[:-1]},
                       {'writers':['omitted-old-writer']},{'store':f.stores['quota']}):
            with self.subTest(change=change),self.assertRaises((ValueError,StateUnavailable)):f.make(**change)
        self.assertEqual((rows(f.control),rows(f.audit),f.untouched()),before)

    def test_inventory_cannot_claim_nonempty_unknown_or_boolean_as_zero(self):
        for value in (1,None,False,'0',-1):
            inv=copy.deepcopy(self.f.inventory);inv['sources'][0]['count']=value
            with self.subTest(value=value),self.assertRaises(UsageConflict):inventory(inv,sources=self.f.sources,writers=self.f.writers)

    def test_empty_or_duplicated_sources_and_omitted_role_refused(self):
        f=self.f
        for src in ([],[f.sources[0]]*3,[dict(v,role='accounts') for v in f.sources]):
            inv=dict(f.inventory,sources=[dict(v,count=0,snapshot='a'*64) for v in src])
            with self.subTest(src=src),self.assertRaises(UsageConflict):inventory(inv,sources=src,writers=f.writers)

    def test_observer_outage_or_contradiction_never_starts(self):
        f=self.f;before=rows(f.control),rows(f.audit),f.untouched()
        for change in ({'legacy_disabled':False},{'route_closed':False},{'legacy_inflight':1},
                       {'bootstrap_inflight':1},{'inventory':'0'*64}):
            f.refresh(**change)
            with self.subTest(change=change),self.assertRaises(StateUnavailable):f.step('subscription')
        f.tick(31);f.h.release.n.a.now+=31
        with self.assertRaises(StateUnavailable):f.step('subscription')
        self.assertEqual((rows(f.control),rows(f.audit),f.untouched()),before)

    def test_observer_cannot_sign_operator_action(self):
        f=self.f;inner=f.inner('subscription');cmd=f.command('subscription',inner)
        cmd=f.sign(cmd['payload'],'observer')
        with self.assertRaises(StateUnavailable):f.x.execute(cmd,inner=inner)
        self.assertEqual(rows(f.control),[])

    def test_stage_order_and_inner_hash_are_pinned(self):
        f=self.f
        for stage in ('quota','release'):
            with self.assertRaises(UsageConflict):f.step(stage)
        inner=f.inner('subscription');cmd=f.command('subscription',inner,inner_sha256='0'*64)
        with self.assertRaises(StateUnavailable):f.x.execute(cmd,inner=inner)
        self.assertEqual(rows(f.audit),[])

    def test_completed_duplicate_after_expiry_is_read_only_without_observer(self):
        f=self.f;cmd,inner,result=f.step('subscription');before=rows(f.control),rows(f.audit),f.untouched()
        f.h.release.n.a.now+=1000
        with patch.object(f.observer,'load',side_effect=AssertionError('read-only completed replay')):
            self.assertEqual(f.x.execute(cmd,inner=inner),result)
        self.assertEqual((rows(f.control),rows(f.audit),f.untouched()),before)

    def test_lost_claim_response_requires_new_drain_observation(self):
        f=self.f;original=f.control.compare_exchange
        def lost(*args):original(*args);raise StateUnavailable('lost claim response')
        with patch.object(f.control,'compare_exchange',side_effect=lost),self.assertRaises(StateUnavailable):f.step('subscription')
        self.assertEqual(f.x.inspect()['status'],'running');self.assertEqual(rows(f.stores['subscription']),[])
        with self.assertRaises(StateUnavailable):f.step('subscription',recover=True)
        f.tick();f.step('subscription',recover=True);self.assertEqual(f.x.inspect()['status'],'complete')

    def test_lost_core_reply_recovers_without_reactivating_subscription(self):
        f=self.f;method=f.runtime.activate_fresh
        def lost(*args):method(*args);raise StateUnavailable('lost activation response')
        with patch.object(f.runtime,'activate_fresh',side_effect=lost),self.assertRaises(StateUnavailable):f.step('subscription')
        saved=rows(f.stores['subscription']),rows(f.stores['subscription_audit'])
        f.tick()
        with patch.object(f.runtime,'activate_fresh',side_effect=AssertionError('must read prior result')):f.step('subscription',recover=True)
        self.assertEqual((rows(f.stores['subscription']),rows(f.stores['subscription_audit'])),saved)

    def test_lost_completion_response_returns_exact_receipt(self):
        f=self.f;original=f.control.compare_exchange;inner=f.inner('subscription');cmd=f.command('subscription',inner)
        def lost(k,g,v):
            result=original(k,g,v)
            if v['status']=='complete':raise StateUnavailable('lost done response')
            return result
        with patch.object(f.control,'compare_exchange',side_effect=lost),self.assertRaises(StateUnavailable):f.x.execute(cmd,inner=inner)
        before=rows(f.control),rows(f.audit);receipt=f.x.inspect()
        self.assertEqual(f.x.execute(cmd,inner=inner),receipt)
        self.assertEqual((rows(f.control),rows(f.audit)),before)

    def test_missing_completion_audit_is_not_recreated_by_status(self):
        f=self.f;f.step('subscription');before=rows(f.control),rows(f.audit);load=f.audit.load
        missing=key('fresh-bootstrap-result',f.x.inspect()['result'])
        with patch.object(f.audit,'load',side_effect=lambda k:None if k==missing else load(k)),self.assertRaises(StateUnavailable):f.x.inspect()
        self.assertEqual((rows(f.control),rows(f.audit)),before)

    def test_quota_failure_stays_paused_until_exact_signed_recovery(self):
        f=self.f;f.step('subscription');before=f.untouched()
        with patch.object(f.provider,'enable_new',side_effect=StateUnavailable('provider unavailable')),self.assertRaises(StateUnavailable):f.step('quota')
        self.assertEqual(f.rollout.inspect()['phase'],'paused');pause=f.rollout._read()[1]
        f.tick()
        with self.assertRaises(UsageConflict):f.step('quota',recover=True)
        f.tick();inner=dict(action='resume-paused',expected_pause_sha256=digest(pause))
        f.step('quota',recover=True,inner=inner);f.step('release')
        self.assertEqual(f.reader.require_active()['phase'],'active');self.assertEqual(f.untouched(),before)

    def test_stale_pause_hash_cannot_resume(self):
        f=self.f;f.step('subscription')
        with patch.object(f.provider,'freeze_legacy',side_effect=StateUnavailable('offline')),self.assertRaises(StateUnavailable):f.step('quota')
        before=rows(f.control),rows(f.audit);f.tick()
        with self.assertRaises(UsageConflict):f.step('quota',recover=True,inner=dict(action='resume-paused',expected_pause_sha256='0'*64))
        self.assertEqual((rows(f.control),rows(f.audit)),before)

    def test_midnight_recovery_preserves_partial_state_and_requires_reconciliation(self):
        f=self.f
        with patch.object(f.runtime,'activate_fresh',side_effect=StateUnavailable('offline')),self.assertRaises(StateUnavailable):f.step('subscription')
        before=rows(f.control),rows(f.audit),f.untouched();f.tick(86400)
        with self.assertRaises(UsageConflict):f.step('subscription',recover=True)
        self.assertEqual((rows(f.control),rows(f.audit),f.untouched()),before)

    def test_new_plan_cannot_replace_partially_written_initialization(self):
        f=self.f;f.step('subscription');p=copy.deepcopy(f.plan);p['config']='0'*64
        alternate=f.make(plan=p,expected_plan=digest(p),expected_config='0'*64)
        before=rows(f.control),rows(f.audit)
        with self.assertRaises(StateUnavailable):alternate.inspect()
        self.assertEqual((rows(f.control),rows(f.audit)),before)

    def test_concurrent_claim_winner_only_executes(self):
        f=self.f;inner=f.inner('subscription');commands=[f.command('subscription',inner),f.command('subscription',inner)]
        original=f.control.compare_exchange;barrier=Barrier(2);calls=[];activate=f.runtime.activate_fresh
        def racing(k,g,v):
            if g==0:barrier.wait(timeout=5)
            return original(k,g,v)
        def counted(*a):calls.append(1);return activate(*a)
        def run(command):
            try:f.make().execute(command,inner=inner);return 'complete'
            except (StateUnavailable,UsageConflict):return 'conflict'
        with patch.object(f.control,'compare_exchange',side_effect=racing),patch.object(f.runtime,'activate_fresh',side_effect=counted),ThreadPoolExecutor(2) as pool:
            results=list(pool.map(run,commands))
        self.assertEqual(sorted(results),['complete','conflict']);self.assertEqual(calls,[1])

    def test_no_automatic_replay_of_an_unfinished_command(self):
        f=self.f;inner=f.inner('subscription');cmd=f.command('subscription',inner)
        with patch.object(f.runtime,'activate_fresh',side_effect=StateUnavailable('offline')),self.assertRaises(StateUnavailable):f.x.execute(cmd,inner=inner)
        before=rows(f.control),rows(f.audit)
        with self.assertRaises((StateUnavailable,UsageConflict)):f.x.execute(cmd,inner=inner)
        self.assertEqual((rows(f.control),rows(f.audit)),before)

    def test_same_physical_sqlite_lane_alias_refused(self):
        f=self.f
        for changes in ({'store':SQLiteStateStore(f.audit.path)},
                        {'observer':SQLiteStateStore(f.control.path)},
                        {'audit':SQLiteStateStore(f.stores['quota'].path)}):
            with self.subTest(changes=changes),self.assertRaises(UsageConflict):f.make(**changes)

    def test_nested_gcs_prefix_alias_refused(self):
        a=GCSStateStore(SimpleNamespace(name='synthetic-control'),prefix='ops/control')
        b=GCSStateStore(SimpleNamespace(name='synthetic-control'),prefix='ops/control/audit')
        with self.assertRaises(UsageConflict):self.f.make(store=a,audit=b)

    def test_plan_and_intent_storage_bounds_before_mutation(self):
        f=self.f;p=copy.deepcopy(f.plan);p['inventory']['writers']=['x'*9000]
        with self.assertRaises(UsageConflict):f.make(plan=p,expected_plan=digest(p))
        # A signed command that is not the exact inner schema must not create a
        # claim or an oversized audit, even when the outer signer approves it.
        inner=f.inner('subscription');inner['payload']['padding']='x'*12000
        inner=f.sign(inner['payload']);cmd=f.command('subscription',inner)
        with self.assertRaises(StateUnavailable):f.x.execute(cmd,inner=inner)
        self.assertEqual(rows(f.control),[]);self.assertEqual(rows(f.audit),[])

    def test_inner_pause_or_wrong_signature_rejected_before_claim(self):
        f=self.f;f.step('subscription');f.step('quota');before=rows(f.control),rows(f.audit)
        for action in ('service-release-pause','service-release-upgrade','service-release-resume'):
            inner=f.inner('release');inner['payload']['action']=action;inner=f.sign(inner['payload'])
            with self.subTest(action=action),self.assertRaises(StateUnavailable):f.x.execute(f.command('release',inner),inner=inner)
        inner=f.sign(f.inner('release')['payload'],'observer')
        with self.assertRaises(StateUnavailable):f.x.execute(f.command('release',inner),inner=inner)
        self.assertEqual((rows(f.control),rows(f.audit)),before)

    def test_lost_release_confirmation_recovers_exact_generation(self):
        f=self.f;f.step('subscription');f.step('quota')
        with patch.object(ServiceReleaseController,'_confirm',side_effect=StateUnavailable('lost release confirmation')),self.assertRaises(StateUnavailable):f.step('release')
        saved=f.reader.snapshot();self.assertEqual(saved[1]['phase'],'active')
        with self.assertRaises(StateUnavailable):f.reader.require_active()
        f.tick();f.step('release',recover=True,inner=f.inner('release',confirm=True))
        self.assertEqual(f.reader.snapshot(),saved)
        self.assertEqual(f.reader.require_active(),saved[1])

    def test_completed_bootstrap_replay_never_undoes_later_pause(self):
        f=self.f;f.step('subscription');f.step('quota');cmd,inner,result=f.step('release')
        paused=f.inner('release');paused['payload']['action']='service-release-pause';paused=f.sign(paused['payload'])
        ServiceReleaseController(reader=f.reader,runtime=f.runtime,rollout=f.rollout,evidence=f.evidence).apply(paused)
        saved=f.reader.snapshot();f.tick(1000)
        self.assertEqual(f.x.execute(cmd,inner=inner),result)
        self.assertEqual(f.reader.snapshot(),saved)
        with self.assertRaises(StateUnavailable):f.reader.require_active()

    def test_eight_uncertain_attempts_preserved_then_manual_reconciliation(self):
        f=self.f
        with patch.object(f.runtime,'activate_fresh',side_effect=StateUnavailable('offline')):
            for attempt in range(1,9):
                if attempt>1:f.tick()
                with self.assertRaises(StateUnavailable):f.step('subscription',recover=attempt>1)
                self.assertEqual(f.x.inspect()['attempt'],attempt)
        before=rows(f.control),rows(f.audit);f.tick()
        with self.assertRaises(UsageConflict):f.step('subscription',recover=True)
        self.assertEqual((rows(f.control),rows(f.audit)),before)

    def test_public_readers_cannot_admit_between_bootstrap_steps(self):
        f=self.f;f.step('subscription')
        with self.assertRaises(StateUnavailable):f.runtime._policy(f.runtime.links.active)
        self.assertFalse(f.rollout.admission_allowed(f.usage))
        f.step('quota')
        with self.assertRaises(StateUnavailable):f.runtime._policy(f.runtime.links.active)
        self.assertFalse(f.rollout.admission_allowed(f.usage))
        f.step('release')
        self.assertEqual(f.runtime._policy(f.runtime.links.active)['identity'],digest(f.sub))
        self.assertTrue(f.rollout.admission_allowed(f.usage))

    def test_conflicting_subscription_origin_is_retained_not_overwritten(self):
        f=self.f;other=dict(f.sub,namespace='other-fresh-origin');now=f.h.clock()
        old=dict(kind='lite-migration-observation/1',namespace=other['namespace'],manifest=digest(other),
                 source=other['source'],image=other['image'],inventory=other['inventory'],fence=other['fence'],
                 legacy_disabled=True,legacy_inflight=0,migration_inflight=0,stable_reader=True,issued_at=now,expires_at=now+30)
        f.stores['observer'].compare_exchange(key('observation',other['namespace']),0,f.sign(old,'observer'))
        approval=f.inner('subscription');approval['payload']['manifest']=digest(other)
        f.runtime.activate_fresh(other,f.sign(approval['payload']))
        saved=rows(f.stores['subscription']),rows(f.stores['subscription_audit'])
        with self.assertRaises(StateUnavailable):f.step('subscription')
        self.assertEqual((rows(f.stores['subscription']),rows(f.stores['subscription_audit'])),saved)
        self.assertEqual(rows(f.audit),[])

    def test_existing_fixture_records_and_audits_retained(self):
        f=self.f;before={k:rows(s) for k,s in f.original_stores.items()}
        f.finish()
        self.assertEqual({k:rows(s) for k,s in f.original_stores.items()},before)

    def test_real_gcs_cas_adapter_roundtrip_and_restart(self):
        f=self.f
        for attr in ('control','audit','observer'):
            setattr(f,attr,GCSStateStore(FakeBucket(),prefix='bootstrap-'+attr))
        f.refresh();f.x=f.make();f.step('subscription');f.x=f.make()
        f.step('quota');f.x=f.make();f.step('release')
        self.assertEqual(f.make().inspect()['status'],'complete')
        self.assertEqual(f.untouched(),{k:[] for k in f.untouched()})

    def test_gcs_lost_claim_ack_retains_generation_for_explicit_recovery(self):
        f=self.f;f.control=GCSStateStore(FakeBucket(),prefix='bootstrap-control');f.x=f.make()
        original=f.control.compare_exchange
        def lost(*a):original(*a);raise StateUnavailable('response lost after GCS generation write')
        with patch.object(f.control,'compare_exchange',side_effect=lost),self.assertRaises(StateUnavailable):f.step('subscription')
        saved=f.control.load(f.x.key);self.assertEqual(saved[1]['status'],'running')
        f.tick();f.x=f.make();f.step('subscription',recover=True)
        self.assertGreater(f.control.load(f.x.key)[0],saved[0])
        self.assertEqual(f.x.inspect()['attempt'],2)


if __name__=='__main__':unittest.main()
