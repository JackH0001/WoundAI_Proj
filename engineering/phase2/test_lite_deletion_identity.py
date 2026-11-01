"""Fresh signed Apple identity, real App Attest / SQLite; no live accounts or deletion."""
import json
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from lite_account_identity import IdentityRejected
from lite_attest_state import StateUnavailable
from lite_deletion_identity import DeletionIdentityExpired
from lite_usage_ledger import digest
from test_lite_account_lifecycle import Fixture
from test_lite_account_deletion import coordinator
import test_lite_account_deletion as deletion
from test_lite_subscription_grace import rows
import test_lite_native_purchase as native


class RecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): native.legacy.LinksTests.setUpClass()
    def setUp(self):
        self.f=Fixture(self); self.f.login(); self.d=coordinator(self.f); self.r=self.d.reauthentication
        self.owner='device-one';self.op='d'*32;self.attempt='e'*32
        self.account=digest(self.f.b.registry.registration(self.owner)['subject'])
        self.f.f.h.a.now+=1201  # Ordinary service login has expired.
    def challenge(self,attempt=None,op=None,account=None):
        return self.r.challenge(self.owner,op or self.op,attempt or self.attempt,account or self.account)
    def confirm(self,q=None,token=None):
        q=q or self.challenge(); token=token or self.f.f.token(q)
        return self.r.confirm(self.owner,q['operation'],q['reauth_operation'],q['account_sha256'],token,q['state'])
    def test_expired_first_request_then_same_account_recovers_without_login_or_usage_change(self):
        with self.assertRaises(IdentityRejected): self.d.begin(self.owner,self.op)
        b=self.f.b;before=[rows(s) for s in (b.store,b.registry.store,self.f.f.h.f.ledger.store,self.f.g.store)]
        q=self.challenge();reply=self.confirm(q)
        self.assertEqual(reply['operation'],self.op);self.assertFalse(reply['account_deleted'])
        self.assertEqual(before,[rows(s) for s in (b.store,b.registry.store,self.f.f.h.f.ledger.store,self.f.g.store)])
        self.assertEqual(len(self.f.g.calls),1) # Only the original login exchanged a code.
        with self.assertRaises(IdentityRejected): b.require_session(self.owner)
    def test_challenge_only_never_closes_or_restores_service(self):
        self.challenge();self.assertEqual(self.d.lifecycle._read(self.f.person)[1]['phase'],'active')
        with self.assertRaises(IdentityRejected):self.f.b.require_session(self.owner)
    def test_same_attempt_is_idempotent(self):
        q=self.challenge();before=rows(self.d.store),rows(self.d.audit)
        self.assertEqual(q,self.challenge());self.assertEqual(before,(rows(self.d.store),rows(self.d.audit)))
    def test_different_account_token_rejected_without_mutation(self):
        q=self.challenge();before=rows(self.d.store)
        with self.assertRaises(IdentityRejected):self.confirm(q,self.f.f.token(q,sub='different-person'))
        self.assertEqual(before,rows(self.d.store));self.assertEqual(self.d.lifecycle._read(self.f.person)[1]['phase'],'active')
    def test_wrong_expected_account_rejected_before_challenge(self):
        with self.assertRaises(IdentityRejected):self.challenge(account='f'*64)
    def test_wrong_nonce_and_audience_and_stale_identity_rejected(self):
        q=self.challenge()
        for change in ({'nonce':'f'*64},{'aud':'other-app'},{'iat':q['created_at']-31}):
            with self.subTest(change=change),self.assertRaises(IdentityRejected):self.confirm(q,self.f.f.token(q,**change))
    def test_expired_challenge_requires_explicit_new_attempt(self):
        q=self.challenge();self.f.f.h.a.now+=300
        with self.assertRaises(DeletionIdentityExpired):self.challenge()
        with self.assertRaises(DeletionIdentityExpired):self.confirm(q)
        new=self.challenge(attempt='c'*32);self.assertNotEqual(new['nonce'],q['nonce'])
        self.assertEqual(self.confirm(new)['operation'],self.op)
    def test_unexpired_challenge_cannot_be_replaced(self):
        q=self.challenge()
        with self.assertRaises(IdentityRejected):self.challenge(attempt='c'*32)
        self.assertEqual(self.challenge(),q)
    def test_old_attempt_cannot_supersede_new_challenge(self):
        q=self.challenge();self.f.f.h.a.now+=300;new=self.challenge(attempt='c'*32)
        with self.assertRaises(IdentityRejected):self.challenge()
        with self.assertRaises(IdentityRejected):self.confirm(q)
        self.assertEqual(self.challenge(attempt='c'*32),new)
    def test_attempt_cannot_be_rebound_to_other_deletion(self):
        self.challenge()
        with self.assertRaises(StateUnavailable):self.challenge(op='f'*32)
    def test_confirm_response_loss_resumes_original_request_after_jwt_expires(self):
        q=self.challenge();self.confirm(q);self.f.f.h.a.now+=700
        r=self.d.begin(self.owner,self.op);self.assertEqual(r['operation'],self.op)
        self.assertEqual(r,self.d.status(self.owner,self.op));self.assertFalse(r['account_deleted'])
    def test_verified_proof_durable_before_closure_failure(self):
        q=self.challenge()
        with patch.object(self.d.lifecycle,'close',side_effect=StateUnavailable('offline')),self.assertRaises(StateUnavailable):self.confirm(q)
        self.f.f.h.a.now+=700
        self.assertEqual(self.d.begin(self.owner,self.op)['operation'],self.op)
    def test_verified_commit_response_loss_recovers_without_reusing_jwt(self):
        q=self.challenge();real=self.d.store.compare_exchange
        def lose(k,r,v):
            result=real(k,r,v)
            if v.get('phase')=='verified':raise StateUnavailable('reply lost')
            return result
        with patch.object(self.d.store,'compare_exchange',side_effect=lose),self.assertRaises(StateUnavailable):self.confirm(q)
        self.assertEqual(self.d.begin(self.owner,self.op)['operation'],self.op)
    def test_verified_proof_write_failure_cannot_close(self):
        q=self.challenge()
        with patch.object(self.d.store,'compare_exchange',side_effect=StateUnavailable('offline')),self.assertRaises(StateUnavailable):self.confirm(q)
        self.assertEqual(self.d.lifecycle._read(self.f.person)[1]['phase'],'active')
        with self.assertRaises(IdentityRejected):self.d.begin(self.owner,self.op)
    def test_audit_failure_blocks_closure_but_preserves_verified_resume(self):
        q=self.challenge();real=self.d.audit.compare_exchange
        def fail(k,r,v):
            if 'verified_at' in v:raise StateUnavailable('audit offline')
            return real(k,r,v)
        with patch.object(self.d.audit,'compare_exchange',side_effect=fail),self.assertRaises(StateUnavailable):self.confirm(q)
        self.assertEqual(self.d.lifecycle._read(self.f.person)[1]['phase'],'active')
        self.assertEqual(self.d.begin(self.owner,self.op)['operation'],self.op)
    def test_duplicate_confirm_same_receipt_single_closure(self):
        q=self.challenge();token=self.f.f.token(q)
        with ThreadPoolExecutor(max_workers=2) as ex:
            results=list(ex.map(lambda _:self.confirm(q,token),range(2)))
        self.assertEqual(results[0],results[1]);self.assertEqual(self.d.lifecycle._read(self.f.person)[1]['closure']['operation'],self.op)
    def test_verified_proof_cannot_authorize_another_operation_or_device(self):
        self.confirm()
        for owner,op in ((self.owner,'f'*32),('other-device',self.op)):
            with self.assertRaises((IdentityRejected,StateUnavailable,ValueError)):self.d.begin(owner,op)
    def test_verified_proof_cannot_be_replaced_or_new_token_replayed(self):
        q=self.challenge();self.confirm(q)
        with self.assertRaises(IdentityRejected):self.challenge(attempt='c'*32)
        with self.assertRaises(IdentityRejected):self.confirm(q,self.f.f.token(q,exp=self.f.f.now()+601))
    def test_no_raw_credentials_in_saved_evidence(self):
        q=self.challenge();token=self.f.f.token(q);self.confirm(q,token)
        data=repr([rows(self.d.store),rows(self.d.audit)])
        for secret in (token,'apple-human-1','synthetic-delete-code'):self.assertNotIn(secret,data)
    def test_corrupt_proof_is_not_reset(self):
        self.challenge();k=self.r._key(self.owner,self.op);rev,c=self.d.store.load(k);c['phase']='verified'
        self.d.store.compare_exchange(k,rev,c)
        with self.assertRaises(StateUnavailable):self.d.begin(self.owner,self.op)
        with self.assertRaises(StateUnavailable):self.challenge()
    def test_challenge_checkpoint_write_loss_reuses_same_durable_nonce(self):
        with patch.object(self.d.store,'compare_exchange',side_effect=StateUnavailable('offline')),self.assertRaises(StateUnavailable):self.challenge()
        q=self.challenge();self.assertEqual(q,self.challenge());self.assertEqual(self.confirm(q)['operation'],self.op)
    def test_no_registry_or_lifecycle_creation_for_unknown_installation(self):
        with self.assertRaises((IdentityRejected,StateUnavailable,ValueError)):
            self.r.challenge('new-install',self.op,self.attempt,self.account)


