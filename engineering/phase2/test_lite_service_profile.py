"""Public Lite service perimeter, pre-cloud config, and actual app startup."""
import importlib.util
import io
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import Mock, patch
from flask import Flask
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'Backend/Flask'))
from lite_service_profile import service_profile, install_lite_perimeter, POST_ROUTES
import lite_attest_config as config
from test_lite_bucket_policy import metadata, environment

ROOT=Path(__file__).resolve().parents[2]


def lite_environment(runtime):
    env=environment()
    env.update(WOUNDAI_SERVICE_PROFILE='lite',WOUNDAI_ENABLE_LITE_API='1',WOUNDAI_STORE='gcs',
               WOUNDAI_GCS_PREFIX='lite-public-v1',WOUNDAI_GCS_BUCKET='synthetic-media',
               WOUNDAI_RUNTIME_DIR=runtime,LITE_IP_SALT=secrets.token_hex(32))
    return env


class ProfileConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.env=lite_environment(self.folder.name)
    def test_default_medical_and_strict_profile(self):
        self.assertEqual(service_profile({}),'medical');self.assertEqual(service_profile(self.env),'lite')
        for value in ('','Lite','public',None):
            with self.subTest(value=value),self.assertRaises(ValueError):service_profile({'WOUNDAI_SERVICE_PROFILE':value})
    def test_public_routing_must_be_explicit_before_sdk(self):
        for key in ('WOUNDAI_ENABLE_LITE_API','WOUNDAI_STORE','WOUNDAI_GCS_PREFIX','WOUNDAI_GCS_BUCKET',
                    'WOUNDAI_LITE_SECURITY_BUCKET','WOUNDAI_RUNTIME_DIR','LITE_IP_SALT'):
            env=dict(self.env);env.pop(key)
            with self.subTest(key=key),patch.object(config.storage,'Client') as client:
                with self.assertRaises(ValueError):config.build_lite_security(env)
                client.assert_not_called()
    def test_local_store_shared_buckets_and_weak_salt_rejected(self):
        for key,value in [('WOUNDAI_STORE','local'),('WOUNDAI_GCS_PREFIX','flywheel'),
                          ('WOUNDAI_GCS_BUCKET','synthetic-security'),('LITE_IP_SALT','short'),
                          ('WOUNDAI_RUNTIME_DIR','relative')]:
            env=dict(self.env);env[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):service_profile(env)
    def test_clinical_and_demo_settings_rejected(self):
        for key in ('ADMIN_PASSWORD','JWT_SECRET_KEY','FLASK_SECRET_KEY','CARE_RECEIPT_SECRET',
                    'WOUNDAI_AUDIT_BUCKET','WOUNDAI_DEMO_SEED_USER','WOUNDAI_DEMO_SEED_FUTURE'):
            env=dict(self.env);env[key]='synthetic'
            with self.subTest(key=key),self.assertRaises(ValueError):service_profile(env)
    def test_both_buckets_are_reloaded_before_admission_store_created(self):
        security,media=Mock(),Mock();security._properties=metadata();media._properties=metadata('synthetic-media')
        media.name='synthetic-media';media.list_blobs.return_value=[]
        with patch.object(config.storage,'Client') as client,patch.object(config,'_refuse_cloud_in_test_process'):
            client.return_value.bucket.side_effect=lambda name:{'synthetic-security':security,'synthetic-media':media}[name]
            result=config.build_lite_security(self.env)
            self.assertEqual(len(result),3)
            from lite_fenced_store import FencedPrivacy
            self.assertIsInstance(result[2],FencedPrivacy)
            self.assertEqual(media.list_blobs.call_count,4)
        for bucket in (security,media):bucket.reload.assert_called_once_with(timeout=10,retry=None)
    def test_media_retention_wrong_project_and_region_prevent_admission(self):
        for key,value in [('versioning',{'enabled':True}),('projectNumber','999999999999'),('location','US')]:
            security,media=Mock(),Mock();security._properties=metadata();media._properties=metadata('synthetic-media');media._properties[key]=value
            with self.subTest(key=key),patch.object(config.storage,'Client') as client,patch.object(config,'_refuse_cloud_in_test_process'),patch.object(config,'GCSStateStore') as state:
                client.return_value.bucket.side_effect=[security,media]
                with self.assertRaises(ValueError):config.build_lite_security(self.env)
                state.assert_not_called()
    def test_legacy_dataset_or_unavailable_inventory_blocks_namespace_cutover(self):
        for outcome in ([Mock()], OSError('cannot list')):
            security,media=Mock(),Mock();security._properties=metadata();media._properties=metadata('synthetic-media')
            media.name='synthetic-media'
            if isinstance(outcome,Exception):media.list_blobs.side_effect=outcome
            else:media.list_blobs.return_value=outcome
            with patch.object(config.storage,'Client') as client,patch.object(config,'_refuse_cloud_in_test_process'):
                client.return_value.bucket.side_effect=[security,media]
                with self.assertRaises((ValueError,OSError)):config.build_lite_security(self.env)

    def test_media_readback_failure_rejects_even_with_good_cached_metadata(self):
        security,media=Mock(),Mock();security._properties=metadata();media._properties=metadata('synthetic-media');media.reload.side_effect=OSError('offline')
        with patch.object(config.storage,'Client') as client,patch.object(config,'_refuse_cloud_in_test_process'),patch.object(config,'GCSStateStore') as state:
            client.return_value.bucket.side_effect=[security,media]
            with self.assertRaises(OSError):config.build_lite_security(self.env)
            state.assert_not_called()
    def test_runner_removes_public_profile_and_hashing_secret(self):
        spec=importlib.util.spec_from_file_location('runner',ROOT/'tools/windows/run_python_tests.py')
        runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
        env=runner.sanitized_test_environment({'WOUNDAI_SERVICE_PROFILE':'lite','lite_ip_salt':'synthetic','PATH':'keep'})
        self.assertEqual(env,{'PATH':'keep','WOUNDAI_REQUIRE_FUNCTIONAL_TESTS':'1'})


