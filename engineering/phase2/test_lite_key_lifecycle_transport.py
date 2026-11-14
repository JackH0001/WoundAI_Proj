"""Real requests/urllib3 wire attempts; local HTTP only, no cloud credentials."""
import copy
import io
import json
import socket
import threading
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from unittest.mock import patch
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from google.auth.credentials import Credentials
from google.auth.transport.requests import AuthorizedSession
from lite_attest_state import StateUnavailable
from lite_key_lifecycle_dispatch import PinnedKeyLifecycle
from lite_key_lifecycle_transport import SingleAttemptKMSTransport
from test_lite_key_lifecycle_dispatch import DispatchFixture
from test_lite_rotating_grant_keys import V1,V2
from test_lite_subscription_grace import rows
import test_lite_native_purchase as native

TOKEN='synthetic-local-bearer-not-a-real-credential'


class CredentialsDouble(Credentials):
    def __init__(self):super().__init__();self.token=TOKEN;self.refreshes=0
    def refresh(self,request):self.refreshes+=1;self.token=TOKEN


class LocalKMS:
    def __init__(self,kms=None):
        self.kms=kms;self.state='ENABLED';self.calls=[];self.fault=None
        outer=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):self.handle_one()
            def do_PATCH(self):self.handle_one()
            def do_POST(self):self.handle_one()
            def handle_one(self):
                raw=self.rfile.read(int(self.headers.get('Content-Length','0')))
                body=None if not raw else json.loads(raw)
                action='get' if self.command=='GET' else ('disable' if self.command=='PATCH' else self.path.rsplit(':',1)[1])
                outer.calls.append(dict(method=self.command,path=self.path,action=action,
                    authorization_valid=self.headers.get('Authorization')=='Bearer '+TOKEN,body=body))
                fault=outer.fault
                if action!='get':outer.fault=None
                if action!='get' and fault not in ('401','503','redirect'):
                    outer.state={'disable':'DISABLED','destroy':'DESTROY_SCHEDULED','restore':'DISABLED'}[action]
                    if outer.kms is not None:outer.kms.states[V1]=outer.state
                if action!='get' and fault=='drop':
                    self.connection.shutdown(socket.SHUT_RDWR);self.connection.close();return
                status=200
                if action!='get' and fault in ('401','503'):status=int(fault)
                if action!='get' and fault=='redirect':status=307
                data=dict(name=V1,state=outer.state,algorithm='GOOGLE_SYMMETRIC_ENCRYPTION',protectionLevel='SOFTWARE')
                if outer.state=='DESTROY_SCHEDULED':data['destroyTime']='2026-12-12T00:00:00Z'
                data=json.dumps(data).encode() if status==200 else b'synthetic private provider error'
                self.send_response(status)
                if status==307:self.send_header('Location','https://untrusted.invalid/never-send')
                self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def close(self):self.server.shutdown();self.server.server_close();self.thread.join(timeout=5)
    @contextmanager
    def route(self):
        original=HTTPAdapter.send;connect=socket.socket.connect;port=self.server.server_port
        def local(adapter,request,**kw):
            u=urlsplit(request.url)
            if u.scheme!='https' or u.hostname!='cloudkms.googleapis.com':raise AssertionError('unexpected provider destination')
            if kw['verify'] is not True or kw['proxies']:raise AssertionError('TLS or proxy policy changed')
            r=copy.copy(request);r.url='http://127.0.0.1:'+str(port)+u.path+('?' +u.query if u.query else '')
            return original(adapter,r,**kw)
        def only_local(sock,address):
            if address!=('127.0.0.1',port):raise AssertionError('external network forbidden')
            return connect(sock,address)
        with patch.object(HTTPAdapter,'send',local),patch.object(socket.socket,'connect',only_local):yield


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.now=1000
        self.p=PinnedKeyLifecycle.with_access_token(version=V1,protected_version=V2,access_token=TOKEN,expires_at=1500,clock=lambda:self.now)
        self.addCleanup(self.p.close);self.t=self.p.session;self.sent=[]
        def send(adapter,request,**kw):
            self.sent.append((request,kw));r=requests.Response();r.status_code=200;r.request=request;r.url=request.url
            r.raw=io.BytesIO(json.dumps(dict(name=V1,state='ENABLED',algorithm='GOOGLE_SYMMETRIC_ENCRYPTION',protectionLevel='SOFTWARE')).encode());return r
        p=patch.object(HTTPAdapter,'send',send);p.start();self.addCleanup(p.stop)
    def test_exact_request_tls_no_proxy_and_no_auth_handler(self):
        self.assertEqual(self.p.describe()['state'],'ENABLED');r,kw=self.sent[0]
        self.assertEqual(r.url,'https://cloudkms.googleapis.com/v1/'+V1)
        self.assertEqual(r.headers['Authorization'],'Bearer '+TOKEN);self.assertTrue(kw['verify']);self.assertFalse(kw['proxies'])
        self.assertEqual(kw['timeout'],(5,10));self.assertIsNone(self.t._session.auth)
    def test_environment_proxy_ca_and_netrc_ignored(self):
        with patch.dict('os.environ',{'HTTPS_PROXY':'http://127.0.0.1:1','REQUESTS_CA_BUNDLE':'missing-test-ca','NETRC':'missing-test-netrc'}):self.p.describe()
        self.assertTrue(self.sent[0][1]['verify']);self.assertFalse(self.sent[0][1]['proxies'])
    def test_no_credential_discovery(self):
        with patch('google.auth.default',side_effect=AssertionError('ADC forbidden')):self.p.describe()
        self.assertEqual(len(self.sent),1)
    def test_expired_or_near_expiry_bearer_stops_before_send(self):
        for now in (1480,1500,1600):
            self.now=now
            with self.assertRaises(StateUnavailable):self.p.describe()
        self.assertEqual(self.sent,[])
    def test_unbounded_expiry_and_bad_clock_stop_before_send(self):
        for now in (-1,float('nan'),True,0):
            self.now=now
            if now==0 and type(now) is int:self.t._expires=4000
            with self.assertRaises(StateUnavailable):self.p.describe()
        self.assertEqual(self.sent,[])
    def test_invalid_token_not_echoed(self):
        for token in ('x','synthetic\r\nSecret: value',None):
            with self.assertRaises(ValueError) as caught:
                SingleAttemptKMSTransport(version=V1,access_token=token,expires_at=1500)
            self.assertEqual(str(caught.exception),'explicit version, bearer and expiry required')
    def test_mutated_retry_policy_rejected(self):
        self.t._session.adapters['https://'].max_retries=Retry(total=2)
        with self.assertRaises(StateUnavailable):self.p.describe()
        self.assertEqual(self.sent,[])
    def test_mutated_auth_hooks_proxy_tls_or_environment_rejected(self):
        for name,value in [('auth',object()),('hooks',{'response':[lambda r:r]}),('trust_env',True),('proxies',{'https':'other'}),('verify',False),('cert','fake')]:
            s=self.t._session;old=getattr(s,name);setattr(s,name,value)
            with self.assertRaises(StateUnavailable):self.p.describe()
            setattr(s,name,old)
        self.assertEqual(self.sent,[])
    def test_only_reviewed_host_version_actions_are_allowed(self):
        for method,url,body,params in [('GET','https://cloudkms.googleapis.com/v1/'+V2,None,{}),('GET','https://else.invalid',None,{}),
            ('PATCH',self.t._base,{'name':V1,'state':'ENABLED'},{'updateMask':'state'}),('POST',self.t._base+':import',{},{}),
            ('GET',self.t._base,None,{'other':'value'})]:
            with self.assertRaises(StateUnavailable):self.t.request(method,url,json=body,params=params,timeout=(5,10),stream=True,allow_redirects=False)
        self.assertEqual(self.sent,[])
    def test_redirects_or_unbounded_read_not_accepted(self):
        for change in ({'allow_redirects':True},{'stream':False},{'timeout':None}):
            kw=dict(params={},json=None,timeout=(5,10),allow_redirects=False,stream=True);kw.update(change)
            with self.assertRaises(StateUnavailable):self.t.request('GET',self.t._base,**kw)
        self.assertEqual(self.sent,[])
    def test_closed_transport_cannot_send_and_drops_own_token_reference(self):
        self.p.close();self.assertIsNone(self.t._token)
        with self.assertRaises(StateUnavailable):self.p.describe()
        self.assertEqual(self.sent,[])
    def test_wire_exception_does_not_echo_credentials(self):
        with patch.object(HTTPAdapter,'send',side_effect=requests.ConnectionError(TOKEN)),self.assertRaises(StateUnavailable) as caught:self.p.describe()
        self.assertNotIn(TOKEN,str(caught.exception))
    def test_factory_invalid_version_pair_closes_owned_transport(self):
        with patch.object(SingleAttemptKMSTransport,'close',autospec=True) as closed,self.assertRaises(ValueError):
            PinnedKeyLifecycle.with_access_token(version=V1,protected_version=V1,access_token=TOKEN,expires_at=1500)
        closed.assert_called_once()


class WireTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):native.legacy.LinksTests.setUpClass()
    def setUp(self):
        self.x=DispatchFixture(self);self.http=LocalKMS(self.x.f.kms);self.addCleanup(self.http.close)
        self.route=self.http.route();self.route.__enter__();self.addCleanup(self.route.__exit__,None,None,None)
        self.x.provider=PinnedKeyLifecycle.with_access_token(version=V1,protected_version=V2,
            access_token=TOKEN,expires_at=1500,clock=lambda:self.x.now)
        self.addCleanup(self.x.provider.close);self.x.plan('disable')
    def mutations(self):return [x for x in self.http.calls if x['method']!='GET']
    def test_google_default_repeats_401_but_explicit_transport_does_not(self):
        credentials=CredentialsDouble();session=AuthorizedSession(credentials);session.trust_env=False;self.addCleanup(session.close)
        old=PinnedKeyLifecycle(session=session,version=V1,protected_version=V2)
        self.http.fault='401';self.assertEqual(old.dispatch('disable')['state'],'DISABLED')
        self.assertEqual(len(self.mutations()),2);self.assertEqual(credentials.refreshes,1)
        self.http.state='ENABLED';self.x.f.kms.states[V1]='ENABLED';self.http.fault='401';self.http.calls=[]
        with self.assertRaises(StateUnavailable):self.x.provider.dispatch('disable')
        self.assertEqual(len(self.mutations()),1);self.assertEqual(self.http.state,'ENABLED')
    def test_actual_socket_lost_reply_replay_restart_and_readonly_reconcile(self):
        f=self.x.f;before=rows(f.g.store),rows(f.key_store),rows(f.key_audit),rows(f.f.h.f.ledger.store)
        self.http.fault='drop';c=self.x.command()
        with self.assertRaises(StateUnavailable):self.x.e.execute(c)
        self.assertEqual(self.http.state,'DISABLED');self.assertEqual(self.x.e.inspect()['phase'],'uncertain')
        count=len(self.http.calls);self.x.e=self.x.restart()
        with self.assertRaises(StateUnavailable):self.x.e.execute(c)
        self.assertEqual(len(self.http.calls),count)
        r=self.x.e.reconcile(self.x.drain(c));self.assertEqual(r['provider_state'],'DISABLED')
        self.assertEqual(self.x.e.execute(c),r);self.assertEqual(len(self.mutations()),1)
        self.assertEqual(before,(rows(f.g.store),rows(f.key_store),rows(f.key_audit),rows(f.f.h.f.ledger.store)))
        self.assertEqual(f.cipher.open(*self.x.b_snapshot)['refresh_token'],'private-refresh-token')
    def test_401_after_claim_requires_drain_no_refresh_or_automatic_retry(self):
        self.http.fault='401';c=self.x.command()
        with self.assertRaises(StateUnavailable):self.x.e.execute(c)
        self.assertEqual(len(self.mutations()),1);self.assertEqual(self.x.e.inspect()['phase'],'uncertain')
        self.x.e.reconcile(self.x.drain(c));self.assertEqual(len(self.mutations()),1)
    def test_503_after_claim_is_not_retried(self):
        self.http.fault='503';c=self.x.command()
        with self.assertRaises(StateUnavailable):self.x.e.execute(c)
        with self.assertRaises(StateUnavailable):self.x.e.execute(c)
        self.assertEqual(len(self.mutations()),1)
    def test_redirect_not_followed_or_sent_credentials(self):
        self.http.fault='redirect';c=self.x.command()
        with self.assertRaises(StateUnavailable):self.x.e.execute(c)
        self.assertEqual(len(self.mutations()),1)
        self.assertTrue(all(x['authorization_valid'] for x in self.http.calls))
    def test_schedule_and_restore_are_one_request_each(self):
        self.x.perform('disable');_,scheduled=self.x.perform('schedule');_,restored=self.x.perform('restore')
        self.assertEqual([r['action'] for r in self.mutations()],['disable','destroy','restore'])
        self.assertEqual(scheduled['provider_state'],'DESTROY_SCHEDULED');self.assertEqual(restored['provider_state'],'DISABLED')
    def test_expired_token_after_claim_has_no_wire_mutation(self):
        c=self.x.command();save=self.x.e._save
        def expire(*args,**kw):
            row=save(*args,**kw)
            if kw.get('claim'):self.x.provider.session._expires=self.x.now
            return row
        with patch.object(self.x.e,'_save',side_effect=expire),self.assertRaises(StateUnavailable):self.x.e.execute(c)
        self.assertEqual(self.mutations(),[]);self.assertEqual(self.x.e.inspect()['phase'],'uncertain')
    def test_unavailable_endpoint_is_a_single_attempt(self):
        c=self.x.command();calls=[];old=self.x.provider.session._session.adapters['https://'].send
        def lost(req,**kw):
            if req.method!='GET':calls.append(req.method);raise requests.ConnectionError('synthetic connection failure')
            return old(req,**kw)
        with patch.object(self.x.provider.session._session.adapters['https://'],'send',side_effect=lost),self.assertRaises(StateUnavailable):self.x.e.execute(c)
        self.assertEqual(calls,['PATCH']);self.assertEqual(self.mutations(),[])


if __name__=='__main__':unittest.main()
