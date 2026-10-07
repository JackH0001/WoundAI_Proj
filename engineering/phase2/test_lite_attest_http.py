"""Flask Lite business routes under real synthetic App Attest cryptography.

Temporary SQLite security state/budget and LocalStore media; no external service.
"""
import ast
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from flask import Flask
from werkzeug.test import EnvironBuilder
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'Backend/Flask'))
from lite_attest_http import install_lite_attest, CHALLENGE_PATH, REGISTER_PATH
from lite_attest_budget import RequestBudget, BudgetExceeded
from lite_privacy_state import PrivacyState
from lite_attest_state import SQLiteStateStore, StateUnavailable
from lite_attest_assertion import CATEGORY, VERSION
from lite_attest_request import client_data
import test_lite_attest_enrollment as enrollment_fixture
import test_lite_quota as media_fixture


def b64(value):return base64.b64encode(value).decode('ascii')


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.e=enrollment_fixture.EnrollmentTests();self.e.setUp();self.addCleanup(self.e.doCleanups)
        self.f=media_fixture.LiteQuotaTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.app=self.f.client.application;self.client=self.f.client
        self.budget_store=SQLiteStateStore(Path(self.e.temp.name)/'budget.sqlite')
        self.budget=RequestBudget(self.budget_store,minute_limit=100,day_limit=1000,clock=lambda:self.e.now)
        self.privacy=PrivacyState(SQLiteStateStore(Path(self.e.temp.name)/'privacy.sqlite'))
        install_lite_attest(self.app,service=self.e.service,budget=self.budget,privacy=self.privacy)
        self.key=b64(self.e.key_id);self.counter=0

    def register(self):
        challenge=self.client.post(CHALLENGE_PATH,json=dict(key_id=self.key,purpose='register'))
        self.assertEqual(challenge.status_code,200,challenge.json)
        self.assertEqual(challenge.json['challenge'],b64(self.e.challenge.nonce))
        result=self.client.post(REGISTER_PATH,json=dict(key_id=self.key,challenge_id=challenge.json['challenge_id'],attestation=b64(self.e.encoded)))
        self.assertEqual(result.status_code,200,result.json)
        self.owner=result.json['installation'];return result

    def headers(self,path,raw,content_type,method='POST',purpose='assert'):
        response=self.client.post(CHALLENGE_PATH,json=dict(key_id=self.key,purpose=purpose))
        self.assertEqual(response.status_code,200,response.json)
        challenge=response.json;self.counter+=1
        rid='11111111-1111-4111-8111-111111111111'
        data=client_data(audience='lite-test',installation=self.owner,key_id=self.e.key_id,
                        challenge=base64.b64decode(challenge['challenge']),request_id=rid,
                        method=method,path=path,content_type=content_type,body=raw)
        auth=hashlib.sha256(self.e.r.policy.app_id.encode()).digest()+b'\x80'+self.counter.to_bytes(4,'big')
        auth+=cbor2.dumps({CATEGORY:2,VERSION:'33'})
        signature=self.e.r.key.sign(hashlib.sha256(auth+hashlib.sha256(data).digest()).digest(),ec.ECDSA(hashes.SHA256()))
        assertion=cbor2.dumps(dict(signature=signature,authenticatorData=auth))
        return {'X-Lite-Key-ID':self.key,'X-Lite-Assertion':b64(assertion),
                'X-Lite-Challenge-ID':challenge['challenge_id'],'X-Lite-Request-ID':rid}

    def segment_bytes(self,owner=None):
        builder=EnvironBuilder(method='POST',data=dict(anon_id=owner or self.owner,research_consent='true',
                                            image=(io.BytesIO(self.f.jpeg),'synthetic.jpg')))
        try:
            env=builder.get_environ();return env['wsgi.input'].read(),env['CONTENT_TYPE']
        finally:builder.close()

    def post_signed(self,path,raw,content_type='application/json'):
        headers=self.headers(path,raw,content_type)
        return self.client.post(path,data=raw,content_type=content_type,headers=headers)

    def test_full_registration_segment_revision_and_delete(self):
        self.register();path='/api/v1/lite/segment';raw,ctype=self.segment_bytes()
        response=self.post_signed(path,raw,ctype);self.assertEqual(response.status_code,200,response.json)
        image=response.json['image_id']
        self.assertTrue(self.f.store.exists('lite/'+self.owner+'/'+image+'.jpg'))
        revision=dict(anon_id=self.owner,image_id=image,revision=1,research_consent=True,payload_json=json.dumps(dict(
            polygons=[[[10,10],[39,10],[39,39]]],image_w=64,image_h=64,surface_cm2=1,projected_cm2=1,
            source='manual',consent_version='2026-10-03.1')))
        raw=json.dumps(revision).encode();response=self.post_signed('/api/v1/lite/annotation/revision',raw)
        self.assertEqual(response.status_code,200,response.json)
        path='/api/v1/lite/data/'+self.owner;headers=self.headers(path,b'','',method='DELETE')
        response=self.client.delete(path,headers=headers)
        self.assertEqual(response.status_code,200,response.json)
        self.assertFalse(self.f.store.exists('lite/'+self.owner+'/'+image+'.jpg'))

    def test_signed_ai_revision_preserves_geometry_location_and_unverified_grade(self):
        self.register();raw,ctype=self.segment_bytes()
        upload=self.post_signed('/api/v1/lite/segment',raw,ctype)
        self.assertEqual(upload.status_code,200,upload.json)
        iid=upload.json['image_id']
        payload=dict(polygons=[[[10,10],[39,10],[39,39]]],image_w=64,image_h=64,
                     surface_cm2=4.5,projected_cm2=4.2,source='ai',consent_version='2026-10-03.1',
                     wound_id='9ce45d4c-241c-400a-a29b-9635908fd271',wound_side='left',wound_site='heel')
        body=json.dumps(dict(anon_id=self.owner,image_id=iid,revision=1,research_consent=True,
                             payload_json=json.dumps(payload))).encode()
        for _ in range(2):
            response=self.post_signed('/api/v1/lite/annotation/revision',body)
            self.assertEqual(response.status_code,200,response.json)
        rows=media_fixture.lite._lite_label_rows_fresh()
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['label_grade'],'ai_unverified')
        self.assertEqual(rows[0]['surface_cm2'],4.5);self.assertEqual(rows[0]['wound_site'],'heel')
        self.assertEqual(media_fixture.lite._latest_lite_labels(rows),{})
        path='/api/v1/lite/data/'+self.owner
        response=self.client.delete(path,headers=self.headers(path,b'','',method='DELETE',purpose='withdraw'))
        self.assertEqual(response.status_code,200,response.json)
        self.assertFalse(any('surface_cm2' in row for row in media_fixture.lite._lite_label_rows_fresh()))

    def test_real_loopback_http_preserves_signed_multipart_bytes(self):
        import requests
        from werkzeug.serving import make_server
        server=make_server('127.0.0.1',0,self.app)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        session=requests.Session();session.trust_env=False
        base='http://127.0.0.1:'+str(server.server_port)
        try:
            challenge=session.post(base+CHALLENGE_PATH,json={'key_id':self.key,'purpose':'register'},timeout=5)
            self.assertEqual(challenge.status_code,200)
            result=session.post(base+REGISTER_PATH,json=dict(key_id=self.key,challenge_id=challenge.json()['challenge_id'],attestation=b64(self.e.encoded)),timeout=5)
            self.assertEqual(result.status_code,200);self.owner=result.json()['installation']
            raw,ctype=self.segment_bytes();path='/api/v1/lite/segment';headers=self.headers(path,raw,ctype)
            headers['Content-Type']=ctype
            result=session.post(base+path,data=raw,headers=headers,timeout=5)
            self.assertEqual(result.status_code,200,result.text)
            self.assertTrue(self.f.store.exists('lite/'+self.owner+'/'+result.json()['image_id']+'.jpg'))
            self.assertEqual(session.post(base+path,data=raw,headers=headers,timeout=5).status_code,401)
        finally:
            session.close();server.shutdown();thread.join(5);server.server_close()
        self.assertFalse(thread.is_alive())

    def test_missing_configuration_blocks_all_lite_and_leaves_health_available(self):
        app=Flask('closed');install_lite_attest(app)
        @app.route('/api/health')
        def health():return {'status':'ok'}
        client=app.test_client()
        for path in [CHALLENGE_PATH,REGISTER_PATH,'/api/v1/lite/segment','/api/v1/lite/annotation/revision']:
            response=client.post(path,json={});self.assertEqual(response.status_code,503)
        self.assertEqual(client.get('/api/health').status_code,200)

    def test_unsigned_requests_never_reach_model_or_store(self):
        with patch.object(media_fixture.lite,'_SEGMENT',side_effect=AssertionError('model must not run')):
            response=self.f.post();self.assertEqual(response.status_code,401)
        self.assertEqual(list(Path(self.f.tmp.name).rglob('*.jpg')),[])
        response=self.client.delete('/api/v1/lite/data/victim')
        self.assertEqual(response.status_code,401)

    def test_other_owner_cannot_upload_or_annotate(self):
        self.register();raw,ctype=self.segment_bytes('victim')
        response=self.post_signed('/api/v1/lite/segment',raw,ctype)
        self.assertEqual(response.status_code,403)
        response=self.post_signed('/api/v1/lite/annotation',json.dumps({'anon_id':'victim'}).encode())
        self.assertEqual(response.status_code,403)
        self.assertEqual(list(Path(self.f.tmp.name).rglob('*.jpg')),[])

    def test_body_tampering_replay_and_content_type_tampering(self):
        self.register();path='/api/v1/lite/segment';raw,ctype=self.segment_bytes();headers=self.headers(path,raw,ctype)
        response=self.client.post(path,data=raw+b' ',content_type=ctype,headers=headers)
        self.assertEqual(response.status_code,401)
        response=self.client.post(path,data=raw,content_type=ctype+'; charset=utf-8',headers=headers)
        self.assertEqual(response.status_code,401)
        response=self.client.post(path,data=raw,content_type=ctype,headers=headers)
        self.assertEqual(response.status_code,200,response.json)
        response=self.client.post(path,data=raw,content_type=ctype,headers=headers)
        self.assertEqual(response.status_code,401)
        self.assertEqual(len(list(Path(self.f.tmp.name).rglob('*.jpg'))),1)

    def test_duplicate_owner_fields_json_and_multipart_rejected(self):
        self.register()
        raw=(' {"anon_id":"'+self.owner+'","anon_id":"victim"}').encode()
        self.assertEqual(self.post_signed('/api/v1/lite/annotation',raw).status_code,401)
        raw,ctype=self.segment_bytes();boundary=ctype.split('boundary=')[1].strip('"')
        field=(f'--{boundary}\r\nContent-Disposition: form-data; name="anon_id"\r\n\r\n{self.owner}\r\n').encode()
        raw=field+raw
        self.assertEqual(self.post_signed('/api/v1/lite/segment',raw,ctype).status_code,401)

    def test_invalid_control_inputs_and_canonical_base64(self):
        alphabet='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'
        noncanonical=self.key[:-2]+alphabet[alphabet.index(self.key[-2])+1]+'='
        for data in [dict(key_id=noncanonical,purpose='register'),dict(key_id=self.key,purpose='register',installation='victim'),dict(key_id=self.key+'=',purpose='register'),
                     dict(key_id=self.key,purpose='unknown')]:
            response=self.client.post(CHALLENGE_PATH,json=data);self.assertEqual(response.status_code,401)
        duplicate='{"key_id":"'+self.key+'","key_id":"x","purpose":"register"}'
        self.assertEqual(self.client.post(CHALLENGE_PATH,data=duplicate,content_type='application/json').status_code,401)
        self.assertEqual(self.client.post(CHALLENGE_PATH,data=b'x'*4097,content_type='application/json').status_code,413)

    def test_query_encoded_path_unknown_route_and_compressed_body_rejected(self):
        self.app.add_url_rule('/api/v1/lite/new-route','unprotected_new_route',lambda:{'unexpected':'exposure'},methods=['POST'])
        valid=dict(key_id=self.key,purpose='register')
        for path in [CHALLENGE_PATH+'?x=1','/api/v1/lite/attest/%63hallenge']:
            self.assertEqual(self.client.post(path,json=valid).status_code,401)
        self.assertEqual(self.client.post('/api/v1/lite/new-route',json={}).status_code,404)
        self.assertEqual(self.client.post(CHALLENGE_PATH,json=valid,headers={'Content-Encoding':'gzip'}).status_code,401)

    def test_limiter_and_state_failures_do_not_fall_back(self):
        with patch.object(self.budget,'reserve',side_effect=BudgetExceeded(12)):
            response=self.client.post(CHALLENGE_PATH,json={})
            self.assertEqual(response.status_code,429);self.assertEqual(response.headers['Retry-After'],'12')
        with patch.object(self.budget,'reserve',side_effect=StateUnavailable('secret detail')):
            response=self.client.post(CHALLENGE_PATH,json={})
            self.assertEqual(response.status_code,503);self.assertNotIn(b'secret detail',response.data)
        with patch.object(self.e.store,'load',side_effect=StateUnavailable('broken')):
            response=self.client.post(CHALLENGE_PATH,json=dict(key_id=self.key,purpose='register'))
            self.assertEqual(response.status_code,503)

    def test_console_reads_still_require_jwt_audit_permission(self):
        from flask_jwt_extended import JWTManager, create_access_token
        import secrets
        self.app.config['JWT_SECRET_KEY']=secrets.token_hex(32)
        JWTManager(self.app)
        with self.app.app_context():
            admin=create_access_token(identity='test-admin',additional_claims={'role':'admin'})
            nurse=create_access_token(identity='test-nurse',additional_claims={'role':'nurse'})
        path='/api/v1/lite/records'
        self.assertEqual(self.client.get(path).status_code,401)
        self.assertEqual(self.client.get(path,headers={'Authorization':'Bearer '+nurse}).status_code,403)
        response=self.client.get(path,headers={'Authorization':'Bearer '+admin})
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(self.client.post('/api/v1/lite/segment',headers={'Authorization':'Bearer '+admin}).status_code,401)

    def test_application_installs_gate_before_lite_blueprint(self):
        source=Path(__file__).resolve().parents[2]/'Backend/Flask/app.py';tree=ast.parse(source.read_text())
        blocks=[n for n in tree.body if isinstance(n,ast.If) and isinstance(n.test,ast.Name) and n.test.id=='LITE_API_ENABLED']
        self.assertEqual(len(blocks),1)
        calls=sorted([n for n in ast.walk(blocks[0]) if isinstance(n,ast.Call)],key=lambda n:n.lineno)
        gate=[n.lineno for n in calls if isinstance(n.func,ast.Name) and n.func.id=='install_lite_attest']
        business=[n.lineno for n in calls if isinstance(n.func,ast.Attribute) and n.func.attr=='register_blueprint']
        self.assertEqual(len(gate),1);self.assertTrue(business);self.assertLess(gate[0],min(business))


class BudgetTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'budget.sqlite';self.now=1000
        self.store=SQLiteStateStore(self.path)
    def budget(self,minute=2,day=3):return RequestBudget(self.store,minute_limit=minute,day_limit=day,clock=lambda:self.now)
    def test_minute_daily_reset_and_restart(self):
        self.budget().reserve();self.budget().reserve()
        with self.assertRaises(BudgetExceeded):self.budget().reserve()
        self.now=1020;self.budget().reserve()
        with self.assertRaises(BudgetExceeded):self.budget().reserve()
        self.now=86400;self.budget().reserve()
        self.assertEqual(self.store.load(self.budget().key)[1]['day_used'],1)
    def test_parallel_global_limit_has_one_winner(self):
        barrier=threading.Barrier(8)
        def work(_):
            b=RequestBudget(SQLiteStateStore(self.path),minute_limit=1,day_limit=1,clock=lambda:self.now);barrier.wait(5)
            try:b.reserve();return 'ok'
            except BudgetExceeded:return 'limited'
        with ThreadPoolExecutor(8) as pool:results=list(pool.map(work,range(8)))
        self.assertEqual(results.count('ok'),1)
    def test_backward_clock_corrupt_state_and_unknown_write_outcome(self):
        budget=self.budget();budget.reserve();self.now=0
        with self.assertRaises(StateUnavailable):budget.reserve()
        self.now=1000;rev,state=self.store.load(budget.key);state['day_used']=-1;self.store.compare_exchange(budget.key,rev,state)
        with self.assertRaises(StateUnavailable):budget.reserve()
    def test_unavailable_persistence_cannot_reserve(self):
        with patch.object(self.store,'compare_exchange',side_effect=StateUnavailable('lost reply')):
            with self.assertRaises(StateUnavailable):self.budget().reserve()
        with patch.object(self.store,'compare_exchange',return_value=False):
            with self.assertRaises(StateUnavailable):self.budget().reserve()


