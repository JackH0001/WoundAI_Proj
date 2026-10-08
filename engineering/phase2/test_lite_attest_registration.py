"""Synthetic attestation chain/nonce tests plus Apple's published CA sample.

Apple sample certificates: https://developer.apple.com/documentation/devicecheck/attestation-object-validation-guide
Only their certificate path is a positive fixture, at its historical validity
date. Neither these tests nor the opaque synthetic receipts prove real-device
registration, receipt validation or durable challenge consumption.
"""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import cbor2
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'Backend/Flask'))
import lite_attest_registration as registration
from lite_attest_assertion import AssertionPolicy, AssertionRejected, CATEGORY, VERSION


NOW = datetime(2026, 10, 4, tzinfo=timezone.utc)


def name(label):
    return x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, label)])


def certificate(key, subject, issuer, issuer_key, *, ca, nonce=None, valid_from=None,
                valid_to=None, digital_signature=True, key_cert_sign=None, eku=None,
                unknown_critical=False):
    usage_ca = ca if key_cert_sign is None else key_cert_sign
    builder = (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer)
               .public_key(key.public_key()).serial_number(x509.random_serial_number())
               .not_valid_before(valid_from or NOW - timedelta(days=1))
               .not_valid_after(valid_to or NOW + timedelta(days=1))
               .add_extension(x509.BasicConstraints(ca=ca, path_length=None), True)
               .add_extension(x509.KeyUsage(digital_signature, False, False, False, False,
                                             usage_ca, usage_ca, None, None), True)
               .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False)
               .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()), False))
    if not ca:
        builder = builder.add_extension(x509.ExtendedKeyUsage(eku or [registration.ATTEST_EKU]), False)
    if nonce is not None:
        builder = builder.add_extension(x509.UnrecognizedExtension(registration.NONCE_OID, nonce), False)
    if unknown_critical:
        builder = builder.add_extension(x509.UnrecognizedExtension(x509.ObjectIdentifier('1.2.3.4.5'), b'\x05\x00'), True)
    return builder.sign(issuer_key, hashes.SHA384())


