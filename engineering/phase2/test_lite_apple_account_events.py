"""Signed synthetic Apple events and session races; retain all original state."""
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import jwt
from flask import Flask
from lite_account_identity import AccountIdentityBridge,IdentityRejected
from lite_apple_account_events import AppleAccountEvents,EVENT_PATH,blueprint
from lite_attest_state import SQLiteStateStore,StateUnavailable
from lite_subscription_executor import key
from lite_usage_ledger import UsageConflict
from test_lite_account_identity import IdentityFixture
from test_lite_subscription_grace import rows
import test_lite_native_purchase as native


class EventTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):native.legacy.LinksTests.setUpClass()
    def setUp(self):
        self.f=IdentityFixture(self);self.b=self.f.b;self.legacy=self.b
        self.events=AppleAccountEvents(verifier=self.f.verifier,
            store=SQLiteStateStore(self.f.h.f.root/'apple-events.sqlite'),
            audit_store=SQLiteStateStore(self.f.h.f.root/'apple-events-audit.sqlite'),
            namespace=self.b.namespace,identity_key=self.b.secret)
        self.f.b=self.rebuild();self.b=self.f.b
    def rebuild(self,events=True):
        return AccountIdentityBridge(runtime=self.b.runtime,verifier=self.b.verifier,store=self.b.store,
            audit_store=self.b.audit,namespace=self.b.namespace,identity_key=self.b.secret,
            events=self.events if events else None)
    def token(self,kind='consent-revoked',sub='apple-human-1',jti='event-1',at=None,changes=None,string_event=False):
        e=dict(type=kind,sub=sub,event_time=self.f.now() if at is None else at)
        c=dict(iss=self.f.verifier.issuer,aud=self.f.verifier.audience,iat=self.f.now(),jti=jti,
            events=json.dumps(e) if string_event else e)
        c.update(changes or {})
        return jwt.encode(c,self.f.private,algorithm='RS256',headers={'kid':'synthetic-rsa'})
    def login(self,device='one',sub='apple-human-1'):
        c=self.f.challenge(device);return c,self.f.confirm(c,sub=sub)
    def advance(self,n=2):self.f.h.a.now+=n
    def test_revocation_blocks_both_devices_preserves_quota_and_records(self):
        self.login('one');self.login('two');self.f.use(1,'one')
        before=rows(self.f.h.f.ledger.store),rows(self.b.registry.store),rows(self.b.audit)
        self.events.accept(self.token())
        for d in ('one','two'):
            with self.assertRaises(IdentityRejected):self.b.require_session(d)
        self.assertEqual(before,(rows(self.f.h.f.ledger.store),rows(self.b.registry.store),rows(self.b.audit)))
    def test_explicit_new_login_after_revocation_reuses_account_and_quota(self):
        _,r=self.login('one');self.f.use(1,'one');before=rows(self.f.h.f.ledger.store)
        self.events.accept(self.token());self.advance();_,next=self.login('one')
        self.assertEqual(r['app_account_token'],next['app_account_token']);self.assertEqual(before,rows(self.f.h.f.ledger.store))
    def test_deleted_account_never_reenabled_by_new_id_token(self):
        self.login();self.events.accept(self.token('account-deleted'));self.advance()
        with self.assertRaises(IdentityRejected):self.login()
    def test_late_old_confirm_cannot_activate(self):
        c=self.f.challenge('one');signed=self.f.token(c);self.events.accept(self.token())
        with self.assertRaises(IdentityRejected):self.f.confirm(c,signed)
        self.assertIsNone(self.b.registry.store.load(self.b.registry.key('one')))
    def test_event_racing_with_provision_prevents_authorized_result(self):
        c=self.f.challenge('one');signed=self.f.token(c);real=self.b.registry.provision
        def concurrent(*args):
            result=real(*args);self.events.accept(self.token());return result
        with patch.object(self.b.registry,'provision',side_effect=concurrent),self.assertRaises(IdentityRejected):self.f.confirm(c,signed)
        with self.assertRaises(IdentityRejected):self.b.require_session('one')
    def test_old_event_does_not_cancel_newer_explicit_consent(self):
        at=self.f.now()-5;self.login();self.events.accept(self.token(at=at));self.b.require_session('one')
    def test_duplicate_event_is_idempotent(self):
        signed=self.token();first=self.events.accept(signed);before=rows(self.events.store),rows(self.events.audit)
        self.assertEqual(first,self.events.accept(signed));self.assertEqual(before,(rows(self.events.store),rows(self.events.audit)))
    def test_duplicate_event_concurrent(self):
        signed=self.token()
        with ThreadPoolExecutor(max_workers=4) as pool:r=list(pool.map(lambda _:self.events.accept(signed),range(8)))
        self.assertTrue(all(x==r[0] for x in r))
    def test_same_jti_different_payload_cannot_change_fence(self):
        self.events.accept(self.token());before=rows(self.events.store)
        with self.assertRaises(UsageConflict):self.events.accept(self.token('account-deleted'))
        self.assertEqual(before,rows(self.events.store))
    def test_out_of_order_revocations_cannot_lower_fence(self):
        self.events.accept(self.token());self.events.accept(self.token(jti='old',at=self.f.now()-100))
        with self.assertRaises(IdentityRejected):self.events.require_identity(self.events.identity('apple-human-1'),self.f.now())
    def test_email_changes_do_not_reactivate_or_stop_session(self):
        self.login();self.events.accept(self.token('email-disabled'));self.b.require_session('one')
        self.events.accept(self.token(jti='revoke'))
        self.events.accept(self.token('email-enabled',jti='email'))
        with self.assertRaises(IdentityRejected):self.b.require_session('one')
    def test_corrupt_other_account_does_not_block_this_account(self):
        identity=self.events.identity('other')
        self.events.store.compare_exchange(key('apple-event-fence',identity),0,{'bad':True})
        self.login();self.b.require_session('one')
        with self.assertRaises(StateUnavailable):self.events.require_identity(identity,self.f.now())
    def test_audit_failure_does_not_acknowledge(self):
        self.login()
        with patch.object(self.events.audit,'compare_exchange',side_effect=StateUnavailable('disk')),self.assertRaises(StateUnavailable):self.events.accept(self.token())
        with self.assertRaises(IdentityRejected):self.b.require_session('one')
        self.assertEqual(self.events.accept(self.token())['state'],'applied')
    def test_fence_write_failure_can_retry_same_event(self):
        signed=self.token()
        with patch.object(self.events.store,'compare_exchange',side_effect=StateUnavailable('disk')),self.assertRaises(StateUnavailable):self.events.accept(signed)
        self.assertEqual(self.events.accept(signed)['state'],'applied')
    def test_lost_receipt_after_fence_stays_revoked_then_retries(self):
        self.login();signed=self.token();real=self.events.audit.compare_exchange
        def lost(k,*args):
            r=real(k,*args)
            if args[-1].get('state')=='applied':raise StateUnavailable('reply lost')
            return r
        with patch.object(self.events.audit,'compare_exchange',side_effect=lost),self.assertRaises(StateUnavailable):self.events.accept(signed)
        with self.assertRaises(IdentityRejected):self.b.require_session('one')
        self.assertEqual(self.events.accept(signed)['state'],'applied')
    def test_protection_cannot_be_silently_removed_on_restart(self):
        with self.assertRaises(StateUnavailable):self.rebuild(events=False)
    def test_existing_instance_without_event_authority_cannot_bypass_activation(self):
        self.login()
        with self.assertRaises(StateUnavailable):self.legacy.require_session('one')
    def test_same_event_id_concurrent_different_payload_only_one_can_bind(self):
        signed=[self.token(),self.token('account-deleted')]
        def apply(s):
            try:return self.events.accept(s)
            except (StateUnavailable,UsageConflict):return None
        with ThreadPoolExecutor(max_workers=2) as pool:result=list(pool.map(apply,signed))
        self.assertEqual(sum(r is not None for r in result),1)
        winner=next(r['type'] for r in result if r is not None)
        row=self.events._read(self.events.identity('apple-human-1'))[1]
        self.assertEqual(row['deleted'],winner=='account-deleted')
    def test_preexisting_session_requires_explicit_login_after_enabling_events(self):
        c,r=self.login();proof_key=self.b._authorization_key('one',c['operation']);real=self.b.audit.load
        with patch.object(self.b.audit,'load',side_effect=lambda k:None if k==proof_key else real(k)),self.assertRaises(IdentityRejected):self.b.require_session('one')
    def test_no_raw_subject_token_or_email_retained(self):
        signed=self.token();self.events.accept(signed)
        text=repr(rows(self.events.audit))+repr(rows(self.events.store))
        self.assertNotIn('apple-human-1',text);self.assertNotIn(signed,text)
    def test_string_events_supported(self):self.assertEqual(self.events.accept(self.token(string_event=True))['state'],'applied')
    def test_invalid_signature_audience_and_event_types(self):
        for signed in [self.token(changes=dict(aud='another')),self.token('unsupported'),self.token(changes=dict(iat=self.f.now()+31)),self.token(changes=dict(iat=self.f.now()-7*86400-1)),self.token(changes=dict(nbf=self.f.now()+10)),self.token()[:-6]+'abcdef']:
            with self.subTest(signed=signed[-6:]),self.assertRaises(IdentityRejected):self.events.accept(signed)
        self.assertEqual(rows(self.events.audit),[])
    def test_http_signature_route_is_explicit_and_no_store(self):
        closed=Flask('closed');self.assertEqual(closed.test_client().post(EVENT_PATH,json={}).status_code,404)
        app=Flask('synthetic-events')
        app.register_blueprint(blueprint(self.events));client=app.test_client()
        response=client.post(EVENT_PATH,json=dict(payload=self.token()))
        self.assertEqual(response.status_code,200);self.assertEqual(response.headers['Cache-Control'],'no-store')
        for path,data in [(EVENT_PATH,{}),(EVENT_PATH,dict(payload='unsigned')),(EVENT_PATH+'?extra=1',dict(payload=self.token()))]:
            self.assertEqual(client.post(path,json=data).status_code,400)
        self.assertEqual(client.post(EVENT_PATH,data=b'x'*20001,content_type='application/json').status_code,400)


class EventGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):native.legacy.LinksTests.setUpClass()
    def setUp(self):
        from test_lite_account_identity import IdentityHTTPTests
        self.h=IdentityHTTPTests()
        def configured(host,runtime_fixture=None):
            f=IdentityFixture(host,runtime_fixture);b=f.b
            self.events=AppleAccountEvents(verifier=f.verifier,store=SQLiteStateStore(f.h.f.root/'event-http.sqlite'),
                audit_store=SQLiteStateStore(f.h.f.root/'event-http-audit.sqlite'),namespace=b.namespace,identity_key=b.secret)
            f.b=AccountIdentityBridge(runtime=b.runtime,verifier=b.verifier,store=b.store,audit_store=b.audit,
                namespace=b.namespace,identity_key=b.secret,events=self.events)
            return f
        with patch('test_lite_account_identity.IdentityFixture',side_effect=configured):self.h.setUp()
        self.addCleanup(self.h.doCleanups)
    def signed(self):
        f=self.h.f
        return jwt.encode(dict(iss=f.verifier.issuer,aud=f.verifier.audience,iat=f.now(),jti='http-event',
            events=dict(type='consent-revoked',sub='apple-human-1',event_time=f.now())),f.private,
            algorithm='RS256',headers={'kid':'synthetic-rsa'})
    def test_verified_event_needs_apple_signature_not_device_attestation(self):
        self.h.login();client=self.h.h.client
        self.assertEqual(client.post(EVENT_PATH,json={'payload':'unsigned'}).status_code,400)
        response=client.post(EVENT_PATH,json={'payload':self.signed()})
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(self.h.post('/api/v1/lite/purchase/context',dict(operation='2'*32)).status_code,401)
    def test_revocation_does_not_block_independent_research_withdrawal(self):
        self.h.login();client=self.h.h.client
        self.assertEqual(client.post(EVENT_PATH,json={'payload':self.signed()}).status_code,200)
        path='/api/v1/lite/data/'+self.h.h.owner
        response=client.delete(path,headers=self.h.h.headers(path,b'','',method='DELETE',purpose='withdraw'))
        self.assertEqual(response.status_code,200,response.json)

if __name__=='__main__':unittest.main()
