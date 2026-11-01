"""Pinned private factory, actual durable CAS and synthetic KMS; no credentials."""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
import lite_key_startup as startup
from lite_account_lifecycle import AccountLifecycle
from lite_attest_state import SQLiteStateStore, StateUnavailable
from lite_key_readiness import PATH
from lite_store_locations import LimitedStateStore, location
from lite_subscription_executor import key
from lite_usage_ledger import digest
from test_lite_key_provisioning import KMS
import test_lite_key_provisioning as provisioning
from test_lite_subscription_grace import rows


def raw(c): return json.dumps(c, sort_keys=True, separators=(',', ':')).encode()


def configuration(life, ring, now):
    d = dict(source='a'*40, image='b'*64, project='synthetic-project', project_number='123456789',
        service_account='key-worker@synthetic-project.iam.gserviceaccount.com',
        invoker_account='native-web@synthetic-project.iam.gserviceaccount.com', keyring=ring)
    manifest = dict(v=1, namespace='synthetic-key-plan', scope=life.scope, keyring=ring,
        source=d['source'], image=d['image'], operation='c'*64, not_before=now, expires_at=now+900, max_creates=5)
    return dict(schema='lite-key-startup/1', enabled=True, deployment=d, binding=life.binding,
        buckets={'synthetic-key-state': {'metageneration': '1'}}, manifest=manifest, manifest_sha256=digest(manifest),
        stores={n: dict(bucket='synthetic-key-state', prefix=('lite-key-prepare/' if n in ('state','audit')
            else 'lite-native/')+'acceptance/'+n) for n in startup.STORES})


class Providers:
    def __init__(self, c, stores, kms):
        self.c, self.stores, self.real_kms = c, stores, kms
        self.kms = startup.CreateOnlyKMSSession(self, c['deployment']['keyring'])
        self.calls = []; self.ring_missing = False; self.wrong_identity = False
        self.metadata = dict(kind='storage#bucket', name='synthetic-key-state', projectNumber='123456789',
            location='ASIA-EAST1', metageneration='1', softDeletePolicy={'retentionDurationSeconds':'0'},
            iamConfiguration=dict(publicAccessPrevention='enforced', uniformBucketLevelAccess={'enabled':True}))
    def request(self, method, url, **kw):
        if url == 'https://cloudkms.googleapis.com/v1/'+self.c['deployment']['keyring']:
            self.calls.append('keyring-get')
            return SimpleNamespace(status_code=404 if self.ring_missing else 200,
                content=json.dumps({'name':self.c['deployment']['keyring']}).encode())
        return self.real_kms.request(method, url, **kw)
    def connect(self, c): self.calls.append('connect')
    def bucket(self, name):
        def reload(**kw):
            assert kw == dict(timeout=10, retry=None); self.calls.append('bucket-get')
        return SimpleNamespace(name=name, _properties=self.metadata, reload=reload)
    def identity_checked(self, c):
        self.calls.append('identity')
        if self.wrong_identity: raise ValueError('different identity')
    def state(self, bucket, prefix): return self.stores[prefix.rsplit('/',1)[1]]


class Fixture:
    def __init__(self, host):
        tmp=tempfile.TemporaryDirectory();host.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
        self.now=1793400000;self.clock=lambda:self.now
        self.stores={n:SQLiteStateStore(self.root/(n+'.sqlite')) for n in startup.STORES}
        binding=dict(namespace='synthetic-native', issuer='https://appleid.apple.com', audience='com.woundai.lite', identity_key_sha256='f'*64)
        self.life=AccountLifecycle(store=self.stores['lifecycle'], audit_store=self.stores['lifecycle_audit'],
            binding=binding, clock=self.clock, service_catalog=True)
        self.c=configuration(self.life,'projects/synthetic-project/locations/asia-east1/keyRings/acceptance',self.now)
        self.kms=KMS();self.providers=Providers(self.c,self.stores,self.kms)
        self.person='d'*64;self.life.initialize_new(self.person,existing_account=False)
        node=self.life.reserve_grant(self.person,'synthetic-device','1'*32)
        self.body=dict(v=1,scope=self.life.scope,person=self.person,grant=node['grant_id'],
            version=startup.CreateOnlyKeyProvider(session=self.kms,keyring=self.c['deployment']['keyring']).version(self.life.scope,self.person))
    def build(self, **changes):
        kw=dict(expected_config=hashlib.sha256(raw(self.c)).hexdigest(),expected_source='a'*40,expected_image='b'*64,
            providers=self.providers,clock=self.clock)
        kw.update(changes);return startup.build_key_worker(raw(self.c),**kw)


