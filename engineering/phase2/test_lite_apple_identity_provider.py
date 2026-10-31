"""Key lifecycle uses synthetic signatures and injected transport; no Apple calls."""
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from email.message import Message
from unittest.mock import patch
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from lite_account_identity import IdentityRejected
from lite_attest_state import StateUnavailable
from lite_apple_identity_provider import RefreshingAppleIdentityVerifier, fetch_apple_keys, APPLE_KEYS_URL, _NoRedirect


class ProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.private=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        cls.other=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    def setUp(self):
        self.wall=1900000000;self.mono=1000;self.calls=0
        self.body=self.keys(self.private,'one')
        self.v=RefreshingAppleIdentityVerifier(audience='com.woundai.lite',fetch=self.fetch,
            clock=lambda:self.wall,monotonic=lambda:self.mono,ttl=60)
    def keys(self,private,kid):
        k=json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
        k.update(alg='RS256',use='sig',kid=kid)
        return json.dumps({'keys':[k]}).encode()
    def fetch(self):
        self.calls+=1
        if isinstance(self.body,Exception):raise self.body
        return self.body
    def token(self,private=None,kid='one',headers=None,**changes):
        c=dict(iss='https://appleid.apple.com',aud='com.woundai.lite',sub='synthetic-user',
            iat=self.wall,exp=self.wall+600,nonce='n')
        c.update(changes)
        return jwt.encode(c,private or self.private,algorithm='RS256',headers=headers or dict(kid=kid))
    def advance(self,n):self.wall+=n;self.mono+=n
    def test_no_network_on_construction(self):
        self.assertEqual(self.calls,0);self.assertFalse(self.v.health()['ready'])
    def test_known_key_is_cached(self):
        for _ in range(4):self.assertEqual(self.v.verify(self.token(),'n')['sub'],'synthetic-user')
        self.assertEqual(self.calls,1)
    def test_parallel_cold_requests_fetch_once(self):
        signed=self.token()
        with ThreadPoolExecutor(max_workers=8) as pool:r=list(pool.map(lambda _:self.v.verify(signed,'n'),range(16)))
        self.assertEqual(len(r),16);self.assertEqual(self.calls,1)
    def test_rotation_unknown_kid_refreshes_then_old_key_is_removed(self):
        self.v.verify(self.token(),'n');self.advance(31);self.body=self.keys(self.other,'two')
        self.v.verify(self.token(self.other,'two'),'n');self.assertEqual(self.calls,2)
        with self.assertRaises(StateUnavailable):self.v.verify(self.token(),'n')
    def test_unknown_kids_do_not_amplify_fetches(self):
        with self.assertRaises(IdentityRejected):self.v.verify(self.token(kid='missing'),'n')
        for n in range(20):
            with self.assertRaises(StateUnavailable):self.v.verify(self.token(kid=str(n)),'n')
        self.assertEqual(self.calls,1)
    def test_bad_signature_for_known_kid_does_not_refresh(self):
        self.v.verify(self.token(),'n')
        with self.assertRaises(IdentityRejected):self.v.verify(self.token(self.other),'n')
        self.assertEqual(self.calls,1)
    def test_untrusted_header_url_and_algorithm_rejected_before_fetch(self):
        for h in [dict(kid='one',jku='https://example.invalid/keys'),dict(kid='one',x5u='https://example.invalid/key'),dict(kid='one',crit=['x']),dict(kid='one',typ='not-JWT')]:
            with self.subTest(h=h),self.assertRaises(IdentityRejected):self.v.verify(self.token(headers=h),'n')
        self.assertEqual(self.calls,0)
    def test_outage_after_expiry_never_uses_stale_key(self):
        signed=self.token();self.v.verify(signed,'n');self.advance(60);self.body=TimeoutError()
        with self.assertRaises(StateUnavailable):self.v.verify(signed,'n')
        self.assertFalse(self.v.health()['ready']);self.assertEqual(self.calls,2)
    def test_retry_backoff_then_recovery_does_not_change_account(self):
        signed=self.token();self.body=TimeoutError()
        with self.assertRaises(StateUnavailable):self.v.verify(signed,'n')
        self.body=self.keys(self.private,'one')
        with self.assertRaises(StateUnavailable):self.v.verify(signed,'n')
        self.advance(30);self.assertEqual(self.v.verify(signed,'n')['sub'],'synthetic-user');self.assertEqual(self.calls,2)
    def test_failed_background_refresh_keeps_only_still_fresh_key(self):
        signed=self.token();self.v.verify(signed,'n');self.advance(31);self.body=TimeoutError()
        with self.assertRaises(StateUnavailable):self.v.refresh()
        self.assertTrue(self.v.health()['ready']);self.v.verify(signed,'n')
        self.advance(30)
        with self.assertRaises(StateUnavailable):self.v.verify(signed,'n')
    def test_malformed_duplicate_oversized_and_invalid_jwks_fail_closed(self):
        for body in [b'{}',b'{"keys":[],"keys":[]}',b'x'*65537,b'{"keys":[1]}',b'{"keys":[]}',b'not json']:
            with self.subTest(body=body[:30]):
                self.body=body
                with self.assertRaises(StateUnavailable):self.v.refresh()
                self.assertFalse(self.v.health()['ready']);self.advance(30)
    def test_expiry_is_monotonic_even_if_wall_clock_stalls(self):
        signed=self.token();self.v.verify(signed,'n');self.mono+=60;self.body=TimeoutError()
        with self.assertRaises(StateUnavailable):self.v.verify(signed,'n')
    def test_clock_regression_refuses_instead_of_extending_cache(self):
        self.v.verify(self.token(),'n');self.wall-=1
        with self.assertRaises(StateUnavailable):self.v.verify(self.token(),'n')
    def test_fetch_over_deadline_refused(self):
        def slow():self.advance(16);return self.body
        self.v.fetch=slow
        with self.assertRaises(StateUnavailable):self.v.verify(self.token(),'n')
    def test_monotonic_expiry_during_verification_is_refused(self):
        signed=self.token();self.v.verify(signed,'n');snapshot=self.v._snapshot;real=snapshot.verify
        def slow(*args):
            result=real(*args);self.mono+=60;return result
        with patch.object(snapshot,'verify',side_effect=slow),self.assertRaises(StateUnavailable):self.v.verify(signed,'n')
    def test_bounded_configuration(self):
        for kwargs in [dict(ttl=0),dict(ttl=3601),dict(ttl=True),dict(retry_seconds=0),dict(audience='')]:
            args=dict(audience='com.woundai.lite');args.update(kwargs)
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):RefreshingAppleIdentityVerifier(**args)
    def test_health_does_not_expose_user_or_token(self):
        self.v.verify(self.token(),'n');s=json.dumps(self.v.health())
        self.assertNotIn('synthetic-user',s);self.assertNotIn(self.token(),s)


