"""Whole runner -> existing retirement core, with synthetic GCS/KMS only.

Original AES envelopes and append-only audits are real; no cloud credentials,
research records, Apple grants or live key versions enter these tests.
"""
import base64
import copy
import hashlib
import io
import json
import os
import socket
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import requests
from urllib3.response import HTTPResponse

from lite_attest_state import GCSStateStore, StateUnavailable
from lite_key_lifecycle_dispatch import PinnedKeyLifecycle
from lite_provider_budget import Allocation, BudgetUnavailable
from lite_disposition_bootstrap import StartupCredentials
from lite_shared_retirement_runner import (run, preflight, build, SchemaStore, RestrictedGCS,
    RecoveryOnlySession, RetirementProviders, READ, LANES)
from lite_store_locations import location
from lite_subscription_executor import canonical, key
from lite_usage_ledger import digest
from test_lite_attest_state import FakeBucket
from test_lite_shared_key_retirement import RetirementFixture
from test_lite_rotating_grant_keys import V1,V2,RING,KEK
from test_lite_subscription_grace import rows
import test_lite_native_purchase as native


class ProvidersDouble:
    def __init__(self,f):self.f=f;self.connected=0;self.activated=0;self.closed=0
    def connect(self,c,*,clock):self.connected+=1
    def factory(self):return lambda n,l:self.f.stores[(l['bucket'],l['prefix'])]
    def activate(self,allocation):
        self.activated+=1
        self.recovery=self.f.x.f.kms;self.lifecycle=self.f.x.provider;self.inventory=self.f.inventory
    def close(self):self.closed+=1


class RunnerFixture:
    def __init__(self,host):
        self.x=RetirementFixture(host);x=self.x;self.stores={};x.now=x.f.lifecycle.now()
        def migrate(name,old=None):
            if isinstance(old,GCSStateStore):s=old
            else:
                b=FakeBucket();b.name='synthetic-retirement-state'
                prefix=('native/'+name) if name in READ else ('lite-retirement/test-run/'+name)
                if name.startswith('budget_'):prefix='lite-retirement-budget/test-run/'+name
                s=GCSStateStore(b,prefix=prefix)
                if old is not None:
                    for k,_,raw in rows(old):s.compare_exchange(bytes.fromhex(k),0,json.loads(raw))
            self.stores[(s.bucket.name,s.prefix)]=s;return s
        f=x.f
        f.key_store=f.cipher.store=migrate('custody',f.cipher.store)
        f.key_audit=f.cipher.audit=migrate('custody_audit',f.cipher.audit)
        f.lifecycle.store=migrate('lifecycle',f.lifecycle.store)
        f.lifecycle.audit=migrate('lifecycle_audit',f.lifecycle.audit)
        x.reviews=migrate('reviews');x.state=migrate('dispatch');x.audit=migrate('dispatch_audit')
        for s in x.x.sources:s['project_number']='999999'
        x.repin()
        lane_stores=dict(custody=f.cipher.store,custody_audit=f.cipher.audit,lifecycle=f.lifecycle.store,
            lifecycle_audit=f.lifecycle.audit,reviews=x.reviews,dispatch=x.state,dispatch_audit=x.audit)
        loc={n:dict(bucket=s.bucket.name,prefix=s.prefix) for n,s in lane_stores.items()}
        budgets={n:migrate('budget_'+n) for n in ('control','audit')}
        budgets={n:dict(bucket=s.bucket.name,prefix=s.prefix) for n,s in budgets.items()}
        self.c=dict(schema='lite-shared-retirement-runner/1',enabled=True,operation_namespace='test-run',
            deployment=dict(project='synthetic-only',project_number='999999',
                service_account='retirement-test@synthetic-only.iam.gserviceaccount.com',source='a'*40,image='b'*64),
            binding=f.lifecycle.binding,service_catalog=f.lifecycle.service_catalog,keyring=RING,
            shared_key=dict(scheme='shared-kms-envelope-1',crypto_key=KEK,write_version=V2,read_versions=[V1,V2]),
            manifest=x.m,signers={n:base64.b64encode(v).decode() for n,v in zip(('operator','drain','reviewer'),x.keys)},
            locations=loc,buckets={l['bucket']:dict(metageneration='1') for l in (*loc.values(),*budgets.values())},
            budget_stores=budgets,provider_budget=dict(v=1,namespace='test-run',manifest=digest(x.m),
                source='a'*40,image='b'*64,not_before=x.now,expires_at=x.now+500,max_allocations=20,
                requests=1000,send_bytes=1024**2,receive_bytes=128*1024**2,seconds=300),
            bootstrap_budget=dict(requests=128,send_bytes=1024**2,receive_bytes=8*1024**2,seconds=120))
        self.providers=[]
    def inventory(self,*args,**kw):
        code,body=self.x.x.get(*args,**kw)
        if isinstance(body,dict) and 'projectNumber' in body:body['projectNumber']='999999'
        return code,body
    def request(self,mode,**payload):
        return dict(schema='lite-shared-retirement-request/1',manifest=digest(self.c['manifest']),mode=mode,payload=payload)
    def call(self,mode,**payload):
        p=ProvidersDouble(self);self.providers.append(p);r=self.request(mode,**payload)
        return run(canonical(self.c),canonical(r),expected_config=digest(self.c),expected_request=digest(r),
            expected_source='a'*40,expected_image='b'*64,providers=p,clock=lambda:self.x.now)
    def snapshot(self):
        return {n:copy.deepcopy((self.stores[(l['bucket'],l['prefix'])].bucket.live,
                                 self.stores[(l['bucket'],l['prefix'])].bucket.versions))
                for n,l in self.c['locations'].items() if n in READ}


class RunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):native.legacy.LinksTests.setUpClass()
    def setUp(self):
        n=patch.object(socket.socket,'connect',side_effect=AssertionError('external network forbidden'))
        n.start();self.addCleanup(n.stop);self.f=RunnerFixture(self);self.x=self.f.x
    def prepared(self):return self.f.call('prepare')['result']['review_sha256']
    def plan(self,review,action='disable'):return self.f.call('plan',review=review,action=action)['result']
    def execute(self,p,c=None):
        return self.f.call('execute',plan=p,command=c or self.x.command(p),scope=self.x.scope(p))
    def bad_config(self,mutate):
        c=copy.deepcopy(self.f.c);mutate(c);r=self.f.request('prepare');p=ProvidersDouble(self.f)
        with self.assertRaises((ValueError,StateUnavailable,KeyError,TypeError)):
            run(canonical(c),canonical(r),expected_config=digest(c),expected_request=digest(r),
                expected_source='a'*40,expected_image='b'*64,providers=p,clock=lambda:self.x.now)
        self.assertEqual(p.connected,0)
    def test_complete_runner_disable_schedule_restore_preserves_original_data(self):
        before=self.f.snapshot();review=self.prepared()
        for action,state in (('disable','DISABLED'),('schedule','DESTROY_SCHEDULED'),('restore','DISABLED')):
            p=self.plan(review,action);result=self.execute(p)
            self.assertEqual(result['result']['provider_state'],state)
            self.assertFalse(result['account_deleted']);self.assertFalse(result['automatic_retry'])
        self.assertEqual(len(self.x.mutations()),3);self.assertEqual(self.f.snapshot(),before)
        self.assertTrue(all(p.closed==1 for p in self.f.providers))
        self.assertNotIn('wrapped_dek',json.dumps(result));self.assertNotIn('private-refresh-token',json.dumps(result))
    def test_lost_mutation_reply_cannot_be_retried_reconcile_does_not_mutate(self):
        p=self.plan(self.prepared());c=self.x.command(p)
        self.x.wire.after=lambda a:(_ for _ in ()).throw(TimeoutError()) if a=='disable' else None
        with self.assertRaises(StateUnavailable):self.execute(p,c)
        self.x.wire.after=lambda a:None
        with self.assertRaises(StateUnavailable):self.execute(p,c)
        self.assertEqual(len(self.x.mutations()),1)
        result=self.f.call('reconcile',plan=p,drain=self.x.drain(p,c))
        self.assertEqual(result['result']['provider_state'],'DISABLED')
        self.assertEqual(len(self.x.mutations()),1)
    def test_new_budget_window_reconciles_old_plan_without_moving_journals(self):
        review=self.prepared();self.execute(self.plan(review))
        plan=self.plan(review,'schedule');command=self.x.command(plan);self.execute(plan,command)
        before=self.x.mutations().copy();original_dispatch=self.f.c['locations']['dispatch'].copy()
        self.x.wire.finish_destruction();self.x.now+=31*86400
        budget=self.f.c['provider_budget'];budget.update(namespace='next-window',not_before=self.x.now,expires_at=self.x.now+500)
        for n,l in self.f.c['budget_stores'].items():
            prior=self.f.stores[(l['bucket'],l['prefix'])]
            l['prefix']='lite-retirement-budget/next-window/'+n
            self.f.stores[(l['bucket'],l['prefix'])]=GCSStateStore(prior.bucket,prefix=l['prefix'])
        result=self.f.call('reconcile',plan=plan,drain=self.x.drain(plan,command))
        self.assertEqual(result['result']['provider_state'],'DESTROYED')
        self.assertEqual(self.x.mutations(),before)
        self.assertEqual(self.f.c['locations']['dispatch'],original_dispatch)
    def test_finished_replay_and_inspection_never_repeat_provider_work(self):
        p=self.plan(self.prepared());c=self.x.command(p);a=self.execute(p,c)
        kms=self.x.wire.calls.copy();crypto=self.x.f.kms.calls.copy()
        b=self.execute(p,c);self.f.call('inspect',plan=p)
        self.assertEqual(a['result'],b['result']);self.assertEqual(kms,self.x.wire.calls);self.assertEqual(crypto,self.x.f.kms.calls)
    def test_unrecovered_backup_blocks_whole_runner(self):
        b=self.x.x.buckets['synthetic-custody-mirror']
        name=self.x.x.store._name(self.x.f.cipher._key(self.x.b));del b.live[name]
        with self.assertRaises(StateUnavailable):self.prepared()
        self.assertEqual(self.x.mutations(),[])
    def test_missing_custody_configuration_does_not_initialize_live_namespace(self):
        s=self.x.f.cipher.store;n=s._name(key('account-grant-cipher-config',self.x.f.cipher.scope));del s.bucket.live[n]
        before=self.f.snapshot()
        with self.assertRaises(StateUnavailable):self.prepared()
        self.assertEqual(self.f.snapshot(),before)
    def test_missing_lifecycle_configuration_does_not_initialize(self):
        s=self.x.f.lifecycle.store;n=s._name(key('account-lifecycle-config',self.f.c['binding']['namespace']));del s.bucket.live[n]
        before=self.f.snapshot()
        with self.assertRaises(StateUnavailable):self.prepared()
        self.assertEqual(self.f.snapshot(),before)
    def test_budget_exhaustion_survives_a_new_runner(self):
        self.f.c['provider_budget']['max_allocations']=1
        self.prepared()
        with self.assertRaises(BudgetUnavailable):self.prepared()
        self.assertEqual(self.f.providers[-1].activated,0);self.assertEqual(self.f.providers[-1].closed,1)
    def test_lost_allocation_ack_cannot_dispatch(self):
        l=self.f.c['budget_stores']['control'];s=self.f.stores[(l['bucket'],l['prefix'])];old=s.compare_exchange
        def lost(k,rev,v):
            ok=old(k,rev,v)
            if 'allocations' in v:raise StateUnavailable('lost acknowledgement')
            return ok
        with patch.object(s,'compare_exchange',side_effect=lost),self.assertRaises(BudgetUnavailable):self.prepared()
        self.assertEqual(self.f.providers[-1].activated,0);self.assertEqual(self.x.mutations(),[])
    def test_wrong_signature_is_rejected_before_credentials(self):
        p=self.plan(self.prepared());c=self.x.command(p);c['signature']=base64.b64encode(bytes(64)).decode()
        with self.assertRaises(ValueError):self.execute(p,c)
        self.assertEqual(self.f.providers[-1].connected,0)
    def test_scope_review_signature_is_required_before_credentials(self):
        p=self.plan(self.prepared());c=self.x.command(p);s=self.x.scope(p);s['signature']=base64.b64encode(bytes(64)).decode()
        with self.assertRaises(StateUnavailable):self.f.call('execute',plan=p,command=c,scope=s)
        self.assertEqual(self.f.providers[-1].connected,0)
    def test_expired_command_rejected_before_credentials(self):
        p=self.plan(self.prepared());c=self.x.command(p);self.x.now+=301
        with self.assertRaises(ValueError):self.execute(p,c)
        self.assertEqual(self.f.providers[-1].connected,0)
    def test_unknown_request_mode_rejected_before_credentials(self):
        with self.assertRaises(ValueError):self.f.call('destroy_all')
        self.assertEqual(self.f.providers[-1].connected,0)
    def test_extra_action_payload_rejected(self):
        with self.assertRaises(ValueError):self.f.call('prepare',force=True)
        self.assertEqual(self.f.providers[-1].connected,0)
    def test_disabled_configuration_rejected(self):self.bad_config(lambda c:c.update(enabled=False))
    def test_source_mismatch_rejected(self):self.bad_config(lambda c:c['deployment'].update(source='f'*40))
    def test_image_mismatch_rejected(self):self.bad_config(lambda c:c['deployment'].update(image='f'*64))
    def test_other_project_key_rejected(self):self.bad_config(lambda c:c.update(keyring=RING.replace('synthetic-only','other-project')))
    def test_same_signer_rejected(self):self.bad_config(lambda c:c['signers'].update(drain=c['signers']['operator']))
    def test_wrong_lifecycle_scope_rejected(self):self.bad_config(lambda c:c['binding'].update(namespace='other'))
    def test_wrong_application_rejected(self):self.bad_config(lambda c:c['binding'].update(audience='com.woundai.medical'))
    def test_alias_budget_and_custody_rejected(self):self.bad_config(lambda c:c['budget_stores'].update(control=c['locations']['custody']))
    def test_alias_review_and_dispatch_rejected(self):self.bad_config(lambda c:c['locations'].update(reviews=c['locations']['dispatch']))
    def test_extra_bucket_rejected(self):self.bad_config(lambda c:c['buckets'].update(unlisted=dict(metageneration='1')))
    def test_wrong_manifest_location_rejected(self):self.bad_config(lambda c:c['locations']['custody'].update(prefix='other/keys'))
    def test_unpinned_input_rejected_without_provider(self):
        p=ProvidersDouble(self.f);r=self.f.request('prepare')
        with self.assertRaises(ValueError):run(canonical(self.f.c),canonical(r),expected_config='0'*64,
            expected_request=digest(r),expected_source='a'*40,expected_image='b'*64,providers=p,clock=lambda:1000)
        self.assertEqual(p.connected,0)
    def test_storage_adapters_enforce_read_and_create_only(self):
        raw=self.x.reviews;k=key('test','item');w=RestrictedGCS(raw,'create');w.compare_exchange(k,0,{'ok':True})
        with self.assertRaises(StateUnavailable):w.compare_exchange(k,1,{'ok':False})
        with self.assertRaises(StateUnavailable):RestrictedGCS(raw,'read').compare_exchange(key('test','new'),0,{})
    def test_recovery_transport_cannot_encrypt_disable_or_other_key(self):
        session=RecoveryOnlySession(self.x.f.kms,self.f.c['shared_key']);before=self.x.f.kms.calls.copy()
        for method,path in [('POST',V2+':encrypt'),('PATCH',V1),('POST',V1+':destroy'),('POST',KEK+'-other:decrypt')]:
            with self.assertRaises(StateUnavailable):session.request(method,'https://cloudkms.googleapis.com/v1/'+path)
        self.assertEqual(self.x.f.kms.calls,before)
    def test_attached_provider_requires_no_background_requests_before_acquire(self):
        p=RetirementProviders()
        with patch.dict(os.environ,{'DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA':'false'}),patch('lite_shared_retirement_runner.acquire') as acquire:
            with self.assertRaises(ValueError):p.connect(self.f.c,clock=lambda:1000)
            acquire.assert_not_called()
    def test_real_provider_composition_uses_only_attached_finite_credentials(self):
        c=self.f.c;now=self.x.now
        credentials=StartupCredentials(c['deployment']['service_account'],'synthetic-bearer-token-only',
            wall_end=now+1000,mono_end=now+1000,clock=lambda:self.x.now,monotonic=lambda:self.x.now)
        meter=Allocation(c['bootstrap_budget'],'control',{'expires_at':now+120},clock=lambda:self.x.now,monotonic=lambda:self.x.now)
        work=Allocation(c['provider_budget'],'work',{'expires_at':now+300},clock=lambda:self.x.now,monotonic=lambda:self.x.now)
        p=RetirementProviders()
        with patch.dict(os.environ,{'DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA':'true'}), \
                patch('lite_shared_retirement_runner.acquire',return_value=(credentials,meter)) as acquire, \
                patch('google.auth.default',side_effect=AssertionError('ADC forbidden')):
            p.connect(c,clock=lambda:self.x.now);p.activate(work)
            acquire.assert_called_once()
            self.assertTrue(p.control.closed)
            self.assertIs(p.storage.allocation,work);self.assertIs(p.recovery.allocation,work)
            self.assertIs(p.lifecycle.session._metered.allocation,work)
            self.assertEqual(p.storage.session._max_refresh_attempts,0)
            self.assertEqual(p.recovery.session._max_refresh_attempts,0)
            self.x.now+=1001
            with self.assertRaises(StateUnavailable):p.lifecycle.describe()
        p.close();self.assertIsNone(credentials.token)
    def test_reconcile_invalid_drain_stops_before_credentials(self):
        p=self.plan(self.prepared());c=self.x.command(p);self.execute(p,c)
        d=self.x.drain(p,c);d['signature']=base64.b64encode(bytes(64)).decode()
        with self.assertRaises(ValueError):self.f.call('reconcile',plan=p,drain=d)
        self.assertEqual(self.f.providers[-1].connected,0)
    def test_expired_dispatch_plan_stops_before_credentials(self):
        p=self.plan(self.prepared());self.x.now+=901
        # Fresh finite budget, original operation paths preserved.
        b=self.f.c['provider_budget'];b.update(not_before=self.x.now,expires_at=self.x.now+400)
        with self.assertRaises(ValueError):self.execute(p)
        self.assertEqual(self.f.providers[-1].connected,0)
    def test_each_mode_records_distinct_finite_allocations(self):
        a=self.f.call('prepare');b=self.f.call('plan',review=a['result']['review_sha256'],action='disable')
        self.assertNotEqual(a['allocation'],b['allocation'])
        l=self.f.c['budget_stores']['control'];s=self.f.stores[(l['bucket'],l['prefix'])]
        row=s.load(key('provider-budget-state','test-run'))[1];self.assertEqual(len(row['allocations']),2)


