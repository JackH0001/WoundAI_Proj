"""Real bootstrap/ledger core, synthetic stores, no cloud or customer images."""
import copy
import hashlib
import io
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from PIL import Image
from lite_attest_state import SQLiteStateStore, StateUnavailable
from lite_fresh_bootstrap_runner import run, read_regular
from lite_provider_budget import BudgetUnavailable
from lite_subscription_executor import canonical
from lite_subscription_runtime import SubscriptionRuntime
from lite_usage_ledger import digest
import test_lite_fresh_bootstrap_factory as factory_tests
import test_lite_fresh_bootstrap as bootstrap_tests
from test_lite_fresh_bootstrap import b64, rows


class Providers:
    def __init__(self, host):
        self.host=host;self.connected=0;self.closed=0;self.activated=[]
    def connect(self,c,*,clock):self.connected+=1
    def factory(self,c):
        def factory(name,loc):
            self.host.provider_calls.append(name)
            return self.host.states[name]
        return factory
    def activate(self,allocation):self.activated.append(allocation.name)
    def close(self):self.closed+=1


class RunnerTests(unittest.TestCase):
    setUpClass=classmethod(lambda cls:factory_tests.FactoryTests.setUpClass())
    def setUp(self):
        factory_tests.FactoryTests.setUp(self)
        f=self.f;self.provider_calls=[];self.providers=Providers(self)
        for name in ('control','audit'):
            self.states['budget-'+name]=SQLiteStateStore(f.h.release.n.f.root/('runner-budget-'+name+'.db'))
        d=self.config['deployment'];now=f.h.clock()
        self.runner=dict(schema='lite-bootstrap-runner/1',enabled=True,
            deployment={**{k:d[k] for k in ('project','project_number','source','image')},
                        'service_account':'lite-bootstrap-operator@'+d['project']+'.iam.gserviceaccount.com'},
            native_config=digest(self.config),plan=f.plan,sources=f.sources,writers=f.writers,
            locations=self.locations,buckets={v['bucket']:{'metageneration':'3'} for v in self.locations.values()},
            budget_stores={name:dict(bucket=self.locations['gate']['bucket'],
                prefix='lite-bootstrap/'+f.plan['namespace']+'/budget-'+name) for name in ('control','audit')},
            provider_budget=dict(v=1,namespace=f.plan['namespace'],manifest=digest(f.plan),source=d['source'],image=d['image'],
                not_before=now-1,expires_at=now+1800,max_allocations=8,requests=3000,send_bytes=1024**2,receive_bytes=64*1024**2,seconds=300),
            bootstrap_budget=dict(requests=128,send_bytes=1024**2,receive_bytes=8*1024**2,seconds=120))
    def request(self,stage=None,recover=False):
        inner=None if stage is None else self.f.inner(stage)
        return dict(schema='lite-bootstrap-request/1',plan=digest(self.f.plan),mode='inspect' if stage is None else 'execute',
            command=None if stage is None else self.f.command(stage,inner,recover),inner=inner)
    def invoke(self,request,**updates):
        r=self.runner
        args=dict(raw=canonical(r),native_raw=canonical(self.config),request_raw=canonical(request),
            expected_config=digest(r),expected_native=digest(self.config),expected_request=digest(request),
            expected_source=r['deployment']['source'],expected_image=r['deployment']['image'],providers=self.providers,clock=self.f.h.clock)
        args.update(updates);return run(**args)
    def snapshot(self):return {n:rows(s) for n,s in self.states.items()}
    def test_inspect_never_allocates_or_writes(self):
        before=self.snapshot();result=self.invoke(self.request())
        self.assertIsNone(result['result']);self.assertIsNone(result['allocation'])
        self.assertEqual(before,self.snapshot());self.assertEqual(self.providers.activated,[])
        self.assertNotIn('budget-control',self.provider_calls);self.assertEqual(self.providers.closed,1)
    def test_three_one_shot_commands_activate_real_core_and_preserve_old_data(self):
        before=self.f.untouched()
        for stage in ('subscription','quota','release'):
            result=self.invoke(self.request(stage));self.assertEqual(result['result']['stage'],stage)
        self.assertEqual(self.f.untouched(),before)
        self.assertEqual(self.f.reader.require_active()['phase'],'active')
        self.assertEqual(len(set(self.providers.activated)),3)
    def test_duplicate_complete_step_never_activates_again(self):
        request=self.request('subscription');first=self.invoke(request)
        before=rows(self.states['subscription']),rows(self.states['subscription_audit'])
        with patch.object(SubscriptionRuntime,'activate_fresh',side_effect=AssertionError('repeat activation')):
            again=self.invoke(request)
        self.assertEqual(first['result'],again['result'])
        self.assertEqual(before,(rows(self.states['subscription']),rows(self.states['subscription_audit'])))
    def test_lost_reply_requires_signed_recovery_and_keeps_audit(self):
        original=SubscriptionRuntime.activate_fresh
        def lost(runtime,*args):original(runtime,*args);raise StateUnavailable('synthetic reply lost')
        with patch.object(SubscriptionRuntime,'activate_fresh',lost),self.assertRaises(StateUnavailable):
            self.invoke(self.request('subscription'))
        self.assertEqual(self.f.x.inspect()['status'],'running')
        saved=rows(self.states['subscription_audit']);self.f.tick()
        with patch.object(SubscriptionRuntime,'activate_fresh',side_effect=AssertionError('repeat activation')):
            result=self.invoke(self.request('subscription',recover=True))
        self.assertEqual(result['result']['status'],'complete');self.assertEqual(rows(self.states['subscription_audit']),saved)
        self.assertEqual(self.providers.closed,2)
    def test_budget_exhaustion_does_not_advance_or_refund(self):
        self.runner['provider_budget']['max_allocations']=1
        self.invoke(self.request('subscription'));before=self.snapshot()
        with self.assertRaises(BudgetUnavailable):self.invoke(self.request('quota'))
        self.assertEqual(self.snapshot(),before)
        self.assertEqual(self.f.x.inspect()['stage'],'subscription')
    def test_unknown_allocation_outcome_never_dispatches(self):
        control=self.states['budget-control'];write=control.compare_exchange
        def lost(*args):write(*args);raise StateUnavailable('lost')
        with patch.object(control,'compare_exchange',lost),self.assertRaises(StateUnavailable):self.invoke(self.request('subscription'))
        self.assertIsNone(self.f.x.inspect());self.assertEqual(self.providers.activated,[])
    def test_stale_or_open_route_observation_cannot_activate(self):
        self.f.refresh(route_closed=False)
        with self.assertRaises(StateUnavailable):self.invoke(self.request('subscription'))
        self.assertIsNone(self.f.x.inspect());self.assertEqual(rows(self.states['subscription']),[])
    def test_closed_window_never_connects(self):
        self.runner['provider_budget']['expires_at']=self.f.h.clock()
        with self.assertRaises(ValueError):self.invoke(self.request())
        self.assertEqual(self.providers.connected,0)
    def test_wrong_pins_never_connect(self):
        for field,value in [('expected_config','0'*64),('expected_native','0'*64),('expected_request','0'*64),
                            ('expected_source','0'*40),('expected_image','0'*64)]:
            with self.subTest(field=field),self.assertRaises(ValueError):self.invoke(self.request(),**{field:value})
        self.assertEqual(self.providers.connected,0)
    def test_customer_identity_cannot_initialize_itself(self):
        self.runner['deployment']['service_account']=self.config['deployment']['service_account']
        with self.assertRaises(ValueError):self.invoke(self.request())
        self.assertEqual(self.providers.connected,0)
    def test_budget_cannot_alias_any_customer_or_control_lane(self):
        for loc in (self.config['stores']['grants'],self.locations['gate'],self.runner['budget_stores']['audit']):
            self.runner['budget_stores']['control']=copy.deepcopy(loc)
            with self.assertRaises(ValueError):self.invoke(self.request())
        self.assertEqual(self.providers.connected,0)
    def test_wrong_plan_budget_does_not_allocate(self):
        self.runner['provider_budget']['manifest']='0'*64
        with self.assertRaises(ValueError):self.invoke(self.request('subscription'))
        self.assertEqual(self.providers.connected,0)
    def test_inspect_cannot_smuggle_a_command(self):
        request=self.request('subscription');request['mode']='inspect'
        with self.assertRaises(ValueError):self.invoke(request)
        self.assertEqual(self.providers.connected,0)
    def test_unsigned_or_changed_inner_command_never_connects(self):
        for change in ('signature','inner'):
            request=self.request('subscription')
            if change=='signature':request['command']['signature']='bad'
            else:request['inner']=None
            with self.subTest(change=change),self.assertRaises((ValueError,StateUnavailable)):self.invoke(request)
        self.assertEqual(self.providers.connected,0)
    def test_corrupt_core_plan_never_connects(self):
        self.runner['plan']['usage']['namespace']='wrong-namespace'
        self.runner['provider_budget']['manifest']=digest(self.runner['plan'])
        with self.assertRaises(ValueError):self.invoke(self.request())
        self.assertEqual(self.providers.connected,0)
    def test_bucket_list_must_be_exact(self):
        self.runner['buckets']['unreviewed-bucket']={'metageneration':'1'}
        with self.assertRaises(ValueError):self.invoke(self.request())
        self.assertEqual(self.providers.connected,0)
    def test_provider_connection_failure_still_closes_transport(self):
        with patch.object(self.providers,'connect',side_effect=StateUnavailable('offline')),self.assertRaises(StateUnavailable):
            self.invoke(self.request())
        self.assertEqual(self.providers.closed,1);self.assertEqual(self.provider_calls,[])
    def test_customer_replay_after_bootstrap_debits_only_once(self):
        for stage in ('subscription','quota','release'):self.invoke(self.request(stage))
        h=self.f.h;host=bootstrap_tests.BootstrapTests();host.h=h;host.backend=h.compose();host.client=host.backend.create_app().test_client()
        host.e=h.e;host.key=b64(h.e.key_id);host.counter=0;host.login_serial=870
        host.register();host.login();buf=io.BytesIO();Image.new('RGB',(64,64),(190,100,120)).save(buf,format='JPEG')
        host.f=SimpleNamespace(jpeg=buf.getvalue());host.image_hash=hashlib.sha256(host.f.jpeg).hexdigest()
        first=host.complete(1);again=host.run_request(1)
        self.assertEqual(first.json,again.json);self.assertEqual((h.model_calls,host.quota()['used']),(1,1))
        old=rows(self.states['bootstrap']),rows(self.states['bootstrap_audit'])
        self.f.tick(31);host.backend=h.compose();host.client=host.backend.create_app().test_client()
        self.assertEqual(host.run_request(1).json,first.json);self.assertEqual((h.model_calls,host.quota()['used']),(1,1))
        self.assertEqual(old,(rows(self.states['bootstrap']),rows(self.states['bootstrap_audit'])))


class PublicInputTests(unittest.TestCase):
    def test_regular_bounded_input_and_links(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'config.json';p.write_bytes(b'{}')
            self.assertEqual(read_regular(p,2),b'{}')
            with self.assertRaises(ValueError):read_regular(p,1)
            link=Path(td)/'link';link.symlink_to(p)
            with self.assertRaises(OSError):read_regular(link,2)
    def test_relative_input_refused(self):
        with self.assertRaises(ValueError):read_regular('untrusted.json',100)


if __name__=='__main__':unittest.main()