class ConfigurationTests(unittest.TestCase):
    def config(self):
        return dict(WOUNDAI_LITE_ATTEST_APP_ID='ABCDEFGHIJ.com.woundai.lite',
                    WOUNDAI_LITE_ATTEST_VERSIONS='33,34',WOUNDAI_LITE_ATTEST_AUDIENCE='lite-public',
                    WOUNDAI_LITE_SECURITY_PROJECT='synthetic-project',WOUNDAI_LITE_SECURITY_BUCKET='synthetic-security',
                    WOUNDAI_LITE_SECURITY_PROJECT_NUMBER='123456789012',
                    WOUNDAI_LITE_BUDGET_MINUTE='20',WOUNDAI_LITE_BUDGET_DAY='100')
    def test_every_required_setting_checked_before_cloud_client(self):
        import lite_attest_config as config
        for missing in self.config():
            env=self.config();del env[missing]
            with self.subTest(missing=missing),patch.object(config.storage,'Client') as client:
                with self.assertRaises(ValueError):config.build_lite_security(env)
                client.assert_not_called()
    def test_invalid_policy_and_shared_buckets_rejected(self):
        import lite_attest_config as config
        for field,value in [('WOUNDAI_LITE_ATTEST_VERSIONS','33,33'),('WOUNDAI_LITE_ATTEST_APP_ID','wrong'),
                ('WOUNDAI_LITE_BUDGET_MINUTE','101'),('WOUNDAI_LITE_BUDGET_DAY','0'),
                ('WOUNDAI_GCS_BUCKET','synthetic-security'),('WOUNDAI_AUDIT_BUCKET','synthetic-security')]:
            env=self.config();env[field]=value
            with self.subTest(field=field),patch.object(config.storage,'Client') as client:
                with self.assertRaises(ValueError):config.build_lite_security(env)
                client.assert_not_called()
    def test_valid_config_separates_state_and_budget_namespaces(self):
        import lite_attest_config as config
        # A fake SDK and explicit test guard override together; never real ADC.
        with patch.object(config.storage,'Client') as client,patch.object(config,'_refuse_cloud_in_test_process'):
            client.return_value.bucket.return_value._properties = {
                'kind':'storage#bucket','name':'synthetic-security','projectNumber':'123456789012','metageneration':'1',
                'iamConfiguration':{'publicAccessPrevention':'enforced','uniformBucketLevelAccess':{'enabled':True}},
                'softDeletePolicy':{'retentionDurationSeconds':'0'}}
            service,budget,privacy=config.build_lite_security(self.config())
            client.return_value.bucket.return_value.reload.assert_called_once_with(timeout=10,retry=None)
        client.assert_called_once_with(project='synthetic-project')
        self.assertEqual(len({service.store.prefix,budget.store.prefix,privacy.store.prefix}),3)
        self.assertEqual(service.admission.environment,'production')
        self.assertEqual(service.admission.policy.allowed_categories,frozenset({2,4}))
        self.assertEqual(service.admission.policy.allowed_bundle_versions,frozenset({'33','34'}))
    def test_real_test_process_refuses_even_valid_cloud_routing(self):
        import lite_attest_config as config
        with patch.object(config.storage,'Client') as client:
            with self.assertRaises(RuntimeError):config.build_lite_security(self.config())
            client.assert_not_called()
    def test_runner_strips_new_routing_and_enable_flag(self):
        import importlib.util
        path=Path(__file__).resolve().parents[2]/'tools/windows/run_python_tests.py'
        spec=importlib.util.spec_from_file_location('http_isolation_runner',path)
        runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
        env=self.config();env['WOUNDAI_ENABLE_LITE_API']='1';env['PATH']='keep'
        self.assertEqual(runner.sanitized_test_environment(env),{'PATH':'keep','WOUNDAI_REQUIRE_FUNCTIONAL_TESTS':'1'})


if __name__=='__main__':unittest.main()
