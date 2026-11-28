"""First owner registration uses a local SQLite store and synthetic Apple trust roots."""
import base64
import copy
import hashlib
import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from lite_attest_enrollment import RegistrationService
from lite_attest_http import CHALLENGE_PATH, REGISTER_PATH, InternalOwner
from lite_attest_state import GCSStateStore, SQLiteStateStore
import lite_native_startup as startup
import lite_owner_enrollment as enrollment
from test_lite_attest_state import FakeBucket
import test_lite_attest_enrollment as attest
import test_lite_native_backend as native
from test_lite_signed_client import SignedServiceClient, b64


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


class OwnerEnrollmentTests(SignedServiceClient, unittest.TestCase):
    def setUp(self):
        self.e = attest.EnrollmentTests()
        self.e.setUp()
        self.addCleanup(self.e.doCleanups)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = self.e.now
        self.security = SQLiteStateStore(self.root/'new-device.sqlite')
        self.counter_store = SQLiteStateStore(self.root/'enrollment-counter.sqlite')
        self.key = b64(self.e.key_id)
        self.audience = enrollment.AUDIENCE
        self.counter = 0
        self.bucket = 'woundai-lite-native-state-123456789'
        self.config = dict(schema='lite-native-startup/3', enabled=True,
            deployment=dict(project='synthetic-only', project_number='123456789', source='3'*40, image='4'*64,
                            service_account='native-test@synthetic-only.iam.gserviceaccount.com'),
            app=dict(team='ABCDEFGHIJ', bundle='com.woundai.lite', version='33', attest_audience=self.audience),
            apple=dict(environment='Sandbox'), stores=dict(security=dict(bucket=self.bucket, prefix='lite-native/owner/security')),
            buckets={self.bucket:dict(metageneration='1')})
        self.plan = dict(schema='lite-owner-enrollment/1', starts=self.now, until=self.now+600,
            native_sha256=sha(encode(self.config)), key_id=self.e.key_id.hex(),
            service_account='enroll-only@synthetic-only.iam.gserviceaccount.com')
        self.connections = []
        self.client = self.build().test_client()

    def stores(self, dep, location):
        self.connections.append((dep,location))
        return self.security, self.counter_store

    def build(self, plan=None, config=None, **overrides):
        plan = self.plan if plan is None else plan
        raw = encode(self.config if config is None else config)
        plan_raw = encode(plan)
        opts = dict(config_sha=sha(raw), plan_sha=sha(plan_raw), source='3'*40, image='4'*64,
                    clock=lambda:self.now, stores=self.stores)
        return enrollment.build(raw, plan_raw, **dict(opts, **overrides))

    def start(self):
        r = self.client.post(CHALLENGE_PATH, json=dict(purpose='register',key_id=self.key))
        self.assertEqual(r.status_code,200,r.json)
        self.assertEqual(r.json['audience'], self.audience)
        return r.json

    def proof(self, result):
        return dict(key_id=self.key, challenge_id=result['challenge_id'],
                    attestation=b64(self.e.make_proof(challenge=base64.b64decode(result['challenge']))))

    def test_registration_then_lost_reply_returns_same_installation_across_restart(self):
        first = self.start()
        self.assertEqual(self.start(),first)
        proof = self.proof(first)
        one = self.client.post(REGISTER_PATH,json=proof)
        self.assertEqual(one.status_code,200,one.json)
        row = self.security.load(self.e.key_id)
        self.assertEqual(row[1]['status'],'active')
        self.assertEqual(one.json,dict(installation=row[1]['installation'],key_id=self.key))
        again = self.build().test_client().post(REGISTER_PATH,json=proof)
        self.assertEqual(again.json,one.json)
        self.assertEqual(self.security.load(self.e.key_id),row)
        self.assertEqual(again.headers['Cache-Control'],'no-store')

    def test_original_owner_review_uses_new_installation_without_reenrollment(self):
        proof = self.proof(self.start())
        result = self.client.post(REGISTER_PATH,json=proof)
        self.assertEqual(result.status_code,200,result.json)
        self.owner = result.json['installation']
        native.NativeBackendTests.setUpClass()
        h = native.Fixture(self)
        r = RegistrationService(self.security,policy=self.e.r.policy,audience=self.audience,clock=h.clock)
        backend = h.compose(security=r)
        pin = sha(self.owner.encode())
        self.client = backend.create_app(internal_owner=InternalOwner('native',pin,review_until=h.clock()+600)).test_client()
        op='b'*32
        c=native.NativeBackendTests.post(self,'account/challenge',operation=op)
        self.assertEqual(c.status_code,200,c.json)
        ack=native.NativeBackendTests.post(self,'account/confirm',operation=op,identity_token=h.identity.token(c.json),
                                          state=c.json['state'],authorization_code='synthetic')
        self.assertEqual(ack.status_code,202,ack.json)
        self.assertEqual(ack.json['error'],'lite_owner_review_pending')
        self.assertIsNone(backend.identity.existing_account_pin(self.owner))
        self.assertEqual((h.model_calls,h.grant.calls,h.kms.calls),(0,[],[]))

    def test_key_and_purpose_must_match_before_any_storage_write(self):
        for body in [dict(purpose='register',key_id=b64(bytes(32))),
                     dict(purpose='assert',key_id=self.key),dict(purpose='withdraw',key_id=self.key)]:
            self.assertEqual(self.client.post(CHALLENGE_PATH,json=body).status_code,403)
        self.assertIsNone(self.security.load(self.e.key_id))
        self.assertIsNone(self.counter_store.load(sha_key()))

    def test_only_two_enrollment_paths_exist(self):
        self.assertEqual({rule.rule for rule in self.build().url_map.iter_rules()},{REGISTER_PATH,CHALLENGE_PATH})
        for path in ('account/challenge','service/run','purchase/confirm','research/rejoin','records','annotation'):
            self.assertEqual(self.client.post('/api/v1/lite/'+path,json={}).status_code,404)
        self.assertIsNone(self.counter_store.load(sha_key()))

    def test_window_boundaries_and_cold_restart_never_extend_it(self):
        self.now-=1
        self.assertEqual(self.client.post(CHALLENGE_PATH,json=dict(key_id=self.key,purpose='register')).status_code,503)
        self.now+=1
        proof=self.proof(self.start())
        original=self.security.load(self.e.key_id)
        self.now=self.plan['until']
        self.assertEqual(self.client.post(REGISTER_PATH,json=proof).status_code,503)
        self.assertEqual(self.security.load(self.e.key_id),original)
        with self.assertRaises(ValueError): self.build()

    def test_expiry_during_proof_validation_leaves_pending_record(self):
        proof=self.proof(self.start())
        before=self.security.load(self.e.key_id)
        verifier=self.e.receipt_verify.side_effect
        def advance(*args,**kwargs):
            verified=verifier(*args,**kwargs)
            self.now=self.plan['until']
            return verified
        self.e.receipt_verify.side_effect=advance
        self.assertEqual(self.client.post(REGISTER_PATH,json=proof).status_code,503)
        self.assertEqual(self.security.load(self.e.key_id),before)

    def test_storage_adapter_touches_only_the_two_reviewed_objects(self):
        bucket = FakeBucket()
        def stores(deployment, location):
            return tuple(GCSStateStore(bucket,prefix=p) for p in location[2:])
        self.client = self.build(stores=stores).test_client()
        proof = self.proof(self.start())
        result = self.client.post(REGISTER_PATH,json=proof)
        self.assertEqual(result.status_code,200,result.json)
        security_name = 'lite-native/owner/security/'+self.e.key_id.hex()+'.json'
        budget_name = 'lite-native/owner/owner-enrollment-budget/'+sha_key().hex()+'.json'
        original = bucket.live[security_name]
        self.client = self.build(stores=stores).test_client()
        self.assertEqual(self.client.post(REGISTER_PATH,json=proof).json,result.json)
        self.assertEqual(bucket.live[security_name],original)
        self.assertEqual(set(bucket.live),{security_name,budget_name})
        self.assertEqual({call[1] for call in bucket.calls},{security_name,budget_name})

    def test_daily_budget_survives_restart(self):
        self.now+=1
        for i in range(2):
            for _ in range(6): self.start()
            self.assertEqual(self.client.post(CHALLENGE_PATH,json=dict(purpose='register',key_id=self.key)).status_code,429)
            self.client=self.build().test_client()
            self.now+=60
        self.assertEqual(self.client.post(CHALLENGE_PATH,json=dict(purpose='register',key_id=self.key)).status_code,429)
        self.assertEqual(self.counter_store.load(sha_key())[1]['day_used'],12)

    def test_wrong_build_fails_attestation_without_enrollment(self):
        config=copy.deepcopy(self.config)
        config['app']['version']='99'
        self.client=self.build(dict(self.plan,native_sha256=sha(encode(config))),config).test_client()
        result=self.client.post(REGISTER_PATH,json=self.proof(self.start()))
        self.assertEqual(result.status_code,401)
        self.assertEqual(self.security.load(self.e.key_id)[1]['status'],'pending')

    def test_cloud_binding_has_only_native_state_and_dedicated_budget(self):
        dep,location=self.connections[0]
        self.assertEqual(dep['service_account'],self.plan['service_account'])
        self.assertEqual(location,(self.bucket,'1','lite-native/owner/security','lite-native/owner/owner-enrollment-budget'))

    def test_configuration_conflicts_fail_before_provider_creation(self):
        for field,value in [('key_id','bad'),('until',self.now+901),('service_account',self.config['deployment']['service_account']),
                            ('native_sha256','b'*64),('starts',True),('schema','other')]:
            with self.subTest(field=field),self.assertRaises(ValueError): self.build(dict(self.plan,**{field:value}))
        for field,value in [('config_sha','8'*64),('plan_sha','9'*64),('image','0'*64),('source','0'*40)]:
            with self.subTest(field=field),self.assertRaises(ValueError): self.build(**{field:value})
        config=copy.deepcopy(self.config)
        config['stores']['quota']=dict(bucket=self.bucket,prefix='lite-native/owner/owner-enrollment-budget')
        with self.assertRaises(ValueError): self.build(dict(self.plan,native_sha256=sha(encode(config))),config)
        self.assertEqual(len(self.connections),1)

    def test_provider_proves_region_and_policy_before_exposing_precise_storage_names(self):
        dep, location = self.connections[0]
        bucket = Mock()
        bucket._properties = dict(kind='storage#bucket', name=self.bucket, projectNumber=dep['project_number'],
            location='ASIA-EAST1', metageneration='1', softDeletePolicy=dict(retentionDurationSeconds='0'),
            iamConfiguration=dict(publicAccessPrevention='enforced',uniformBucketLevelAccess=dict(enabled=True)))
        with patch.object(enrollment,'verify_attached_identity') as attached, \
             patch('google.auth.compute_engine.Credentials') as credentials, patch('google.cloud.storage.Client') as sdk:
            sdk.return_value.bucket.return_value = bucket
            sec, budget = enrollment.cloud_stores(dep,location)
            attached.assert_called_once_with(dep)
            credentials.assert_called_once_with(service_account_email=self.plan['service_account'])
            sdk.return_value.bucket.assert_called_once_with(self.bucket)
            bucket.reload.assert_called_once_with(timeout=10,retry=None)
            self.assertEqual(sec._name(self.e.key_id),location[2]+'/'+self.plan['key_id']+'.json')
            self.assertEqual(budget._name(sha_key()),location[3]+'/'+sha_key().hex()+'.json')
            bucket._properties['metageneration']='2'
            with self.assertRaises(ValueError): enrollment.cloud_stores(dep,location)

    def test_alternate_storage_endpoint_refused_before_connecting(self):
        dep,location=self.connections[0]
        with patch.dict(os.environ,STORAGE_EMULATOR_HOST='http://synthetic.invalid'), \
             patch.object(enrollment,'verify_attached_identity') as identity:
            with self.assertRaises(ValueError): enrollment.cloud_stores(dep,location)
            identity.assert_not_called()

    def test_factory_needs_no_business_secrets_and_rejects_mixed_settings(self):
        config=self.root/'native.json'; config.write_bytes(encode(self.config))
        plan=self.root/'plan.json'; plan.write_bytes(encode(self.plan))
        env=dict(LITE_INTERNAL_OWNER_MODE='owner-enrollment',LITE_NATIVE_CONFIG=str(config),
                 LITE_NATIVE_CONFIG_SHA256=sha(config.read_bytes()),LITE_NATIVE_SOURCE='3'*40,LITE_NATIVE_IMAGE_SHA256='4'*64,
                 LITE_OWNER_ENROLLMENT_PLAN=str(plan),LITE_OWNER_ENROLLMENT_PLAN_SHA256=sha(plan.read_bytes()))
        real=enrollment.build
        def build(*args,**kwargs): return real(*args,**kwargs,clock=lambda:self.now,stores=self.stores)
        with patch.object(enrollment,'build',side_effect=build),patch.object(startup,'build_native_runtime') as business:
            self.client=startup.application(env).test_client()
            self.start()
            for key in ('LITE_NATIVE_SECRET_DIR','LITE_INTERNAL_INSTALLATION_SHA256','LITE_INTERNAL_ACCOUNT_SHA256',
                        'LITE_RESEARCH_LINK_FILE','LITE_RESEARCH_LINK_SHA256','LITE_RESEARCH_CAPTURE_ENABLED'):
                with self.assertRaises(ValueError): startup.application(dict(env,**{key:'unused'}))
            env.pop('LITE_INTERNAL_OWNER_MODE')
            with self.assertRaises(ValueError): startup.application(env)
            business.assert_not_called()


def sha_key():
    return hashlib.sha256(b'woundlite/global-request-budget/1').digest()
