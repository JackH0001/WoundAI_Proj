"""Offline owner commands exercise the actual runner/core with synthetic keys."""
import copy
import hashlib
import io
import json
from pathlib import Path
import secrets
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from PIL import Image

import lite_bootstrap_commands as commands
import lite_operational_materials as custody
from lite_attest_state import StateUnavailable
from lite_fresh_bootstrap import FreshBootstrapExecutor
from lite_service_release import ServiceReleaseController
from lite_subscription_executor import canonical
from lite_subscription_runtime import SubscriptionRuntime
from lite_usage_ledger import digest
from test_lite_fresh_bootstrap import b64, rows
import test_lite_fresh_bootstrap as bootstrap
import test_lite_fresh_bootstrap_runner as runner
import test_lite_fresh_bootstrap_factory as factory


class CommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):factory.FactoryTests.setUpClass()
    request=runner.RunnerTests.request
    invoke=runner.RunnerTests.invoke
    snapshot=runner.RunnerTests.snapshot
    def setUp(self):
        runner.RunnerTests.setUp(self)
        n=self.f.h.release.n;c=self.config
        keys={r:hashlib.sha256(('command-fixture-'+r).encode()).digest() for r in custody.ROLES}
        keys.update(operator=n.signer.private_bytes_raw(),observer=n.observer_signer.private_bytes_raw())
        self.material=custody.Material(dict(project=c['deployment']['project'],environment='owner-sandbox',
            namespace=c['namespace'],audience=c['app']['bundle'],issuer='https://appleid.apple.com',
            media_bucket='synthetic-media'), 'b'*32, self.f.h.clock(), keys)
        self.pin=digest(self.material.manifest());self.serial=0
    def prepare(self,stage,*,recover=False,receipt=None,**updates):
        receipt=self.invoke(self.request()) if receipt is None else receipt;self.serial+=1
        self.inputs=(canonical(self.runner),canonical(self.config),canonical(receipt))
        kw=dict(expected_config=digest(self.runner),expected_native=digest(self.config),expected_receipt=digest(receipt),
                expected_source=self.runner['deployment']['source'],expected_image=self.runner['deployment']['image'],
                stage=stage,recover=recover,nonce=f'{self.serial:064x}',clock=self.f.h.clock)
        kw.update(updates);return commands.prepare(*self.inputs,**kw)
    def sign(self,draft,**updates):
        kw=dict(material=self.material,expected_manifest=self.pin,key_id='operator',clock=self.f.h.clock)
        kw.update(updates);return commands.sign(draft,*self.inputs,**kw)
    def step(self,stage,*,recover=False):
        return self.invoke(self.sign(self.prepare(stage,recover=recover)))
    def test_review_and_sign_do_not_write_or_create_observation(self):
        receipt=self.invoke(self.request());before=self.snapshot();calls=self.providers.connected
        draft=self.prepare('subscription',receipt=receipt);request=self.sign(draft)
        self.assertEqual(before,self.snapshot());self.assertEqual(calls,self.providers.connected)
        self.assertFalse(draft['observer_created']);self.assertEqual(request['mode'],'execute')
        self.assertNotIn('signature',draft['command']);self.assertNotIn('inner_sha256',draft['command'])
    def test_three_reviewed_steps_and_http_replay_are_one_model_one_debit(self):
        old=self.f.untouched()
        for stage in ('subscription','quota','release'):self.assertEqual(self.step(stage)['result']['stage'],stage)
        self.assertEqual(old,self.f.untouched())
        h=self.f.h;host=bootstrap.BootstrapTests();host.h=h;host.backend=h.compose();host.client=host.backend.create_app().test_client()
        host.e=h.e;host.key=b64(h.e.key_id);host.counter=0;host.login_serial=991
        host.register();host.login();buf=io.BytesIO();Image.new('RGB',(64,64),(200,130,110)).save(buf,format='JPEG')
        host.f=SimpleNamespace(jpeg=buf.getvalue());host.image_hash=hashlib.sha256(host.f.jpeg).hexdigest()
        result=host.complete(1);self.assertEqual(host.run_request(1).json,result.json)
        self.assertEqual((h.model_calls,host.quota()['used']),(1,1))
    def test_same_signed_request_replay_keeps_exact_receipt_and_core(self):
        request=self.sign(self.prepare('subscription'));first=self.invoke(request);before=self.f.untouched()
        with patch.object(SubscriptionRuntime,'activate_fresh',side_effect=AssertionError('repeat activation')):
            again=self.invoke(request)
        self.assertEqual(first['result'],again['result']);self.assertEqual(before,self.f.untouched())
    def test_lost_subscription_response_recovered_without_second_activation(self):
        real=SubscriptionRuntime.activate_fresh
        def lost(runtime,*a):real(runtime,*a);raise StateUnavailable('synthetic reply lost')
        with patch.object(SubscriptionRuntime,'activate_fresh',lost),self.assertRaises(StateUnavailable):self.step('subscription')
        with self.assertRaises(ValueError):self.prepare('subscription')
        self.f.tick();saved=rows(self.states['subscription_audit'])
        with patch.object(SubscriptionRuntime,'activate_fresh',side_effect=AssertionError('duplicate activation')):
            self.assertEqual(self.step('subscription',recover=True)['result']['status'],'complete')
        self.assertEqual(saved,rows(self.states['subscription_audit']))
    def test_paused_quota_recovery_uses_inspected_pause_hash(self):
        self.step('subscription')
        from lite_fresh_bootstrap_factory import ClosedPreparationProvider
        with patch.object(ClosedPreparationProvider,'enable_new',side_effect=StateUnavailable('offline')),self.assertRaises(StateUnavailable):self.step('quota')
        self.f.tick();draft=self.prepare('quota',recover=True)
        self.assertEqual(draft['inner']['action'],'resume-paused')
        observed=json.loads(self.inputs[2])['inspection']['quota']
        self.assertEqual(draft['inner']['expected_pause_sha256'],digest(observed))
        self.assertEqual(self.invoke(self.sign(draft))['result']['status'],'complete')
        self.step('release')
    def test_release_confirmation_loss_prepares_confirm_not_republish(self):
        self.step('subscription');self.step('quota')
        with patch.object(ServiceReleaseController,'_confirm',side_effect=StateUnavailable('lost ack')),self.assertRaises(StateUnavailable):self.step('release')
        self.f.tick();draft=self.prepare('release',recover=True)
        self.assertEqual(draft['inner']['action'],'service-release-confirm')
        self.assertEqual(self.invoke(self.sign(draft))['result']['status'],'complete')
    def test_observer_absence_cannot_be_replaced_by_owner_signature(self):
        request=self.sign(self.prepare('subscription'))
        with patch.object(self.f.observer,'load',return_value=None),self.assertRaises(StateUnavailable):self.invoke(request)
        self.assertEqual(rows(self.states['subscription']),[])
    def test_foreign_or_modified_receipt_rejected(self):
        original=self.invoke(self.request())
        for update in ({'source':'a'*40},{'image':'a'*64},{'plan':'a'*64},{'request':'a'*64},
                       {'schema':'lite-bootstrap-runner-receipt/1'},{'mode':'execute'},
                       {'allocation':'x'},{'provider_mutation_requested':True},{'automatic_recovery':True}):
            with self.subTest(update=update),self.assertRaises(ValueError):self.prepare('subscription',receipt=dict(original,**update))
    def test_wrong_independent_input_pins_rejected(self):
        for field,value in [('expected_config','a'*64),('expected_native','a'*64),('expected_receipt','a'*64),
                            ('expected_source','a'*40),('expected_image','a'*64)]:
            with self.subTest(field=field),self.assertRaises(ValueError):self.prepare('subscription',**{field:value})
    def test_stale_or_future_observation_is_not_signable(self):
        original=self.invoke(self.request())
        for time in (self.f.h.clock()-61,self.f.h.clock()+1,True):
            altered=copy.deepcopy(original);altered['inspection']['observed_at']=time
            with self.subTest(time=time),self.assertRaises(ValueError):self.prepare('subscription',receipt=altered)
    def test_delayed_signing_requires_new_inspection(self):
        draft=self.prepare('subscription');self.f.tick(61)
        with self.assertRaises(ValueError):self.sign(draft)
    def test_unordered_and_implicit_recovery_refused(self):
        for stage in ('quota','release'):
            with self.subTest(stage=stage),self.assertRaises(ValueError):self.prepare(stage)
        with self.assertRaises(ValueError):self.prepare('subscription',recover=True)
        self.step('subscription')
        with self.assertRaises(ValueError):self.prepare('subscription')
    def test_draft_changes_cannot_broaden_signed_actions(self):
        original=self.prepare('subscription')
        for mutate in (lambda d:d['command'].update(action='bootstrap-recover'),
                       lambda d:d['command'].update(expected_state='a'*64),
                       lambda d:d['inner'].update(action='service-release-upgrade'),
                       lambda d:d.update(sign_inner=False),lambda d:d.update(observer_created=True)):
            d=copy.deepcopy(original);mutate(d)
            with self.assertRaises(ValueError):self.sign(d)
    def test_wrong_manifest_role_key_or_scope_refused(self):
        draft=self.prepare('subscription')
        with self.assertRaises(ValueError):self.sign(draft,expected_manifest='a'*64)
        with self.assertRaises(ValueError):self.sign(draft,key_id='observer')
        wrong=copy.deepcopy(self.material);wrong.keys['operator']=secrets.token_bytes(32)
        with self.assertRaises(ValueError):self.sign(draft,material=wrong,expected_manifest=digest(wrong.manifest()))
        for field,value in [('environment','production'),('project','other-project'),('namespace','other'),('audience','com.other.app')]:
            wrong=copy.deepcopy(self.material);wrong.scope[field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):self.sign(draft,material=wrong,expected_manifest=digest(wrong.manifest()))
    def test_same_backup_and_draft_produce_same_request_without_private_output(self):
        draft=self.prepare('subscription');request=self.sign(draft)
        self.assertEqual(request,self.sign(draft))
        for raw in self.material.keys.values():
            self.assertNotIn(raw.hex().encode(),canonical(request));self.assertNotIn(b64(raw).encode(),canonical(request))
    def test_late_state_change_blocks_old_request_without_overwrite(self):
        request=self.sign(self.prepare('subscription'));self.step('subscription');before=self.snapshot()
        with self.assertRaises(StateUnavailable):self.invoke(request)
        # A failed invocation can consume an operational budget slice; existing
        # subscription/usage records and completed bootstrap receipt stay intact.
        for lane in ('subscription','subscription_audit','quota','bootstrap','bootstrap_audit'):
            self.assertEqual(before[lane],self.snapshot()[lane])
    def test_inspection_detects_control_change_without_writes(self):
        before=self.snapshot()
        with patch.object(FreshBootstrapExecutor,'inspect',side_effect=[None,{'changed':True}]),self.assertRaises(StateUnavailable):self.invoke(self.request())
        self.assertEqual(before,self.snapshot())
    def test_inspection_core_failure_is_not_empty_or_new_authority(self):
        before=self.snapshot()
        with patch('lite_usage_rollout.UsageRollout._read',side_effect=StateUnavailable('unreadable')),self.assertRaises(StateUnavailable):self.invoke(self.request())
        self.assertEqual(before,self.snapshot())
    def test_draft_expiration_is_bounded_by_budget_and_day(self):
        self.runner['provider_budget']['expires_at']=self.f.h.clock()+45
        self.assertEqual(self.prepare('subscription')['command']['expires_at'],self.f.h.clock()+45)
        self.f.tick(86400)
        with self.assertRaises(ValueError):self.prepare('subscription')
    def test_cli_prepare_preserves_output_and_refuses_noninteractive_sign(self):
        root=self.f.h.release.n.f.root.resolve()/'commands';root.mkdir(mode=0o700)
        draft=self.prepare('subscription')
        for name,data in zip(('runner','native','inspection'),self.inputs):(root/(name+'.json')).write_bytes(data)
        (root/'draft.json').write_bytes(canonical(draft))
        script=Path(commands.__file__);common=[sys.executable,'-B',str(script)]
        paths=['--config',str(root/'runner.json'),'--native',str(root/'native.json'),'--inspection',str(root/'inspection.json')]
        args=common+['prepare']+paths+['--output',str(root/'prepared.json'),'--stage','subscription',
            '--config-sha256',digest(self.runner),'--native-sha256',digest(self.config),'--inspection-sha256',draft['receipt'],
            '--source',draft['source'],'--image',draft['image']]
        # CLI real clock differs from the fixture: reject expired window before
        # any output. Successful terminal flow is tested with an injected clock.
        r=subprocess.run(args,input=b'',capture_output=True)
        self.assertNotEqual(r.returncode,0);self.assertFalse((root/'prepared.json').exists())
        args=common+['sign']+paths+['--output',str(root/'signed.json'),'--draft',str(root/'draft.json'),
            '--draft-sha256',digest(draft),'--backup',str(root/'missing-backup'),'--custody-receipt',str(root/'missing-receipt'),'--key-id','operator']
        r=subprocess.run(args,input=b'',capture_output=True)
        self.assertNotEqual(r.returncode,0);self.assertFalse((root/'signed.json').exists())
        self.assertNotIn(b'Traceback',r.stdout+r.stderr)


if __name__=='__main__':unittest.main()
