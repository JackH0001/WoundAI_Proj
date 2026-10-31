"""Mounted startup pins + signed HTTP. Generated keys/local SQLite only.

Only transports and test trust roots are replaced. The startup parser, account
graph, signature checks, quota, durable release and replay guards run normally.
"""
import base64
import copy
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch, MagicMock
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from PIL import Image
import lite_native_startup as startup
import test_lite_native_backend as native
from lite_attest_state import SQLiteStateStore, StateUnavailable


def encoded(c): return json.dumps(c,sort_keys=True,separators=(',',':')).encode()
def sha(raw): return hashlib.sha256(raw).hexdigest()


class Providers:
    def __init__(self,h):
        self.h=h; self.calls=[]; self.kms=startup.RuntimeKMSSession(h.kms)
        self.metadata=dict(kind='storage#bucket',name='synthetic-native-state',projectNumber='123456789',
            location='ASIA-EAST1',metageneration='1',softDeletePolicy=dict(retentionDurationSeconds='0'),
            iamConfiguration=dict(publicAccessPrevention='enforced',uniformBucketLevelAccess=dict(enabled=True)))
    def model(self,package,expected):
        self.calls.append('model');return SimpleNamespace(raster=self.h.inference,version='a'*64)
    def connect(self,c):self.calls.append('connect')
    def bucket(self,name):
        self.calls.append('bucket')
        def reload(**kw):
            assert kw==dict(timeout=10,retry=None);self.calls.append('reload')
        return SimpleNamespace(name=name,_properties=self.metadata,reload=reload)
    def identity_checked(self,c): self.calls.append('identity')
    def state(self,b,prefix):self.calls.append('state');return self.h.all_stores[prefix.rsplit('/',1)[1]]
    def apple(self,*args):return self.h.release.n.a.verifier,self.h.release.n.a.client
    def identity(self,clock):return self.h.identity.verifier
    def oauth(self,secret):return self.h.grant.provider