class StartupTests(unittest.TestCase):
    def setUp(self):self.f=Fixture(self)
    def test_explicit_off_rejected_without_provider(self):
        self.f.c['enabled']=False
        with self.assertRaises(ValueError):self.f.build()
        self.assertEqual(self.f.providers.calls,[])
    def test_three_pins_independent_and_checked_before_io(self):
        for k,v in [('expected_config','0'*64),('expected_source','0'*40),('expected_image','0'*64)]:
            with self.subTest(k=k),self.assertRaises(ValueError):self.f.build(**{k:v})
        self.assertEqual(self.f.providers.calls,[])
    def test_duplicate_json_keys_rejected_before_io(self):
        b=raw(self.f.c).replace(b'"enabled":true',b'"enabled":true,"enabled":true')
        with self.assertRaises(ValueError):startup.build_key_worker(b,expected_config=hashlib.sha256(b).hexdigest(),
            expected_source='a'*40,expected_image='b'*64,providers=self.f.providers)
        self.assertEqual(self.f.providers.calls,[])
    def test_unbounded_plan_rejected_before_io(self):
        for field,value in [('max_creates',1001),('max_creates',True),('expires_at',self.f.now+3601),('scope','0'*64)]:
            c=copy.deepcopy(self.f.c);self.f.c['manifest'][field]=value;self.f.c['manifest_sha256']=digest(self.f.c['manifest'])
            with self.subTest(field=field,value=value),self.assertRaises(ValueError):self.f.build()
            self.f.c.clear();self.f.c.update(c)
        self.assertEqual(self.f.providers.calls,[])
    def test_worker_cannot_share_web_identity(self):
        self.f.c['deployment']['service_account']=self.f.c['deployment']['invoker_account']
        with self.assertRaises(ValueError):self.f.build()
        self.assertEqual(self.f.providers.calls,[])
    def test_cross_project_or_region_keyring_refused(self):
        for field,value in [('keyring','projects/foreign/locations/asia-east1/keyRings/ring'),('service_account','worker@other.iam.gserviceaccount.com')]:
            c=copy.deepcopy(self.f.c);self.f.c['deployment'][field]=value
            with self.assertRaises(ValueError):self.f.build()
            self.f.c.clear();self.f.c.update(c)
        self.assertEqual(self.f.providers.calls,[])
    def test_prefix_overlap_and_native_writes_rejected(self):
        for name in ('state','lifecycle_audit'):
            c=copy.deepcopy(self.f.c);self.f.c['stores'][name]=copy.deepcopy(self.f.c['stores']['lifecycle'])
            with self.assertRaises(ValueError):self.f.build()
            self.f.c.clear();self.f.c.update(c)
        self.assertEqual(self.f.providers.calls,[])
    def test_unexpected_secret_or_model_field_refused(self):
        for field in ('identity_key','apple_key','models'):
            self.f.c[field]='do-not-send'
            with self.assertRaises(ValueError):self.f.build()
            del self.f.c[field]
        self.assertEqual(self.f.providers.calls,[])
    def test_wrong_bucket_policy_no_state_or_key_writes(self):
        for field,value in [('metageneration','2'),('projectNumber','987654321'),('location','US')]:
            old=copy.deepcopy(self.f.providers.metadata);self.f.providers.metadata[field]=value
            with self.assertRaises(ValueError):self.f.build()
            self.f.providers.metadata=old
        self.assertEqual(rows(self.f.stores['audit']),[]);self.assertEqual(self.f.kms.creates(),0)
    def test_actual_service_identity_checked_before_state_writes(self):
        self.f.providers.wrong_identity=True
        with self.assertRaises(ValueError):self.f.build()
        self.assertEqual(rows(self.f.stores['audit']),[]);self.assertNotIn('keyring-get',self.f.providers.calls)
    def test_missing_keyring_never_created(self):
        self.f.providers.ring_missing=True
        with self.assertRaises(StateUnavailable):self.f.build()
        self.assertEqual(rows(self.f.stores['audit']),[]);self.assertEqual(self.f.kms.rest,[])
    def test_missing_baseline_stops_without_initializing(self):
        self.f.stores['lifecycle']=SQLiteStateStore(self.f.root/'empty.sqlite')
        with self.assertRaises(StateUnavailable):self.f.build()
        self.assertEqual(rows(self.f.stores['lifecycle']),[]);self.assertEqual(rows(self.f.stores['audit']),[])
    def test_wrong_baseline_stops_before_worker_plan_write(self):
        s=self.f.stores['lifecycle'];k=key('account-lifecycle-config',self.f.life.binding['namespace']);old=s.load(k)
        s.compare_exchange(k,old[0],dict(old[1],max_grants=3))
        before=rows(s)
        with self.assertRaises(StateUnavailable):self.f.build()
        self.assertEqual(rows(s),before);self.assertEqual(rows(self.f.stores['audit']),[])
    def test_resolved_store_overlap_rejected(self):
        self.f.stores['state']=self.f.stores['lifecycle']
        with self.assertRaises(ValueError):self.f.build()
        self.assertEqual(rows(self.f.stores['audit']),[])
    def test_startup_only_appends_plan_and_no_keys_or_accounts(self):
        before={k:rows(s) for k,s in self.f.stores.items()};w=self.f.build()
        self.assertEqual(self.f.kms.creates(),0)
        for name in ('state','lifecycle','lifecycle_audit'):self.assertEqual(rows(self.f.stores[name]),before[name])
        self.assertEqual(len(rows(self.f.stores['audit'])),1)
        self.f.build();self.assertEqual(len(rows(self.f.stores['audit'])),1)
        self.assertEqual(location(w.life.store),location(self.f.life.store))
    def test_worker_lifecycle_is_read_only_even_after_success(self):
        w=self.f.build();before=rows(self.f.life.store);w.ensure(self.f.body)
        for s in (w.life.store,w.life.audit):
            with self.assertRaises(StateUnavailable):s.compare_exchange(b'0'*32,0,{'v':1})
        with self.assertRaises(StateUnavailable):w.life.close(self.f.person,'synthetic-device','d'*32)
        self.assertEqual(rows(self.f.life.store),before)
    def test_audit_cannot_replace_existing_evidence(self):
        w=self.f.build();k=key('key-provision-plan',w.identity);old=w.audit.load(k)
        with self.assertRaises(StateUnavailable):w.audit.compare_exchange(k,old[0],{'changed':True})
        self.assertEqual(w.audit.load(k),old)
    def test_restart_after_lost_kms_reply_creates_once(self):
        w=self.f.build();self.f.kms.lost=True
        with self.assertRaises(StateUnavailable):w.ensure(self.f.body)
        before=rows(self.f.life.store);w=self.f.build();r=w.ensure(self.f.body)
        self.assertEqual(r['state'],'ready');self.assertEqual(self.f.kms.creates(),1)
        self.assertEqual(len(self.f.kms.states),1);self.assertEqual(rows(self.f.life.store),before)
    def test_restarts_never_reset_budget(self):
        w=self.f.build();w.ensure(self.f.body);before=rows(w.store)
        for _ in range(3):self.f.build().ensure(self.f.body)
        self.assertEqual(rows(w.store),before);self.assertEqual(self.f.kms.creates(),1)
    def test_expired_restart_readback_works_but_new_creation_blocked(self):
        w=self.f.build();w.ensure(self.f.body);self.f.now+=901
        self.assertEqual(self.f.build().ensure(self.f.body)['state'],'ready')
        person='e'*64;self.f.life.initialize_new(person,existing_account=False)
        n=self.f.life.reserve_grant(person,'second-device','2'*32)
        body=dict(self.f.body,person=person,grant=n['grant_id'],version=w.provider.version(w.life.scope,person))
        with self.assertRaises(StateUnavailable):self.f.build().ensure(body)
        self.assertEqual(self.f.kms.creates(),1)
    def test_closure_seen_by_restarted_reader_never_reopens(self):
        w=self.f.build();self.f.life.close(self.f.person,'synthetic-device','d'*32)
        r=self.f.build().create_app().test_client().post(PATH,json=self.f.body)
        self.assertEqual(r.status_code,409);self.assertEqual(self.f.kms.creates(),0)
    def test_private_factory_has_no_native_or_research_routes(self):
        client=self.f.build().create_app().test_client()
        for path in ('/','/api/v1/lite/account/confirm','/api/v1/lite/service/run','/api/v1/annotations','/admin'):
            self.assertEqual(client.post(path,json={}).status_code,404)
        self.assertEqual(client.post(PATH,json=self.f.body).status_code,200)
    def test_environment_missing_config_does_not_boot(self):
        with patch.object(startup,'build_key_worker') as build:
            with self.assertRaises(ValueError):startup.application({})
            build.assert_not_called()
    def test_factory_hides_provider_error_details(self):
        p=self.f.root/'config.json';p.write_bytes(raw(self.f.c))
        e=dict(LITE_KEY_CONFIG=str(p),LITE_KEY_CONFIG_SHA256='f'*64,LITE_KEY_SOURCE='a'*40,LITE_KEY_IMAGE_SHA256='b'*64)
        with patch.object(startup,'build_key_worker',side_effect=RuntimeError('secret-do-not-log')):
            with self.assertRaises(RuntimeError) as raised:startup.application(e)
        self.assertNotIn('secret-do-not-log',str(raised.exception))
    def test_runtime_provider_rejects_mutation_and_foreign_resource(self):
        p=self.f.providers.kms;base=p.base;v=self.f.body['version']
        for method,url,kw in [('POST',base+':setIamPolicy',{}),('DELETE',base,{}),('POST','https://cloudkms.googleapis.com/v1/'+v+':destroy',{}),
                ('POST','https://cloudkms.googleapis.com/v1/'+v+':encrypt',{}),('GET',base+'/cryptoKeys',{}),
                ('GET',base+'/cryptoKeys/g-'+'a'*52+'/cryptoKeyVersions/2',{}),('GET',base.replace('acceptance','foreign'),{}),
                ('POST',base+'/cryptoKeys',{'params':{'cryptoKeyId':'arbitrary','skipInitialVersionCreation':'false'}})]:
            with self.subTest(url=url),self.assertRaises(StateUnavailable):p.request(method,url,**kw)
        self.assertEqual(self.f.kms.rest,[])


