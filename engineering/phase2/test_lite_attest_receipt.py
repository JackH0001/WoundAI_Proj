"""Receipt CMS/signature/claim checks, with Apple BER and synthetic fixtures.

Public Apple fixture source: DeviceCheck attestation-object-validation-guide.
Historical validation of that receipt is not a new/live device registration.
"""
import base64
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from asn1crypto import cms
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import pkcs7

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'Backend/Flask'))
import lite_attest_receipt as receipt
from lite_attest_registration import AttestedKey
from lite_attest_assertion import AssertionRejected

NOW = datetime(2026, 10, 4, 12, tzinfo=timezone.utc)


def cert(key, subject, issuer, issuer_key, ca, purpose=False, start=None, end=None):
    builder = (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer).public_key(key.public_key())
               .serial_number(x509.random_serial_number()).not_valid_before(start or NOW-timedelta(days=1))
               .not_valid_after(end or NOW+timedelta(days=1))
               .add_extension(x509.BasicConstraints(ca, None), True)
               .add_extension(x509.KeyUsage(not ca, False, False, False, False, ca, ca, None, None), True)
               .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False)
               .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()), False))
    if purpose: builder = builder.add_extension(x509.UnrecognizedExtension(receipt.RECEIPT_PURPOSE, b'\x05\x00'), False)
    return builder.sign(issuer_key, hashes.SHA384())