class Fixture(native.Fixture):
    def __init__(self,host,day_offset=86400):
        super().__init__(host,day_offset)
        r=self.release;n=r.n
        self.all_stores=dict(security=self.e.service.store,budget=self.budget.store,privacy=self.privacy.store,
            accounts=n.accounts.store,links=n.links.store,links_audit=n.links.audit,
            subscription=n.state,subscription_audit=n.audit,observer=n.observer,
            release=r.store,release_audit=r.audit,quota=r.ledger.store,rollout=r.rollout.store,
            rollout_audit=r.rollout.audit,cutover=r.rollout.control,apple_proofs=n.a.a.proofs,**self.stores)
        self.secret_dir=n.f.root/'mounts';self.secret_dir.mkdir()
        def save(name,raw):
            (self.secret_dir/name).write_bytes(raw);return dict(file=name,sha256=sha(raw))
        def p8():
            return ec.generate_private_key(ec.SECP256R1()).private_bytes(serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
        self.config=dict(schema='lite-native-startup/1',enabled=True,namespace=self.kw['namespace'],
            deployment=dict(source=n.r.source,image=n.r.image,project='synthetic-only',project_number='123456789',
                service_account='native-test@synthetic-only.iam.gserviceaccount.com',
                keyring='projects/synthetic-only/locations/asia-east1/keyRings/lite'),
            app=dict(bundle=startup.BUNDLE,app_id=n.a.a.policy.app_id,team=self.e.r.policy.app_id.split('.')[0],
                version='33',attest_audience='lite-test'),
            apple=dict(environment='Sandbox',product=n.a.a.policy.product,oauth_key_id='A'*10,api_key_id='B'*10,
                issuer_id='10000000-0000-4000-8000-000000000001',
                roots=[save('root.pem',n.a.certs[0].public_bytes(serialization.Encoding.PEM))]),
            budgets=dict(minute=5000,day=10000),buckets={'synthetic-native-state':dict(metageneration='1')},
            stores={k:dict(bucket='synthetic-native-state',prefix='lite-native/candidate/'+k) for k in startup.ALL_STORES},
            controls=dict(operator_keys={k:base64.b64encode(v).decode() for k,v in n.evidence.approvers.items()},
                observer_keys={k:base64.b64encode(v).decode() for k,v in n.evidence.observers.items()},
                release=r.manifest,usage=r.rollout.manifest,baselines=r.baselines),
            secrets=dict(identity=save('identity.bin',b'n'*32),oauth=save('oauth.p8',p8()),api=save('api.p8',p8())),
            model=dict(preprocessing_sha256='a'*64))
        self.providers=Providers(self)
    def arguments(self,c=None):
        c=self.config if c is None else c;raw=encoded(c)
        return dict(raw=raw,expected_config=sha(raw),expected_source=self.release.n.r.source,
            expected_image=self.release.n.r.image,secrets=self.secret_dir,
            package=self.secret_dir.parent,providers=self.providers,clock=self.clock)
    def compose(self):
        b=startup.build_native_runtime(**self.arguments())
        # Keep existing test helpers pointed at the newly assembled objects.
        self.release.ledger=b.operations.ledger
        self.release.n.accounts=b.operations.accounts
        return b
    def all_rows(self):return {k:native.rows(s) for k,s in self.all_stores.items()}


class StartupPreflightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):native.NativeBackendTests.setUpClass()
    def setUp(self):self.h=Fixture(self)
    def rejected(self,change,*,provider_calls=False):
        h=self.h;c=copy.deepcopy(h.config);change(c);before=h.all_rows()
        with self.assertRaises((ValueError,StateUnavailable)):startup.build_native_runtime(**h.arguments(c))
        self.assertEqual(before,h.all_rows())
        if not provider_calls:self.assertEqual(h.providers.calls,[])
    def test_absent_or_extra_settings_refused_before_provider(self):
        self.rejected(lambda c:c.pop('enabled'))
        self.rejected(lambda c:c.update(unknown=True))
    def test_enablement_and_schema_are_explicit(self):
        for v in (False,1,'true'):self.rejected(lambda c:c.update(enabled=v))
        self.rejected(lambda c:c.update(schema='lite-native-startup/2'))
    def test_config_source_image_pins_are_independent(self):
        for k,v in [('expected_config','0'*64),('expected_source','0'*40),('expected_image','0'*64)]:
            with self.subTest(k=k),self.assertRaises(ValueError):
                startup.build_native_runtime(**dict(self.h.arguments(),**{k:v}))
        self.assertEqual(self.h.providers.calls,[])
    def test_duplicate_json_fields_refused(self):
        args=self.h.arguments();raw=args['raw'].replace(b'"enabled":true',b'"enabled":false,"enabled":true')
        with self.assertRaises(ValueError):startup.build_native_runtime(**dict(args,raw=raw,expected_config=sha(raw)))
        self.assertEqual(self.h.providers.calls,[])
    def test_optional_private_preparation_origin_is_pinned_and_strict(self):
        for value in ({'origin':'http://unsafe.a.run.app'},{'origin':'https://evil.example'},
                      {'origin':'https://private-synthetic.a.run.app','token':'secret'},None):
            self.rejected(lambda c:c.update(key_preparation=value))

    def test_optional_preparation_requires_injected_matching_client(self):
        from lite_key_readiness import KeyReadinessClient
        h=self.h;h.config['key_preparation']={'origin':'https://synthetic-prepare.a.run.app'}
        captured=[]
        def readiness(c,scope):
            captured.append(scope)
            return KeyReadinessClient(session=MagicMock(),origin=c['key_preparation']['origin'],
                scope=scope,keyring=c['deployment']['keyring'])
        h.providers.readiness=readiness
        b=h.compose()
        self.assertEqual(captured,[b.identity.lifecycle.scope])
        self.assertIsNotNone(b.identity.grants.cipher.readiness)

    def test_cloud_preparation_uses_metadata_identity_not_ambient_credentials(self):
        c=copy.deepcopy(self.h.config);c['key_preparation']={'origin':'https://synthetic-prepare.a.run.app'}
        from google.auth.compute_engine import _metadata
        with patch.object(_metadata,'get_service_account_info',return_value={'email':c['deployment']['service_account']}):
            client=startup.CloudProviders().readiness(c,'a'*64)
        self.assertEqual(client.session.credentials.service_account_email,c['deployment']['service_account'])
        self.assertTrue(client.session.credentials._use_metadata_identity_endpoint)
        self.assertEqual(client.session.credentials._target_audience,c['key_preparation']['origin'])
        with patch.object(_metadata,'get_service_account_info',return_value={'email':'wrong@example.invalid'}):
            with self.assertRaisesRegex(ValueError,'identity differs'):startup.CloudProviders().readiness(c,'a'*64)

    def test_non_sandbox_wrong_app_or_multiple_builds_refused(self):
        self.rejected(lambda c:c['apple'].update(environment='Production'))
        self.rejected(lambda c:c['app'].update(bundle='com.woundai.mmh'))
        self.rejected(lambda c:c['app'].update(version='35,36,38'))
    def test_identity_or_keyring_in_different_project_refused(self):
        self.rejected(lambda c:c['deployment'].update(service_account='native-test@another-project.iam.gserviceaccount.com'))
        self.rejected(lambda c:c['deployment'].update(keyring='projects/another-project/locations/asia-east1/keyRings/lite'))
    def test_all_state_lanes_explicit_no_legacy_prefix_or_alias(self):
        self.rejected(lambda c:c['stores'].pop('operations'))
        self.rejected(lambda c:c['stores']['operations'].update(prefix='research'))
        self.rejected(lambda c:c['stores']['operations'].update(**c['stores']['recovery']))
        self.rejected(lambda c:c['stores']['operations'].update(bucket='another-bucket'))
    def test_release_and_baseline_mismatch_refused(self):
        self.rejected(lambda c:c['controls']['release'].update(image='0'*64))
        self.rejected(lambda c:c['controls']['baselines'][0].update(successes=1))
    def test_operator_and_observer_keys_must_differ(self):
        self.rejected(lambda c:c['controls'].update(observer_keys=c['controls']['operator_keys']))
    def test_secret_content_digest_type_and_path_checked(self):
        self.rejected(lambda c:c['secrets']['identity'].update(sha256='0'*64))
        self.rejected(lambda c:c['secrets']['identity'].update(file='../identity.bin'))
        self.rejected(lambda c:c['secrets'].update(api=c['secrets']['oauth']))
        h=self.h;path=h.secret_dir/'identity.bin';path.write_bytes(b'short')
        self.rejected(lambda c:c['secrets']['identity'].update(sha256=sha(b'short')))
    def test_secret_symlink_rejected(self):
        h=self.h;raw=(h.secret_dir/'identity.bin').read_bytes()
        (h.secret_dir/'other.bin').write_bytes(raw);(h.secret_dir/'link.bin').symlink_to('other.bin')
        self.rejected(lambda c:c['secrets']['identity'].update(file='link.bin'))
    def test_duplicate_or_changed_root_refused(self):
        self.rejected(lambda c:c['apple']['roots'].append(c['apple']['roots'][0]))
        self.rejected(lambda c:c['apple']['roots'][0].update(sha256='0'*64))
    def test_model_failure_precedes_cloud_connection(self):
        with patch.object(self.h.providers,'model',side_effect=ValueError('model hash')):
            self.rejected(lambda c:None)
    def test_bucket_drift_is_refused_before_state_construction(self):
        for change in (dict(metageneration='2'),dict(versioning=dict(enabled=True)),dict(projectNumber='999999999')):
            with self.subTest(change=change):
                saved=copy.deepcopy(self.h.providers.metadata);self.h.providers.metadata.update(change)
                self.rejected(lambda c:None,provider_calls=True)
                self.assertNotIn('state',self.h.providers.calls)
                self.h.providers.metadata=saved;self.h.providers.calls=[]
    def test_identity_check_failure_is_read_only(self):
        with patch.object(self.h.providers,'identity_checked',side_effect=ValueError('identity')):
            self.rejected(lambda c:None,provider_calls=True)
        self.assertNotIn('state',self.h.providers.calls)
    def test_missing_policy_never_activates_or_writes_markers(self):
        h=self.h;empty=SQLiteStateStore(h.secret_dir.parent/'empty-subscription.sqlite')
        h.all_stores['subscription']=empty
        self.rejected(lambda c:None,provider_calls=True)
    def test_startup_does_not_publish_observer_or_advance_rollout(self):
        before=self.h.all_rows();self.h.compose();after=self.h.all_rows()
        for k in startup.PEER_NAMES:
            if k=='accounts':
                self.assertTrue(all(row in after[k] for row in before[k]))
                extra=[json.loads(row[2]) for row in after[k] if row not in before[k]]
                self.assertEqual(len(extra),1)
                self.assertEqual(set(extra[0]),{'binding','service_catalog','v'})
                self.assertEqual(extra[0]['service_catalog'],'linked-operations/1')
            else:self.assertEqual(before[k],after[k],k)
        stable=self.h.all_rows();self.h.compose();self.assertEqual(stable,self.h.all_rows())
        self.assertEqual(self.h.grant.calls,[]);self.assertEqual(self.h.kms.calls,[])
    def test_web_transport_cannot_provision_or_destroy(self):
        session=MagicMock();p=startup.RuntimeKMSSession(session)
        for verb,url in [('DELETE','x'),('POST','x:destroy'),('POST','x'),('POST','x:restore')]:
            with self.subTest(url=url),self.assertRaises(StateUnavailable):p.request(verb,url)
        session.request.assert_not_called()
        p.request('GET','x');p.request('POST','x:encrypt');p.request('POST','x:decrypt')
        self.assertEqual(session.request.call_count,3)
    def test_operational_provider_never_mutates(self):
        p=startup.RuntimeRolloutProvider()
        for name in ('observe','enable_new','disable_new','freeze_legacy'):
            with self.subTest(name=name),self.assertRaises(StateUnavailable):getattr(p,name)({})
    def test_factory_requires_mounts_and_redacts_provider_failures(self):
        with self.assertRaises(ValueError):startup.application({})
        h=self.h;path=h.secret_dir/'config.json';path.write_bytes(encoded(h.config))
        env=dict(LITE_NATIVE_CONFIG=str(path),LITE_NATIVE_CONFIG_SHA256=sha(path.read_bytes()),
            LITE_NATIVE_SOURCE=h.release.n.r.source,LITE_NATIVE_IMAGE_SHA256=h.release.n.r.image,
            LITE_NATIVE_SECRET_DIR=str(h.secret_dir))
        with patch.object(startup,'build_native_runtime',side_effect=ValueError('private-token-do-not-log')):
            with self.assertRaises(RuntimeError) as raised:startup.application(env)
        self.assertNotIn('private-token',str(raised.exception))
    def test_cloud_provider_uses_named_runtime_identity_not_adc(self):
        credentials=SimpleNamespace(service_account_email=self.h.config['deployment']['service_account'])
        with (patch('google.auth.default',side_effect=AssertionError('no ADC scan')),
              patch('google.auth.compute_engine.Credentials',return_value=credentials) as create,
              patch('google.cloud.storage.Client') as client,
              patch('google.auth.transport.requests.AuthorizedSession')):
            p=startup.CloudProviders();p.connect(self.h.config);p.identity_checked(self.h.config)
        create.assert_called_once_with(service_account_email=credentials.service_account_email)
        client.assert_called_once_with(project='synthetic-only',credentials=credentials)


