"""First owner discovery with synthetic Apple/App Attest keys and signatures."""
import hashlib
import io
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from PIL import Image

from lite_account_identity import IdentityRejected
from lite_attest_http import InternalOwner
from lite_attest_state import StateUnavailable
from lite_usage_ledger import digest
from test_lite_signed_client import SignedServiceClient, b64
from test_lite_subscription_grace import rows
import test_lite_native_backend as native


class OwnerReviewTests(SignedServiceClient, unittest.TestCase):
    initialize = native.NativeBackendTests.initialize
    post = native.NativeBackendTests.post
    login = native.NativeBackendTests.login
    quota = native.NativeBackendTests.quota

    @classmethod
    def setUpClass(cls): native.NativeBackendTests.setUpClass()

    def setUp(self):
        self.initialize()
        self.until=self.h.clock()+600
        self.pin=hashlib.sha256(self.owner.encode()).hexdigest()
        self.client=self.backend.create_app(internal_owner=InternalOwner('native',self.pin,review_until=self.until)).test_client()
        self.op='c'*32

    def challenge(self):
        r=self.post('account/challenge',operation=self.op)
        self.assertEqual(r.status_code,200,r.json)
        self.c=r.json
        self.signed=self.h.identity.token(self.c)
        return self.c

    def submit(self,**changes):
        fields=dict(operation=self.op,identity_token=self.signed,state=self.c['state'],authorization_code='must-not-exchange')
        return self.post('account/confirm',**dict(fields,**changes))

    def read(self):
        return self.backend.identity.read_owner_review(self.owner,self.op,until=self.until)

    def no_business_effect(self,before):
        after=self.h.snapshot()
        self.assertEqual({k:v for k,v in before.items() if k!='identity_audit'},
                         {k:v for k,v in after.items() if k!='identity_audit'})
        self.assertEqual(self.h.grant.calls,[])
        self.assertEqual(self.h.kms.calls,[])
        self.assertEqual(self.h.model_calls,0)
        self.assertIsNone(self.backend.identity.existing_account_pin(self.owner))

    def test_verified_proposal_never_logs_in_or_touches_apple_grants_quota_model(self):
        self.challenge();before=self.h.snapshot()
        quota=rows(self.h.release.ledger.store)
        r=self.submit();self.assertEqual(r.status_code,202,r.json)
        self.assertEqual(r.json['error'],'lite_owner_review_pending')
        self.assertNotIn('account_sha256',r.json)
        proof=self.read()
        person=self.backend.identity.events.identity('apple-human-1')
        self.assertEqual(proof['account_sha256'],digest('apple-'+person))
        self.no_business_effect(before)
        self.assertEqual(rows(self.h.release.ledger.store),quota)
        text=repr(self.h.snapshot())
        for private in (self.signed,'must-not-exchange','apple-human-1'):
            self.assertNotIn(private,text)
        self.assertEqual(self.post('account/status').status_code,401)

    def test_same_confirmation_replays_one_immutable_proposal(self):
        self.challenge();self.assertEqual(self.submit().status_code,202)
        before=self.h.snapshot();self.h.release.n.a.now+=1
        self.assertEqual(self.submit().status_code,202)
        self.assertEqual(before,self.h.snapshot())

    def test_another_person_same_operation_cannot_replace_proposal(self):
        self.challenge();self.submit();old=self.read()
        signed=self.h.identity.token(self.c,sub='different-person')
        self.assertEqual(self.submit(identity_token=signed).status_code,503)
        self.assertEqual(old,self.read())

    def test_verified_proposal_does_not_enable_any_new_work(self):
        self.challenge();self.submit();before=self.h.snapshot()
        for path in ('service/prepare','purchase/context'):
            response=self.post(path,operation='e'*32)
            self.assertIn(response.status_code,(403,404),response.json)
        self.assertEqual(self.json_request('prepare').status_code,403)
        self.no_business_effect(before)

    def test_different_installation_never_reaches_apple_verification(self):
        self.client=self.backend.create_app(internal_owner=InternalOwner('native','a'*64,review_until=self.until)).test_client()
        with patch.object(self.backend.identity.verifier,'verify',side_effect=AssertionError('must not verify')):
            self.assertEqual(self.post('account/challenge',operation=self.op).status_code,403)

    def test_invalid_apple_claims_never_create_proposal(self):
        self.challenge();before=self.h.snapshot()
        for change in ({'nonce':'wrong'},{'aud':'wrong'},{'iss':'https://wrong.invalid'},
                       {'exp':self.h.clock()},{'iat':self.c['created_at']-31}):
            signed=self.h.identity.token(self.c,**change)
            with self.subTest(change=change):self.assertEqual(self.submit(identity_token=signed).status_code,401)
        self.assertEqual(before,self.h.snapshot())

    def test_wrong_state_never_verifies_token(self):
        self.challenge()
        with patch.object(self.backend.identity.verifier,'verify',side_effect=AssertionError('must not verify')):
            self.assertEqual(self.submit(state='wrong').status_code,401)

    def test_invalid_app_attest_never_reaches_proposal(self):
        response=self.client.post('/api/v1/lite/account/confirm',json={'anon_id':self.owner})
        self.assertIn(response.status_code,(400,401,403))
        with self.assertRaises(StateUnavailable):self.read()

    def test_closed_review_window_blocks_new_login_and_preserves_audit(self):
        self.challenge();self.submit();before=self.h.snapshot()
        self.h.release.n.a.now=self.until
        self.assertEqual(self.submit().status_code,403)
        with self.assertRaises(IdentityRejected):self.read()
        self.assertEqual(before,self.h.snapshot())

    def test_expired_apple_challenge_cannot_submit_but_captured_proof_is_readable(self):
        self.challenge();self.submit();before=self.read()
        self.h.release.n.a.now=self.c['expires_at']
        self.assertEqual(self.submit().status_code,401)
        self.assertEqual(before,self.read())

    def test_logout_invalidates_private_readback_without_erasing_audit(self):
        self.challenge();self.submit();before=rows(self.backend.identity.audit)
        self.assertEqual(self.post('account/logout',operation=self.op).status_code,200)
        with self.assertRaises(IdentityRejected):self.read()
        self.assertTrue(all(row in rows(self.backend.identity.audit) for row in before))

    def test_new_challenge_invalidates_old_proposal(self):
        self.challenge();self.submit()
        self.assertEqual(self.post('account/challenge',operation='d'*32).status_code,200)
        with self.assertRaises(IdentityRejected):self.read()

    def test_logout_while_apple_verifies_does_not_record_review(self):
        self.challenge();verify=self.backend.identity.verifier.verify
        def raced(*args):
            result=verify(*args);self.backend.identity.logout(self.owner,self.op);return result
        with patch.object(self.backend.identity.verifier,'verify',side_effect=raced):
            self.assertEqual(self.submit().status_code,401)
        self.assertIsNone(self.backend.identity.audit.load(self.backend.identity.owner_review_key(self.owner,self.op)))

    def test_lost_audit_ack_does_not_repeat_apple_exchange_or_create_account(self):
        self.challenge();audit=self.backend.identity.audit;real=audit.compare_exchange
        def lost(*args):real(*args);raise StateUnavailable('lost acknowledgment')
        with patch.object(audit,'compare_exchange',side_effect=lost):
            self.assertEqual(self.submit().status_code,503)
        before=self.h.snapshot();self.assertEqual(self.submit().status_code,202)
        self.assertEqual(before,self.h.snapshot());self.no_business_effect(before)

    def test_readback_rejects_changed_material_binding(self):
        self.challenge();self.submit()
        with patch.dict(self.backend.identity.config,identity_key_sha256='b'*64):
            with self.assertRaises(IdentityRejected):self.read()

    def test_review_cannot_be_extended_by_reusing_old_proof(self):
        self.challenge();self.submit()
        with self.assertRaises(IdentityRejected):
            self.backend.identity.read_owner_review(self.owner,self.op,until=self.until+1)

    def test_operator_pinned_mode_then_new_login_runs_once_and_debits_once(self):
        self.challenge();self.submit();proof=self.read()
        self.client=self.backend.create_app(internal_owner=InternalOwner('native',self.pin,proof['account_sha256'])).test_client()
        self.login();first=self.complete()
        self.assertEqual(self.run_request().json,first.json)
        self.assertEqual((self.h.model_calls,self.quota()['used']),(1,1))

    def test_lost_pending_response_cannot_exchange_old_code_after_activation(self):
        self.challenge();self.submit();proof=self.read()
        self.client=self.backend.create_app(internal_owner=InternalOwner('native',self.pin,proof['account_sha256'])).test_client()
        before=self.h.snapshot()
        response=self.submit()
        self.assertEqual(response.status_code,409,response.json)
        self.assertEqual(response.json['error'],'lite_apple_grant_recovery_required')
        self.assertTrue(response.json['explicit_sign_in_required'])
        self.assertFalse(response.json['retry_saved_code'])
        self.no_business_effect(before)

    def test_review_challenge_without_proposal_cannot_be_used_after_activation(self):
        self.challenge()
        person=self.backend.identity.events.identity('apple-human-1')
        self.client=self.backend.create_app(internal_owner=InternalOwner('native',self.pin,digest('apple-'+person))).test_client()
        before=self.h.snapshot()
        self.assertEqual(self.submit().status_code,409)
        self.no_business_effect(before)

    def test_failed_review_marker_never_returns_challenge(self):
        audit=self.backend.identity.audit;real=audit.compare_exchange
        def unavailable(k,*args):
            if k==self.backend.identity.owner_review_only_key(self.owner,self.op):
                raise StateUnavailable('cannot record mode')
            return real(k,*args)
        with patch.object(audit,'compare_exchange',side_effect=unavailable):
            self.assertEqual(self.post('account/challenge',operation=self.op).status_code,503)
        self.assertIsNone(self.backend.identity._read(self.owner))

    def test_wrong_operator_pin_cannot_admit_reviewed_person(self):
        self.challenge();self.submit()
        self.client=self.backend.create_app(internal_owner=InternalOwner('native',self.pin,'b'*64)).test_client()
        self.op='d'*32;self.challenge()
        self.assertEqual(self.submit().status_code,401)
        self.assertEqual(self.h.grant.calls,[])

    def test_prior_participant_keeps_old_results_and_deletion_rights(self):
        self.client=self.backend.create_app().test_client();self.login();self.complete()
        self.client=self.backend.create_app(internal_owner=InternalOwner('native','a'*64,review_until=self.until)).test_client()
        self.post('account/logout',operation=self.login_operation);self.login()
        self.assertEqual(self.json_request('status').status_code,200)
        self.assertEqual(self.json_request('prepare',2).status_code,403)
        self.assertEqual(self.post('account/deletion/request',operation='d'*32,confirmation='delete_account').status_code,202)

    def test_startup_rejects_expired_or_overlong_review_window(self):
        for deadline in (self.h.clock(),self.h.clock()+3601):
            with self.subTest(deadline=deadline),self.assertRaises(ValueError):
                self.backend.create_app(internal_owner=InternalOwner('native',self.pin,review_until=deadline))

    def test_audit_outage_never_reports_pending_success(self):
        self.challenge();before=self.h.snapshot()
        with patch.object(self.backend.identity.audit,'compare_exchange',side_effect=StateUnavailable('offline')):
            self.assertEqual(self.submit().status_code,503)
        self.assertEqual(before,self.h.snapshot())
        self.assertEqual(self.h.grant.calls,[])

    def test_owner_discovery_precedes_release_activation_and_does_not_activate(self):
        release=self.backend.identity.runtime.service_release
        before=rows(release.store),rows(self.h.release.ledger.store)
        with patch.object(release,'require_active',side_effect=StateUnavailable('not initialized')):
            self.challenge();self.assertEqual(self.submit().status_code,202)
            self.assertEqual(self.json_request('prepare').status_code,403)
            self.client=self.backend.create_app(internal_owner=InternalOwner('native',self.pin,self.read()['account_sha256'])).test_client()
            self.assertEqual(self.post('account/challenge',operation='d'*32).status_code,503)
        self.assertEqual(before,(rows(release.store),rows(self.h.release.ledger.store)))


