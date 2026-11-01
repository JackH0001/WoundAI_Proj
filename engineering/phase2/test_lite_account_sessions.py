"""Synthetic signed Apple renewal and real CAS races; no live Apple/GCS."""
import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import jwt
from lite_account_identity import AccountIdentityBridge, IdentityRejected, PREFIX
from lite_apple_account_events import AppleAccountEvents
from lite_apple_oauth import TOKEN_URL, GrantRejected, GrantUncertain
from lite_attest_state import SQLiteStateStore, StateUnavailable
from lite_subscription_executor import key
from lite_usage_ledger import digest
from test_lite_apple_grants import GrantFixture
from test_lite_subscription_grace import rows
import test_lite_native_purchase as native


class RenewalFixture:
    def __init__(self, host):
        self.g=GrantFixture(host);self.f=self.g.f;b=self.f.b
        self.events=AppleAccountEvents(verifier=b.verifier,store=SQLiteStateStore(self.f.h.f.root/'events.sqlite'),
            audit_store=SQLiteStateStore(self.f.h.f.root/'events-audit.sqlite'),namespace=b.namespace,identity_key=b.secret)
        self.f.b=AccountIdentityBridge(runtime=b.runtime,verifier=b.verifier,store=b.store,audit_store=b.audit,
            namespace=b.namespace,identity_key=b.secret,events=self.events,grants=self.g.a,renewal=True)
        self.b=self.f.b;self.g.prepare();self.c=self.g.c;self.initial=self.g.confirm()
    def advance(self,n):
        self.f.h.a.now+=n;self.f.verifier.fetched_at=self.f.now()-1;self.f.verifier.expires_at=self.f.now()+3599
        self.g.token=self.f.token(self.c)
    def renew(self,op='a'*32,generation=0):return self.b.renew(self.c['installation'],self.c['operation'],op,generation)
    def event(self):
        signed=jwt.encode(dict(iss=self.f.verifier.issuer,aud=self.f.verifier.audience,iat=self.f.now(),jti='revoked',
            events=dict(type='consent-revoked',sub='apple-human-1',event_time=self.f.now())),self.f.private,
            algorithm='RS256',headers={'kid':'synthetic-rsa'})
        return self.events.accept(signed)
    def family(self):return self.b.renewals._key(self.c['installation'],self.c['operation'])


class RenewalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):native.legacy.LinksTests.setUpClass()
    def setUp(self):self.f=RenewalFixture(self)
    def test_expired_session_renews_without_prompt_model_quota_or_registry_changes(self):
        f=self.f;before=rows(f.f.h.accounts.store),rows(f.f.h.f.ledger.store)
        f.advance(901)
        with self.assertRaises(IdentityRejected):f.b.status(f.c['installation'])
        r=f.renew();self.assertEqual(r,f.b.status(f.c['installation']));self.assertEqual(r['renewal_generation'],1)
        self.assertEqual(r['account_sha256'],f.initial['account_sha256']);self.assertEqual(f.g.calls,[TOKEN_URL])
        self.assertEqual(before,(rows(f.f.h.accounts.store),rows(f.f.h.f.ledger.store)))
    def test_replay_returns_identical_expiry_not_another_lease(self):
        f=self.f;f.advance(901);r=f.renew();f.advance(40);before=rows(f.b.store),rows(f.b.audit)
        self.assertEqual(f.renew(),r);self.assertEqual(before,(rows(f.b.store),rows(f.b.audit)))
    def test_same_operation_cannot_change_generation(self):
        f=self.f;f.advance(901);f.renew()
        with self.assertRaises(StateUnavailable):f.renew(generation=1)
    def test_new_operation_with_stale_generation_cannot_extend(self):
        f=self.f;f.advance(901);f.renew();f.advance(901)
        with self.assertRaises(IdentityRejected):f.renew(op='b'*32)
        self.assertEqual(f.renew(op='c'*32,generation=1)['renewal_generation'],2)
    def test_expired_replay_does_not_reauthorize(self):
        f=self.f;f.advance(901);r=f.renew();f.advance(901);self.assertEqual(f.renew(),r)
        with self.assertRaises(IdentityRejected):f.b.status(f.c['installation'])
    def test_fresh_session_cannot_renew_early(self):
        with self.assertRaises(IdentityRejected):self.f.renew()
    def test_bounded_family_requires_explicit_signin(self):
        f=self.f;f.advance(30*86400)
        with self.assertRaises(IdentityRejected):f.renew()
        self.assertEqual(f.g.calls,[TOKEN_URL])
    def test_logout_blocks_renewal_and_old_receipt(self):
        f=self.f;f.advance(901);f.renew();f.b.logout(f.c['installation'],f.c['operation'])
        with self.assertRaises(IdentityRejected):f.renew()
    def test_new_login_blocks_old_family(self):
        f=self.f;f.advance(901);f.f.challenge()
        with self.assertRaises(IdentityRejected):f.renew()
    def test_other_device_cannot_use_saved_family(self):
        f=self.f;f.advance(901)
        with self.assertRaises(IdentityRejected):f.b.renew('other',f.c['operation'],'a'*32,0)
    def test_apple_event_fences_original_authorization_after_renewal(self):
        f=self.f;f.advance(901);f.renew();f.advance(1);f.event();f.advance(901)
        with self.assertRaises(IdentityRejected):f.renew(op='b'*32,generation=1)
    def test_revoked_grant_blocks_renewal(self):
        f=self.f;f.advance(901);f.g.revoke()
        with self.assertRaises(IdentityRejected):f.renew()
    def test_durable_policy_prevents_disabling_renewal(self):
        f=self.f;b=f.b
        with self.assertRaises(StateUnavailable):AccountIdentityBridge(runtime=b.runtime,verifier=b.verifier,
            store=b.store,audit_store=b.audit,namespace=b.namespace,identity_key=b.secret,events=f.events,grants=f.g.a)
    def test_already_running_old_bridge_cannot_bypass_marker(self):
        f=self.f;f.b.renewals=None
        with self.assertRaises(StateUnavailable):f.b.require_session(f.c['installation'])
    def test_no_renewal_without_events(self):
        f=self.f;b=f.b
        with self.assertRaises((ValueError,StateUnavailable)):AccountIdentityBridge(runtime=b.runtime,verifier=b.verifier,
            store=b.store,audit_store=b.audit,namespace=b.namespace,identity_key=b.secret,grants=f.g.a,renewal=True)
    def test_two_instances_same_operation_commit_once(self):
        f=self.f;f.advance(901)
        with ThreadPoolExecutor(2) as pool:
            results=list(pool.map(lambda _:f.renew(),range(2)))
        self.assertEqual(results[0],results[1]);self.assertEqual(results[0]['renewal_generation'],1)
    def test_different_concurrent_operations_only_one_wins(self):
        f=self.f;f.advance(901)
        def run(op):
            try:return f.renew(op)
            except IdentityRejected:return None
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(run,['a'*32,'b'*32]))
        self.assertEqual(sum(r is not None for r in results),1)
    def test_cas_reply_loss_readback_preserves_same_expiry(self):
        f=self.f;f.advance(901);real=f.b.store.compare_exchange
        def lose(k,rev,value):
            result=real(k,rev,value)
            if k==f.family():raise StateUnavailable('lost')
            return result
        with patch.object(f.b.store,'compare_exchange',side_effect=lose):r=f.renew()
        self.assertEqual(f.renew(),r)
    def test_write_before_commit_failure_can_retry_same_intent(self):
        f=self.f;f.advance(901);real=f.b.store.compare_exchange
        def fail(k,rev,value):
            if k==f.family():raise StateUnavailable('offline')
            return real(k,rev,value)
        with patch.object(f.b.store,'compare_exchange',side_effect=fail),self.assertRaises(StateUnavailable):f.renew()
        self.assertEqual(f.renew()['renewal_generation'],1)
    def test_audit_receipt_failure_never_re_rotates(self):
        f=self.f;f.advance(901);real=f.b.audit.compare_exchange
        def fail(k,rev,value):
            if value.get('renewal_generation')==1:raise StateUnavailable('audit')
            return real(k,rev,value)
        with patch.object(f.b.audit,'compare_exchange',side_effect=fail),self.assertRaises(StateUnavailable):f.renew()
        self.assertEqual(f.renew()['renewal_generation'],1)
    def test_logout_during_family_commit_blocks_late_response(self):
        f=self.f;f.advance(901);real=f.b.store.compare_exchange
        def race(k,rev,value):
            result=real(k,rev,value)
            if k==f.family():f.b.logout(f.c['installation'],f.c['operation'])
            return result
        with patch.object(f.b.store,'compare_exchange',side_effect=race),self.assertRaises(IdentityRejected):f.renew()
    def test_corrupt_family_not_reset(self):
        f=self.f;rev,r=f.b.store.load(f.family());f.b.store.compare_exchange(f.family(),rev,dict(r,renewal_generation=True))
        with self.assertRaises(StateUnavailable):f.renew()
    def test_new_day_validates_apple_once_and_preserves_original_issued_at(self):
        f=self.f;f.advance(86401);r=f.renew();f.advance(901);f.renew(op='b'*32,generation=1)
        self.assertEqual(f.g.calls.count(TOKEN_URL),2)
        proof=f.b.audit.load(f.b._authorization_key(f.c['installation'],f.c['operation']))[1]
        self.assertEqual(proof['issued_at'],f.c['created_at']);self.assertEqual(r['account_sha256'],f.initial['account_sha256'])
    def test_new_day_apple_rejection_blocks_existing_and_future_renewal(self):
        f=self.f;f.advance(86401)
        with patch.object(f.g.provider,'refresh',side_effect=GrantRejected('revoked')) as call:
            with self.assertRaises(GrantRejected):f.renew()
            with self.assertRaises(GrantRejected):f.renew()
            self.assertEqual(call.call_count,1)
    def test_unknown_refresh_not_replayed_and_ciphertext_preserved(self):
        f=self.f;f.advance(86401);before=f.g.store.load(f.g.grant_key())
        with patch.object(f.g.provider,'refresh',side_effect=GrantUncertain('lost')) as call:
            with self.assertRaises(GrantUncertain):f.renew()
            with self.assertRaises(GrantRejected):f.renew()
            self.assertEqual(call.call_count,1)
        self.assertEqual(f.g.store.load(f.g.grant_key()),before)
    def test_daily_concurrent_validation_dispatches_once(self):
        f=self.f;f.advance(86401);start=threading.Event();finish=threading.Event()
        def hook(url):start.set();self.assertTrue(finish.wait(5))
        f.g.hook=hook
        with ThreadPoolExecutor(2) as pool:
            first=pool.submit(f.renew);self.assertTrue(start.wait(5))
            try:
                with self.assertRaises(StateUnavailable):f.renew()
            finally:finish.set()
            first.result()
        self.assertEqual(f.renew()['renewal_generation'],1);self.assertEqual(f.g.calls.count(TOKEN_URL),2)
    def test_logout_during_apple_refresh_cannot_restore_service(self):
        f=self.f;f.advance(86401);f.g.hook=lambda _:f.b.logout(f.c['installation'],f.c['operation'])
        with self.assertRaises(IdentityRejected):f.renew()
    def test_event_during_apple_refresh_cannot_restore_service(self):
        f=self.f;f.advance(86401);f.g.hook=lambda _:f.event()
        with self.assertRaises(IdentityRejected):f.renew()
    def test_revocation_during_refresh_waits_for_late_credential(self):
        f=self.f;f.advance(86401)
        def hook(url):
            if url==TOKEN_URL:
                with self.assertRaises(GrantUncertain):f.g.revoke()
        f.g.hook=hook
        with self.assertRaises(IdentityRejected):f.renew()
        self.assertEqual(f.g.store.load(f.g.grant_key())[1]['phase'],'ready')
        f.g.hook=lambda _:None
        self.assertEqual(f.g.revoke()['state'],'apple_token_revoked')
        self.assertEqual(f.g.store.load(f.g.grant_key())[1]['phase'],'revoked')
    def test_wrong_refresh_subject_cannot_authorize(self):
        f=self.f;f.advance(86401);f.g.token=f.f.token(f.c,sub='wrong')
        with self.assertRaises(GrantRejected):f.renew()
    def test_refresh_without_nonce_is_valid_when_subject_is_bound(self):
        f=self.f;f.advance(86401);claims=jwt.decode(f.g.token,options={'verify_signature':False});claims.pop('nonce')
        f.g.token=jwt.encode(claims,f.f.private,algorithm='RS256',headers={'kid':'synthetic-rsa'})
        self.assertEqual(f.renew()['renewal_generation'],1)
    def test_refresh_wrong_nonce_is_rejected(self):
        f=self.f;f.advance(86401);f.g.token=f.f.token(f.c,nonce='wrong')
        with self.assertRaises(GrantRejected):f.renew()
    def test_stalled_validation_becomes_explicit_recovery_after_bounded_wait(self):
        f=self.f;f.advance(86401);identity=f.g.a._id(f.c['installation'],f.c['operation'])
        f.g.store.compare_exchange(key('apple-grant-validation',identity),0,dict(v=1,grant_id=identity,phase='validating',checked_at=f.f.now()))
        with self.assertRaises(StateUnavailable):f.renew()
        f.advance(120)
        with self.assertRaises(GrantUncertain):f.renew()
        self.assertEqual(f.g.calls,[TOKEN_URL])
    def test_lease_stops_at_daily_validation_boundary(self):
        f=self.f;f.advance(86399);r=f.renew();self.assertEqual(r['expires_at'],f.c['created_at']+86400)
        f.advance(2);self.assertEqual(f.renew(op='b'*32,generation=1)['renewal_generation'],2)
        self.assertEqual(f.g.calls.count(TOKEN_URL),2)
    def test_refreshing_key_provider_supported_after_key_cache_expiry(self):
        from lite_apple_identity_provider import RefreshingAppleIdentityVerifier
        f=self.f;f.advance(86401)
        verifier=RefreshingAppleIdentityVerifier(audience=f.f.verifier.audience,fetch=lambda:json.dumps(f.f.jwks).encode(),
            clock=f.f.now,monotonic=lambda:float(f.f.now()))
        f.g.a.verifier=verifier
        self.assertEqual(f.renew()['renewal_generation'],1);self.assertEqual(verifier.health()['fetches'],1)

    def test_invalid_expected_generations(self):
        for n in [True,-1,10000,1.2,'0']:
            with self.subTest(n=n),self.assertRaises(IdentityRejected):self.f.renew(generation=n)

class RenewalHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):native.legacy.LinksTests.setUpClass()
    def setUp(self):
        from test_lite_apple_grants import GrantHTTPTests
        self.base=GrantHTTPTests();self.base.setUp();self.addCleanup(self.base.doCleanups)
        self.h=self.base.h;self.g=self.base.g;b=self.base.bridge
        events=AppleAccountEvents(verifier=b.verifier,store=SQLiteStateStore(self.h.f.h.f.root/'events.sqlite'),
            audit_store=SQLiteStateStore(self.h.f.h.f.root/'events-audit.sqlite'),namespace=b.namespace,identity_key=b.secret)
        b.__init__(runtime=b.runtime,verifier=b.verifier,store=b.store,audit_store=b.audit,
            namespace=b.namespace,identity_key=b.secret,grants=self.g.a,events=events,renewal=True)
        self.h.f.b=b;self.b=b
        self.c=self.h.post(PREFIX+'challenge',dict(operation='1'*32)).json
        self.g.token=self.h.f.token(self.c)
        r=self.h.post(PREFIX+'confirm',dict(operation='1'*32,state=self.c['state'],identity_token=self.g.token,authorization_code='synthetic-code'))
        assert r.status_code==200,r.json
        self.h.f.h.a.now+=901
        self.body=dict(operation='1'*32,renewal_operation='a'*32,expected_generation=0)
    def test_expired_attested_session_can_renew_and_replay_without_quota_or_research_changes(self):
        h=self.h;before=rows(h.h.privacy.store),rows(h.h.ledger.store)
        self.assertEqual(h.post(PREFIX+'status',{}).status_code,401)
        first=h.post(PREFIX+'renew',self.body);self.assertEqual(first.status_code,200,first.json)
        self.assertEqual(h.post(PREFIX+'renew',self.body).json,first.json)
        self.assertEqual(h.post(PREFIX+'status',{}).json,first.json)
        self.assertEqual(before,(rows(h.h.privacy.store),rows(h.h.ledger.store)));self.assertEqual(h.h.model_calls,0)
        self.assertEqual(first.headers['Cache-Control'],'no-store')
    def test_unsigned_owner_spoof_and_extra_fields_cannot_renew(self):
        h=self.h;self.assertEqual(h.h.client.post(PREFIX+'renew',json=self.body).status_code,401)
        self.assertEqual(h.post(PREFIX+'renew',dict(self.body,anon_id='other')).status_code,403)
        self.assertEqual(h.post(PREFIX+'renew',dict(self.body,subject='other')).status_code,401)
    def test_logout_blocks_delayed_http_renewal(self):
        h=self.h;self.assertEqual(h.post(PREFIX+'logout',dict(operation='1'*32)).status_code,200)
        self.assertEqual(h.post(PREFIX+'renew',self.body).status_code,401)
    def test_apple_unknown_refresh_maps_to_explicit_recovery(self):
        h=self.h;h.f.h.a.now+=86400
        h.f.verifier.fetched_at=h.f.now()-1;h.f.verifier.expires_at=h.f.now()+3599
        with patch.object(self.g.provider,'refresh',side_effect=GrantUncertain('lost')):
            r=h.post(PREFIX+'renew',self.body)
        self.assertEqual(r.status_code,409,r.json);self.assertTrue(r.json['explicit_sign_in_required'])

if __name__=='__main__':unittest.main()
