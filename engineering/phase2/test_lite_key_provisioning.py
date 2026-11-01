"""Synthetic KMS REST + real SQLite CAS, signed Apple/App Attest login and AES.

No cloud requests, credentials, images, real keys, purchases or deletions.
"""
import copy
from concurrent.futures import ThreadPoolExecutor
import json
from types import SimpleNamespace
import unittest
from threading import RLock
from unittest.mock import patch
from lite_attest_state import StateUnavailable,SQLiteStateStore
from lite_account_lifecycle import AccountClosing
from lite_grant_key_custody import GoogleGrantKeyProvider
from lite_key_readiness import KeyReadinessClient,PATH
from lite_key_provisioning import AccountKeyProvisioner,CreateOnlyKeyProvider
from lite_usage_ledger import digest
from lite_subscription_executor import key
from test_lite_grant_key_custody import KMSSession
from test_lite_subscription_grace import rows
import test_lite_native_backend as native

ORIGIN='https://synthetic-key-prepare-abc.a.run.app'


class KMS(KMSSession):
    def __init__(self):
        super().__init__();self.resources={};self.rest=[];self.lock=RLock();self.before=lambda *a:None
        self.after=lambda *a:None;self.lost=False;self.response_status=None
    def request(self,method,url,*,json,timeout,allow_redirects,params=None):
        self.rest.append((method,url,copy.deepcopy(params)))
        assert timeout==(5,10) and allow_redirects is False
        name=url.split('/v1/',1)[1]
        self.before(method,name)
        if self.response_status is not None:return SimpleNamespace(status_code=self.response_status,content=b'{}')
        if method=='POST' and name.endswith('/cryptoKeys'):
            assert set(params)=={'cryptoKeyId','skipInitialVersionCreation'} and params['skipInitialVersionCreation']=='false'
            name+='/'+params['cryptoKeyId'];version=name+'/cryptoKeyVersions/1'
            with self.lock:
                if name in self.resources:return SimpleNamespace(status_code=409,content=b'{}')
                self.provision(version)
                self.resources[name]=dict(copy.deepcopy(json),name=name,primary={'name':version})
            result=SimpleNamespace(status_code=200,content=__import__('json').dumps(self.resources[name]).encode())
            self.after(method,name)
            if self.lost:self.lost=False;raise TimeoutError('synthetic response lost')
            return result
        if method=='GET' and '/cryptoKeyVersions/' not in name:
            row=self.resources.get(name)
            return SimpleNamespace(status_code=200 if row else 404,content=__import__('json').dumps(row or {}).encode())
        return super().request(method,url,json=json,timeout=timeout,allow_redirects=allow_redirects)
    def creates(self):return sum(m=='POST' and u.endswith('/cryptoKeys') for m,u,p in self.rest)


class HTTPTransport:
    def __init__(self,worker):self.client=worker.create_app().test_client();self.calls=0;self.lost=False;self.response=None
    def request(self,method,url,*,json,timeout,allow_redirects):
        assert method=='POST' and url==ORIGIN+PATH and timeout==(5,20) and allow_redirects is False
        self.calls+=1
        if self.response:return self.response
        r=self.client.post(PATH,json=json)
        if self.lost:self.lost=False;raise TimeoutError('lost private HTTP reply')
        return SimpleNamespace(status_code=r.status_code,content=r.data)


def worker(host,backend,kms,root,clock,*,maximum=5):
    life=backend.identity.lifecycle
    provider=CreateOnlyKeyProvider(session=kms,keyring=backend.identity.grants.cipher.provider.keyring)
    manifest=dict(v=1,namespace='synthetic-key-preparation',scope=life.scope,keyring=provider.keyring,
        source='a'*40,image='b'*64,operation='c'*64,not_before=clock(),expires_at=clock()+900,max_creates=maximum)
    args=dict(enabled=True,manifest=manifest,expected_manifest=digest(manifest),lifecycle=life,provider=provider,
        store=SQLiteStateStore(root/'provision.sqlite'),audit_store=SQLiteStateStore(root/'provision-audit.sqlite'),clock=clock)
    return AccountKeyProvisioner(**args),args


class ProvisionerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):native.NativeBackendTests.setUpClass()
    def setUp(self):
        self.f=native.Fixture(self);self.kms=KMS();self.f.kms=self.kms
        self.f.kw['key_provider']=GoogleGrantKeyProvider(session=self.kms,keyring=self.f.kw['key_provider'].keyring)
        self.b=self.f.compose();self.life=self.b.identity.lifecycle
        self.w,self.args=worker(self,self.b,self.kms,self.f.release.n.f.root,self.f.clock)
        self.body=self.account()
    def account(self,subject='synthetic-human',device='synthetic-device',op='1'*32):
        person=self.b.identity.events.identity(subject)
        self.life.initialize_new(person,existing_account=False)
        self.life.reserve_grant(person,device,op)
        return dict(v=1,scope=self.life.scope,person=person,grant=digest([self.life.binding['namespace'],device,op]),
                    version=self.w.provider.version(self.life.scope,person))
    def ensure(self):return self.w.ensure(self.body)
    def test_default_off_and_exact_manifest_pin(self):
        for changed in ({'enabled':False},{'expected_manifest':'0'*64}):
            with self.assertRaises(ValueError):AccountKeyProvisioner(**dict(self.args,**changed))
        self.assertEqual(self.kms.rest,[])
    def test_invalid_time_and_counts_rejected_before_provider(self):
        for k,v in [('max_creates',0),('max_creates',1001),('max_creates',True),('expires_at',self.f.clock()+3601),('not_before',-1)]:
            m=dict(self.args['manifest'],**{k:v})
            with self.assertRaises(ValueError):AccountKeyProvisioner(**dict(self.args,manifest=m,expected_manifest=digest(m)))
        self.assertEqual(self.kms.rest,[])
    def test_physical_store_overlap_refused(self):
        for changed in ({'audit_store':self.w.store},{'store':self.life.store}):
            with self.assertRaises(ValueError):AccountKeyProvisioner(**dict(self.args,**changed))
    def test_exact_request_and_cross_account_resource_refused(self):
        for change in ({'scope':'0'*64},{'version':self.body['version'].replace('/1','/2')},{'v':True},{'code':'secret'}):
            with self.assertRaises(ValueError):self.w.ensure(dict(self.body,**change))
        self.assertEqual(self.kms.rest,[])
    def test_unregistered_or_unindexed_grant_cannot_allocate(self):
        for change in ({'person':'0'*64,'version':self.w.provider.version(self.life.scope,'0'*64)},{'grant':'0'*64}):
            with self.assertRaises(StateUnavailable):self.w.ensure(dict(self.body,**change))
        self.assertEqual(self.kms.creates(),0)
    def test_create_and_replay_one_resource_no_extra_budget_or_grants(self):
        before=rows(self.life.store);first=self.ensure();after=rows(self.w.store)
        self.assertEqual(self.ensure(),first);self.assertEqual(self.kms.creates(),1)
        self.assertEqual(rows(self.w.store),after);self.assertEqual(rows(self.life.store),before)
        self.assertEqual(list(self.kms.states),[self.body['version']])
    def test_restart_preserves_receipt_and_fixed_version(self):
        expected=self.ensure();other=AccountKeyProvisioner(**self.args)
        self.assertEqual(other.ensure(self.body),expected);self.assertEqual(self.kms.creates(),1)
    def test_simultaneous_workers_never_allocate_second_version(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            out=list(pool.map(lambda _:self.ensure(),range(2)))
        self.assertEqual(out[0],out[1]);self.assertEqual(len(self.kms.states),1)
        self.assertLessEqual(self.kms.creates(),2)
    def test_lost_create_reply_readback_no_second_post(self):
        self.kms.lost=True
        with self.assertRaises(StateUnavailable):self.ensure()
        self.assertEqual(self.kms.creates(),1)
        self.assertEqual(self.ensure()['state'],'ready');self.assertEqual(self.kms.creates(),1)
    def test_failure_before_provider_can_resume_same_resource(self):
        once=[True]
        def fail(method,path):
            if method=='POST' and once[0]:once[0]=False;raise TimeoutError()
        self.kms.before=fail
        with self.assertRaises(StateUnavailable):self.ensure()
        self.assertEqual(len(self.kms.states),0)
        self.assertEqual(self.ensure()['state'],'ready');self.assertEqual(len(self.kms.states),1)
    def test_expired_window_blocks_new_but_acknowledges_existing(self):
        self.ensure();other=self.account('second')
        self.f.release.n.a.now+=901
        self.assertEqual(self.ensure()['state'],'ready')
        with self.assertRaisesRegex(StateUnavailable,'window'):self.w.ensure(other)
        self.assertEqual(self.kms.creates(),1)
    def test_repeated_or_multi_device_account_does_not_charge_again(self):
        self.ensure();body=self.account(device='device-two',op='2'*32)
        self.w.ensure(body);self.assertEqual(self.kms.creates(),1)
    def test_count_bound_applies_across_people_and_restarts(self):
        for i in range(5):self.w.ensure(self.account(str(i)))
        w=AccountKeyProvisioner(**self.args)
        with self.assertRaisesRegex(StateUnavailable,'budget exhausted'):w.ensure(self.body)
        self.assertEqual(self.kms.creates(),5)
    def test_corrupt_budget_never_resets(self):
        k=key('key-provision-budget',self.w.identity)
        self.w.store.compare_exchange(k,0,dict(v=1,manifest=self.w.identity,used=-1))
        with self.assertRaisesRegex(StateUnavailable,'budget corrupt'):self.ensure()
        self.assertEqual(self.kms.creates(),0)
    def test_closed_account_never_creates(self):
        self.life.close(self.body['person'],'synthetic-device','d'*32)
        with self.assertRaises(AccountClosing):self.ensure()
        self.assertEqual(self.kms.creates(),0)
    def test_closure_during_create_retains_orphan_audit_never_reports_ready(self):
        self.kms.after=lambda *a:self.life.close(self.body['person'],'synthetic-device','d'*32)
        with self.assertRaises(AccountClosing):self.ensure()
        self.assertEqual(self.w._job(self.body['person'])[1]['phase'],'closed')
        self.assertIn('cleanup_required',repr(rows(self.w.audit)))
        self.assertEqual(self.kms.states[self.body['version']],'ENABLED')
        with self.assertRaises(AccountClosing):self.ensure()
        self.assertEqual(self.kms.creates(),1)
    def test_audit_failure_before_dispatch_never_creates(self):
        real=self.w.audit.compare_exchange
        def fail(k,*a):raise StateUnavailable('synthetic audit write failure')
        with patch.object(self.w.audit,'compare_exchange',side_effect=fail),self.assertRaises(StateUnavailable):self.ensure()
        self.assertEqual(self.kms.creates(),0)
        self.assertEqual(self.ensure()['state'],'ready')
    def test_ready_checkpoint_lost_response_recovers(self):
        real=self.w.store.compare_exchange
        def lost(k,rev,row):
            result=real(k,rev,row)
            if row.get('phase')=='ready':raise StateUnavailable('ack lost')
            return result
        with patch.object(self.w.store,'compare_exchange',side_effect=lost):self.ensure()
        self.assertEqual(self.ensure()['state'],'ready');self.assertEqual(self.kms.creates(),1)
    def test_existing_unowned_resource_not_silently_adopted(self):
        self.w.provider.create(self.body['scope'],self.body['person'])
        with self.assertRaisesRegex(StateUnavailable,'unowned'):self.ensure()
        self.assertEqual(self.kms.creates(),1)
    def test_forbidden_403_not_treated_as_missing(self):
        self.kms.response_status=403
        with self.assertRaises(StateUnavailable):self.ensure()
        self.assertEqual(self.kms.creates(),0)
    def test_wrong_policy_never_reconfigured(self):
        self.ensure();name=next(iter(self.kms.resources));old=copy.deepcopy(self.kms.resources[name])
        for change in ({'purpose':'ASYMMETRIC_SIGN'},{'rotationPeriod':'86400s'},{'destroyScheduledDuration':'86400s'},
                {'labels':{}},{'primary':{'name':self.body['version'][:-1]+'2'}},{'primary':None},{'importOnly':True}):
            self.kms.resources[name]=dict(old,**change)
            with self.assertRaises(StateUnavailable):self.ensure()
        self.assertEqual(self.kms.creates(),1)
    def test_disabled_scheduled_destroyed_are_never_replaced(self):
        self.ensure()
        for state in ('DISABLED','DESTROY_SCHEDULED','DESTROYED'):
            self.kms.states[self.body['version']]=state
            with self.assertRaises(StateUnavailable):self.ensure()
        self.assertEqual(self.kms.creates(),1)
    def test_missing_version_not_created_or_rotated(self):
        self.ensure();del self.kms.states[self.body['version']]
        with self.assertRaises(StateUnavailable):self.ensure()
        self.assertEqual(self.kms.creates(),1)
    def test_worker_only_requests_exact_create_and_reads(self):
        self.ensure()
        for method,url,params in self.kms.rest:
            self.assertIn(method,('GET','POST'))
            if method=='POST':self.assertTrue(url.endswith('/cryptoKeys'))
            self.assertNotIn(':decrypt',url);self.assertNotIn(':destroy',url)
    def test_internal_route_limits_and_redaction(self):
        c=self.w.create_app().test_client()
        self.assertEqual(c.get(PATH).status_code,405)
        self.assertEqual(c.post('/api/v1/lite/account/confirm',json={}).status_code,404)
        self.assertEqual(c.post(PATH,data=b'x'*4097,content_type='application/json').status_code,413)
        self.kms.response_status=403;r=c.post(PATH,json=self.body)
        self.assertEqual(r.status_code,503);self.assertFalse(r.json['apple_code_exchanged'])
        self.assertNotIn(self.body['person'],r.get_data(as_text=True));self.assertEqual(r.headers['Cache-Control'],'no-store')


class ProvisionedLoginTests(unittest.TestCase):
    setUpClass=classmethod(lambda cls:native.NativeBackendTests.setUpClass())
    register=native.NativeBackendTests.register
    headers=native.NativeBackendTests.headers
    post_signed=native.NativeBackendTests.post_signed
    json_request=native.NativeBackendTests.json_request
    run_request=native.NativeBackendTests.run_request
    complete=native.NativeBackendTests.complete
    post=native.NativeBackendTests.post
    quota=native.NativeBackendTests.quota
    def setUp(self):
        native.NativeBackendTests.initialize(self)
        self.kms=KMS();self.h.kms=self.kms
        self.h.kw['key_provider']=GoogleGrantKeyProvider(session=self.kms,keyring=self.h.kw['key_provider'].keyring)
        self.backend=self.h.compose()
        self.w,self.args=worker(self,self.backend,self.kms,self.h.release.n.f.root,self.h.clock)
        self.transport=HTTPTransport(self.w)
        self.readiness=KeyReadinessClient(session=self.transport,origin=ORIGIN,scope=self.w.life.scope,keyring=self.w.provider.keyring)
        self.backend=self.h.compose(key_readiness=self.readiness);self.client=self.backend.create_app().test_client()
    def begin(self,subject='human-new'):
        self.login_serial+=1;op=f'{self.login_serial:032x}'
        r=self.post('account/challenge',operation=op);self.assertEqual(r.status_code,200)
        c=r.json;signed=self.h.identity.token(c,sub=subject);self.h.grant.token=signed
        self.fields=dict(operation=op,identity_token=signed,state=c['state'],authorization_code='synthetic-code-'+op)
        self.person=self.backend.identity.events.identity(subject)
    def confirm(self):return self.post('account/confirm',**self.fields)
    def test_signed_fresh_account_login_then_two_free_model_calls(self):
        self.begin();r=self.confirm();self.assertEqual(r.status_code,200,r.json)
        self.complete(1);self.complete(2)
        self.assertEqual(self.json_request('prepare',3).status_code,429)
        self.assertEqual((self.h.model_calls,self.quota()['used'],self.kms.creates()),(2,2,1))
        self.assertEqual(len(self.h.grant.calls),1)
    def test_lost_private_reply_preserves_code_and_exact_login_retry(self):
        self.begin();self.transport.lost=True
        self.assertEqual(self.confirm().status_code,503);self.assertEqual(self.h.grant.calls,[])
        self.assertEqual(self.confirm().status_code,200);self.assertEqual(len(self.h.grant.calls),1)
        self.assertEqual(self.kms.creates(),1)
        self.assertEqual(self.confirm().status_code,200);self.assertEqual(len(self.h.grant.calls),1)
    def test_lost_provider_response_restart_uses_original_key_and_code_once(self):
        self.begin();self.kms.lost=True
        self.assertEqual(self.confirm().status_code,503);self.assertEqual(len(self.h.grant.calls),0)
        self.w=AccountKeyProvisioner(**self.args);self.transport.client=self.w.create_app().test_client()
        self.backend=self.h.compose(key_readiness=self.readiness);self.client=self.backend.create_app().test_client()
        self.assertEqual(self.confirm().status_code,200);self.assertEqual(len(self.h.grant.calls),1)
        self.assertEqual(self.kms.creates(),1)
    def test_invalid_apple_signature_never_invokes_private_service(self):
        self.begin();self.fields['identity_token']='not-signed'
        self.assertEqual(self.confirm().status_code,401)
        self.assertEqual((self.transport.calls,self.kms.creates()),(0,0));self.assertEqual(self.h.grant.calls,[])
    def test_private_service_failure_keeps_single_use_code_unconsumed(self):
        self.begin();self.transport.response=SimpleNamespace(status_code=503,content=b'{}')
        self.assertEqual(self.confirm().status_code,503);self.assertEqual(self.h.grant.calls,[])
        self.transport.response=None;self.assertEqual(self.confirm().status_code,200)
    def test_wrong_private_receipt_cannot_authorize_code_exchange(self):
        self.begin();self.transport.response=SimpleNamespace(status_code=200,content=json.dumps(dict(
            v=1,state='ready',request_sha256='0'*64,version='wrong')).encode())
        self.assertEqual(self.confirm().status_code,503);self.assertEqual(self.h.grant.calls,[])
    def test_closure_during_key_creation_never_exchanges_or_reopens(self):
        self.begin()
        self.kms.after=lambda *a:self.w.life.close(self.person,self.owner,'d'*32)
        self.assertEqual(self.confirm().status_code,503);self.assertEqual(self.h.grant.calls,[])
        self.assertEqual(self.confirm().status_code,401);self.assertEqual(self.kms.creates(),1)
    def test_native_public_routes_do_not_include_key_creation(self):
        self.assertEqual(self.client.post(PATH,json={}).status_code,404)
    def test_plaintext_code_token_and_account_not_in_provisioning_records(self):
        self.begin();self.assertEqual(self.confirm().status_code,200)
        evidence=repr(rows(self.w.store)+rows(self.w.audit))
        for v in ('human-new',self.fields['identity_token'],self.fields['authorization_code'],'private-refresh-token'):
            self.assertNotIn(v,evidence)
    def test_readiness_scope_is_validated(self):
        bad=KeyReadinessClient(session=self.transport,origin=ORIGIN,scope='0'*64,keyring=self.w.provider.keyring)
        with self.assertRaises(ValueError):self.h.compose(key_readiness=bad)


if __name__=='__main__':unittest.main()