class PublicTransportTests(unittest.TestCase):
    def response(self,body=b'{"keys":[]}',status=200,url=APPLE_KEYS_URL,mime='application/json',length=None,encoding=None):
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def geturl(self):return url
            def read1(self,n):
                part=self.body[:n];self.body=self.body[n:];return part
        r=Response();r.status=status;r.body=body;r.headers=Message();r.headers['Content-Type']=mime
        if length is not None:r.headers['Content-Length']=length
        if encoding:r.headers['Content-Encoding']=encoding
        return r
    def test_fixed_url_get_no_credentials(self):
        with patch('lite_apple_identity_provider.urllib.request.build_opener') as opener:
            opener.return_value.open.return_value=self.response()
            self.assertEqual(fetch_apple_keys(),b'{"keys":[]}')
            req=opener.return_value.open.call_args.args[0]
            self.assertEqual(req.full_url,APPLE_KEYS_URL);self.assertEqual(req.get_method(),'GET')
            self.assertIsNone(req.data);self.assertNotIn('Authorization',req.headers)
    def test_redirect_handler_rejects(self):
        with self.assertRaises(StateUnavailable):_NoRedirect().redirect_request(None,None,302,'',{},'https://example.invalid')
    def test_invalid_http_properties_refused(self):
        for args in [dict(status=503),dict(url='https://example.invalid'),dict(mime='text/html'),dict(length='65537'),dict(length='x'),dict(length='3'),dict(body=b'x'*65537),dict(encoding='gzip')]:
            with self.subTest(args={k:str(v)[:40] for k,v in args.items()}),patch('lite_apple_identity_provider.urllib.request.build_opener') as o:
                o.return_value.open.return_value=self.response(**args)
                with self.assertRaises(StateUnavailable):fetch_apple_keys()

if __name__=='__main__':unittest.main()