class MeteredLifecycleTests(unittest.TestCase):
    def meter(self,requests=2):
        return Allocation(dict(requests=requests,send_bytes=4096,receive_bytes=131072),'test',
            {'expires_at':1300},clock=lambda:1000,monotonic=lambda:1000)
    def response(self,req,status=200):
        body=canonical(dict(name=V1,state='ENABLED',algorithm='GOOGLE_SYMMETRIC_ENCRYPTION',protectionLevel='SOFTWARE'))
        r=requests.Response();r.status_code=status;r.url=req.url;r.request=req
        r.headers['Content-Length']=str(len(body));r.raw=HTTPResponse(body=io.BytesIO(body),preload_content=False)
        return r
    def test_production_transport_charges_each_attempt_before_sending(self):
        meter=self.meter(1);p=PinnedKeyLifecycle.with_access_token(version=V1,protected_version=V2,
            access_token='synthetic-bearer-token-only',expires_at=1500,clock=lambda:1000,allocation=meter)
        self.addCleanup(p.close)
        with patch('requests.adapters.HTTPAdapter.send',side_effect=lambda req,**kw:self.response(req)) as send:
            self.assertEqual(p.describe()['state'],'ENABLED')
            with self.assertRaises(StateUnavailable):p.describe()
            self.assertEqual(send.call_count,1);self.assertEqual(meter.by_provider['kms'],1)
    def test_401_is_one_attempt_with_no_refresh_or_retry(self):
        meter=self.meter();p=PinnedKeyLifecycle.with_access_token(version=V1,protected_version=V2,
            access_token='synthetic-bearer-token-only',expires_at=1500,clock=lambda:1000,allocation=meter)
        self.addCleanup(p.close)
        with patch('requests.adapters.HTTPAdapter.send',side_effect=lambda req,**kw:self.response(req,401)) as send:
            with self.assertRaises(StateUnavailable):p.dispatch('disable')
            self.assertEqual(send.call_count,1);self.assertEqual(meter.by_provider['kms'],1)


if __name__=='__main__':unittest.main()