class OwnerReviewConfigurationTests(unittest.TestCase):
    def test_requires_explicit_mode_and_exact_non_account_fields(self):
        fields=dict(LITE_INTERNAL_OWNER_MODE='owner-review',LITE_INTERNAL_INSTALLATION_SHA256='a'*64,
                    LITE_INTERNAL_REVIEW_UNTIL='1800000600')
        self.assertTrue(InternalOwner.configured(fields,'native').review_only)
        for delta in ({'LITE_INTERNAL_ACCOUNT_SHA256':'b'*64},{'LITE_INTERNAL_REVIEW_UNTIL':'0'},
                      {'LITE_INTERNAL_REVIEW_UNTIL':None},{'LITE_INTERNAL_OWNER_MODE':'owner-only'}):
            with self.subTest(delta=delta),self.assertRaises(ValueError):
                InternalOwner.configured(dict(fields,**delta),'native')
        with self.assertRaises(ValueError):InternalOwner.configured(fields,'research')
        self.assertIsNone(InternalOwner.configured({},'native'))

    def test_strict_owner_still_requires_account_pin(self):
        with self.assertRaises(ValueError):InternalOwner('native','a'*64)
        self.assertFalse(InternalOwner('native','a'*64,'b'*64).review_only)


class OwnerReviewBootstrapTests(SignedServiceClient, unittest.TestCase):
    post = native.NativeBackendTests.post
    login = native.NativeBackendTests.login
    quota = native.NativeBackendTests.quota

    @classmethod
    def setUpClass(cls): native.NativeBackendTests.setUpClass()

    def test_subscription_only_startup_review_then_quota_and_release_same_day(self):
        import socket
        from test_lite_fresh_bootstrap import Fixture
        for name in ('connect','connect_ex'):
            guard=patch.object(socket.socket,name,side_effect=AssertionError('synthetic only'))
            guard.start();self.addCleanup(guard.stop)
        bootstrap=Fixture(self);self.h=h=bootstrap.h
        # A policy definition is required by the real startup graph. The private
        # subscription step does not publish service or initialize user quotas.
        bootstrap.step('subscription')
        self.backend=h.compose();self.e=h.e;self.key=b64(self.e.key_id);self.counter=0;self.login_serial=300
        self.client=self.backend.create_app().test_client();self.register()
        pin=hashlib.sha256(self.owner.encode()).hexdigest();until=h.clock()+600
        self.client=self.backend.create_app(internal_owner=InternalOwner('native',pin,review_until=until)).test_client()
        # App Attest counters are deliberately shared with the synthetic device;
        # authenticated requests advance them. All prior business state stays put.
        original={k:rows(s) for k,s in bootstrap.original_stores.items() if k!='security'}
        before={k:rows(bootstrap.stores[k]) for k in ('accounts','links','quota','release','grants')}
        op='e'*32;c=self.post('account/challenge',operation=op)
        self.assertEqual(c.status_code,200,c.json)
        signed=h.identity.token(c.json)
        response=self.post('account/confirm',operation=op,state=c.json['state'],identity_token=signed,authorization_code='never-exchange')
        self.assertEqual(response.status_code,202,response.json)
        proof=self.backend.identity.read_owner_review(self.owner,op,until=until)
        self.assertEqual(before,{k:rows(bootstrap.stores[k]) for k in before})
        self.assertEqual(h.grant.calls,[])
        self.assertEqual(self.json_request('balance').status_code,403)
        bootstrap.step('quota');bootstrap.step('release')
        self.assertEqual(bootstrap.x.inspect()['status'],'complete')
        self.backend=h.compose()
        self.client=self.backend.create_app(internal_owner=InternalOwner('native',pin,proof['account_sha256'])).test_client()
        self.login()
        buf=io.BytesIO();Image.new('RGB',(64,64),(200,100,120)).save(buf,format='JPEG')
        self.f=SimpleNamespace(jpeg=buf.getvalue());self.image_hash=hashlib.sha256(self.f.jpeg).hexdigest()
        first=self.complete();self.assertEqual(first.json,self.run_request().json)
        self.assertEqual((h.model_calls,self.quota()['used']),(1,1))
        self.assertEqual(original,{k:rows(s) for k,s in bootstrap.original_stores.items() if k!='security'})
