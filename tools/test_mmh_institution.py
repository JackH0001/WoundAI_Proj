"""Synthetic dedicated-org API validation; temp LocalStore only, no cloud calls."""
import importlib
import json
import os
import secrets
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'Backend'/'Flask'))
import institution_context as context
import auth_users
from store import LocalStore, GcsStore
from flask import Flask
from flask_jwt_extended import JWTManager, create_access_token
import api_users
import api_flywheel

ORG='mmhps20261007'
class InstitutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=LocalStore(self.tmp.name)
        for target in ('institution_context.BOUND_ORG','auth_users.BOUND_ORG'):
            p=patch(target,ORG);p.start();self.addCleanup(p.stop)
        p=patch.object(auth_users,'DEFAULT_ORG',ORG);p.start();self.addCleanup(p.stop)
        p=patch.object(auth_users,'_store',return_value=self.store);p.start();self.addCleanup(p.stop)
        p=patch.object(api_flywheel,'_store',return_value=self.store);p.start();self.addCleanup(p.stop)
        self.password=secrets.token_urlsafe(24)
    def user(self,user='admin',role='admin'):
        return auth_users.upsert_user(ORG,user,role,self.password)
    def test_runner_strips_institution_config(self):
        import importlib.util
        path=Path(__file__).resolve().parent/'windows'/'run_python_tests.py'
        spec=importlib.util.spec_from_file_location('mmh_runner',path)
        runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
        result=runner.sanitized_test_environment({'WOUNDAI_INSTITUTION_ORG':ORG,'woundai_security_bucket':'s','KEEP':'yes'})
        self.assertNotIn('WOUNDAI_INSTITUTION_ORG',result);self.assertNotIn('woundai_security_bucket',result)
        self.assertEqual(result['KEEP'],'yes')
    def test_config_rejects_empty_uppercase_default_whitespace_and_lite(self):
        self.assertIsNone(context.configured_org({}))
        self.assertEqual(context.configured_org({'WOUNDAI_INSTITUTION_ORG':ORG}),ORG)
        for x in ('','MMHPS20261007','default',ORG+'\n',' '+ORG,[],23):
            with self.assertRaises(ValueError):context.configured_org({'WOUNDAI_INSTITUTION_ORG':x})
        with self.assertRaises(ValueError):context.configured_org({'WOUNDAI_INSTITUTION_ORG':ORG,'WOUNDAI_SERVICE_PROFILE':'lite'})
    def test_org_is_required_for_all_account_writes(self):
        for org in ('default','other',None,ORG+'\n'):
            with self.assertRaises(ValueError):auth_users.upsert_user(org,'dr01','physician',self.password)
        self.assertEqual(auth_users.list_users(),[])
    def test_foreign_account_in_store_rejected_not_hidden(self):
        self.store.append_line('users.jsonl',json.dumps({'org':'other','user':'dr01','role':'physician'}))
        with self.assertRaises(RuntimeError):auth_users.list_users()
    def test_bootstrap_uses_institution_and_survives_fresh_store(self):
        with patch.dict(os.environ,{'ADMIN_PASSWORD':self.password}):
            self.assertEqual(auth_users.bootstrap_from_env()['org'],ORG)
            with patch.object(auth_users,'_store',return_value=LocalStore(self.tmp.name)):
                self.assertIsNone(auth_users.bootstrap_from_env())
                self.assertEqual(auth_users.authenticate(ORG,'admin',self.password)[1],'ok')
    def test_bootstrap_refuses_failed_audit_before_creating_account(self):
        with patch.dict(os.environ,{'ADMIN_PASSWORD':self.password}), patch.object(api_flywheel,'audit_intent',side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError):auth_users.bootstrap_from_env()
        self.assertEqual(auth_users.list_users(),[])
    def test_institution_gcs_requires_three_distinct_buckets(self):
        import store
        for security,audit in ((None,'audit'),('media','audit'),('security','media'),('same','same')):
            env={'WOUNDAI_STORE':'gcs','WOUNDAI_GCS_BUCKET':'media'}
            if security:env['WOUNDAI_SECURITY_BUCKET']=security
            if audit:env['WOUNDAI_AUDIT_BUCKET']=audit
            with patch.dict(os.environ,env,clear=True), patch.object(store,'_ACTIVE',None), patch.object(store,'_refuse_cloud_in_test_process'), patch.object(store,'GcsStore') as ctor:
                with self.assertRaises(RuntimeError):store.get_store()
                ctor.assert_not_called()
    def test_foreign_login_refused_even_if_user_name_matches(self):
        self.user()
        self.assertIsNone(auth_users.authenticate('default','admin',self.password)[0])
    def claims(self):return {'org':ORG,'user':'admin','sub':ORG+':admin','role':'admin'}
    def test_token_requires_matching_identity_and_live_role(self):
        self.user();claims=self.claims()
        self.assertTrue(context.token_matches_institution(claims,auth_users.get_user))
        for field,value in [('org','other'),('user','else'),('sub','other:admin'),('role','physician')]:
            altered=dict(claims);altered[field]=value
            self.assertFalse(context.token_matches_institution(altered,auth_users.get_user))
        auth_users.upsert_user(ORG,'admin','admin',disabled=True)
        self.assertFalse(context.token_matches_institution(claims,auth_users.get_user))
    def test_unavailable_account_state_refuses_token(self):
        def fail(*args):raise IOError('offline')
        self.assertFalse(context.token_matches_institution(self.claims(),fail))
    def test_legacy_token_behavior_unchanged_without_binding(self):
        with patch.object(context,'BOUND_ORG',None):
            self.assertTrue(context.token_matches_institution({},lambda *args:None))
    def test_security_route_is_exact_and_audit_wins(self):
        store=object.__new__(GcsStore)
        store._bucket,store._bucket_name='media-object','media'
        store._security_bucket,store._security_bucket_name='security-object','security'
        store._audit_bucket,store._audit_bucket_name='audit-object','audit'
        for key in ('users.jsonl','users.jsonl/entry.json'):
            self.assertEqual(store._target(key)[1],'security')
        for key in ('images/photo.jpg','users.jsonlX/file'):
            self.assertEqual(store._target(key)[1],'media')
        self.assertEqual(store._target('audit.jsonl')[1],'audit')
        with self.assertRaises(PermissionError):store.move('users.jsonl/entry.json','other')
    def test_console_omitted_org_creates_institution_and_foreign_write_rejected(self):
        self.user();app=Flask(__name__);app.config['JWT_SECRET_KEY']='test-'+('x'*40)
        jwt=JWTManager(app)
        jwt.token_verification_loader(lambda h,c:context.token_matches_institution(c,auth_users.get_user))
        app.register_blueprint(api_users.users_bp)
        with app.app_context():
            token=create_access_token(identity=ORG+':admin',additional_claims={'org':ORG,'user':'admin','role':'admin'})
        headers={'Authorization':'Bearer '+token};cli=app.test_client()
        response=cli.post('/api/v1/users',headers=headers,json={'user':'ns01','role':'nurse','generate_password':True})
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(response.json['user']['identity'],ORG+':ns01')
        self.assertNotIn('gt.verify',[p for p in auth_users.PERMS if auth_users.can('nurse',p)])
        denied=cli.post('/api/v1/users',headers=headers,json={'org':'other','user':'ns02','role':'nurse','generate_password':True})
        self.assertEqual(denied.status_code,400)
        auth_users.upsert_user(ORG,'admin','admin',disabled=True)
        self.assertEqual(cli.get('/api/v1/users',headers=headers).status_code,400)

if __name__=='__main__':unittest.main(verbosity=2)
