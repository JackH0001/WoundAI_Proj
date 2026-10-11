"""Real composition with synthetic signed IAP and in-memory HTTP services."""
import os
import time
import unittest
from unittest.mock import Mock, patch

import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization

from . import runtime as r
from .authentication import IAP_ISSUER
from .audit import BUCKET
from .iap_policy import RESOURCE
from .test_audit import ObjectHttp, Response
from .test_iap_policy import MemoryAdapter
from .test_sessions import Backend


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.raw = Mock()
        self.tokens = Mock()
        self.tokens.token.side_effect = lambda purpose: purpose + '-SYNTHETIC'

    def test_backend_headers_remain_distinct(self):
        http = r.ScopedHttp(self.raw, self.tokens, 'backend')
        http.get(r.SESSION_URL, headers={'Authorization':'Bearer APP-SYNTHETIC'})
        args = self.raw.request.call_args.kwargs
        self.assertEqual(args['headers'], {'Authorization':'Bearer APP-SYNTHETIC',
                                          'Proxy-Authorization':'Bearer backend-SYNTHETIC'})
        self.assertFalse(args['allow_redirects'])

    def test_no_auth_refresh_or_write_retry_after_401(self):
        self.raw.request.return_value = Response(401)
        result = r.ScopedHttp(self.raw,self.tokens,'backend').post(r.LOGIN_URL,json={'username':'synthetic'})
        self.assertEqual(result.status_code,401)
        self.raw.request.assert_called_once()
        self.tokens.token.assert_called_once_with('backend')

    def test_scope_and_security_overrides_rejected_before_credentials(self):
        http = r.ScopedHttp(self.raw,self.tokens,'backend')
        cases = [('GET','https://attacker.test',{}),('POST',r.SESSION_URL,{}),
                 ('GET',r.SESSION_URL,{'headers':{'Proxy-Authorization':'steal'}}),
                 ('GET',r.SESSION_URL,{'allow_redirects':True}),
                 ('GET',r.SESSION_URL,{'verify':False})]
        for method,url,args in cases:
            with self.subTest(url=url,args=args),self.assertRaises(r.RuntimeErrorSafe):
                http.request(method,url,**args)
        self.raw.request.assert_not_called();self.tokens.token.assert_not_called()

    def test_cloud_scope_does_not_include_clinical_objects_or_delete(self):
        http=r.ScopedHttp(self.raw,self.tokens,'cloud')
        for method,url,args in [('DELETE',f'https://storage.googleapis.com/storage/v1/b/{BUCKET}/o/x',{}),
            ('GET',f'https://storage.googleapis.com/storage/v1/b/{BUCKET}/o/clinical.json',{}),
            ('POST',f'https://storage.googleapis.com/upload/storage/v1/b/{BUCKET}/o',{'params':{'name':'clinical.json'}})]:
            with self.assertRaises(r.RuntimeErrorSafe):http.request(method,url,**args)
        self.tokens.token.assert_not_called()

    def test_public_keys_never_receive_service_credentials(self):
        r.ScopedHttp(self.raw,self.tokens,'public').get(r.IAP_KEYS)
        self.assertEqual(self.raw.request.call_args.kwargs['headers'],{})
        self.tokens.token.assert_not_called()

    def test_underlying_error_redacted(self):
        self.raw.request.side_effect=TimeoutError('secret')
        with self.assertRaisesRegex(r.RuntimeErrorSafe,'^transport_result_unconfirmed$'):
            r.ScopedHttp(self.raw,self.tokens,'backend').post(r.LOGIN_URL)
        self.assertEqual(self.raw.request.call_count,1)

    def test_http_disables_environment_credentials_and_retries(self):
        with r.new_http() as http:
            self.assertFalse(http.trust_env)
            self.assertEqual(http.get_adapter('https://example.test').max_retries.total,0)


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.now=[1000]
        self.http=Mock()
        self.email=r.IDENTITY
        self.claims={'email':r.IDENTITY,'iss':'https://accounts.google.com',
                     'aud':r.BACKEND_AUDIENCE,'exp':2000}
        def get(url,**kwargs):
            result=Mock(status_code=200,headers={'Metadata-Flavor':'Google'})
            if url==r.METADATA+'email':result.text=self.email
            elif url==r.METADATA+'token':result.json.return_value={'access_token':'synthetic','expires_in':600,'token_type':'Bearer'}
            elif url==r.METADATA+'identity':result.text=jwt.encode(self.claims,'test-only-key-for-cache-inspection-123',algorithm='HS256')
            else:raise AssertionError(url)
            return result
        self.http.get.side_effect=get
        self.tokens=r.AttachedTokens(self.http,clock=lambda:self.now[0])

    def test_attached_identity_required_before_token_request(self):
        self.email='wrong@identity.test'
        with self.assertRaises(r.RuntimeErrorSafe):self.tokens.token('cloud')
        self.assertEqual(self.http.get.call_count,1)

    def test_cache_refresh_and_fixed_metadata_headers(self):
        self.assertEqual(self.tokens.token('cloud'),'synthetic')
        self.tokens.token('cloud');self.assertEqual(self.http.get.call_count,2)
        self.now[0]+=550;self.tokens.token('cloud');self.assertEqual(self.http.get.call_count,4)
        for args in self.http.get.call_args_list:
            self.assertEqual(args.kwargs['headers'],{'Metadata-Flavor':'Google'})
            self.assertFalse(args.kwargs['allow_redirects'])

    def test_backend_metadata_audience_and_cache_claims(self):
        self.tokens.token('backend')
        self.assertEqual(self.http.get.call_args.kwargs['params'],{'audience':r.BACKEND_AUDIENCE,'format':'full'})
        for key,value in [('aud','wrong'),('email','wrong'),('exp',1001),('iss','wrong')]:
            self.tokens.cache.clear();old=self.claims[key];self.claims[key]=value
            with self.assertRaises(r.RuntimeErrorSafe):self.tokens.token('backend')
            self.claims[key]=old

    def test_startup_default_never_discovers_credentials(self):
        with patch.dict(os.environ,{},clear=True),patch.object(r,'new_http') as http:
            with self.assertRaisesRegex(r.RuntimeErrorSafe,'not_explicitly_configured'):r.cloud_app()
            http.assert_not_called()


