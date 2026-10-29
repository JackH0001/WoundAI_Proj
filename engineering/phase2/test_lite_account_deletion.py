"""Local synthetic account-deletion intake and worker recovery; no live cleanup."""
import json
from pathlib import Path
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from lite_account_deletion import AccountDeletion, DeletionBusy, PREFIX
from lite_account_identity import IdentityRejected
from lite_attest_state import SQLiteStateStore, StateUnavailable
from lite_apple_oauth import GrantUncertain, REVOKE_URL, TOKEN_URL
from lite_subscription_executor import key
from test_lite_account_lifecycle import Fixture
from test_lite_subscription_grace import rows
import test_lite_native_purchase as native


def coordinator(f):
    root=f.f.h.f.root
    return AccountDeletion(bridge=f.b,store=SQLiteStateStore(root/'deletion.sqlite'),
                           audit_store=SQLiteStateStore(root/'deletion-audit.sqlite'))


class DeletionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): native.legacy.LinksTests.setUpClass()
    def setUp(self):
        self.f=Fixture(self);self.d=coordinator(self.f)
        self.c=self.f.login();self.person=self.f.person
    def begin(self): return self.d.begin('device-one','d'*32)
    def status(self): return self.d.status('device-one','d'*32)
    def test_intake_does_not_revoke_or_claim_account_data_deleted(self):
        r=self.begin();self.assertEqual(r['apple_revocation'],'not_started')
        self.assertEqual(r['data_disposition'],'not_started');self.assertFalse(r['account_deleted'])
        self.assertFalse(r['subscription_cancelled']);self.assertTrue(r['local_records_preserved'])
        self.assertEqual(self.f.g.calls,[TOKEN_URL])
    def test_same_request_recovers_after_closure_and_session_expiry(self):
        r=self.begin();self.f.f.h.a.now+=1000
        self.assertEqual(self.begin(),r);self.assertEqual(self.status(),r)
    def test_other_device_and_operation_cannot_read_progress(self):
        self.f.login('device-two');self.begin()
        with self.assertRaises(IdentityRejected):self.d.status('device-two','d'*32)
        with self.assertRaises(IdentityRejected):self.d.status('device-one','e'*32)
    def test_first_request_requires_live_session(self):
        self.f.f.h.a.now+=901
        with self.assertRaises(IdentityRejected):self.begin()
        self.assertEqual(self.f.lifecycle._read(self.person)[1]['phase'],'active')
    def test_worker_revokes_all_devices_without_resetting_usage_research_or_registry(self):
        self.f.login('device-two');self.f.login()
        before=rows(self.f.b.registry.store),rows(self.f.f.h.f.ledger.store)
        self.begin();r=self.d.advance(self.person)
        self.assertEqual(r['grant_total'],3);self.assertEqual(r['grants_revoked'],3)
        self.assertEqual(r['apple_revocation'],'confirmed');self.assertFalse(r['account_deleted'])
        self.assertEqual(before,(rows(self.f.b.registry.store),rows(self.f.f.h.f.ledger.store)))
        self.assertEqual(self.f.g.calls.count(REVOKE_URL),3)
        self.assertEqual(self.d.advance(self.person),r)
        self.assertEqual(self.f.g.calls.count(REVOKE_URL),3)
    def test_pending_grant_does_not_block_other_grants(self):
        self.f.lifecycle.reserve_grant(self.person,'not-exchanged','e'*32)
        self.begin();r=self.d.advance(self.person)
        self.assertEqual(r['grants_examined'],2);self.assertEqual(r['grants_revoked'],1)
        self.assertEqual(r['apple_revocation'],'pending');self.assertEqual(r['data_disposition'],'not_started')
        self.assertEqual(self.f.g.calls.count(REVOKE_URL),1)
        r2=self.d.advance(self.person)
        self.assertEqual(r2['revocation_scan'],2);self.assertEqual(r2['grants_revoked'],1)
        self.assertEqual(self.f.g.calls.count(REVOKE_URL),1)
    def test_provider_failure_remains_pending_and_retry_never_reexchanges(self):
        self.begin();self.f.g.revoke_error=True
        r=self.d.advance(self.person);self.assertEqual(r['apple_revocation'],'pending')
        self.f.g.revoke_error=False;r=self.d.advance(self.person)
        self.assertEqual(r['apple_revocation'],'confirmed');self.assertEqual(self.f.g.calls.count(TOKEN_URL),1)
    def test_batch_limit_is_enforced_and_larger_inventory_resumes(self):
        for i in range(125):self.f.lifecycle.reserve_grant(self.person,'uncaptured',f'{i+100:032x}')
        self.begin();r=self.d.advance(self.person,limit=100)
        self.assertEqual(r['grants_examined'],100);self.assertNotEqual(r['apple_revocation'],'confirmed')
        r=coordinator(self.f).advance(self.person,limit=100)
        self.assertEqual(r['grants_examined'],126);self.assertEqual(r['grants_revoked'],1)
        self.assertEqual(r['apple_revocation'],'pending')
    def test_bad_limit_has_no_side_effect(self):
        self.begin();before=rows(self.d.store)
        for limit in [0,101,True,1.5]:
            with self.assertRaises(ValueError):self.d.advance(self.person,limit=limit)
        self.assertEqual(before,rows(self.d.store));self.assertEqual(self.f.g.calls,[TOKEN_URL])
    def test_live_worker_prevents_second_dispatch(self):
        self.begin();started=threading.Event();finish=threading.Event()
        def hook(url):
            if url==REVOKE_URL:started.set();self.assertTrue(finish.wait(5))
        self.f.g.hook=hook
        with ThreadPoolExecutor(1) as pool:
            work=pool.submit(self.d.advance,self.person);self.assertTrue(started.wait(5))
            try:
                with self.assertRaises(DeletionBusy):coordinator(self.f).advance(self.person)
            finally:finish.set()
            self.assertEqual(work.result()['apple_revocation'],'confirmed')
        self.assertEqual(self.f.g.calls.count(REVOKE_URL),1)
    def test_claim_write_response_loss_never_dispatches_and_expiry_resumes(self):
        self.begin();real=self.d.store.compare_exchange
        def lost(k,rev,c):
            result=real(k,rev,c)
            if c.get('phase')=='running':raise StateUnavailable('lost claim response')
            return result
        with patch.object(self.d.store,'compare_exchange',side_effect=lost),self.assertRaises(StateUnavailable):self.d.advance(self.person)
        self.assertEqual(self.f.g.calls,[TOKEN_URL])
        with self.assertRaises(DeletionBusy):self.d.advance(self.person)
        self.f.f.h.a.now+=self.d.LEASE_SECONDS
        self.assertEqual(self.d.advance(self.person)['apple_revocation'],'confirmed')
    def test_progress_write_response_loss_reads_back_same_confirmation(self):
        self.begin();real=self.d.store.compare_exchange
        def lost(k,rev,c):
            result=real(k,rev,c)
            if c.get('phase')=='revoked':raise StateUnavailable('lost checkpoint response')
            return result
        with patch.object(self.d.store,'compare_exchange',side_effect=lost):r=self.d.advance(self.person)
        self.assertEqual(r['apple_revocation'],'confirmed');self.assertEqual(self.status(),r)
    def test_audit_failure_after_provider_success_resumes_without_second_revoke(self):
        self.begin();real=self.d.audit.compare_exchange
        def fail(k,rev,c):
            if 'outcome' in c:raise StateUnavailable('audit down')
            return real(k,rev,c)
        with patch.object(self.d.audit,'compare_exchange',side_effect=fail),self.assertRaises(StateUnavailable):self.d.advance(self.person)
        self.f.f.h.a.now+=self.d.LEASE_SECONDS
        self.assertEqual(self.d.advance(self.person)['apple_revocation'],'confirmed')
        self.assertEqual(self.f.g.calls.count(REVOKE_URL),1)
    def test_lease_expires_during_provider_call_late_worker_cannot_advance(self):
        self.begin()
        def hook(url):
            if url==REVOKE_URL:self.f.f.h.a.now+=self.d.LEASE_SECONDS
        self.f.g.hook=hook
        with self.assertRaises(DeletionBusy):self.d.advance(self.person)
        self.assertNotEqual(self.status()['apple_revocation'],'confirmed')
        self.f.g.hook=lambda _:None
        self.assertEqual(self.d.advance(self.person)['apple_revocation'],'confirmed')
        self.assertEqual(self.f.g.calls.count(REVOKE_URL),1)
    def test_corrupt_inventory_stops_without_skipping_grant(self):
        self.begin();n=self.f.lifecycle._read(self.person)[1]['head'];k=key('account-grant-node',n)
        rev,c=self.f.lifecycle.store.load(k);self.f.lifecycle.store.compare_exchange(k,rev,dict(c,position=2))
        with self.assertRaises(StateUnavailable):self.d.advance(self.person)
        self.assertEqual(self.f.g.calls,[TOKEN_URL]);self.assertFalse(self.status()['account_deleted'])
    def test_corrupt_checkpoint_is_never_reset(self):
        self.begin();k=self.d._job_key(self.person);rev,c=self.d.store.load(k)
        self.d.store.compare_exchange(k,rev,dict(c,revoked=True))
        with self.assertRaises(StateUnavailable):self.status()
    def test_missing_checkpoint_after_intent_can_be_created_without_reauth(self):
        self.f.close();self.f.f.h.a.now+=1000
        self.assertEqual(self.status()['apple_revocation'],'not_started')
    def test_no_token_or_identity_claims_in_worker_audit(self):
        self.begin();self.d.advance(self.person);raw=repr(rows(self.d.store)+rows(self.d.audit))
        for secret in ['apple-human-1','private-refresh-token','private-access-token',self.f.g.signed]:self.assertNotIn(secret,raw)
    def test_scan_receipt_failure_is_repaired_without_provider_replay(self):
        self.begin();real=self.d.audit.compare_exchange
        def fail(k,rev,c):
            if c.get('apple_revocation')=='confirmed':raise StateUnavailable('scan receipt unavailable')
            return real(k,rev,c)
        with patch.object(self.d.audit,'compare_exchange',side_effect=fail),self.assertRaises(StateUnavailable):self.d.advance(self.person)
        self.assertEqual(self.status()['apple_revocation'],'confirmed')
        self.assertEqual(self.f.g.calls.count(REVOKE_URL),1)
        self.assertTrue(any(json.loads(c).get('apple_revocation')=='confirmed' for _,_,c in rows(self.d.audit)))
    def test_bad_provider_receipt_is_not_counted_as_revoked(self):
        self.begin()
        with patch.object(self.f.g.a,'revoke',return_value=dict(v=1,state='apple_token_revoked',grant_id='0'*64)):
            r=self.d.advance(self.person)
        self.assertEqual(r['grants_revoked'],0);self.assertEqual(r['apple_revocation'],'pending')
    def test_rejected_one_grant_does_not_prevent_other_revoke(self):
        self.f.login('device-two');self.begin();real=self.f.g.a.revoke
        def reject(device,op):
            if device=='device-two':raise IdentityRejected('provider rejected')
            return real(device,op)
        with patch.object(self.f.g.a,'revoke',side_effect=reject):r=self.d.advance(self.person)
        self.assertEqual(r['grants_examined'],2);self.assertEqual(r['grants_revoked'],1)
        self.assertEqual(r['apple_revocation'],'pending')
    def test_other_account_remains_active(self):
        self.f.login('other','apple-human-2');self.begin();self.d.advance(self.person)
        self.assertEqual(self.f.b.status('other')['state'],'signed_in')
    def test_constructor_rejects_shared_progress_and_audit(self):
        with self.assertRaises(ValueError):AccountDeletion(bridge=self.f.b,store=self.d.store,audit_store=self.d.store)


class DeletionHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):native.legacy.LinksTests.setUpClass()
    def setUp(self):
        from test_lite_account_identity import IdentityHTTPTests
        import lite_attest_http as http
        from lite_account_lifecycle import AccountLifecycle
        from lite_apple_grants import AppleGrantAuthority
        from test_lite_apple_grants import GrantFixture
        real=http.install_lite_attest;self.h=IdentityHTTPTests()
        def install(app,**kw):
            b=kw['identity'];root=Path(b.runtime.registry.store.path).parent
            # IdentityHTTPTests assigns f before installing the signed gate.
            self.g=GrantFixture(self,self.h.f)
            life=AccountLifecycle(store=SQLiteStateStore(root/'life.sqlite'),
                audit_store=SQLiteStateStore(root/'life-audit.sqlite'),binding=self.g.a.binding,clock=b.now)
            self.g.store=SQLiteStateStore(root/'indexed-vault.sqlite')
            self.g.a=AppleGrantAuthority(provider=self.g.provider,verifier=b.verifier,store=self.g.store,
                audit_store=self.g.audit,cipher=self.g.cipher,namespace=b.namespace,identity_key=b.secret,lifecycle=life)
            b.__init__(runtime=b.runtime,verifier=b.verifier,store=b.store,audit_store=b.audit,
                namespace=b.namespace,identity_key=b.secret,grants=self.g.a,lifecycle=life)
            self.d=AccountDeletion(bridge=b,store=SQLiteStateStore(root/'deletion.sqlite'),
                audit_store=SQLiteStateStore(root/'deletion-audit.sqlite'))
            return real(app,**kw,deletion=self.d)
        with patch.object(http,'install_lite_attest',side_effect=install):self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        c=self.h.post('/api/v1/lite/account/challenge',dict(operation='1'*32)).json
        self.g.token=self.h.f.token(c)
        r=self.h.post('/api/v1/lite/account/confirm',dict(operation=c['operation'],state=c['state'],
            identity_token=self.g.token,authorization_code='synthetic-delete-code'))
        self.assertEqual(r.status_code,200,r.json)
        self.owner=self.h.h.owner;self.person=self.d.bridge._read(self.owner)[1]['account']['identity_sha256']
    def post(self,kind='request',**extra):
        b=dict(anon_id=self.owner,operation='d'*32)
        if kind=='request':b['confirmation']='delete_account'
        b.update(extra);raw=json.dumps(b).encode();path=PREFIX+kind
        headers=self.h.h.headers(path,raw,'application/json',purpose='withdraw')
        return self.h.h.client.post(path,data=raw,content_type='application/json',headers=headers)
    def test_signed_request_and_status_remain_available_after_closure(self):
        r=self.post();self.assertEqual(r.status_code,202,r.json);self.assertFalse(r.json['account_deleted'])
        self.assertEqual(self.post('status').json,r.json);self.assertEqual(self.post().json,r.json)
        self.assertEqual(r.headers['Cache-Control'],'no-store');self.assertEqual(self.g.calls,[TOKEN_URL])
        self.d.advance(self.person)
        self.assertEqual(self.post('status').json['apple_revocation'],'confirmed')
        self.assertFalse(self.post('status').json['account_deleted'])
    def test_explicit_confirmation_and_owner_required(self):
        self.assertEqual(self.post(confirmation='').status_code,409)
        self.assertEqual(self.post(anon_id='other').status_code,403)
        self.assertEqual(self.post(account='chosen').status_code,409)
        self.assertEqual(self.d.lifecycle._read(self.person)[1]['phase'],'active')
    def test_unsigned_request_cannot_close(self):
        r=self.h.h.client.post(PREFIX+'request',json=dict(anon_id=self.owner,operation='d'*32,confirmation='delete_account'))
        self.assertEqual(r.status_code,401);self.assertEqual(self.d.lifecycle._read(self.person)[1]['phase'],'active')
    def test_general_budget_exhaustion_does_not_block_privacy_lane(self):
        from lite_attest_budget import BudgetExceeded
        real=self.h.h.budget.reserve
        def limited(*args,**kwargs):
            if kwargs.get('scope','general')=='general':raise BudgetExceeded(60)
            return real(*args,**kwargs)
        with patch.object(self.h.h.budget,'reserve',side_effect=limited):
            self.assertEqual(self.post().status_code,202);self.assertEqual(self.post('status').status_code,202)
    def test_general_challenge_cannot_enter_deletion_privacy_routes(self):
        for suffix in ('request','status'):
            path=PREFIX+suffix;raw=json.dumps(dict(anon_id=self.owner,operation='d'*32,confirmation='delete_account')).encode()
            headers=self.h.h.headers(path,raw,'application/json',purpose='assert')
            r=self.h.h.client.post(path,data=raw,content_type='application/json',headers=headers)
            self.assertEqual(r.status_code,401)
        self.assertEqual(self.d.lifecycle._read(self.person)[1]['phase'],'active')
    def test_withdrawal_challenge_cannot_authorize_purchase(self):
        path='/api/v1/lite/purchase/context';raw=json.dumps(dict(anon_id=self.owner,operation='e'*32)).encode()
        headers=self.h.h.headers(path,raw,'application/json',purpose='withdraw')
        r=self.h.h.client.post(path,data=raw,content_type='application/json',headers=headers)
        self.assertEqual(r.status_code,401)
    def test_status_has_no_worker_side_effects_or_model_calls(self):
        self.post();before=rows(self.d.store),rows(self.d.audit)
        for _ in range(3):self.assertEqual(self.post('status').status_code,202)
        self.assertEqual(before,(rows(self.d.store),rows(self.d.audit)))
        self.assertEqual(self.g.calls,[TOKEN_URL]);self.assertEqual(self.h.h.model_calls,0)
    def test_pending_intake_error_does_not_claim_complete_or_leak_secrets(self):
        with patch.object(self.d.store,'compare_exchange',side_effect=StateUnavailable('private-secret')):
            r=self.post();self.assertEqual(r.status_code,503);self.assertNotIn('private-secret',r.get_data(as_text=True))
        self.assertEqual(self.post().status_code,202)
    def test_research_withdrawal_is_independent(self):
        self.post();h=self.h.h;path='/api/v1/lite/data/'+self.owner
        r=h.client.delete(path,headers=h.headers(path,b'','',method='DELETE',purpose='withdraw'))
        self.assertEqual(r.status_code,200);self.assertFalse(self.post('status').json['account_deleted'])
    def test_no_delete_worker_endpoint_is_exposed(self):
        self.assertNotIn(PREFIX+'advance',{r.rule for r in self.h.h.app.url_map.iter_rules()})
    def test_request_rejects_other_operation_after_closure(self):
        self.post();r=self.post(operation='e'*32)
        self.assertEqual(r.status_code,401);self.assertEqual(self.post('status').status_code,202)


if __name__=='__main__':unittest.main()