class RecoveryHTTPTests(unittest.TestCase):
    setUpClass=classmethod(deletion.DeletionHTTPTests.setUpClass.__func__)
    setUp=deletion.DeletionHTTPTests.setUp
    post=deletion.DeletionHTTPTests.post
    def challenge(self):
        return self.post('reauth/challenge',reauth_operation='e'*32,
            account_sha256=digest(self.d.bridge.registry.registration(self.owner)['subject']),confirmation='continue_deletion')
    def confirm(self,q,**extra):
        b=dict(reauth_operation=q['reauth_operation'],account_sha256=q['account_sha256'],state=q['state'],identity_token=self.h.f.token(q));b.update(extra)
        return self.post('reauth/confirm',**b)
    def test_signed_privacy_recovery_after_session_expiry(self):
        self.h.f.h.a.now+=1201
        self.assertEqual(self.post().status_code,401)
        r=self.challenge();self.assertEqual(r.status_code,200,r.json)
        done=self.confirm(r.json);self.assertEqual(done.status_code,202,done.json)
        self.assertEqual(self.post().json,done.json);self.assertEqual(self.h.h.model_calls,0)
        self.assertEqual(len(self.g.calls),1)
    def test_unknown_field_and_authorization_code_never_accepted(self):
        q=self.challenge().json
        self.assertEqual(self.confirm(q,authorization_code='never-exchange').status_code,409)
        self.assertEqual(self.d.lifecycle._read(self.person)[1]['phase'],'active')
    def test_wrong_apple_account_returns_identity_required(self):
        q=self.challenge().json;self.assertEqual(self.confirm(q,identity_token=self.h.f.token(q,sub='other')).status_code,401)
    def test_routes_require_privacy_challenge_not_service_assertion(self):
        for path in ('reauth/challenge','reauth/confirm'):
            from lite_account_deletion import PREFIX
            target=PREFIX+path;raw=b'{}';h=self.h.h
            headers=h.headers(target,raw,'application/json',purpose='assert')
            self.assertEqual(h.client.post(target,data=raw,content_type='application/json',headers=headers).status_code,401)
    def test_expiry_has_specific_error_for_explicit_retry(self):
        q=self.challenge().json;self.h.f.h.a.now+=300
        r=self.confirm(q);self.assertEqual(r.status_code,410,r.json);self.assertEqual(r.json['error'],'lite_deletion_reauth_expired')
    def test_general_quota_exhaustion_does_not_block_reauth(self):
        from lite_attest_budget import BudgetExceeded
        real=self.h.h.budget.reserve
        def limited(*a,**kw):
            if kw.get('scope','general')=='general':raise BudgetExceeded(60)
            return real(*a,**kw)
        with patch.object(self.h.h.budget,'reserve',side_effect=limited):
            q=self.challenge();self.assertEqual(q.status_code,200,q.json);self.assertEqual(self.confirm(q.json).status_code,202)


if __name__=='__main__':unittest.main()