class CompositionTests(unittest.TestCase):
    def test_signed_login_policy_audit_accounts_and_logout_in_one_runtime(self):
        backend=Backend();policy=MemoryAdapter();objects=ObjectHttp()
        key=ec.generate_private_key(ec.SECP256R1())
        pem=key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        public=Mock();public.get.return_value=Response(body={'synthetic':pem})
        class Cloud:
            def post(self,url,**kwargs):
                if url==f'https://iap.googleapis.com/v1/{RESOURCE}:getIamPolicy':
                    return Response(body=policy.get_policy(RESOURCE))
                if url==f'https://iap.googleapis.com/v1/{RESOURCE}:setIamPolicy':
                    policy.set_policy(RESOURCE,kwargs['json']['policy'])
                    return Response(body=policy.get_policy(RESOURCE))
                return objects.post(url,**kwargs)
            def get(self,url,**kwargs):return objects.get(url,**kwargs)
        app=r.assemble(backend_http=backend,cloud_http=Cloud(),public_http=public)
        client=app.test_client()
        token=jwt.encode({'iss':IAP_ISSUER,'aud':r.AUDIENCE,'iat':int(time.time())-1,
            'exp':int(time.time())+300,'sub':'accounts.google.com:synthetic','email':r.ADMINS[0]},
            key,algorithm='ES256',headers={'kid':'synthetic'})
        headers={'X-Goog-IAP-JWT-Assertion':token,'Origin':r.ORIGIN}
        def get(path):return client.get(path,base_url=r.ORIGIN,headers=headers)
        def post(path,data,csrf,session=False):
            h=dict(headers);h['X-MMH-Session-CSRF' if session else 'X-MMH-CSRF']=csrf
            return client.post(path,base_url=r.ORIGIN,headers=h,json=data)
        first=get('/api/session');self.assertEqual(first.status_code,200)
        self.assertEqual(post('/api/session/login',{'username':'admin','password':'synthetic-test'},
                              first.json['session_csrf'],True).status_code,200)
        members=get('/api/members');self.assertEqual(members.status_code,200)
        self.assertTrue(members.json['account_management_enabled'])
        result=post('/api/entry',{'email':'synthetic@example.test','action':'grant',
            'policy_snapshot':members.json['policy_snapshot']},members.json['csrf'])
        self.assertEqual(result.status_code,200)
        self.assertEqual(len(objects.objects),2)
        self.assertEqual(len(policy.writes),1)
        updated=get('/api/members')
        self.assertTrue(any(row['email']=='synthetic@example.test' and row['direct'] for row in updated.json['entries']))
        created=post('/api/accounts',{'action':'create','user':'synthetic-doctor','role':'physician',
            'display_name':'Synthetic','disabled':False,'account_snapshot':updated.json['account_snapshot']},updated.json['csrf'])
        self.assertEqual(created.status_code,200)
        self.assertTrue(any(row['user']=='synthetic-doctor' for row in get('/api/members').json['accounts']))
        state=get('/api/session')
        self.assertEqual(post('/api/session/logout',{},state.json['session_csrf'],True).status_code,200)
        self.assertEqual(get('/api/members').status_code,401)


if __name__=='__main__':unittest.main()
