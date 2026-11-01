import base64, io, json, os, unittest
from unittest.mock import patch
import google_crc32c
from google.auth.credentials import AnonymousCredentials
from google.auth.transport.requests import AuthorizedSession
from google.cloud.storage import Client
from requests import Response
from requests.adapters import HTTPAdapter
from urllib3.response import HTTPResponse
from lite_attest_state import GCSStateStore, StateUnavailable
from lite_provider_transport import BudgetedSession, apple_revoker
from lite_provider_budget import BudgetUnavailable
from test_lite_provider_budget import Fixture


class Adapter(HTTPAdapter):
    def __init__(self,responses):super().__init__(max_retries=0);self.responses=list(responses);self.calls=[]
    def send(self,request,**kw):
        self.calls.append((request,kw))
        value=self.responses.pop(0)
        if isinstance(value,Exception):raise value
        code,body,headers=value
        response=Response();response.status_code=code;response.url=request.url
        response.headers.update(headers);response.request=request
        response.raw=HTTPResponse(body=io.BytesIO(body),headers=headers,preload_content=False,decode_content=False)
        return response


def reply(value,code=200,headers=None):
    body=value if isinstance(value,bytes) else json.dumps(value).encode()
    return code,body,dict({'Content-Length':str(len(body)),'Content-Type':'application/json'},**(headers or {}))


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ,DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA='true'))
        self.f=Fixture(self);self.f.p.update(requests=20,send_bytes=100000,receive_bytes=1000000)
        self.lease=self.f.build().allocate()
    def transport(self,responses,**kw):
        session=AuthorizedSession(AnonymousCredentials(),max_refresh_attempts=0);session.trust_env=False
        self.adapter=Adapter(responses);session.mount('https://',self.adapter);self.addCleanup(session.close)
        return BudgetedSession(session,self.lease,provider='gcs',authorized=True,**kw)
    def test_json_response_is_bounded_before_sdk_access(self):
        t=self.transport([reply({'ok':True})]);r=t.request('GET','https://storage.googleapis.com/a',timeout=10)
        self.assertEqual(r.json(),{'ok':True});self.assertEqual(self.lease.used['requests'],1)
        self.assertEqual(self.adapter.calls[0][0].headers['Accept-Encoding'],'identity')
        self.assertLessEqual(self.adapter.calls[0][1]['timeout'][0],5)
    def test_request_body_is_bounded_and_counted(self):
        t=self.transport([reply({})]);t.request('POST','https://storage.googleapis.com/a',json={'a':1})
        self.assertEqual(self.adapter.calls[0][0].body,b'{"a":1}');self.assertEqual(self.lease.used['send_bytes'],7)
    def test_file_and_generator_bodies_refused(self):
        t=self.transport([])
        for body in (io.BytesIO(b'x'),iter([b'x'])):
            with self.assertRaises(BudgetUnavailable):t.request('POST','https://storage.googleapis.com/a',data=body)
        self.assertEqual(len(self.adapter.calls),0)
    def test_foreign_or_plaintext_endpoint_refused(self):
        t=self.transport([])
        for url in ('http://storage.googleapis.com/a','https://other.invalid/a','https://user@storage.googleapis.com/a',
                    'https://storage.googleapis.com:444/a','https://storage.googleapis.com/a#fragment'):
            with self.assertRaises(BudgetUnavailable):t.request('GET',url)
        self.assertEqual(len(self.adapter.calls),0)
    def test_redirect_never_replayed(self):
        t=self.transport([reply(b'',302,{'Location':'https://other.invalid'})])
        with self.assertRaises(BudgetUnavailable):t.request('GET','https://storage.googleapis.com/a')
        self.assertEqual(len(self.adapter.calls),1)
    def test_auth_401_does_not_replay_request(self):
        t=self.transport([reply({'error':'unauthorized'},401)])
        self.assertEqual(t.request('GET','https://storage.googleapis.com/a').status_code,401)
        self.assertEqual(len(self.adapter.calls),1)
    def test_network_failure_consumes_reserved_capacity(self):
        t=self.transport([TimeoutError('never expose credential text')],response_limit=100)
        with self.assertRaisesRegex(BudgetUnavailable,'unconfirmed') as error:t.request('GET','https://storage.googleapis.com/a')
        self.assertNotIn('credential',str(error.exception));self.assertEqual(self.lease.used['receive_bytes'],100)
    def test_oversized_declared_reply_is_refused(self):
        t=self.transport([reply(b'12345')],response_limit=4)
        with self.assertRaises(BudgetUnavailable):t.request('GET','https://storage.googleapis.com/a')
        self.assertEqual(self.lease.snapshot()['completed_responses'],0)
    def test_unknown_length_is_stopped_at_capacity(self):
        t=self.transport([(200,b'123456',{})],response_limit=5)
        with self.assertRaises(BudgetUnavailable):t.request('GET','https://storage.googleapis.com/a')
        self.assertEqual(self.lease.snapshot()['response_bytes_observed'],5)
    def test_truncated_reply_not_exposed_as_complete(self):
        t=self.transport([(200,b'abc',{'Content-Length':'10'})])
        with self.assertRaises(BudgetUnavailable):t.request('GET','https://storage.googleapis.com/a')
        self.assertEqual(self.lease.snapshot()['completed_responses'],0)
    def test_compressed_reply_refused(self):
        t=self.transport([reply(b'x',headers={'Content-Encoding':'gzip'})])
        with self.assertRaises(BudgetUnavailable):t.request('GET','https://storage.googleapis.com/a')
    def test_deadline_no_extra_network_call(self):
        t=self.transport([]);self.f.now+=30
        with self.assertRaises(BudgetUnavailable):t.request('GET','https://storage.googleapis.com/a')
        self.assertEqual(len(self.adapter.calls),0)
    def test_real_gcs_state_metadata_and_generation_download(self):
        data=b'{"v":1}';crc=base64.b64encode(google_crc32c.value(data).to_bytes(4,'big')).decode()
        t=self.transport([reply(dict(generation='7',size=str(len(data)),crc32c=crc)),reply(data,headers={'x-goog-hash':'crc32c='+crc})])
        client=Client(project='synthetic-project',credentials=AnonymousCredentials(),_http=t)
        store=GCSStateStore(client.bucket('synthetic-bucket'),prefix='lite-native/test/state')
        self.assertEqual(store.load(b'x'*32),(7,{'v':1}));self.assertEqual(len(self.adapter.calls),2)
        self.assertIn('ifGenerationMatch=7',self.adapter.calls[1][0].url)
    def test_real_gcs_conditional_upload_preserved(self):
        t=self.transport([reply(dict(generation='8'))])
        store=GCSStateStore(Client(project='synthetic-project',credentials=AnonymousCredentials(),_http=t).bucket('synthetic-bucket'))
        self.assertTrue(store.compare_exchange(b'x'*32,7,{'v':1}));self.assertEqual(len(self.adapter.calls),1)
        self.assertIn('ifGenerationMatch=7',self.adapter.calls[0][0].url)
        self.assertGreater(self.lease.used['send_bytes'],0)
    def test_explicit_429_retries_are_all_counted_and_bounded(self):
        self.f=Fixture(self);self.f.p.update(requests=2,send_bytes=100000,receive_bytes=1000000)
        self.lease=self.f.build().allocate()
        t=self.transport([reply({'error':{'message':'throttle'}},429)]*4)
        store=GCSStateStore(Client(project='synthetic-project',credentials=AnonymousCredentials(),_http=t).bucket('synthetic-bucket'))
        with patch('lite_attest_state.time.sleep'):
            with self.assertRaises(StateUnavailable):store.compare_exchange(b'x'*32,7,{'v':1})
        self.assertEqual(len(self.adapter.calls),2);self.assertEqual(self.lease.used['requests'],2)
    def test_real_sdk_single_shot_download_uses_bounded_memory(self):
        data=b'abc';crc=base64.b64encode(google_crc32c.value(data).to_bytes(4,'big')).decode()
        t=self.transport([reply(data,headers={'x-goog-hash':'crc32c='+crc})])
        b=Client(project='synthetic-project',credentials=AnonymousCredentials(),_http=t).bucket('synthetic-bucket').blob('x',generation=1)
        self.assertEqual(b.download_as_bytes(single_shot_download=True,checksum='crc32c',retry=None),data)
        self.assertEqual(len(self.adapter.calls),1)
    def test_apple_revoke_only_and_bounded_form(self):
        adapter=Adapter([reply(b'')]);import requests
        session=requests.Session();session.trust_env=False;session.mount('https://',adapter);self.addCleanup(session.close)
        with patch('lite_provider_transport.PlainProviderSession',return_value=session):post=apple_revoker(self.lease)
        with self.assertRaises(StateUnavailable):post('https://appleid.apple.com/auth/token',{})
        status,body=post('https://appleid.apple.com/auth/revoke',{'token':'synthetic','client_secret':'synthetic'})
        self.assertEqual((status,body),(200,b''));self.assertEqual(len(adapter.calls),1)
        self.assertEqual(self.lease.snapshot()['providers']['apple'],1)
    def test_transport_not_exposed_after_response_limit_failure(self):
        t=self.transport([(200,b'abcde',{})],response_limit=4)
        with self.assertRaises(BudgetUnavailable):t.request('GET','https://storage.googleapis.com/a')
        with self.assertRaises(BudgetUnavailable):t.request('GET','https://storage.googleapis.com/a')
        self.assertEqual(len(self.adapter.calls),1)


if __name__=='__main__':unittest.main()