def name(value): return x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, value)])
def date(value): return value.isoformat(timespec='milliseconds').replace('+00:00', 'Z').encode()


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.root_key = ec.generate_private_key(ec.SECP384R1())
        self.issuer_key = ec.generate_private_key(ec.SECP384R1())
        self.signing_key = ec.generate_private_key(ec.SECP256R1())
        self.device_key = ec.generate_private_key(ec.SECP256R1())
        self.root = cert(self.root_key, name('test root'), name('test root'), self.root_key, True)
        self.issuer = cert(self.issuer_key, name('test issuer'), self.root.subject, self.root_key, True)
        self.signer = cert(self.signing_key, name('test receipt signer'), self.issuer.subject, self.issuer_key, False, purpose=True)
        self.credential = cert(self.device_key, name('test device'), self.issuer.subject, self.issuer_key, False)
        self.point = self.device_key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        self.attested = AttestedKey(hashlib.sha256(self.point).digest(), self.point, 'production', 2, '33', b'')
        self.challenge = b'c' * 32; self.client_hash = hashlib.sha256(self.challenge).digest()
        self.app_id = 'ABCDEFGHIJ.com.woundai.lite'

    def fields(self):
        return {2: self.app_id.encode(), 3: self.credential.public_bytes(serialization.Encoding.DER),
                4: self.client_hash, 5: b'synthetic-token', 6: b'ATTEST', 7: b'production',
                12: date(NOW-timedelta(seconds=30)), 21: date(NOW+timedelta(days=30))}

    def payload(self, fields=None):
        return receipt.ReceiptPayload([{'type': k, 'version': 1, 'value': v}
                                      for k, v in (self.fields() if fields is None else fields).items()]).dump()

    def signed(self, payload=None, *, no_attrs=False, signer=None, digest=None):
        builder = (pkcs7.PKCS7SignatureBuilder().set_data(self.payload() if payload is None else payload)
                   .add_signer(signer or self.signer, self.signing_key, digest or hashes.SHA256())
                   .add_certificate(self.issuer))
        options = [pkcs7.PKCS7Options.Binary]
        if no_attrs: options.append(pkcs7.PKCS7Options.NoAttributes)
        return builder.sign(serialization.Encoding.DER, options)

    def verify(self, encoded=None, **changes):
        kwargs = dict(app_id=self.app_id, attested=self.attested, client_hash=self.client_hash, root=self.root, now=NOW)
        return receipt._verify_with_root(self.signed() if encoded is None else encoded, **dict(kwargs, **changes))

    def test_signed_attributes_and_direct_payload_signatures(self):
        for no_attrs in (False, True):
            encoded = self.signed(no_attrs=no_attrs)
            result = self.verify(encoded)
            self.assertEqual(result.sha256, hashlib.sha256(encoded).hexdigest())

    def test_official_apple_ber_receipt_historical_verification(self):
        path = Path(__file__).with_name('fixtures')/'app_attest/apple_guide_receipt.base64'
        encoded = base64.b64decode(path.read_text(), validate=False)
        root = x509.load_pem_x509_certificate(receipt.ROOT_PATH.read_bytes())
        now = datetime(2026, 4, 21, 18, 14, tzinfo=timezone.utc)
        fields = receipt._fields(receipt._verified_content(encoded, root, now))
        credential = x509.load_der_x509_certificate(fields[3])
        point = credential.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        attested = AttestedKey(hashlib.sha256(point).digest(), point, 'production', None, None, encoded)
        # Apple's sample passes raw example text as clientDataHash. Our public
        # verifier always hashes a 32-byte challenge; never copy this sample use.
        result = receipt._verify_with_root(encoded, app_id='1234567890.com.example.myapp', attested=attested,
                    client_hash=b'example_server_challenge', root=root, now=now)
        self.assertEqual(result.created_at, datetime(2026, 4, 21, 18, 13, 12, 153000, tzinfo=timezone.utc))
        with self.assertRaises(AssertionRejected):
            receipt._verify_with_root(encoded, app_id='1234567890.com.example.myapp', attested=attested,
                                       client_hash=b'example_server_challenge', root=root, now=NOW)

    def test_creation_time_boundaries(self):
        for age in (0, 300):
            fields = self.fields(); fields[12] = date(NOW-timedelta(seconds=age))
            self.verify(self.signed(self.payload(fields)))
        for age in (-1, 301):
            fields = self.fields(); fields[12] = date(NOW-timedelta(seconds=age))
            with self.subTest(age=age), self.assertRaises(AssertionRejected): self.verify(self.signed(self.payload(fields)))

    def test_expired_receipt(self):
        fields = self.fields(); fields[21] = date(NOW)
        with self.assertRaises(AssertionRejected): self.verify(self.signed(self.payload(fields)))

    def test_app_client_hash_environment_and_type_are_bound(self):
        for field, value in ((2, b'OTHERTEAM1.com.woundai.lite'), (4, b'x'*32),
                             (6, b'RECEIPT'), (7, b'development')):
            fields = self.fields(); fields[field] = value
            with self.subTest(field=field), self.assertRaises(AssertionRejected): self.verify(self.signed(self.payload(fields)))

    def test_development_credential_accepts_apple_sandbox_receipt(self):
        fields = self.fields(); fields[7] = b'sandbox'
        self.verify(self.signed(self.payload(fields)), attested=replace(self.attested, environment='development'))

    def test_receipt_environment_mapping_is_exact_and_cannot_cross_environments(self):
        for credential_environment, receipt_environment in (
                ('production', b'sandbox'), ('development', b'production'),
                ('development', b'development'), ('development', b'Sandbox'),
                ('production', b'Production'), ('sandbox', b'sandbox'),
                ('unknown', b'unknown'), ('production', b'')):
            fields = self.fields(); fields[7] = receipt_environment
            with self.subTest(credential=credential_environment, receipt=receipt_environment):
                with self.assertRaises(AssertionRejected):
                    self.verify(self.signed(self.payload(fields)),
                                attested=replace(self.attested, environment=credential_environment))

    def test_missing_environment_does_not_admit_unknown_credential_environment(self):
        fields = self.fields(); del fields[7]
        with self.assertRaises(AssertionRejected):
            self.verify(self.signed(self.payload(fields)), attested=replace(self.attested, environment='unknown'))

    def test_missing_optional_environment_still_binds_attested_key(self):
        fields = self.fields(); del fields[7]
        self.verify(self.signed(self.payload(fields)))

    def test_wrong_attested_public_key_or_key_id(self):
        for attested in (replace(self.attested, public_key_x962=b'x'*65), replace(self.attested, key_id=b'x'*32)):
            with self.assertRaises(AssertionRejected): self.verify(attested=attested)

    def test_receipt_credential_is_for_another_key(self):
        fields = self.fields(); fields[3] = self.signer.public_bytes(serialization.Encoding.DER)
        with self.assertRaises(AssertionRejected): self.verify(self.signed(self.payload(fields)))

    def test_missing_duplicate_or_wrong_version_fields(self):
        fields = self.fields(); del fields[4]
        with self.assertRaises(AssertionRejected): self.verify(self.signed(self.payload(fields)))
        attrs = receipt.ReceiptPayload.load(self.payload())
        attrs.append({'type': 2, 'version': 1, 'value': self.app_id.encode()})
        with self.assertRaises(AssertionRejected): self.verify(self.signed(attrs.dump()))
        attrs = receipt.ReceiptPayload.load(self.payload()); attrs[0]['version'] = 2
        with self.assertRaises(AssertionRejected): self.verify(self.signed(attrs.dump()))

    def test_malformed_dates_tokens_and_certificate(self):
        for field, value in ((12, b'2026-10-04'), (12, b'2026-10-04T12:00:00+00:00'),
                             (21, b'2026-13-04T12:00:00Z'), (5, b''), (3, b'invalid DER')):
            fields = self.fields(); fields[field] = value
            with self.subTest(field=field), self.assertRaises(AssertionRejected): self.verify(self.signed(self.payload(fields)))

    def test_tampered_payload_with_unchanged_signed_digest(self):
        obj = cms.ContentInfo.load(self.signed())
        fields = self.fields(); fields[5] = b'different token'
        obj['content']['encap_content_info']['content'] = self.payload(fields)
        with self.assertRaises(AssertionRejected): self.verify(obj.dump())

    def test_tampered_signature(self):
        obj = cms.ContentInfo.load(self.signed())
        signature = bytearray(obj['content']['signer_infos'][0]['signature'].native); signature[-1] ^= 1
        obj['content']['signer_infos'][0]['signature'] = bytes(signature)
        with self.assertRaises(AssertionRejected): self.verify(obj.dump())

    def test_duplicate_signed_attributes_even_if_resigned(self):
        obj = cms.ContentInfo.load(self.signed()); signer = obj['content']['signer_infos'][0]
        signer['signed_attrs'].append({'type': 'content_type', 'values': ['data']})
        signer['signature'] = self.signing_key.sign(signer['signed_attrs'].untag().dump(force=True), ec.ECDSA(hashes.SHA256()))
        with self.assertRaises(AssertionRejected): self.verify(obj.dump())

    def test_wrong_root_or_missing_signer_purpose(self):
        unrelated_key = ec.generate_private_key(ec.SECP384R1())
        unrelated_root = cert(unrelated_key, name('other root'), name('other root'), unrelated_key, True)
        with self.assertRaises(AssertionRejected): self.verify(root=unrelated_root)
        signer = cert(self.signing_key, name('generic signer'), self.issuer.subject, self.issuer_key, False)
        with self.assertRaises(AssertionRejected): self.verify(self.signed(signer=signer))

    def test_expired_and_future_signer_certificate(self):
        for times in ({'end': NOW-timedelta(seconds=1)}, {'start': NOW+timedelta(seconds=1)}):
            signer = cert(self.signing_key, name('expired/future signer'), self.issuer.subject, self.issuer_key,
                          False, purpose=True, **times)
            with self.assertRaises(AssertionRejected): self.verify(self.signed(signer=signer))

    def test_unsupported_algorithms_signer_count_and_sid(self):
        obj = cms.ContentInfo.load(self.signed()); obj['content']['digest_algorithms'][0]['algorithm'] = 'sha1'
        with self.assertRaises(AssertionRejected): self.verify(obj.dump())
        obj = cms.ContentInfo.load(self.signed()); obj['content']['signer_infos'].append(obj['content']['signer_infos'][0])
        with self.assertRaises(AssertionRejected): self.verify(obj.dump())
        obj = cms.ContentInfo.load(self.signed()); obj['content']['signer_infos'][0]['sid'].chosen['serial_number'] = 1
        with self.assertRaises(AssertionRejected): self.verify(obj.dump(force=True))

    def test_duplicate_certificates_and_detached_payload(self):
        obj = cms.ContentInfo.load(self.signed()); obj['content']['certificates'].append(obj['content']['certificates'][0])
        with self.assertRaises(AssertionRejected): self.verify(obj.dump())
        obj = cms.ContentInfo.load(self.signed()); obj['content']['encap_content_info']['content'] = None
        with self.assertRaises(AssertionRejected): self.verify(obj.dump())

    def test_asn1_bounds_truncation_and_trailing_data(self):
        for value in (b'', b'x'*(receipt.MAX_RECEIPT+1), self.signed()+b'\x00', self.signed()[:-1],
                      b'\x30\x80'*30+b'\x00\x00'*30, b'\x04\x80\x00\x00', b'\x30\x85\xff\xff\xff\xff\xff'):
            with self.subTest(size=len(value)), self.assertRaises(AssertionRejected): self.verify(value)
        with self.assertRaises(AssertionRejected): self.verify(self.signed(self.payload()+b'\x00'))

    def test_public_entry_requires_trusted_inputs_and_pinned_root(self):
        with self.assertRaises(AssertionRejected): receipt.verify_registration_receipt(None, app_id=self.app_id, challenge=self.challenge)
        with self.assertRaises(AssertionRejected): receipt.verify_registration_receipt(self.attested, app_id=self.app_id, challenge=b'c')
        with patch.object(Path, 'read_bytes', return_value=self.root.public_bytes(serialization.Encoding.PEM)):
            with self.assertRaisesRegex(AssertionRejected, 'trusted receipt root unavailable'):
                receipt.verify_registration_receipt(self.attested, app_id=self.app_id, challenge=self.challenge)
        with self.assertRaises(AssertionRejected):
            receipt.verify_registration_receipt(replace(self.attested, receipt=self.signed()), app_id=self.app_id, challenge=self.challenge)


if __name__ == '__main__': unittest.main()