class PerimeterTests(unittest.TestCase):
    def setUp(self):
        self.app=Flask(__name__,static_folder=None);install_lite_perimeter(self.app);self.calls=[]
        @self.app.before_request
        def later_hook():self.calls.append('later')
        def business(**kwargs):self.calls.append('business');return {'hit':True}
        self.app.add_url_rule('/<path:anything>','business',business,methods=['GET','POST','PUT','DELETE','OPTIONS'])
        self.client=self.app.test_client()
    def test_all_five_post_routes_and_withdrawal_allowed(self):
        for path in POST_ROUTES:
            self.assertEqual(self.client.post(path).status_code,200,path)
        self.assertEqual(self.client.delete('/api/v1/lite/data/'+'a'*32).status_code,200)
        self.assertEqual(self.client.get('/api/health').status_code,200)
        self.assertEqual(self.client.head('/api/health').status_code,200)
    def test_clinical_console_future_routes_and_reviewer_reads_never_reach_hooks(self):
        for path in ('/console','/api/auth/login','/api/v1/auth/onetime','/api/auth/exchange','/api/v1/users',
                     '/api/v1/classify','/api/v1/annotation','/api/train','/api/model/retrain','/api/v1/lite/records',
                     '/api/v1/lite/image/a/b','/static/a','/future/clinical','/'):
            for method in ('GET','HEAD','POST','PUT','DELETE','OPTIONS'):
                with self.subTest(path=path,method=method):
                    response=self.client.open(path,method=method)
                    self.assertEqual(response.status_code,404);self.assertEqual(response.headers['Cache-Control'],'no-store')
        self.assertEqual(self.calls,[])
    def test_wrong_methods_encoded_paths_prefixes_and_queries_rejected(self):
        for path,method in [('/api/v1/lite/segment','GET'),('/api/v1/lite/segment','OPTIONS'),
                            ('/api/health','POST'),('/api/health?x=1','GET'),('/api/%76%31/lite/segment','POST'),
                            ('/api/v1/lite/segment/','POST'),('/api/v1/lite/data/not-owner','DELETE'),
                            ('/api/v1/lite/data/'+'A'*32,'DELETE')]:
            with self.subTest(path=path,method=method):self.assertEqual(self.client.open(path,method=method).status_code,404)
        self.assertEqual(self.client.post('/api/v1/lite/segment',environ_overrides={'SCRIPT_NAME':'/prefix'}).status_code,404)
        self.assertEqual(self.calls,[])
    def test_fence_cannot_be_registered_after_existing_hooks(self):
        app=Flask('late')
        @app.before_request
        def unsafe():pass
        with self.assertRaises(ValueError):install_lite_perimeter(app)
    def test_denied_route_does_not_read_body(self):
        class Unreadable(io.BytesIO):
            def read(self,*args):raise AssertionError('body read')
            def readinto(self,*args):raise AssertionError('body read')
        response=self.client.post('/api/auth/login',environ_overrides={'wsgi.input':Unreadable(b'x'),'CONTENT_LENGTH':'1'})
        self.assertEqual(response.status_code,404);self.assertEqual(self.calls,[])


