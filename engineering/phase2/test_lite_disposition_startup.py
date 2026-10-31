"""Actual private factory over existing native records; transports are synthetic."""
import base64,copy,hashlib,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec,ed25519
import lite_disposition_startup as startup
from lite_account_deletion import blueprint
from lite_account_identity import IdentityRejected
from lite_attest_state import GCSStateStore,StateUnavailable
from lite_disposition_executor import PATH
from lite_disposition_worker_probe import WorkerFixture
from lite_service_history import ServiceResultHistory
from lite_store_locations import LimitedStateStore
from lite_subscription_executor import key,canonical
from lite_usage_ledger import digest
from lite_provider_budget import ProviderBudget, BudgetUnavailable
from lite_provider_transport import BudgetedSession
from test_lite_disposition_executor import public,signed
from test_lite_subscription_grace import rows
import test_lite_native_purchase as native


def raw(c):return json.dumps(c,sort_keys=True,separators=(',',':')).encode()


class Providers:
    def __init__(self,f):
        self.f=f;self.calls=[];self.wrong_identity=False
        self.kms=startup.DispositionKMSSession(f.f.f.f.kms,f.c['deployment']['keyring'],digest(f.c['binding']),[f.person])
    def connect(self,c):self.calls.append('connect')
    def bucket(self,name):return self.f.buckets[name]
    def identity_checked(self,c):
        self.calls.append('identity')
        if self.wrong_identity:raise ValueError('wrong runtime')
    def state(self,bucket,prefix):return self.f.stores[prefix.rsplit('/',1)[-1]]
    def activate_budget(self,allocation):self.allocation=allocation
    def oauth(self,secret):return self.f.f.w.p.deletion.grants.provider