def der(cert): return cert.public_bytes(serialization.Encoding.DER)


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.root_key = ec.generate_private_key(ec.SECP384R1())
        self.intermediate_key = ec.generate_private_key(ec.SECP384R1())
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.root = certificate(self.root_key, name('test root'), name('test root'), self.root_key, ca=True)
        self.intermediate = certificate(self.intermediate_key, name('test issuer'), self.root.subject, self.root_key, ca=True)
        self.point = self.key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        self.key_id = hashlib.sha256(self.point).digest()
        self.challenge = b'c' * 32
        self.policy = AssertionPolicy('ABCDEFGHIJ.com.woundai.lite', frozenset({2, 4}), frozenset({'33'}))

    def auth(self, *, app_id=None, counter=0, aaguid=None, credential_id=None, cose=None, ext=None, flags=0xc0):
        key = {1: 2, 3: -7, -1: 1, -2: self.point[1:33], -3: self.point[33:]}
        cid = self.key_id if credential_id is None else credential_id
        data = (hashlib.sha256((app_id or self.policy.app_id).encode()).digest() + bytes([flags]) + counter.to_bytes(4, 'big') +
                (aaguid if aaguid is not None else b'appattest' + b'\x00' * 7) + len(cid).to_bytes(2, 'big') + cid +
                cbor2.dumps(key if cose is None else cose))
        if flags & 0x80:
            data += cbor2.dumps({CATEGORY: 2, VERSION: '33'} if ext is None else ext)
        return data

    def object(self, *, auth=None, nonce=None, omit_nonce=False, cert_changes=None, leaf_key=None):
        auth = self.auth() if auth is None else auth
        expected = hashlib.sha256(auth + hashlib.sha256(self.challenge).digest()).digest()
        nonce = b'\x30\x24\xa1\x22\x04\x20' + expected if nonce is None else nonce
        args = dict(ca=False, nonce=None if omit_nonce else nonce)
        args.update(cert_changes or {})
        leaf = certificate(leaf_key or self.key, name('test credential'), self.intermediate.subject,
                           self.intermediate_key, **args)
        return dict(fmt='apple-appattest', attStmt={'x5c': [der(leaf), der(self.intermediate)],
                                                  'receipt': b'synthetic-receipt-not-validated'}, authData=auth)

    def verify(self, obj=None, **changes):
        options = dict(expected_key_id=self.key_id, challenge=self.challenge, policy=self.policy,
                       environment='production', root=self.root, now=NOW)
        return registration._verify_with_root(cbor2.dumps(self.object() if obj is None else obj), **dict(options, **changes))

    def test_valid_synthetic_production_attestation(self):
        result = self.verify()
        self.assertEqual(result.key_id, self.key_id)
        self.assertEqual(result.public_key_x962, self.point)
        self.assertEqual((result.validation_category, result.bundle_version), (2, '33'))

    def test_development_requires_matching_environment_and_category(self):
        obj = self.object(auth=self.auth(aaguid=b'appattestdevelop', ext={CATEGORY: 3, VERSION: '33'}))
        self.assertEqual(self.verify(obj, environment='development',
                                    policy=replace(self.policy, allowed_categories=frozenset({3}))).environment, 'development')
        with self.assertRaises(AssertionRejected): self.verify(obj)
        with self.assertRaises(AssertionRejected): self.verify(environment='development')

    def test_apple_encoded_uint32_extensions_without_ed(self):
        auth = self.auth(ext={CATEGORY: b'\x02\x00\x00\x00', VERSION: '33'})
        auth = auth[:32] + b'\x40' + auth[33:]
        self.assertEqual(self.verify(self.object(auth=auth)).validation_category, 2)

    def test_wrong_challenge_and_key_id(self):
        for changes in (dict(challenge=b'x' * 32), dict(expected_key_id=b'x' * 32)):
            with self.assertRaises(AssertionRejected): self.verify(**changes)

    def test_matching_supplied_ids_cannot_replace_certificate_key_hash(self):
        other_id = b'x' * 32
        with self.assertRaises(AssertionRejected):
            self.verify(self.object(auth=self.auth(credential_id=other_id)), expected_key_id=other_id)

    def test_nonce_missing_wrong_or_ambiguous(self):
        with self.assertRaises(AssertionRejected): self.verify(self.object(omit_nonce=True))
        for nonce in (b'x' * 38, b'\x30\x24\xa1\x22\x04\x20' + b'x' * 32,
                      b'\x30\x24\xa1\x22\x04\x20' + b'x' * 32 + b'\x00'):
            with self.subTest(nonce=nonce[:6]), self.assertRaises(AssertionRejected): self.verify(self.object(nonce=nonce))

    def test_modified_authenticator_after_certificate_issued(self):
        obj = self.object()
        auth = bytearray(obj['authData']); auth[-1] ^= 1; obj['authData'] = bytes(auth)
        with self.assertRaises(AssertionRejected): self.verify(obj)

    def test_equivalent_extension_encoding_still_changes_bound_nonce(self):
        obj = self.object()
        obj['authData'] = self.auth(ext={CATEGORY: b'\x02\x00\x00\x00', VERSION: '33'})
        with self.assertRaisesRegex(AssertionRejected, 'nonce mismatch'): self.verify(obj)

    def test_wrong_app_or_nonzero_initial_counter(self):
        for auth in (self.auth(app_id='ABCDEFGHIJ.com.woundai.app'), self.auth(counter=1)):
            with self.assertRaises(AssertionRejected): self.verify(self.object(auth=auth))

    def test_wrong_aaguid_or_credential_id(self):
        for auth in (self.auth(aaguid=b'x' * 16), self.auth(credential_id=b'x' * 32),
                     self.auth(credential_id=self.key_id[:-1])):
            with self.assertRaises(AssertionRejected): self.verify(self.object(auth=auth))

    def test_cose_schema_algorithm_and_point(self):
        good = {1: 2, 3: -7, -1: 1, -2: self.point[1:33], -3: self.point[33:]}
        for changes in ({1: 3}, {3: -257}, {-1: 2}, {-2: b'x' * 32}, {-3: b'x' * 31}, {4: 1}):
            with self.subTest(changes=changes), self.assertRaises(AssertionRejected):
                self.verify(self.object(auth=self.auth(cose=good | changes)))

    def test_extension_category_version_schema(self):
        for ext in ({CATEGORY: 3, VERSION: '33'}, {CATEGORY: b'\x00\x00\x00\x02', VERSION: '33'},
                    {CATEGORY: 2, VERSION: '34'}, {CATEGORY: 2, VERSION: 33}, {CATEGORY: 2},
                    {CATEGORY: 2, VERSION: '33', 'other': 0}):
            with self.subTest(ext=ext), self.assertRaises(AssertionRejected): self.verify(self.object(auth=self.auth(ext=ext)))

    def test_missing_extensions_requires_explicit_legacy_policy(self):
        obj = self.object(auth=self.auth(flags=0x40))
        with self.assertRaises(AssertionRejected): self.verify(obj)
        self.assertIsNone(self.verify(obj, policy=replace(self.policy, require_extensions=False)).bundle_version)

    def test_flags_and_trailing_data(self):
        for auth in (self.auth(flags=0x80), self.auth(flags=0xc4), self.auth() + b'\x00'):
            with self.assertRaises(AssertionRejected): self.verify(self.object(auth=auth))

    def test_expired_and_not_yet_valid_leaf(self):
        for options in ({'valid_to': NOW - timedelta(hours=1)}, {'valid_from': NOW + timedelta(hours=1)}):
            with self.assertRaises(AssertionRejected): self.verify(self.object(cert_changes=options))

    def test_expired_intermediate(self):
        self.intermediate = certificate(self.intermediate_key, name('test issuer'), self.root.subject,
                                        self.root_key, ca=True, valid_to=NOW - timedelta(hours=1))
        with self.assertRaises(AssertionRejected): self.verify()

    def test_unrelated_trust_root(self):
        other_key = ec.generate_private_key(ec.SECP384R1())
        other = certificate(other_key, name('other'), name('other'), other_key, ca=True)
        with self.assertRaises(AssertionRejected): self.verify(root=other)

    def test_broken_signature_or_chain_order_and_extra_cert(self):
        original = self.object()
        original_certs = original['attStmt']['x5c']
        leaf = bytearray(original_certs[0]); leaf[-1] ^= 1
        for certs in ([bytes(leaf), original_certs[1]], list(reversed(original_certs)),
                      original_certs + [der(self.root)], [original_certs[0]],
                      [original_certs[0] + b'\x00', original_certs[1]]):
            obj = dict(original, attStmt=dict(original['attStmt'], x5c=certs))
            with self.subTest(count=len(certs)), self.assertRaises(AssertionRejected): self.verify(obj)

    def test_leaf_ca_key_usage_eku_and_unknown_critical_extension(self):
        for options in ({'ca': True}, {'key_cert_sign': True}, {'digital_signature': False},
                        {'eku': [x509.ExtendedKeyUsageOID.CLIENT_AUTH]}, {'unknown_critical': True}):
            with self.subTest(options=options), self.assertRaises(AssertionRejected):
                self.verify(self.object(cert_changes=options))

    def test_non_p256_credential(self):
        with self.assertRaises(AssertionRejected):
            self.verify(self.object(leaf_key=ec.generate_private_key(ec.SECP384R1())))

    def test_envelope_receipt_and_auth_sizes(self):
        obj = self.object()
        variants = [dict(obj, fmt='none'), dict(obj, extra=1), dict(obj, authData=b'x' * 2049),
                    dict(obj, attStmt=dict(obj['attStmt'], extra=1))]
        variants += [dict(obj, attStmt=dict(obj['attStmt'], receipt=value)) for value in (b'', b'x' * 32769, 'text')]
        for variant in variants:
            with self.assertRaises(AssertionRejected): self.verify(variant)

    def test_invalid_policy_and_input_types(self):
        for changes in (dict(policy=None), dict(environment='staging'), dict(challenge='c' * 32),
                        dict(challenge=b'c' * 31), dict(expected_key_id=b'k' * 33),
                        dict(policy=replace(self.policy, allowed_categories=frozenset({2, 3})))):
            with self.subTest(changes=list(changes)), self.assertRaises(AssertionRejected): self.verify(**changes)

    def test_public_verifier_rejects_synthetic_root(self):
        with self.assertRaises(AssertionRejected):
            registration.verify_attestation(cbor2.dumps(self.object()), expected_key_id=self.key_id,
                                              challenge=self.challenge, policy=self.policy, environment='production')

    def test_bundled_root_fingerprint_and_substitution(self):
        root = x509.load_pem_x509_certificate(registration.ROOT_PATH.read_bytes())
        self.assertEqual(root.fingerprint(hashes.SHA256()).hex(), registration.ROOT_SHA256)
        with patch.object(Path, 'read_bytes', return_value=self.root.public_bytes(serialization.Encoding.PEM)):
            with self.assertRaisesRegex(AssertionRejected, 'trusted Apple root unavailable'):
                registration.verify_attestation(b'\xa0', expected_key_id=self.key_id,
                    challenge=self.challenge, policy=self.policy, environment='production')

    def test_official_apple_chain_valid_only_at_historical_date(self):
        fixtures = Path(__file__).with_name('fixtures') / 'app_attest'
        certs = [der(x509.load_pem_x509_certificate((fixtures / name).read_bytes()))
                 for name in ('apple_guide_leaf.pem', 'apple_guide_intermediate.pem')]
        root = x509.load_pem_x509_certificate(registration.ROOT_PATH.read_bytes())
        self.assertIsInstance(registration._verify_chain(certs, root, datetime(2026, 4, 21, tzinfo=timezone.utc)), x509.Certificate)
        with self.assertRaises(AssertionRejected): registration._verify_chain(certs, root, NOW)


if __name__ == '__main__': unittest.main()