class FactoryLoginTests(provisioning.ProvisionedLoginTests):
    """Run the signed-login suite through the real private startup factory."""
    def setUp(self):
        ring='projects/synthetic-project/locations/asia-east1/keyRings/acceptance'
        original=provisioning.native.Fixture
        class PinnedFixture(original):
            def __init__(inner,*args,**kw):
                super().__init__(*args,**kw)
                inner.kw['key_provider']=__import__('lite_grant_key_custody').GoogleGrantKeyProvider(session=inner.kms,keyring=ring)
        with patch.object(provisioning.native,'Fixture',PinnedFixture):super().setUp()
        life=self.backend.identity.lifecycle
        self.c=configuration(life,ring,self.h.clock())
        root=self.h.release.n.f.root
        self.provider=Providers(self.c,dict(state=SQLiteStateStore(root/'startup-jobs.sqlite'),
            audit=SQLiteStateStore(root/'startup-audit.sqlite'),lifecycle=life.store,lifecycle_audit=life.audit),self.kms)
        self.restart_worker()
        self.readiness=__import__('lite_key_readiness').KeyReadinessClient(session=self.transport,
            origin=__import__('test_lite_key_provisioning').ORIGIN,scope=life.scope,keyring=ring)
        self.backend=self.h.compose(key_readiness=self.readiness);self.client=self.backend.create_app().test_client()
    def restart_worker(self):
        self.w=startup.build_key_worker(raw(self.c),expected_config=hashlib.sha256(raw(self.c)).hexdigest(),
            expected_source='a'*40,expected_image='b'*64,providers=self.provider,clock=self.h.clock)
        self.transport.client=self.w.create_app().test_client()
    def test_lost_provider_response_restart_uses_original_key_and_code_once(self):
        self.begin();self.kms.lost=True;before=dict(self.fields)
        self.assertEqual(self.confirm().status_code,503);self.assertEqual(len(self.h.grant.calls),0)
        self.restart_worker()
        self.assertEqual(self.confirm().status_code,200);self.assertEqual(len(self.h.grant.calls),1)
        self.assertEqual(self.kms.creates(),1);self.assertEqual(self.fields,before)
    def test_closure_during_key_creation_never_exchanges_or_reopens(self):
        self.begin()
        self.kms.after=lambda *a:self.backend.identity.lifecycle.close(self.person,self.owner,'d'*32)
        self.assertEqual(self.confirm().status_code,503);self.assertEqual(self.h.grant.calls,[])
        self.restart_worker();self.assertEqual(self.confirm().status_code,401);self.assertEqual(self.kms.creates(),1)
    def test_plaintext_code_token_and_account_not_in_provisioning_records(self):
        self.begin();self.assertEqual(self.confirm().status_code,200)
        evidence=repr(rows(self.provider.stores['state'])+rows(self.provider.stores['audit']))
        for v in ('human-new',self.fields['identity_token'],self.fields['authorization_code']):self.assertNotIn(v,evidence)


if __name__=='__main__':unittest.main()
