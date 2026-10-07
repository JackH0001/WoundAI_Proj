"""Synthetic P-256 assertions: crypto/parser regressions, NOT Apple device proof."""
from dataclasses import replace
import hashlib
from pathlib import Path
import sys
import unittest

import cbor2
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'Backend/Flask'))
from lite_attest_assertion import (AssertionPolicy, AssertionRejected, CATEGORY,
                                  VERSION, MAX_ASSERTION, verify_assertion)
from lite_attest_request import client_data


class AssertionTests(unittest.TestCase):
    def setUp(self):
        self.private = ec.generate_private_key(ec.SECP256R1())
        self.point = self.private.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        self.key_id = hashlib.sha256(self.point).digest()
        self.policy = AssertionPolicy('ABCDEFGHIJ.com.woundai.lite', frozenset({2, 4}), frozenset({'33'}))
        self.request = dict(audience='woundlite-test', installation='synthetic-install',
                            key_id=self.key_id, challenge=b'c' * 32,
                            request_id='00000000-0000-0000-0000-000000000001',
                            method='POST', path='/api/v1/lite/annotation/revision',
                            content_type='application/json', body=b'{"revision":1}')
        self.data = client_data(**self.request)

    def auth(self, *, counter=1, flags=0x81, extension=None, app_id=None):
        tail = b''
        if flags & 0x80:
            tail = cbor2.dumps(extension if extension is not None else {CATEGORY: 2, VERSION: '33'})
        return (hashlib.sha256((app_id or self.policy.app_id).encode()).digest() +
                bytes([flags]) + counter.to_bytes(4, 'big') + tail)

    def signed(self, *, auth=None, data=None, private=None, prehashed_nonce=False):
        auth = self.auth() if auth is None else auth
        data = self.data if data is None else data
        digest = hashlib.sha256(auth + hashlib.sha256(data).digest()).digest()
        algorithm = utils.Prehashed(hashes.SHA256()) if prehashed_nonce else hashes.SHA256()
        signature = (private or self.private).sign(digest, ec.ECDSA(algorithm))
        return cbor2.dumps({'signature': signature, 'authenticatorData': auth})

    def verify(self, encoded=None, **overrides):
        args = dict(public_key_x962=self.point, expected_key_id=self.key_id,
                    client_data=self.data, previous_counter=0, policy=self.policy)
        return verify_assertion(self.signed() if encoded is None else encoded,
                                **dict(args, **overrides))

    def test_valid_testflight_and_store_assertions(self):
        for category in (2, 4):
            result = self.verify(self.signed(auth=self.auth(extension={CATEGORY: category, VERSION: '33'})))
            self.assertEqual((result.counter, result.validation_category, result.bundle_version), (1, category, '33'))

    def test_apple_uint32_bytes_and_signed_extensions_without_ed_flag(self):
        for category in (2, 4):
            auth = self.auth(extension={CATEGORY: category.to_bytes(4, 'little'), VERSION: '33'})
            auth = auth[:32] + b'\x01' + auth[33:]
            self.assertEqual(self.verify(self.signed(auth=auth)).validation_category, category)
        for bad in (b'\x02', b'\x00\x00\x00\x02', b'\x03\x00\x00\x00'):
            with self.assertRaises(AssertionRejected):
                self.verify(self.signed(auth=self.auth(extension={CATEGORY: bad, VERSION: '33'})))

    def test_real_apple_c0_flag_with_strict_signed_extension_tail(self):
        auth = self.auth(flags=0xc0, extension={CATEGORY: (2).to_bytes(4, 'little'), VERSION: '33'})
        result = self.verify(self.signed(auth=auth))
        self.assertEqual((result.counter, result.validation_category, result.bundle_version), (1, 2, '33'))
        # Flags are authenticated; changing a valid envelope without resigning fails.
        envelope = cbor2.loads(self.signed(auth=auth))
        envelope['authenticatorData'] = auth[:32] + b'\x80' + auth[33:]
        with self.assertRaises(AssertionRejected): self.verify(cbor2.dumps(envelope))
        # AT must not introduce credential bytes, tolerate truncation, or bypass
        # category/version checks. The exception is only the observed 0xc0 form.
        for bad in (auth[:37], auth[:37] + b'\x00' * 18 + auth[37:], auth + b'\x00',
                    self.auth(flags=0xc1), self.auth(flags=0xc4), self.auth(flags=0x40),
                    self.auth(flags=0xc0, extension={CATEGORY: 3, VERSION: '33'}),
                    self.auth(flags=0xc0, extension={CATEGORY: 2, VERSION: '34'})):
            with self.subTest(flags=bad[32], size=len(bad)), self.assertRaises(AssertionRejected):
                self.verify(self.signed(auth=bad))

    def test_independent_standard_ecdsa_message_signature(self):
        # Independent low-level equivalent: ECDSA consumes SHA256(nonce),
        # matching the real Apple signatures rather than mirroring the helper.
        auth = self.auth()
        nonce = hashlib.sha256(auth + hashlib.sha256(self.data).digest()).digest()
        signature = self.private.sign(hashlib.sha256(nonce).digest(), ec.ECDSA(utils.Prehashed(hashes.SHA256())))
        self.assertEqual(self.verify(cbor2.dumps({'signature': signature, 'authenticatorData': auth})).counter, 1)

    def test_prehashed_nonce_is_rejected(self):
        with self.assertRaises(AssertionRejected): self.verify(self.signed(prehashed_nonce=True))

    def test_each_request_binding_tamper_breaks_signature(self):
        valid = self.signed()
        changes = dict(audience='other', installation='other', key_id=b'x' * 32, challenge=b'x' * 32,
                       request_id='00000000-0000-0000-0000-000000000002',
                       path='/api/v1/lite/segment', content_type='text/plain', body=b'{"revision":2}')
        for field, value in changes.items():
            with self.subTest(field=field), self.assertRaises(AssertionRejected):
                self.verify(valid, client_data=client_data(**dict(self.request, **{field: value})))

    def test_delete_cannot_be_replayed_as_post(self):
        deletion = client_data(**dict(self.request, method='DELETE', path='/api/v1/lite/data/synthetic-install',
                                     content_type='', body=b''))
        with self.assertRaises(AssertionRejected): self.verify(self.signed(data=deletion))

    def test_signature_from_another_key_fails(self):
        with self.assertRaises(AssertionRejected):
            self.verify(self.signed(private=ec.generate_private_key(ec.SECP256R1())))

    def test_signature_and_authenticated_bytes_tamper(self):
        original = cbor2.loads(self.signed())
        for field in ('signature', 'authenticatorData'):
            changed = bytearray(original[field]); changed[-1] ^= 1
            with self.subTest(field=field), self.assertRaises(AssertionRejected):
                self.verify(cbor2.dumps(dict(original, **{field: bytes(changed)})))

    def test_app_prefix_and_bundle_mismatch_even_with_valid_signature(self):
        for app in ('OTHERTEAM1.com.woundai.lite', 'ABCDEFGHIJ.com.woundai.app'):
            with self.subTest(app=app), self.assertRaises(AssertionRejected):
                self.verify(self.signed(auth=self.auth(app_id=app)))

    def test_counter_zero_equal_lower_and_exhausted(self):
        for current, old in ((0, 0), (1, 1), (1, 2), (0xffffffff, 0xffffffff)):
            with self.subTest(current=current, old=old), self.assertRaises(AssertionRejected):
                self.verify(self.signed(auth=self.auth(counter=current)), previous_counter=old)
        self.assertEqual(self.verify(self.signed(auth=self.auth(counter=8)), previous_counter=2).counter, 8)

    def test_invalid_stored_counters(self):
        for old in (True, -1, 2**32, 1.0, None, '0'):
            with self.subTest(old=old), self.assertRaises(AssertionRejected): self.verify(previous_counter=old)

    def test_registration_point_and_key_id_checks(self):
        for changes in (dict(expected_key_id=b'x' * 32), dict(expected_key_id='x' * 32),
                        dict(public_key_x962=b'\x04' + b'\x00' * 64,
                             expected_key_id=hashlib.sha256(b'\x04' + b'\x00' * 64).digest()),
                        dict(public_key_x962=b'\x02' + self.point[1:33]), dict(public_key_x962=None)):
            with self.subTest(changes=list(changes)), self.assertRaises(AssertionRejected): self.verify(**changes)

    def test_development_enterprise_and_unknown_categories_rejected(self):
        for value in (0, 1, 3, 5, 6, 10, 2**32, '2', True):
            with self.subTest(value=value), self.assertRaises(AssertionRejected):
                self.verify(self.signed(auth=self.auth(extension={CATEGORY: value, VERSION: '33'})))

    def test_bundle_version_and_extension_schema(self):
        variants = ({CATEGORY: 2, VERSION: 33}, {CATEGORY: 2, VERSION: '34'}, {CATEGORY: 2},
                    {VERSION: '33'}, {CATEGORY: 2, VERSION: '33', 'extra': 'x'}, {})
        for ext in variants:
            with self.subTest(ext=ext), self.assertRaises(AssertionRejected):
                self.verify(self.signed(auth=self.auth(extension=ext)))

    def test_legacy_absence_requires_explicit_server_policy(self):
        old = self.signed(auth=self.auth(flags=1))
        with self.assertRaises(AssertionRejected): self.verify(old)
        result = self.verify(old, policy=replace(self.policy, require_extensions=False))
        self.assertIsNone(result.bundle_version)
        # Legacy policy never excuses a PRESENT invalid extension.
        bad = self.signed(auth=self.auth(extension={CATEGORY: 3, VERSION: '33'}))
        with self.assertRaises(AssertionRejected):
            self.verify(bad, policy=replace(self.policy, require_extensions=False))

    def test_authenticator_length_flags_and_unrecognised_tail(self):
        for auth in (b'x' * 36, b'x' * 1025, self.auth(flags=1) + b'\xa0',
                     self.auth(flags=0x41), self.auth(flags=0x85), self.auth(flags=0x89), self.auth()[:37]):
            with self.subTest(length=len(auth)), self.assertRaises(AssertionRejected):
                self.verify(self.signed(auth=auth))
        self.assertEqual(self.verify(self.signed(auth=self.auth(flags=0x80))).counter, 1)

    def test_envelope_schema_and_limits(self):
        value = cbor2.loads(self.signed())
        for bad in (b'', b'x' * (MAX_ASSERTION + 1), cbor2.dumps({}), cbor2.dumps(1),
                    cbor2.dumps(dict(value, extra=b'x')), cbor2.dumps(dict(value, signature='text')),
                    cbor2.dumps(dict(value, signature=b'x' * 73)),
                    cbor2.dumps(dict(value, authenticatorData='text')), self.signed() + b'\x00'):
            with self.subTest(size=len(bad)), self.assertRaises(AssertionRejected): self.verify(bad)

    def test_duplicate_keys_rejected_not_last_value_wins(self):
        row = cbor2.loads(self.signed())
        duplicate = b'\xa3' + b''.join(cbor2.dumps(k) + cbor2.dumps(v) for k, v in
                                      [('signature', b'bad'), *row.items()])
        with self.assertRaises(AssertionRejected): self.verify(duplicate)
        ext = b'\xa3' + b''.join(cbor2.dumps(k) + cbor2.dumps(v) for k, v in
                                [(CATEGORY, 3), (CATEGORY, 2), (VERSION, '33')])
        with self.assertRaises(AssertionRejected): self.verify(self.signed(auth=self.auth()[:37] + ext))

    def test_tags_cycles_indefinite_huge_lengths_and_deep_maps_rejected(self):
        for bad in (b'\xd9\xd9\xf7' + self.signed(), b'\xd8\x1c\xa1\x00\xd8\x1d\x00',
                    b'\xbf\xff', b'\x5b' + b'\xff' * 8, b'\xb8\xff',
                    b'\xa1\x00' * 8 + b'\x00', b'\x7b', b'\xf6'):
            with self.subTest(bad=bad[:8]), self.assertRaises(AssertionRejected): self.verify(bad)

    def test_map_order_and_nonminimal_unsigned_encoding_are_accepted(self):
        row = cbor2.loads(self.signed())
        reversed_map = b'\xb8\x02' + b''.join(cbor2.dumps(k) + cbor2.dumps(v) for k, v in reversed(list(row.items())))
        self.assertEqual(self.verify(reversed_map).counter, 1)

    def test_invalid_server_policy_or_client_data(self):
        for changes in (dict(app_id='com.woundai.lite'), dict(allowed_categories=frozenset()),
                        dict(allowed_categories=frozenset({True})), dict(allowed_bundle_versions=frozenset()),
                        dict(allowed_bundle_versions=frozenset({'33\n'})), dict(require_extensions='false')):
            with self.subTest(changes=changes), self.assertRaises(ValueError): replace(self.policy, **changes)
        for changes in (dict(policy=None), dict(client_data='text'), dict(client_data=b''), dict(client_data=b'x' * 4097)):
            with self.subTest(changes=list(changes)), self.assertRaises(AssertionRejected): self.verify(**changes)


if __name__ == '__main__': unittest.main()