class StartupHTTPTests(unittest.TestCase):
    """The same signed user journeys, now built entirely from mounted settings."""
    register=native.NativeBackendTests.register
    headers=native.NativeBackendTests.headers
    post_signed=native.NativeBackendTests.post_signed
    json_request=native.NativeBackendTests.json_request
    run_request=native.NativeBackendTests.run_request
    complete=native.NativeBackendTests.complete
    post=native.NativeBackendTests.post
    login=native.NativeBackendTests.login
    quota=native.NativeBackendTests.quota
    @classmethod
    def setUpClass(cls):native.NativeBackendTests.setUpClass()
    def setUp(self):
        self.h=Fixture(self);self.e=self.h.e;self.backend=self.h.compose()
        self.app=self.backend.create_app();self.client=self.app.test_client()
        self.counter=0;self.key=native.attest_http.b64(self.e.key_id);self.register()
        buf=io.BytesIO();Image.new('RGB',(64,64),(200,100,120)).save(buf,format='JPEG')
        self.f=SimpleNamespace(jpeg=buf.getvalue());self.image_hash=sha(self.f.jpeg);self.login_serial=100
    test_free_two=native.NativeBackendTests.test_end_to_end_two_free_then_block_without_research_or_original_mutation
    test_restart_replay=native.NativeBackendTests.test_signed_login_and_service_receipt_replay_after_restart_never_reexecutes
    test_purchase_restore=native.NativeBackendTests.test_purchase_and_restore_total_ten_preserves_two_used
    test_pause_and_delete=native.NativeBackendTests.test_pause_blocks_new_work_and_still_allows_deletion_intake
    test_renewal=native.NativeBackendTests.test_expired_session_renews_without_interactive_apple_login_or_second_charge
    test_cross_day=native.NativeBackendTests.test_cross_midnight_recovery_preserves_original_and_executes_once
    test_reply_lost=native.NativeBackendTests.test_model_result_saved_before_settlement_outage_reads_back_without_rerun
    test_late_result=native.NativeBackendTests.test_late_model_after_closure_cannot_write_result_or_report_success
    test_routes=native.NativeBackendTests.test_candidate_rejects_research_and_worker_routes_before_any_state_touch


if __name__=='__main__':unittest.main()
