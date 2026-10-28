import io
import json
import unittest
import urllib.error
import urllib.parse
from email.message import Message
from unittest.mock import patch
import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from lite_apple_oauth import (AppleClientSecret, AppleOAuthProvider, GrantRejected,
    GrantUncertain, post_apple_form, TOKEN_URL, REVOKE_URL, _NoRedirect)


class OAuthTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.payload = dict(access_token='synthetic-access', refresh_token='synthetic-refresh',
            id_token='synthetic-id', expires_in=3600, token_type='Bearer')
        self.response = (200, json.dumps(self.payload).encode())
        def post(url, fields):
            self.calls.append((url, fields))
            if isinstance(self.response, Exception): raise self.response
            return self.response
        self.p = AppleOAuthProvider(client_id='com.woundai.lite', client_secret=lambda:'synthetic-client', post=post)

    def test_exchange_uses_native_fixed_fields_and_no_redirect_uri(self):
        self.assertEqual(self.p.exchange('single-use'), self.payload)
        url, fields = self.calls[0]
        self.assertEqual(url, TOKEN_URL)
        self.assertEqual(fields, dict(client_id='com.woundai.lite', client_secret='synthetic-client',
            grant_type='authorization_code', code='single-use'))

    def test_revoke_accepts_empty_200_and_uses_refresh_hint(self):
        self.response = (200, b'')
        self.assertTrue(self.p.revoke('synthetic-refresh'))
        self.assertEqual(self.calls[0][0], REVOKE_URL)
        self.assertEqual(self.calls[0][1]['token_type_hint'], 'refresh_token')

    def test_unknown_reply_is_not_retried_or_exposed(self):
        self.response = OSError('sensitive-response-body')
        with self.assertRaises(GrantUncertain) as cm: self.p.exchange('single-use')
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn('sensitive', str(cm.exception)); self.assertTrue(cm.exception.__suppress_context__)

    def test_invalid_grant_has_redacted_rejection(self):
        self.response = (400, b'{"error":"invalid_grant","error_description":"sensitive"}')
        with self.assertRaises(GrantRejected) as cm: self.p.exchange('single-use')
        self.assertNotIn('sensitive', str(cm.exception))

    def test_other_errors_are_uncertain_not_silent_free_or_retry(self):
        for response in [(400,b'{"error":"invalid_client"}'), (429,b''), (500,b'secret'), (True,b'')]:
            with self.subTest(response=response), self.assertRaises(GrantUncertain):
                self.response=response; self.p.exchange('single-use')

    def test_malformed_or_duplicate_token_reply_fails_closed(self):
        for body in [b'{}', b'{"id_token":"a","id_token":"b"}', b'null', b'x'*16385]:
            self.response=(200, body)
            with self.assertRaises(GrantUncertain): self.p.exchange('single-use')

    def test_token_types_sizes_expiry_are_bounded(self):
        for change in [dict(refresh_token=''),dict(access_token='x'*2049),dict(id_token='x'*6001),
                       dict(expires_in=True),dict(expires_in=0),dict(expires_in=86401),dict(token_type='other')]:
            self.response=(200,json.dumps(dict(self.payload,**change)).encode())
            with self.subTest(change=list(change)),self.assertRaises(GrantUncertain): self.p.exchange('single-use')

    def test_invalid_code_is_never_dispatched(self):
        for value in [None, '', 'x'*4097, 'a\nb', '中文字']:
            with self.assertRaises(GrantRejected): self.p.exchange(value)
        self.assertEqual(self.calls, [])

    def test_malformed_revoke_reply_remains_pending(self):
        with self.assertRaises(GrantUncertain): self.p.revoke('synthetic-refresh')

    def test_client_secret_is_signed_es256_for_exact_client_and_short_lifetime(self):
        key=ec.generate_private_key(ec.SECP256R1()); now=1900000000
        signer=AppleClientSecret(team='ABCDEFGHIJ',key_id='KLMNOPQRST',client_id='com.woundai.lite',
            private_key=key,clock=lambda:now)
        token=signer(); header=jwt.get_unverified_header(token)
        c=jwt.decode(token,key.public_key(),algorithms=['ES256'],audience='https://appleid.apple.com',
            options={'verify_exp':False,'verify_iat':False})
        self.assertEqual(header['kid'],'KLMNOPQRST');self.assertEqual(c['sub'],'com.woundai.lite')
        self.assertEqual(c['exp']-c['iat'],300);self.assertEqual(c['iss'],'ABCDEFGHIJ')

    def test_wrong_signing_curve_is_rejected(self):
        with self.assertRaises(ValueError): AppleClientSecret(team='ABCDEFGHIJ',key_id='KLMNOPQRST',
            client_id='com.woundai.lite',private_key=ec.generate_private_key(ec.SECP384R1()),clock=lambda:0)


class Response(io.BytesIO):
    def __init__(self, body=b'{}', status=200, url=TOKEN_URL, **headers):
        super().__init__(body); self.code=status; self.url=url; self.headers=Message()
        self.headers['Content-Type']='application/json'
        for k,v in headers.items(): self.headers[k.replace('_','-')]=v
    def geturl(self): return self.url


class TransportTests(unittest.TestCase):
    def send(self,response):
        with patch('lite_apple_oauth.urllib.request.build_opener') as build:
            build.return_value.open.return_value=response
            result=post_apple_form(TOKEN_URL,dict(code='sensitive+&=',client_id='com.woundai.lite'))
            req=build.return_value.open.call_args.args[0]
            self.assertEqual(req.method,'POST');self.assertEqual(req.full_url,TOKEN_URL)
            self.assertEqual(urllib.parse.parse_qs(req.data.decode())['code'],['sensitive+&='])
            self.assertEqual(build.return_value.open.call_count,1)
            return result

    def test_bounded_success_uses_form_encoding(self): self.assertEqual(self.send(Response()),(200,b'{}'))

    def test_redirect_handler_refuses_credentials_forwarding(self):
        with self.assertRaises(GrantUncertain): _NoRedirect().redirect_request(None,None,302,'',{},'https://example.invalid')

    def test_endpoint_override_rejected_before_network(self):
        with patch('lite_apple_oauth.urllib.request.build_opener') as build:
            with self.assertRaises(ValueError): post_apple_form('https://example.invalid',{})
            build.assert_not_called()

    def test_wrong_url_compression_incomplete_and_large_response_rejected(self):
        for response in [Response(url='https://example.invalid'),Response(Content_Encoding='gzip'),
                         Response(Content_Length='3'),Response(Content_Length='-1'),Response(b'x'*16385)]:
            with self.subTest(response=response),self.assertRaises(GrantUncertain): self.send(response)

    def test_http_400_body_is_bounded_and_returned_only_to_provider(self):
        headers=Message();headers['Content-Type']='application/json'
        error=urllib.error.HTTPError(TOKEN_URL,400,'do not log',headers,io.BytesIO(b'{"error":"invalid_grant"}'))
        with patch('lite_apple_oauth.urllib.request.build_opener') as build:
            build.return_value.open.side_effect=error
            self.assertEqual(post_apple_form(TOKEN_URL,{}),(400,b'{"error":"invalid_grant"}'))

    def test_deadline_and_transport_exceptions_are_redacted(self):
        with patch('lite_apple_oauth.time.monotonic',side_effect=[1,12]):
            with self.assertRaises(GrantUncertain): self.send(Response())


if __name__=='__main__': unittest.main()