class RealApplicationTests(unittest.TestCase):
    def probe(self,body):
        spec=importlib.util.spec_from_file_location('runner',ROOT/'tools/windows/run_python_tests.py')
        runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
        env=runner.sanitized_test_environment(os.environ)
        env['PYTHONPATH']=os.pathsep.join([str(ROOT/'Backend/Flask'),str(ROOT/'engineering/phase2')])
        with tempfile.TemporaryDirectory() as folder:
            script=Path(folder)/'test_real_lite_profile.py';script.write_text(textwrap.dedent(body))
            result=subprocess.run([sys.executable,'-B',str(script)],cwd=folder,env=env,capture_output=True,text=True,timeout=90)
            self.assertEqual(result.returncode,0,result.stdout[-2500:]+result.stderr[-2500:])
    def test_actual_app_skips_clinical_bootstrap_secrets_database_and_routes(self):
        self.probe('''
            import os,sys
            from pathlib import Path
            from unittest.mock import patch
            from test_lite_service_profile import lite_environment
            import test_lite_attest_http as fixture
            f=fixture.HTTPTests();f.setUp()
            try:
                os.environ.update(lite_environment(os.getcwd()))
                os.environ['WOUNDAI_FLYWHEEL_DIR']=os.path.join(os.getcwd(),'flywheel')
                import auth_users, runtime_secrets, lite_attest_config, sqlite3
                import store
                with patch.dict(sys.modules,{'onnxruntime':None,'tensorflow':None,'imagej':None}), \
                     patch.object(auth_users,'bootstrap_from_env',side_effect=AssertionError('bootstrap')) as boot, \
                     patch.object(auth_users,'seed_demo_from_env',side_effect=AssertionError('seed')) as seed, \
                     patch.object(runtime_secrets,'resolve_secret',side_effect=AssertionError('medical secret')) as secret, \
                     patch.object(sqlite3,'connect',side_effect=AssertionError('clinical database')) as database, \
                     patch.object(lite_attest_config,'build_lite_security',return_value=(f.e.service,f.budget,f.privacy)), \
                     patch('google.cloud.storage.Client',side_effect=AssertionError('real GCS')):
                    import app as module
                    import test_lite_service_profile as target
                    assert Path(module.__file__).resolve() == Path(target.__file__).resolve().parents[2]/"Backend/Flask/app.py"
                    assert module.LITE_SERVICE
                    assert module.auth_users is None
                    assert not {'users','console','flywheel'} & set(module.app.blueprints)
                    client=module.app.test_client()
                    assert client.post('/api/auth/login',json={}).status_code==404
                    assert client.get('/console').status_code==404
                    assert client.get('/api/v1/lite/records').status_code==404
                    response=client.get('/api/health')
                    assert response.status_code==503 and response.json['profile']=='lite'
                    assert 'database' not in response.json['services'] and 'store' not in response.json
                    assert not Path('wound_analysis.db').exists()
                    for mock in (boot,seed,secret,database):mock.assert_not_called()
                assert client.post('/api/v1/lite/segment',data=b'unsigned',content_type='application/json').status_code==401
            finally:f.doCleanups()
        ''')
    def test_actual_app_refuses_start_when_lite_security_configuration_fails(self):
        self.probe('''
            import os,sys
            from unittest.mock import patch
            from test_lite_service_profile import lite_environment
            os.environ.update(lite_environment(os.getcwd()))
            import lite_attest_config
            with patch.dict(sys.modules,{'onnxruntime':None,'tensorflow':None,'imagej':None}), \
                 patch.object(lite_attest_config,'build_lite_security',side_effect=RuntimeError('synthetic config failure')):
                try:import app
                except RuntimeError as e:assert str(e)=='Lite service initialization failed'
                else:raise AssertionError('Lite app started with unavailable security')
        ''')
    def test_signed_lifecycle_passes_behind_perimeter(self):
        import test_lite_attest_http as fixture
        f=fixture.HTTPTests();f.setUp();self.addCleanup(f.doCleanups)
        hooks=f.app.before_request_funcs.pop(None)
        install_lite_perimeter(f.app)
        f.app.before_request_funcs[None].extend(hooks)
        f.test_full_registration_segment_revision_and_delete()


if __name__=='__main__':unittest.main()