class Fixture:
    def __init__(self,host):
        init=startup.GoogleGrantKeyProvider.__init__
        def region(provider,*,session,keyring):
            return init(provider,session=session,keyring=keyring.replace("/locations/test/","/locations/asia-east1/"))
        with patch.object(startup.GoogleGrantKeyProvider,"__init__",region):self.f=WorkerFixture(host)
        f=self.f
        f.f.ops.store=GCSStateStore(f.service_bucket,prefix='lite-native/test/operations')
        f.service=ServiceResultHistory(disposition=f.f.results,store=f.service.jobs,audit_store=f.service.audit,
            policy=dict(v=1,id='synthetic-v1',bucket=f.service_bucket.name,prefix=f.f.ops.store.prefix))
        f.w=f.worker();f.ready();self.person=f.person
        p=f.w.p;d=p.deletion;m=f.h.m;self.now=d.lifecycle.now();self.seq=0
        self.stores=dict(lifecycle=d.lifecycle.store,lifecycle_audit=d.lifecycle.audit,identity=d.bridge.store,
            identity_audit=d.bridge.audit,accounts=d.bridge.registry.store,quota=p.results.ops.ledger.store,
            keys=p.cipher.store,keys_audit=p.cipher.audit,grants=d.grants.store,grants_audit=d.grants.audit,
            deletion=d.store,deletion_audit=d.audit,results=p.results.store,results_audit=p.results.audit,
            progress=p.store,progress_audit=p.audit,research=m.r.store,research_closure=m.closure.store,
            research_closure_audit=m.closure.audit,research_media=m.w.store,research_media_audit=m.w.audit,
            service_history=f.service.jobs,service_history_audit=f.service.audit,research_history=f.h.jobs,
            research_history_audit=f.h.audit,worker=f.store,worker_audit=f.audit,
            executor=f.f.store('startup-executor'),executor_audit=f.f.store('startup-executor-audit'),operations=f.f.ops.store,
            provider_budget=f.f.store('startup-provider-budget'),provider_budget_audit=f.f.store('startup-provider-budget-audit'))
        assert set(self.stores)==set(startup.STORES)
        self.operator=ed25519.Ed25519PrivateKey.generate();self.drainer=ed25519.Ed25519PrivateKey.generate()
        self.buckets={f.service_bucket.name:f.service_bucket,f.h.bucket.name:f.h.bucket}
        for name,b in self.buckets.items():
            b._properties.update(kind='storage#bucket',name=name,projectNumber='123456789',location='ASIA-EAST1',metageneration='1',
                iamConfiguration=dict(publicAccessPrevention='enforced',uniformBucketLevelAccess={'enabled':True}))
        tmp=tempfile.TemporaryDirectory();host.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
        private=ec.generate_private_key(ec.SECP256R1()).private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,serialization.NoEncryption());(self.root/'oauth.pem').write_bytes(private)
        manifest=dict(v=1,namespace='startup-test',source='a'*40,image='b'*64,operation='c'*64,
            accounts=[dict(person=self.person,plan_sha256=digest(f.w._plan(self.person)))],
            not_before=self.now,expires_at=self.now+900,max_commands=20,steps=12,limit=2,seconds=30)
        self.c=dict(schema='lite-disposition-startup/1',enabled=True,
            deployment=dict(source='a'*40,image='b'*64,project='synthetic-project',project_number='123456789',
                service_account='disposition@synthetic-project.iam.gserviceaccount.com',keyring=p.cipher.provider.keyring),
            binding=d.lifecycle.binding,buckets={n:dict(metadata_sha256=digest(b._properties)) for n,b in self.buckets.items()},
            stores={n:dict(bucket=f.service_bucket.name,prefix='lite-native/test/'+n) for n in self.stores},
            media=dict(bucket=f.h.bucket.name),policies=dict(service=f.service.policy,research=f.h.policy),
            manifest=manifest,manifest_sha256=digest(manifest),
            provider_budget=dict(v=1,namespace='startup-budget',manifest=digest(manifest),source='a'*40,image='b'*64,
                not_before=self.now,expires_at=self.now+3600,max_allocations=12,requests=5000,
                send_bytes=1024**2,receive_bytes=32*1024**2,seconds=60),
            signers=dict(operator={'operator':base64.b64encode(public(self.operator)).decode()},
                drain={'observer':base64.b64encode(public(self.drainer)).decode()}),
            apple=dict(team='TEAM000001',key_id='OAUTH00001',file='oauth.pem',sha256=hashlib.sha256(private).hexdigest()))
        # Existing test ring uses its own project, kept exact instead of renamed.
        project=p.cipher.provider.keyring.split('/')[1]
        self.c['deployment'].update(project=project,service_account='disposition@'+project+'.iam.gserviceaccount.com')
        self.providers=Providers(self)
    def build(self,**changes):
        kw=dict(expected_config=hashlib.sha256(raw(self.c)).hexdigest(),expected_source='a'*40,expected_image='b'*64,
            secrets=self.root,providers=self.providers,clock=lambda:self.now)
        kw.update(changes);self.e=startup.build_disposition(raw(self.c),**kw);return self.e
    def command(self,action='run'):
        self.seq+=1;p=dict(kind='lite-disposition-command/1',manifest=digest(self.c['manifest']),command=f'{self.seq:032x}',
            expected_state=self.e._state(self.e._read()),issued_at=self.now,expires_at=self.now+120,
            action=action,person=self.person,replaces=None)
        return signed(self.operator,p,'operator')
    def snap(self):
        return (self.f.f.preserved(),copy.deepcopy(self.f.h.bucket.history),copy.deepcopy(self.f.service_bucket.versions),
            self.f.f.f.kms.calls.copy(),self.f.f.f.g.calls.copy())
    def finish(self):
        for _ in range(12):
            e=self.build();response=e.create_app().test_client().post(PATH,json=dict(command=self.command(),drain=None))
            assert response.status_code==200,response.json
            status=e.w.status(self.person);v=status['observations']['grant_key']
            if v and v['kms_state']=='DESTROY_SCHEDULED':self.f.f.f.kms.destroy_now(v['version'])
            if all(s=='observed' for s in status['states'].values()):return response.json
        raise AssertionError('did not finish')


class StartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):native.legacy.LinksTests.setUpClass()
    def setUp(self):self.f=Fixture(self)
    def test_startup_reads_original_binding_without_provider_side_effects(self):
        before=self.f.snap();e=self.f.build();self.assertEqual(before,self.f.snap())
        self.assertIsNone(e.w.p.deletion.reauthentication)
        for obj,fields in ((e.w.p.deletion.bridge,('secret','runtime','verifier','begin_account_closure')),
                (e.w.p.deletion.grants,('_identity_key','verifier','capture','validate_for_session')),
                (e.w.p.results.ops,('inference','run','prepare'))):
            for field in fields:self.assertFalse(hasattr(obj,field),field)
    def test_whole_signed_flow_preserves_source_quota_and_original_plan(self):
        before=self.f.f.f.preserved();capture=bytes(self.f.f.f.capture);r=self.f.finish()
        self.assertTrue(all(s=='observed' for s in r['worker']['states'].values()))
        self.assertEqual(before,self.f.f.f.preserved());self.assertEqual(capture,self.f.f.f.capture)
        self.assertEqual(self.f.f.f.calls,1);self.assertFalse(r['account_deleted'])
        self.assertEqual(len(self.f.f.h.bucket.history),len(self.f.f.h.bucket.live))
    def test_restart_same_completed_command_no_repeat(self):
        e=self.f.build();cmd=self.f.command();first=e.execute(cmd);calls=self.f.snap()
        self.assertEqual(self.f.build().execute(cmd),first);self.assertEqual(calls,self.f.snap())
    def test_missing_pins_or_off_do_not_connect(self):
        for n in ('expected_config','expected_source','expected_image'):
            with self.assertRaises(ValueError):self.f.build(**{n:'0'*(40 if n=='expected_source' else 64)})
        self.f.c['enabled']=False
        with self.assertRaises(ValueError):self.f.build()
        self.assertEqual(self.f.providers.calls,[])
    def test_bad_manifest_rejected_before_cloud(self):
        self.f.c['manifest']['steps']=101;self.f.c['manifest_sha256']=digest(self.f.c['manifest'])
        with self.assertRaises(ValueError):self.f.build()
        self.assertEqual(self.f.providers.calls,[])
    def test_unknown_fields_and_wrong_region_rejected(self):
        self.f.c['identity_secret']='forbidden'
        with self.assertRaises(ValueError):self.f.build()
        del self.f.c['identity_secret'];self.f.c['deployment']['keyring']=self.f.c['deployment']['keyring'].replace('asia-east1','us-east1')
        with self.assertRaises(ValueError):self.f.build()
        self.assertEqual(self.f.providers.calls,[])
    def test_same_operator_and_drain_keys_rejected(self):
        self.f.c['signers']['drain']=self.f.c['signers']['operator']
        with self.assertRaises(ValueError):self.f.build()
        self.assertEqual(self.f.providers.calls,[])
    def test_changed_oauth_key_refused_before_cloud(self):
        (self.f.root/'oauth.pem').write_bytes(b'bad')
        with self.assertRaises(ValueError):self.f.build()
        self.assertEqual(self.f.providers.calls,[])
    def test_configured_path_alias_refused_before_cloud(self):
        self.f.c['stores']['executor']=self.f.c['stores']['keys']
        with self.assertRaises(ValueError):self.f.build()
        self.assertEqual(self.f.providers.calls,[])
    def test_resolved_alias_refused(self):
        self.f.stores['executor']=self.f.stores['keys'];before=self.f.snap()
        with self.assertRaises(ValueError):self.f.build()
        self.assertEqual(before,self.f.snap())
    def test_media_cannot_share_state_bucket(self):
        self.f.c['media']['bucket']=self.f.c['stores']['keys']['bucket']
        with self.assertRaises(ValueError):self.f.build()
        self.assertEqual(self.f.providers.calls,[])
    def test_changed_bucket_policy_stops_without_cleanup(self):
        before=self.f.snap();next(iter(self.f.buckets.values()))._properties['metageneration']='2'
        with self.assertRaises(ValueError):self.f.build()
        self.assertEqual(before,self.f.snap())
    def test_wrong_runtime_identity_stops_without_cleanup(self):
        self.f.providers.wrong_identity=True;before=self.f.snap()
        with self.assertRaises(ValueError):self.f.build()
        self.assertEqual(before,self.f.snap())
    def test_missing_native_config_never_recreated(self):
        target=key('account-deletion-config',self.f.c['binding']['namespace']);s=self.f.stores['deletion'];load=s.load
        with patch.object(s,'load',side_effect=lambda k:None if k==target else load(k)):
            with self.assertRaises(StateUnavailable):self.f.build()
        self.assertIsNotNone(s.load(target))
    def test_missing_grant_crypto_marker_stops(self):
        target=key('apple-grant-crypto-mode',self.f.c['binding']['namespace']);s=self.f.stores['grants'];load=s.load
        with patch.object(s,'load',side_effect=lambda k:None if k==target else load(k)):
            with self.assertRaises(StateUnavailable):self.f.build()
    def test_unknown_account_does_not_initialize(self):
        self.f.c['manifest']['accounts'][0]['person']='0'*64;self.f.c['manifest_sha256']=digest(self.f.c['manifest'])
        self.f.c['provider_budget']['manifest']=self.f.c['manifest_sha256']
        before=self.f.snap()
        with self.assertRaises(ValueError):self.f.build()
        self.assertEqual(before,self.f.snap())
    def test_wrong_account_plan_refuses_entry(self):
        self.f.c['manifest']['accounts'][0]['plan_sha256']='0'*64;self.f.c['manifest_sha256']=digest(self.f.c['manifest'])
        self.f.c['provider_budget']['manifest']=self.f.c['manifest_sha256']
        before=self.f.snap()
        with self.assertRaises(ValueError):self.f.build()
        self.assertEqual(before,self.f.snap())
    def test_read_only_views_and_create_only_audit_are_enforced(self):
        e=self.f.build();s=e.w.p.deletion.bridge
        for store in (s.store,s.audit,s.registry.store,s.lifecycle.store,s.lifecycle.audit,e.w.p.results.ops.ledger.store):
            with self.assertRaises(StateUnavailable):store.compare_exchange(b'x'*32,0,{})
        with self.assertRaises(StateUnavailable):e.audit.compare_exchange(b'x'*32,1,{})
    def test_public_deletion_routes_cannot_use_worker_context(self):
        d=self.f.build().w.p.deletion
        with self.assertRaises(ValueError):blueprint(d)
        with self.assertRaises(IdentityRejected):d.begin('device-one','d'*32)
    def test_only_signed_private_route_available(self):
        client=self.f.build().create_app().test_client()
        for p in ('/api/v1/lite/account/challenge','/api/v1/lite/service/run','/api/v1/lite/account/deletion/request','/admin','/'):
            self.assertEqual(client.post(p,json={}).status_code,404)
        self.assertEqual(client.post(PATH,json={'command':{},'drain':None}).status_code,409)
    def test_worker_uses_original_result_and_grant_validators(self):
        e=self.f.build();a=e.w.p.deletion.grants;original=self.f.f.w.p.deletion.grants
        node=e.w.p.deletion.lifecycle._node(self.f.person,e.w.p.deletion._plan(self.f.person)['head'])
        self.assertEqual(a._row(node['grant_id']),original._row(node['grant_id']))
        self.assertEqual(a._validation(node['grant_id']),original._validation(node['grant_id']))
        subject='apple-'+self.f.person
        self.assertEqual(e.w.p.results.ops.load(subject,self.f.f.f.op),self.f.f.f.ops.load(subject,self.f.f.f.op))
    def test_missing_deployment_environment_refuses_factory(self):
        with self.assertRaises(ValueError):startup.application({})
    def test_transport_denies_unreviewed_kms_and_apple_calls(self):
        s=self.f.providers.kms;v=next(iter(s.versions));k=next(iter(s.keys));before=len(self.f.f.f.f.kms.calls)
        for method,url in [('POST',v+':encrypt'),('POST',v+':restore'),('POST',k+':encrypt'),('POST',k+'/cryptoKeyVersions'),('GET',v[:-1]+'2')]:
            with self.assertRaises(StateUnavailable):s.request(method,url)
        self.assertEqual(before,len(self.f.f.f.f.kms.calls))
        with self.assertRaises(StateUnavailable):startup.revoke_transport('https://appleid.apple.com/auth/token',{})
    def test_expired_window_still_inspects_but_cannot_dispatch(self):
        self.f.now+=1000;e=self.f.build()
        self.assertFalse(e.inspect(self.f.person)['account_deleted'])
        with self.assertRaises(ValueError):e.execute(self.f.command())
    def budget(self):
        return ProviderBudget(policy=self.f.c['provider_budget'],expected=digest(self.f.c['provider_budget']),
            store=self.f.stores['provider_budget'],audit_store=self.f.stores['provider_budget_audit'],clock=lambda:self.f.now)
    def test_factory_reserves_before_activating_operational_transport(self):
        called=[];activate=self.f.providers.activate_budget
        def checked(allocation):
            called.append(self.budget().status()['allocated_attempts']);activate(allocation)
        with patch.object(self.f.providers,'activate_budget',side_effect=checked):self.f.build()
        self.assertEqual(called,[1]);self.assertIs(self.f.e.provider_allocation,self.f.providers.allocation)
    def test_factory_restart_cannot_reset_provider_allocation(self):
        self.f.c['provider_budget']['max_allocations']=1;self.f.build();before=self.f.snap()
        with self.assertRaises(BudgetUnavailable):self.f.build()
        self.assertEqual(before,self.f.snap());self.assertEqual(self.budget().status()['allocated_attempts'],1)
    def test_activation_failure_consumes_allocation_without_cleanup(self):
        before=self.f.snap()
        with patch.object(self.f.providers,'activate_budget',side_effect=StateUnavailable('startup interrupted')):
            with self.assertRaises(StateUnavailable):self.f.build()
        self.assertEqual(before,self.f.snap());self.assertEqual(self.budget().status()['allocated_attempts'],1)
        self.f.build();self.assertEqual(self.budget().status()['allocated_attempts'],2)
    def test_changed_budget_cannot_reuse_existing_journal(self):
        self.f.build();self.f.c['provider_budget']['requests']+=1
        with self.assertRaises(BudgetUnavailable):self.f.build()
    def test_budget_manifest_mismatch_stops_before_connection(self):
        self.f.c['provider_budget']['manifest']='0'*64
        with self.assertRaises(ValueError):self.f.build()
        self.assertEqual(self.f.providers.calls,[])
    def test_cloud_factory_binds_all_operational_transports_to_one_allocation(self):
        import os
        self.enterContext(patch.dict(os.environ,DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA='true'))
        from google.auth.credentials import AnonymousCredentials
        credentials=AnonymousCredentials();credentials.service_account_email=self.f.c['deployment']['service_account']
        provider=startup.CloudProviders()
        with patch('google.auth.compute_engine.Credentials',return_value=credentials):provider.connect(self.f.c)
        allocation=self.budget().allocate();provider.activate_budget(allocation)
        self.addCleanup(provider.control.close);self.addCleanup(provider.client.close)
        self.addCleanup(provider.kms.session.close)
        self.assertEqual(provider.control._max_refresh_attempts,0)
        storage=provider.bucket(next(iter(self.f.buckets))).client._http
        self.assertIsInstance(storage,BudgetedSession);self.assertIs(storage.allocation,allocation)
        self.assertIs(provider.kms.session.allocation,allocation)
        self.assertEqual(storage.session._max_refresh_attempts,0)
        self.assertEqual(provider.kms.session.session._max_refresh_attempts,0)

    def test_cloud_factory_refuses_implicit_background_requests(self):
        import os
        with patch.dict(os.environ,DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA='false'):
            with patch('google.auth.compute_engine.Credentials') as credentials:
                with self.assertRaisesRegex(ValueError,'background'):startup.CloudProviders().connect(self.f.c)
                credentials.assert_not_called()

    def test_exhaustion_during_work_preserves_claim_and_blocks_blind_replay(self):
        self.f.c['provider_budget']['requests']=2
        e=self.f.build();command=self.f.command();before=self.f.snap()
        unavailable=patch.object(e.store,'load',side_effect=BudgetUnavailable('allocation exhausted'))
        def exhausted(*args,**kw):
            for _ in range(2):
                e.provider_allocation.begin(provider='gcs',method='GET',send_bytes=0,response_limit=1)
            # Failure reporting also needs storage; exhaustion must not erase
            # the original running claim or grant another caller execution.
            unavailable.start()
            e.provider_allocation.begin(provider='gcs',method='GET',send_bytes=0,response_limit=1)
        try:
            with patch.object(e.w,'run',side_effect=exhausted):
                response=e.create_app().test_client().post(PATH,json=dict(command=command,drain=None))
            self.assertEqual(response.status_code,503)
            self.assertFalse(response.json['retry_automatically'])
        finally:unavailable.stop()
        e=self.f.build();view=e.inspect(self.f.person)
        self.assertEqual(set(view['commands'].values()),{'running'})
        with patch.object(e.w,'run',side_effect=AssertionError('must not replay')):
            response=e.create_app().test_client().post(PATH,json=dict(command=command,drain=None))
        self.assertEqual(response.status_code,503);self.assertEqual(before,self.f.snap())


if __name__=='__main__':unittest.main()
