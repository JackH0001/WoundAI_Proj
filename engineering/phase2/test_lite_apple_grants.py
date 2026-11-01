import base64
import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from lite_account_identity import AccountIdentityBridge, IdentityRejected, PREFIX
from lite_apple_oauth import AppleOAuthProvider, GrantUncertain, TOKEN_URL, REVOKE_URL
from lite_apple_grants import AppleGrantAuthority, GrantCipher
from lite_attest_state import SQLiteStateStore, StateUnavailable
from lite_subscription_executor import key
from lite_usage_ledger import digest
from test_lite_account_identity import IdentityFixture
import test_lite_account_identity as identity_tests
from test_lite_subscription_grace import rows
import test_lite_native_purchase as native


class GrantFixture:
    def __init__(self, host, identity=None):
        self.f=identity or IdentityFixture(host); self.calls=[]; self.hook=lambda url:None
        self.token=None; self.revoke_error=False
        def post(url, fields):
            self.calls.append(url);self.hook(url)
            if url==REVOKE_URL:
                if self.revoke_error: raise OSError('private-refresh-token')
                return 200,b''
            return 200,json.dumps(dict(access_token='private-access-token',refresh_token='private-refresh-token',
                id_token=self.token,expires_in=3600,token_type='Bearer')).encode()
        self.provider=AppleOAuthProvider(client_id=self.f.verifier.audience,client_secret=lambda:'synthetic-client-secret',post=post)
        self.cipher=GrantCipher(keys={'one':b'v'*32},current='one')
        self.store=SQLiteStateStore(self.f.h.f.root/'grant-vault.sqlite')
        self.audit=SQLiteStateStore(self.f.h.f.root/'grant-audit.sqlite')
        self.a=self.clone()
    def clone(self,cipher=None):
        return AppleGrantAuthority(provider=self.provider,verifier=self.f.verifier,store=self.store,audit_store=self.audit,
            cipher=cipher or self.cipher,namespace=self.f.b.namespace,identity_key=self.f.b.secret)
    def prepare(self,device='new-device'):
        self.c=self.f.challenge(device);self.signed=self.f.token(self.c);self.token=self.signed
        self.code='synthetic-code-'+self.c['operation'];return self.c
    def capture(self,authority=None):
        return (authority or self.a).capture(self.c['installation'],self.c['operation'],self.c['nonce'],self.signed,self.code)
    def revoke(self): return self.a.revoke(self.c['installation'],self.c['operation'])
    def grant_key(self): return key('apple-grant',self.a._id(self.c['installation'],self.c['operation']))
    def bridge(self):
        f=self.f;b=f.b
        f.b=AccountIdentityBridge(runtime=f.h.r,verifier=f.verifier,store=b.store,audit_store=b.audit,
            namespace=b.namespace,identity_key=b.secret,grants=self.a)
        return f.b
    def confirm(self): return self.f.b.confirm(self.c['installation'],self.c['operation'],self.signed,self.c['state'],self.code)


class GrantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): native.legacy.LinksTests.setUpClass()
    def setUp(self): self.g=GrantFixture(self);self.g.prepare()

    def test_capture_retry_uses_same_encrypted_grant_and_no_second_apple_post(self):
        g=self.g;r=g.capture();before=rows(g.store)
        self.assertEqual(g.capture(g.clone()),r);self.assertEqual(g.calls,[TOKEN_URL]);self.assertEqual(rows(g.store),before)

    def test_plain_tokens_code_subject_never_appear_in_state_or_audit(self):
        g=self.g;g.capture();raw=repr(rows(g.store)+rows(g.audit))
        for secret in [g.signed,g.code,'private-access-token','private-refresh-token','apple-human-1']:
            self.assertNotIn(secret,raw)

    def test_received_subject_mismatch_is_encrypted_blocked_and_revocable(self):
        g=self.g;g.token=g.f.token(g.c,sub='other-person')
        with self.assertRaises(IdentityRejected): g.capture()
        self.assertEqual(g.store.load(g.grant_key())[1]['phase'],'blocked')
        self.assertEqual(g.revoke()['state'],'apple_token_revoked')

    def test_received_nonce_or_signature_mismatch_never_authorizes(self):
        g=self.g;g.token=g.f.token(g.c,nonce='wrong')
        with self.assertRaises(IdentityRejected): g.capture()
        self.assertEqual(g.store.load(g.grant_key())[1]['phase'],'blocked')

    def test_bad_original_identity_never_exchanges(self):
        g=self.g;g.signed=g.f.token(g.c,nonce='wrong')
        with self.assertRaises(IdentityRejected):g.capture()
        self.assertEqual(g.calls,[])

    def test_code_binding_cannot_move_to_another_operation(self):
        g=self.g;g.capture();code=g.code;g.prepare('other-device');g.code=code
        with self.assertRaises(StateUnavailable):g.capture()
        self.assertEqual(g.calls,[TOKEN_URL])

    def test_saved_operation_rejects_different_code(self):
        g=self.g;g.capture();g.code='another-code'
        with self.assertRaises(IdentityRejected):g.capture()
        self.assertEqual(g.calls,[TOKEN_URL])

    def test_two_instances_dispatch_only_once(self):
        g=self.g;started=threading.Event();finish=threading.Event()
        def wait(url): started.set();self.assertTrue(finish.wait(5))
        g.hook=wait
        with ThreadPoolExecutor(2) as pool:
            first=pool.submit(g.capture);self.assertTrue(started.wait(5))
            try:
                with self.assertRaises(GrantUncertain):g.capture(g.clone())
            finally:finish.set()
            r=first.result()
        self.assertEqual(g.capture(),r);self.assertEqual(g.calls,[TOKEN_URL])

    def test_claim_lost_reply_never_dispatches_single_use_code(self):
        g=self.g;real=g.store.compare_exchange
        def lost(k,rev,value):
            result=real(k,rev,value)
            if value.get('phase')=='claimed':raise StateUnavailable('lost')
            return result
        with patch.object(g.store,'compare_exchange',side_effect=lost),self.assertRaises(StateUnavailable):g.capture()
        with self.assertRaises(GrantUncertain):g.capture()
        self.assertEqual(g.calls,[])

    def test_exchange_response_loss_is_uncertain_not_replayed(self):
        g=self.g;g.hook=lambda _:(_ for _ in ()).throw(OSError('private-response'))
        with self.assertRaises(GrantUncertain):g.capture()
        g.hook=lambda _:None
        with self.assertRaises(GrantUncertain):g.capture()
        self.assertEqual(g.calls,[TOKEN_URL])
        with self.assertRaises(GrantUncertain):g.revoke()

    def test_vault_commit_lost_reply_recovers_without_exchanging_again(self):
        g=self.g;real=g.store.compare_exchange
        def lost(k,rev,value):
            result=real(k,rev,value)
            if value.get('phase')=='ready':raise StateUnavailable('lost')
            return result
        with patch.object(g.store,'compare_exchange',side_effect=lost):r=g.capture()
        self.assertEqual(g.capture(),r);self.assertEqual(g.calls,[TOKEN_URL])

    def test_vault_write_before_commit_failure_never_marks_ready(self):
        g=self.g;real=g.store.compare_exchange
        def fail(k,rev,value):
            if value.get('phase')=='ready':raise StateUnavailable('disk')
            return real(k,rev,value)
        with patch.object(g.store,'compare_exchange',side_effect=fail),self.assertRaises(GrantUncertain):g.capture()
        with self.assertRaises(GrantUncertain):g.capture()
        self.assertEqual(g.calls,[TOKEN_URL])

    def test_audit_failure_after_capture_can_retry_without_reexchange(self):
        g=self.g;real=g.audit.compare_exchange
        def fail(k,rev,value):
            if value.get('state')=='grant_ready':raise StateUnavailable('audit')
            return real(k,rev,value)
        with patch.object(g.audit,'compare_exchange',side_effect=fail),self.assertRaises(StateUnavailable):g.capture()
        self.assertEqual(g.capture()['state'],'grant_ready');self.assertEqual(g.calls,[TOKEN_URL])

    def test_cipher_context_tamper_wrong_key_and_bit_flip_fail_closed(self):
        g=self.g;g.capture();row=g.store.load(g.grant_key())[1];env=row['envelope'];ctx=row['context']
        for e,c in [(env,dict(ctx,grant_id='a'*64)),(dict(env,key_id='absent'),ctx),
                    (dict(env,ciphertext=base64.b64encode(b'x'*32).decode()),ctx)]:
            with self.assertRaises(StateUnavailable):g.cipher.open(e,c)

    def test_key_rotation_reads_old_grants_without_rewrite_or_alias_replacement(self):
        g=self.g;r=g.capture();before=g.store.load(g.grant_key())
        cipher=GrantCipher(keys={'one':b'v'*32,'two':b'w'*32},current='two')
        self.assertEqual(g.capture(g.clone(cipher)),r)
        self.assertEqual(g.store.load(g.grant_key()),before)
        with self.assertRaises(StateUnavailable):g.clone(GrantCipher(keys={'one':b'z'*32},current='one'))

    def test_identity_key_cannot_double_as_encryption_key(self):
        g=self.g
        with self.assertRaises(ValueError):g.clone(GrantCipher(keys={'bad':g.f.b.secret},current='bad'))

    def test_revocation_fences_active_before_call_and_current_ciphertext_removed_after(self):
        g=self.g;r=g.capture();g.revoke_error=True
        with self.assertRaises(GrantUncertain):g.revoke()
        with self.assertRaises(IdentityRejected):g.a.require(g.c['installation'],g.c['operation'],r['identity_sha256'])
        self.assertIsNotNone(g.store.load(g.grant_key())[1]['envelope'])
        g.revoke_error=False;receipt=g.revoke()
        self.assertIsNone(g.store.load(g.grant_key())[1]['envelope'])
        n=len(g.calls);self.assertEqual(g.revoke(),receipt);self.assertEqual(len(g.calls),n)

    def test_revoke_during_exchange_blocks_late_activation_and_keeps_revocable_token(self):
        g=self.g
        def revoke(url):
            if url==TOKEN_URL:
                with self.assertRaises(GrantUncertain):g.revoke()
        g.hook=revoke
        with self.assertRaises(IdentityRejected):g.capture()
        g.hook=lambda _:None;self.assertEqual(g.revoke()['state'],'apple_token_revoked')

    def test_revoke_before_capture_forbids_external_exchange(self):
        g=self.g
        with self.assertRaises(GrantUncertain):g.revoke()
        with self.assertRaises(IdentityRejected):g.capture()
        self.assertEqual(g.calls,[])

    def test_revocation_lost_persistence_reply_is_recovered_from_tombstone(self):
        g=self.g;g.capture();real=g.store.compare_exchange
        def lost(k,rev,value):
            result=real(k,rev,value)
            if value.get('phase')=='revoked':raise StateUnavailable('lost')
            return result
        with patch.object(g.store,'compare_exchange',side_effect=lost),self.assertRaises(StateUnavailable):g.revoke()
        self.assertEqual(g.revoke()['state'],'apple_token_revoked');self.assertEqual(g.calls.count(REVOKE_URL),1)

    def test_corrupt_state_cannot_reset(self):
        g=self.g;g.capture();rev,c=g.store.load(g.grant_key());g.store.compare_exchange(g.grant_key(),rev,dict(c,v=True))
        with self.assertRaises(StateUnavailable):g.capture()
        self.assertEqual(g.calls,[TOKEN_URL])

    def test_bridge_requires_code_and_grant_but_does_not_change_quota_research_or_session_limit(self):
        g=self.g;b=g.bridge();before=rows(g.f.h.f.ledger.store)
        g.signed=g.f.token(g.c,exp=g.f.now()+3600);g.token=g.signed
        with self.assertRaises(IdentityRejected):g.f.confirm(g.c,g.signed)
        r=g.confirm();self.assertEqual(b.status(g.c['installation']),r)
        self.assertEqual(rows(g.f.h.f.ledger.store),before)
        g.f.h.a.now+=900
        with self.assertRaises(IdentityRejected):b.status(g.c['installation'])

    def test_bridge_retry_cannot_substitute_different_code(self):
        g=self.g;g.bridge();g.confirm();g.code='different-code'
        with self.assertRaises(IdentityRejected):g.confirm()
        self.assertEqual(g.calls,[TOKEN_URL])

    def test_vault_cannot_be_same_store_as_immutable_audit(self):
        g=self.g
        with self.assertRaises(ValueError):AppleGrantAuthority(provider=g.provider,verifier=g.f.verifier,
            store=g.store,audit_store=g.store,cipher=g.cipher,namespace=g.f.b.namespace,identity_key=g.f.b.secret)

    def test_explicit_fresh_login_after_uncertainty_preserves_account_and_quota(self):
        g=self.g;g.bridge();g.confirm();g.f.use(811,'new-device')
        before=rows(g.f.h.accounts.store),rows(g.f.h.f.ledger.store)
        g.prepare();g.hook=lambda _:(_ for _ in ()).throw(OSError('lost'))
        with self.assertRaises(GrantUncertain):g.confirm()
        with self.assertRaises(IdentityRejected):g.f.b.status('new-device')
        g.hook=lambda _:None;g.prepare();g.confirm()
        self.assertEqual(before,(rows(g.f.h.accounts.store),rows(g.f.h.f.ledger.store)))

    def test_durable_mode_refuses_authority_removal(self):
        g=self.g;b=g.bridge();g.confirm();b.grants=None
        with self.assertRaises(StateUnavailable):b.status(g.c['installation'])
        with self.assertRaises(StateUnavailable):AccountIdentityBridge(runtime=g.f.h.r,verifier=g.f.verifier,
            store=b.store,audit_store=b.audit,namespace=b.namespace,identity_key=b.secret)

    def test_logout_during_exchange_prevents_late_session_and_preserves_grant_for_revocation(self):
        g=self.g;b=g.bridge();g.hook=lambda _:b.logout(g.c['installation'],g.c['operation'])
        with self.assertRaises(IdentityRejected):g.confirm()
        with self.assertRaises(IdentityRejected):b.status(g.c['installation'])
        g.hook=lambda _:None;self.assertEqual(g.revoke()['state'],'apple_token_revoked')

    def test_revocation_blocks_bridge_status_without_deleting_local_or_research_records(self):
        g=self.g;b=g.bridge();g.confirm();before=rows(g.f.h.f.ledger.store),rows(g.f.h.accounts.store)
        g.revoke()
        with self.assertRaises(IdentityRejected):b.status(g.c['installation'])
        self.assertEqual(before,(rows(g.f.h.f.ledger.store),rows(g.f.h.accounts.store)))


class GrantHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): native.legacy.LinksTests.setUpClass()
    def setUp(self):
        self.h=identity_tests.IdentityHTTPTests();real=AccountIdentityBridge.__init__
        # Existing signed App Attest HTTP fixture; inject before Flask route creation.
        def init(b,**kw):
            real(b,**kw)
            if not hasattr(self,'bridge'):self.bridge=b
        with patch.object(AccountIdentityBridge,'__init__',init):self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.g=GrantFixture(self,self.h.f);self.g.bridge()
        # Blueprint closes over the original bridge; enable the same durable authority.
        self.bridge.grants=self.g.a
    def test_attested_confirmation_requires_code_and_preserves_research(self):
        h=self.h;g=self.g;before=rows(h.h.privacy.store),rows(h.h.ledger.store)
        c=h.post(PREFIX+'challenge',dict(operation='1'*32)).json
        signed=h.f.token(c);g.token=signed
        body=dict(operation=c['operation'],state=c['state'],identity_token=signed)
        self.assertEqual(h.post(PREFIX+'confirm',body).status_code,401)
        body['authorization_code']='synthetic-http-code'
        r=h.post(PREFIX+'confirm',body);self.assertEqual(r.status_code,200,r.json)
        self.assertEqual(h.post(PREFIX+'confirm',body).json,r.json)
        self.assertEqual(g.calls,[TOKEN_URL]);self.assertEqual(before,(rows(h.h.privacy.store),rows(h.h.ledger.store)))
        self.assertEqual(h.h.model_calls,0);self.assertEqual(r.headers['Cache-Control'],'no-store')
    def test_unsigned_request_cannot_exchange(self):
        self.assertEqual(self.h.h.client.post(PREFIX+'confirm',json={}).status_code,401)
        self.assertEqual(self.g.calls,[])

    def test_unknown_exchange_response_requires_explicit_sign_in_not_blind_retries(self):
        h=self.h;g=self.g;c=h.post(PREFIX+'challenge',dict(operation='1'*32)).json
        g.hook=lambda _:(_ for _ in ()).throw(OSError('sensitive'))
        body=dict(operation=c['operation'],state=c['state'],identity_token=h.f.token(c),authorization_code='one-code')
        for _ in range(2):
            r=h.post(PREFIX+'confirm',body);self.assertEqual(r.status_code,409,r.json)
            self.assertEqual(r.json,dict(error='lite_apple_grant_recovery_required',keep_local=True,
                explicit_sign_in_required=True,retry_saved_code=False))
        self.assertEqual(g.calls,[TOKEN_URL])

    def test_expired_apple_code_immediately_requests_explicit_sign_in(self):
        h=self.h;g=self.g;c=h.post(PREFIX+'challenge',dict(operation='1'*32)).json
        with patch.object(g.provider,'post',return_value=(400,b'{"error":"invalid_grant"}')) as post:
            r=h.post(PREFIX+'confirm',dict(operation=c['operation'],state=c['state'],
                identity_token=h.f.token(c),authorization_code='expired-code'))
        self.assertEqual(r.status_code,409,r.json);self.assertFalse(r.json['retry_saved_code']);post.assert_called_once()


if __name__=='__main__':unittest.main()
