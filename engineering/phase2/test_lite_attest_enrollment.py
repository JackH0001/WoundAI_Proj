"""Real crypto with private synthetic roots + durable enrollment transactions.

Root/time substitutions below are test-only. No Apple, GCS or credentials used.
"""
import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import cbor2
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'Backend/Flask'))
import lite_attest_enrollment as enrollment
import lite_attest_registration as registration
import lite_attest_receipt as receipt
from lite_attest_state import SQLiteStateStore, GCSStateStore, AdmissionRejected, StateUnavailable, CHALLENGE_TTL
from lite_attest_assertion import AssertionRejected, CATEGORY, VERSION
from lite_attest_request import client_data
import test_lite_attest_registration as registration_fixtures
import test_lite_attest_receipt as receipt_fixtures
from test_lite_attest_state import FakeBucket


class EnrollmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'security.sqlite'
        self.store = SQLiteStateStore(self.path)
        self.now = int(registration_fixtures.NOW.timestamp())
        self.r = registration_fixtures.RegistrationTests(); self.r.setUp()
        self.cms = receipt_fixtures.ReceiptTests(); self.cms.setUp()
        self.key_id = self.r.key_id
        self.service = self.engine(self.store)
        self.challenge = self.service.issue_challenge(self.key_id)
        self.encoded = self.make_proof()
        # Invoke the full verifiers, injecting ONLY test trust roots and time.
        def verify_attestation(encoded, **kwargs):
            return registration._verify_with_root(encoded, **kwargs, root=self.r.root, now=registration_fixtures.NOW)
        def verify_receipt(attested, *, app_id, challenge):
            return receipt._verify_with_root(attested.receipt, app_id=app_id, attested=attested,
                        client_hash=hashlib.sha256(challenge).digest(), root=self.cms.root, now=receipt_fixtures.NOW)
        self.attest_patch = patch.object(enrollment, 'verify_attestation', side_effect=verify_attestation)
        self.receipt_patch = patch.object(enrollment, 'verify_registration_receipt', side_effect=verify_receipt)
        self.attest_verify = self.attest_patch.start(); self.addCleanup(self.attest_patch.stop)
        self.receipt_verify = self.receipt_patch.start(); self.addCleanup(self.receipt_patch.stop)

    def engine(self, store):
        return enrollment.RegistrationService(store, policy=self.r.policy, audience='lite-test', clock=lambda:self.now)

    def make_proof(self, *, challenge=None, wrong_receipt=False):
        self.r.challenge = challenge or self.challenge.nonce
        obj = self.r.object()
        fields = self.cms.fields()
        fields[3] = obj['attStmt']['x5c'][0]
        fields[4] = hashlib.sha256(self.r.challenge).digest()
        if wrong_receipt: fields[2] = b'ABCDEFGHIJ.com.another.app'
        obj['attStmt']['receipt'] = self.cms.signed(self.cms.payload(fields))
        return cbor2.dumps(obj)

    def register(self, **kwargs):
        args = dict(key_id=self.key_id, challenge_id=self.challenge.identifier, encoded=self.encoded)
        args.update(kwargs); return self.service.register(**args)

    def test_full_crypto_registration_then_signed_request(self):
        before = self.store.load(self.key_id)
        result = self.register()
        revision, state = self.store.load(self.key_id)
        self.assertEqual(revision, before[0]+1)
        self.assertEqual(state['status'], 'active')
        self.assertEqual(result.installation, before[1]['installation'])
        self.assertNotIn('nonce', state)
        self.assertEqual(state['counter'], 0)
        self.assertEqual(self.attest_verify.call_args.kwargs['challenge'], self.challenge.nonce)
        self.assertEqual(self.receipt_verify.call_args.kwargs['challenge'], self.challenge.nonce)
        challenge = self.service.admission.issue_challenge(self.key_id)
        request = dict(request_id='11111111-1111-4111-8111-111111111111', method='POST',
                       path='/api/v1/lite/segment', content_type='application/json', body=b'{}')
        data = client_data(audience='lite-test', installation=result.installation,
                          key_id=self.key_id, challenge=challenge.nonce, **request)
        auth = hashlib.sha256(self.r.policy.app_id.encode()).digest()+b'\x80'+(1).to_bytes(4,'big')
        auth += cbor2.dumps({CATEGORY:2, VERSION:'33'})
        signature = self.r.key.sign(hashlib.sha256(auth+hashlib.sha256(data).digest()).digest(), ec.ECDSA(hashes.SHA256()))
        assertion = cbor2.dumps(dict(signature=signature, authenticatorData=auth))
        accepted = self.service.admission.admit(self.key_id, challenge.identifier, assertion, **request)
        self.assertEqual(accepted.installation, result.installation)
        self.assertEqual(accepted.counter, 1)

    def test_idempotent_retry_keeps_counter_challenges_and_owner(self):
        result = self.register()
        rev, state = self.store.load(self.key_id); state['counter'] = 41
        self.assertTrue(self.store.compare_exchange(self.key_id, rev, state))
        challenge = self.service.admission.issue_challenge(self.key_id)
        before = self.store.load(self.key_id)
        self.assertEqual(self.register(), result)
        self.assertEqual(self.store.load(self.key_id), before)
        self.assertEqual(len(before[1]['challenges']), 1)
        self.assertEqual(self.attest_verify.call_count, 1)
        self.assertIn(challenge.identifier, before[1]['challenges'])

    def test_retry_cannot_change_proof_or_challenge(self):
        self.register(); before = self.store.load(self.key_id)
        for args in [dict(encoded=self.encoded+b'0'),dict(challenge_id='f'*32)]:
            with self.subTest(args=list(args)), self.assertRaises(AdmissionRejected): self.register(**args)
        self.assertEqual(before, self.store.load(self.key_id))

    def test_active_or_revoked_key_cannot_issue_registration_again(self):
        self.register()
        with self.assertRaises(AdmissionRejected): self.service.issue_challenge(self.key_id)
        self.service.admission.revoke(self.key_id)
        before = self.store.load(self.key_id)
        with self.assertRaises(AdmissionRejected): self.register()
        with self.assertRaises(AdmissionRejected): self.service.issue_challenge(self.key_id)
        self.assertEqual(before, self.store.load(self.key_id))

    def test_pending_challenge_is_reused_without_resetting_time_or_owner(self):
        before = self.store.load(self.key_id); self.now += 30
        self.assertEqual(self.service.issue_challenge(self.key_id),self.challenge)
        self.assertEqual(before,self.store.load(self.key_id))

    def test_expired_challenge_rotates_and_old_attestation_is_rejected(self):
        self.now += CHALLENGE_TTL
        with self.assertRaises(AdmissionRejected): self.register()
        new = self.service.issue_challenge(self.key_id)
        self.assertNotEqual(new.nonce,self.challenge.nonce)
        with self.assertRaises(AdmissionRejected): self.register()
        with self.assertRaises(AssertionRejected): self.register(challenge_id=new.identifier)
        self.assertEqual(self.store.load(self.key_id)[1]['status'],'pending')

    def test_expiry_during_receipt_verification_does_not_activate(self):
        original = self.receipt_verify.side_effect
        def late(*args,**kwargs):
            result=original(*args,**kwargs);self.now += CHALLENGE_TTL;return result
        self.receipt_verify.side_effect = late
        before = self.store.load(self.key_id)
        with self.assertRaises(AdmissionRejected): self.register()
        self.assertEqual(before,self.store.load(self.key_id))

    def test_invalid_attestation_and_invalid_receipt_leave_pending(self):
        before = self.store.load(self.key_id)
        for data in (b'invalid',self.make_proof(wrong_receipt=True)):
            with self.subTest(length=len(data)),self.assertRaises(AssertionRejected): self.register(encoded=data)
            self.assertEqual(before,self.store.load(self.key_id))

    def test_wrong_key_or_challenge_or_unissued_key(self):
        with self.assertRaises(AdmissionRejected):self.register(key_id=b'x'*32)
        with self.assertRaises(AdmissionRejected):self.register(challenge_id='a'*32)
        other=b'y'*32;challenge=self.service.issue_challenge(other)
        with self.assertRaises(AssertionRejected):self.register(key_id=other,challenge_id=challenge.identifier)

    def test_parallel_identical_registration_commits_only_once(self):
        before=self.store.load(self.key_id); barrier=threading.Barrier(8)
        def work(_):
            engine=self.engine(SQLiteStateStore(self.path));barrier.wait(5)
            return engine.register(self.key_id,self.challenge.identifier,self.encoded)
        with ThreadPoolExecutor(8) as pool:results=list(pool.map(work,range(8)))
        self.assertEqual(len(set(results)),1)
        self.assertEqual(self.store.load(self.key_id)[0],before[0]+1)

    def test_parallel_different_valid_proofs_only_one_is_registered(self):
        other=self.make_proof();barrier=threading.Barrier(2)
        def work(data):
            barrier.wait(5)
            try:self.register(encoded=data);return 'ok'
            except AdmissionRejected:return 'rejected'
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(work,[self.encoded,other]))
        self.assertCountEqual(results,['ok','rejected'])

    def test_process_restart_keeps_owner_and_consumed_registration(self):
        result=self.register()
        restarted=self.engine(SQLiteStateStore(self.path))
        self.assertEqual(restarted.register(self.key_id,self.challenge.identifier,self.encoded),result)
        with self.assertRaises(AdmissionRejected):restarted.issue_challenge(self.key_id)

    def test_unknown_commit_outcome_is_not_reported_as_success_but_retry_recovers(self):
        bucket=FakeBucket();store=GCSStateStore(bucket);service=self.engine(store)
        challenge=service.issue_challenge(self.key_id);data=self.make_proof(challenge=challenge.nonce)
        bucket.lose_response=True
        with self.assertRaises(StateUnavailable):service.register(self.key_id,challenge.identifier,data)
        self.assertEqual(store.load(self.key_id)[1]['status'],'active')
        bucket.lose_response=False
        result=service.register(self.key_id,challenge.identifier,data)
        self.assertEqual(result.installation,store.load(self.key_id)[1]['installation'])

    def test_gcs_services_register_one_generation_and_owner(self):
        bucket=FakeBucket();store=GCSStateStore(bucket);service=self.engine(store)
        challenge=service.issue_challenge(self.key_id);data=self.make_proof(challenge=challenge.nonce)
        before=store.load(self.key_id);barrier=threading.Barrier(8)
        def work(_):
            engine=self.engine(GCSStateStore(bucket));barrier.wait(5)
            return engine.register(self.key_id,challenge.identifier,data)
        with ThreadPoolExecutor(8) as pool:results=list(pool.map(work,range(8)))
        self.assertEqual(len(set(results)),1)
        self.assertEqual(store.load(self.key_id)[0],before[0]+1)

    def test_lost_challenge_response_recovers_same_pending_challenge(self):
        bucket=FakeBucket();store=GCSStateStore(bucket);service=self.engine(store)
        bucket.lose_response=True
        with self.assertRaises(StateUnavailable):service.issue_challenge(self.key_id)
        before=store.load(self.key_id);bucket.lose_response=False
        challenge=service.issue_challenge(self.key_id)
        self.assertEqual(challenge.identifier,before[1]['challenge_id'])
        self.assertEqual(challenge.nonce,base64.b64decode(before[1]['nonce']))
        self.assertEqual(before,store.load(self.key_id))

    def test_storage_failure_and_contended_cas_never_return_success(self):
        before=self.store.load(self.key_id)
        with patch.object(self.store,'compare_exchange',side_effect=StateUnavailable('offline')):
            with self.assertRaises(StateUnavailable):self.register()
        with patch.object(self.store,'compare_exchange',return_value=False):
            with self.assertRaises(StateUnavailable):self.register()
        self.assertEqual(before,self.store.load(self.key_id))

    def test_rotated_challenge_during_verification_prevents_stale_commit(self):
        original=self.store.compare_exchange;first=True
        def exchange(key,rev,state):
            nonlocal first
            if first:
                first=False
                current_rev,current=self.store.load(key)
                current['challenge_id']='9'*32
                self.assertTrue(original(key,current_rev,current))
            return original(key,rev,state)
        with patch.object(self.store,'compare_exchange',side_effect=exchange):
            with self.assertRaises(AdmissionRejected):self.register()
        self.assertEqual(self.store.load(self.key_id)[1]['status'],'pending')

    def test_corrupt_pending_or_registered_metadata_fail_closed(self):
        before=self.store.load(self.key_id)
        for field,value in [('installation','client-chosen'),('nonce','invalid'),('app_id','wrong'),('v',True),('expires_at',0)]:
            rev,state=self.store.load(self.key_id);state[field]=value;self.store.compare_exchange(self.key_id,rev,state)
            with self.subTest(field=field),self.assertRaises(StateUnavailable):self.register()
            rev,_=self.store.load(self.key_id);self.store.compare_exchange(self.key_id,rev,before[1])
        self.register();rev,state=self.store.load(self.key_id);state['registration']['attestation_sha256']='bad'
        self.store.compare_exchange(self.key_id,rev,state)
        with self.assertRaises(StateUnavailable):self.register()
        with self.assertRaises(StateUnavailable):self.service.admission.issue_challenge(self.key_id)

    def test_input_size_clock_and_policy_boundaries(self):
        for args in [dict(key_id=b'x'),dict(challenge_id='bad'),dict(encoded=b''),dict(encoded=b'x'*65537)]:
            with self.subTest(args=list(args)),self.assertRaises(AdmissionRejected):self.register(**args)
        self.now-=1
        with self.assertRaises(AdmissionRejected):self.register()
        with self.assertRaises(StateUnavailable):self.service.issue_challenge(self.key_id)
        with self.assertRaises(ValueError):enrollment.RegistrationService(self.store,policy=self.r.policy,audience='lite-test',environment='development')

    def test_pending_is_not_an_assertion_identity(self):
        with self.assertRaises(StateUnavailable):self.service.admission.issue_challenge(self.key_id)

    def test_public_verifiers_reject_synthetic_roots(self):
        self.attest_patch.stop();self.receipt_patch.stop()
        with self.assertRaises(AssertionRejected):self.register()
        self.assertEqual(self.store.load(self.key_id)[1]['status'],'pending')


if __name__=='__main__': unittest.main()
