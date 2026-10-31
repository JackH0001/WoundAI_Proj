"""Bounded real-provider transport retries with synthetic Apple replies only."""
import ssl
import threading
import unittest
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from lite_apple_oauth import GrantRejected, GrantUncertain, GrantTransportUncertain, post_apple_form, TOKEN_URL
from lite_attest_state import StateUnavailable
from lite_account_identity import IdentityRejected
from lite_subscription_executor import key
from lite_usage_ledger import digest
from test_lite_account_sessions import RenewalFixture
from test_lite_apple_grants import GrantFixture
from test_lite_subscription_grace import rows
import test_lite_native_purchase as native


class RefreshRetryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): native.legacy.LinksTests.setUpClass()
    def setUp(self):
        self.f = RenewalFixture(self); self.f.advance(86401)
        self.wait = patch('lite_apple_grants.time.sleep'); self.sleep = self.wait.start()
        self.addCleanup(self.wait.stop)
    def flaky(self, errors):
        f=self.f; real=f.g.provider.post; calls=[]
        def post(url, fields):
            calls.append(fields['grant_type'])
            if len(calls)<=len(errors): raise errors[len(calls)-1]
            return real(url, fields)
        return patch.object(f.g.provider,'post',side_effect=post),calls
    def test_one_timeout_recovers_same_family_no_model_or_quota_mutation(self):
        f=self.f;before=rows(f.f.h.accounts.store),rows(f.f.h.f.ledger.store)
        mock,calls=self.flaky([TimeoutError('private-token')])
        with mock:r=f.renew()
        self.assertEqual(calls,['refresh_token']*2);self.sleep.assert_called_once_with(1)
        self.assertEqual(r['account_sha256'],f.initial['account_sha256'])
        self.assertEqual(r['renewal_generation'],1)
        self.assertEqual(before,(rows(f.f.h.accounts.store),rows(f.f.h.f.ledger.store)))
        self.assertEqual(f.b.status(f.c['installation']),r)
    def test_two_connection_failures_then_recovery(self):
        mock,calls=self.flaky([ConnectionResetError(),urllib.error.URLError(TimeoutError())])
        with mock:self.f.renew()
        self.assertEqual(len(calls),3);self.assertEqual([x.args for x in self.sleep.call_args_list],[(1,),(2,)])
    def test_exhaustion_does_not_reset_budget_on_next_request_or_new_authority(self):
        f=self.f;mock,calls=self.flaky([TimeoutError()]*4)
        with mock:
            with self.assertRaises(GrantUncertain):f.renew()
            with self.assertRaises(GrantRejected):f.renew()
            f.g.a=f.g.clone();f.b.grants=f.g.a
            with self.assertRaises(GrantRejected):f.renew()
        self.assertEqual(len(calls),3)
    def test_next_same_day_renewal_and_receipt_replay_do_not_call_apple(self):
        f=self.f;mock,calls=self.flaky([TimeoutError()])
        with mock:
            r=f.renew();self.assertEqual(r,f.renew());f.advance(901)
            f.renew(op='b'*32,generation=1)
        self.assertEqual(len(calls),2)
    def test_unclassified_error_is_not_retried(self):
        for error in (GrantUncertain('unknown'),OSError('other'),ssl.SSLCertVerificationError('bad certificate')):
            f=RenewalFixture(self);f.advance(86401)
            with patch.object(f.g.provider,'post',side_effect=error) as p:
                with self.assertRaises(GrantUncertain):f.renew()
                self.assertEqual(p.call_count,1)
    def test_invalid_grant_after_timeout_does_not_retry_again(self):
        mock,calls=self.flaky([TimeoutError(),GrantRejected('revoked')])
        with mock,self.assertRaises(GrantRejected):self.f.renew()
        self.assertEqual(len(calls),2)
    def test_malformed_success_is_not_retried(self):
        with patch.object(self.f.g.provider,'post',return_value=(200,b'{}')) as p:
            with self.assertRaises(GrantUncertain):self.f.renew()
            self.assertEqual(p.call_count,1)
    def test_wrong_signed_subject_is_not_retried(self):
        f=self.f;f.g.token=f.f.token(f.c,sub='wrong-human')
        with patch.object(f.g.provider,'post',wraps=f.g.provider.post) as p:
            with self.assertRaises(GrantRejected):f.renew()
            self.assertEqual(p.call_count,1)
    def test_code_exchange_never_retries_timeout(self):
        g=GrantFixture(self);g.prepare()
        with patch.object(g.provider,'post',side_effect=TimeoutError('secret')) as p:
            with self.assertRaises(GrantUncertain):g.capture()
            with self.assertRaises(GrantUncertain):g.capture()
            self.assertEqual(p.call_count,1)
    def test_retry_attempts_are_audited_without_credentials(self):
        f=self.f;mock,calls=self.flaky([TimeoutError('private-token')])
        with mock:f.renew()
        audit=rows(f.g.audit);raw=repr(audit)
        identity=f.g.a._id(f.c['installation'],f.c['operation'])
        for attempt in (1,2):
            event_id=digest([identity,f.f.now(),attempt])
            self.assertIsNotNone(f.g.audit.load(key('apple-refresh-attempt',event_id)))
            self.assertEqual(f.g.audit.load(key('apple-refresh-transport-failure',event_id)) is not None,attempt==1)
        for s in ('private-token','private-access-token','private-refresh-token',f.g.token):self.assertNotIn(s,raw)
    def test_failure_to_save_retry_intent_stops_before_second_post(self):
        f=self.f;mock,calls=self.flaky([TimeoutError()]);real=f.g.audit.compare_exchange
        identity=f.g.a._id(f.c['installation'],f.c['operation'])
        second=key('apple-refresh-attempt',digest([identity,f.f.now(),2]))
        def reject(k,revision,v):
            if k==second:raise StateUnavailable('audit down')
            return real(k,revision,v)
        with mock,patch.object(f.g.audit,'compare_exchange',side_effect=reject),self.assertRaises(GrantUncertain):f.renew()
        self.assertEqual(len(calls),1)
    def test_revoke_during_backoff_stops_retry_and_does_not_claim_complete(self):
        f=self.f;mock,calls=self.flaky([TimeoutError()])
        def revoke(_):
            with self.assertRaises(GrantUncertain):f.g.revoke()
        self.sleep.side_effect=revoke
        with mock,self.assertRaises(GrantUncertain):f.renew()
        self.assertEqual(len(calls),1)
        self.assertIsNotNone(f.g.store.load(f.g.grant_key())[1]['envelope'])
    def test_lost_process_keeps_durable_claim_no_retry_reclamation(self):
        f=self.f;mock,calls=self.flaky([TimeoutError()]);self.sleep.side_effect=KeyboardInterrupt()
        with mock:
            with self.assertRaises(KeyboardInterrupt):f.renew()
            with self.assertRaises(StateUnavailable):f.renew()
            f.advance(120)
            with self.assertRaises(GrantUncertain):f.renew()
        self.assertEqual(len(calls),1)
    def test_concurrent_caller_cannot_start_its_own_retry_loop(self):
        f=self.f;mock,calls=self.flaky([TimeoutError()]);begun=threading.Event();done=threading.Event()
        def wait(_):begun.set();self.assertTrue(done.wait(5))
        self.sleep.side_effect=wait
        with mock,ThreadPoolExecutor(2) as pool:
            future=pool.submit(f.renew);self.assertTrue(begun.wait(5))
            try:
                with self.assertRaises(StateUnavailable):f.renew()
            finally:done.set()
            future.result()
        self.assertEqual(len(calls),2)
    def test_claim_expiry_stops_before_second_dispatch(self):
        mock,calls=self.flaky([TimeoutError()]);self.sleep.side_effect=lambda _:self.f.advance(120)
        with mock,self.assertRaises(GrantUncertain):self.f.renew()
        self.assertEqual(len(calls),1)
    def test_logout_during_backoff_does_not_restore_session(self):
        f=self.f;mock,calls=self.flaky([TimeoutError()])
        self.sleep.side_effect=lambda _:f.b.logout(f.c['installation'],f.c['operation'])
        with mock,self.assertRaises(IdentityRejected):f.renew()
        with self.assertRaises(IdentityRejected):f.b.status(f.c['installation'])


class TransportClassificationTests(unittest.TestCase):
    def test_real_transport_marks_timeout_but_not_certificate_failure(self):
        for error,expected in [(TimeoutError('secret'),GrantTransportUncertain),
            (urllib.error.URLError(TimeoutError('secret')),GrantTransportUncertain),
            (urllib.error.URLError(ssl.SSLCertVerificationError('secret')),GrantUncertain)]:
            with patch('lite_apple_oauth.urllib.request.build_opener') as p:
                p.return_value.open.side_effect=error
                with self.assertRaises(expected) as e:post_apple_form(TOKEN_URL,{})
                self.assertNotIn('secret',str(e.exception))
                if expected is GrantUncertain:self.assertNotIsInstance(e.exception,GrantTransportUncertain)

if __name__=='__main__':unittest.main()
