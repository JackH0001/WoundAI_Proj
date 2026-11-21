"""Local prototype: signed provenance is not consent or research persistence.

Real signed synthetic App Attest requests produce an existing service receipt.
Proof export then reads it without running inference, writing or charging again.
This module exercises the core; separate HTTP tests cover the opt-in routes.
No cloud provider or key provisioning is enabled.
"""
import base64
import copy
import unittest
from unittest.mock import patch

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from lite_attest_state import StateUnavailable
from lite_service_operations import OperationExpired, OperationUnknown, RESULT_TTL
from lite_research_source_issuer import ResearchSourceIssuer
from lite_research_source import (ResearchSourceVerifier,
                                  SourceRejected, SourceExpired, TTL, message)
import test_lite_service_http as fixture


class ResearchSourceTests(unittest.TestCase):
    def setUp(self):
        self.h = fixture.ServiceHTTPTests()
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.key = Ed25519PrivateKey.generate()
        self.issuer = ResearchSourceIssuer(scope='d'*64, bucket='synthetic-research',
            private_key=self.key.private_bytes_raw(), operations=self.h.ops)
        self.verifier = self.issuer.verifier
        self.target = dict(research='b'*32, consent_epoch='c'*32,
            consent_version='2026-10-16.1', capture_id='fe000000-0000-4000-8000-000000000001',
            image_sha256=self.h.image_hash)

    def issue(self, operation=f'{1:032x}', **changes):
        return self.issuer.issue(self.h.owner, operation=operation, **(self.target | changes))

    def verify(self, value, **changes):
        return self.verifier.verify(value, **(dict(owner=self.target['research'],
            consent_epoch=self.target['consent_epoch']) | changes))

    def test_completed_result_exports_without_model_grant_write_or_charge(self):
        original = self.h.complete().json
        quota = self.h.quota()
        with patch.object(self.h.ops, 'inference', side_effect=AssertionError('no model')), \
             patch.object(self.h.accounts, 'grant', side_effect=AssertionError('no purchase lookup')), \
             patch.object(self.h.ops.store, 'compare_exchange', side_effect=AssertionError('read only')), \
             patch.object(self.h.accounts.store, 'compare_exchange', side_effect=AssertionError('read only')), \
             patch.object(self.h.ledger.store, 'compare_exchange', side_effect=AssertionError('read only')):
            first = self.issue()
            self.h.e.now += 1
            again = self.issue()
        self.assertEqual(first['claim'], again['claim'])
        self.assertEqual(first['signature'], again['signature'])
        self.assertNotEqual(first['ticket_signature'], again['ticket_signature'])
        self.assertEqual(self.verify(first)['result'], original['result'])
        self.assertEqual(self.verify(again)['result_sha256'], original['receipt']['result_sha256'])
        self.assertEqual(self.h.model_calls, 1)
        self.assertEqual(self.h.quota(), quota)

    def test_manual_source_never_claims_ai_or_uses_quota(self):
        before = self.h.quota()
        p = self.verify(self.issue(operation=None))
        self.assertEqual(p['source'], 'manual')
        for field in ('operation', 'model', 'result', 'result_sha256'):
            self.assertIsNone(p[field])
        self.assertEqual(self.h.model_calls, 0)
        self.assertEqual(self.h.quota(), before)

    def test_unstarted_operation_cannot_claim_completed_output(self):
        self.assertEqual(self.h.json_request('prepare').status_code, 200)
        with self.assertRaises(SourceRejected): self.issue()
        self.assertEqual(self.h.model_calls, 0)
        self.assertEqual(self.h.quota()['used'], 0)

    def test_result_without_confirmed_settlement_is_not_exported(self):
        self.assertEqual(self.h.json_request('prepare').status_code, 200)
        with patch.object(self.h.ledger, 'settle', side_effect=StateUnavailable('reply uncertain')):
            self.assertEqual(self.h.run_request().status_code, 503)
        with self.assertRaises(SourceRejected): self.issue()
        self.assertEqual(self.h.model_calls, 1)
        self.assertEqual(self.h.quota()['used'], 0)

    def test_image_change_cannot_relabel_original_result(self):
        self.h.complete()
        with self.assertRaises(SourceRejected): self.issue(image_sha256='f'*64)
        self.assertEqual(self.h.model_calls, 1)

    def test_other_account_cannot_export_original_operation(self):
        self.h.complete()
        other = '1'*32
        self.h.accounts.provision(other, 'another-service-account',
            '20000000-0000-4000-8000-000000000002', None, 'e'*64)
        with self.assertRaises(OperationUnknown):
            self.issuer.issue(other, operation=f'{1:032x}', **self.target)

    def test_closure_before_or_during_export_stops_signature(self):
        self.h.complete()
        for replies in ([StateUnavailable('closed')], [None, StateUnavailable('closed')]):
            with self.subTest(replies=len(replies)), patch.object(self.h.accounts, 'require_active', side_effect=replies):
                with self.assertRaises(StateUnavailable): self.issue()

    def test_destination_epoch_bucket_and_scope_are_bound(self):
        t = self.issue(operation=None)
        for kw in (dict(owner='9'*32), dict(consent_epoch='9'*32)):
            with self.subTest(kw=kw), self.assertRaises(SourceRejected): self.verify(t, **kw)
        for scope, bucket in (('e'*64, 'synthetic-research'), ('d'*64, 'different-research')):
            v = ResearchSourceVerifier(scope=scope, bucket=bucket,
                public_key=self.key.public_key().public_bytes_raw(), clock=self.h.ops.now)
            with self.assertRaises(SourceRejected):
                v.verify(t, owner=self.target['research'], consent_epoch=self.target['consent_epoch'])

    def test_wrong_key_and_withdrawal_purpose_cannot_verify(self):
        t = self.issue(operation=None)
        foreign = Ed25519PrivateKey.generate()
        v = ResearchSourceVerifier(scope='d'*64, bucket='synthetic-research',
            public_key=foreign.public_key().public_bytes_raw(), clock=self.h.ops.now)
        with self.assertRaises(InvalidSignature):
            v.verify(t, owner=self.target['research'], consent_epoch=self.target['consent_epoch'])
        t['signature'] = base64.b64encode(self.key.sign(b'withdraw-linked-research/1\0'+message(t['claim']))).decode()
        with self.assertRaises(InvalidSignature): self.verify(t)

    def test_capture_identity_and_consent_version_tampering_rejected(self):
        t = self.issue(operation=None)
        for field, value in (('capture_id','fe000000-0000-4000-8000-000000000002'),
                             ('image_sha256','a'*64), ('native_installation','a'*32)):
            changed = copy.deepcopy(t); changed['claim'][field] = value
            with self.subTest(field=field), self.assertRaises(InvalidSignature): self.verify(changed)
        changed = copy.deepcopy(t); changed['claim']['consent_version'] = 'unknown'
        with self.assertRaises(SourceRejected): self.verify(changed)

    def test_freshness_is_signed_and_expiry_does_not_rerun(self):
        self.h.complete()
        t = self.issue()
        self.h.e.now += TTL
        with self.assertRaises(SourceExpired): self.verify(t)
        # Audit inspection only. A future receiver must verify an existing completed
        # receipt before opting into this mode; it must not permit a new write.
        self.assertEqual(self.verify(t, allow_expired=True), t['claim'])
        changed = copy.deepcopy(t); changed['issued'] += 1; changed['expires'] += 1
        with self.assertRaises(InvalidSignature): self.verify(changed)
        self.assertEqual(self.verify(self.issue()), t['claim'])
        self.assertEqual(self.h.model_calls, 1)
        self.assertEqual(self.h.quota()['used'], 1)

    def test_expired_service_result_requires_separate_recovery_not_inference(self):
        self.h.complete(); self.h.e.now += RESULT_TTL
        with self.assertRaises(OperationExpired): self.issue()
        self.assertEqual(self.h.model_calls, 1)

    def test_receipt_hash_must_match_retained_result(self):
        self.h.complete()
        receipt = self.h.json_request('status').json['receipt']
        with patch.object(self.h.ledger, 'active_receipt', return_value=receipt | {'result_sha256':'e'*64}):
            with self.assertRaises(SourceRejected): self.issue()

    def test_changed_payload_cannot_claim_original_ai(self):
        self.h.complete(); t = self.issue()
        t['claim']['result']['confidence'] = 0.125
        with self.assertRaises(SourceRejected): self.verify(t)

    def test_malformed_ticket_and_clock_are_fail_closed(self):
        t = self.issue(operation=None)
        for value in (t | {'extra':True}, t | {'signature':''}, t | {'issued':True}, t | {'expires':0}):
            with self.subTest(value=value.keys()), self.assertRaises(SourceRejected): self.verify(value)
        for now in (float('nan'), float('inf'), -1, True):
            with self.subTest(now=now), patch.object(self.verifier, 'clock', return_value=now):
                with self.assertRaises(SourceRejected): self.verify(t, allow_expired=True)

    def test_default_app_has_no_provenance_or_research_capture_endpoint(self):
        for path in ('/api/v1/lite/service/research-source', '/api/v1/lite/research/capture'):
            self.assertEqual(self.h.client.post(path, json={}).status_code, 404)


if __name__ == '__main__': unittest.main()
