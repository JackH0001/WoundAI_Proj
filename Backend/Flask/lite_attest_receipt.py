"""Offline App Attest registration receipt validation (CMS + signed payload).

Only an initial ATTEST receipt is accepted. Fraud-metric refresh and registration
state transactions are separate. Never trust a receipt merely because it parses.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
from pathlib import Path
import re

from asn1crypto import cms, core
from cryptography import x509
from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.verification import PolicyBuilder, ExtensionPolicy, Store, Criticality, VerificationError

from lite_attest_assertion import AssertionRejected
from lite_attest_registration import AttestedKey, _leaf_constraints, _leaf_usage

# Apple's assessing-fraud-risk specification uses this root, not the private
# App Attestation Root CA used for the credential certificate.
# https://www.apple.com/certificateauthority/AppleRootCA-G3.cer
ROOT_PATH = Path(__file__).with_name('certificates') / 'AppleRootCA-G3.pem'
ROOT_SHA256 = '63343abfb89a6a03ebb57e9b3f5fa7be7c4f5c756f3017b3a8c488c3653e9179'
RECEIPT_PURPOSE = x509.ObjectIdentifier('1.2.840.113635.100.12.15')
MAX_RECEIPT = 32768


class ReceiptAttribute(core.Sequence):
    _fields = [('type', core.Integer), ('version', core.Integer), ('value', core.OctetString)]


class ReceiptPayload(core.SetOf):
    _child_spec = ReceiptAttribute


@dataclass(frozen=True)
class VerifiedReceipt:
    sha256: str
    created_at: datetime
    expires_at: datetime


def _bounded_asn1(data):
    """Bound BER nesting/items before the ASN.1 library's lazy parsing.

    Apple's CMS uses indefinite constructed containers and chunked OCTET STRING;
    DER-only decoders reject it. Primitive indefinite lengths remain invalid.
    This is a structural size guard, not a replacement ASN.1/CMS decoder.
    """
    if type(data) is not bytes or not 1 <= len(data) <= MAX_RECEIPT:
        raise AssertionRejected('invalid receipt size')
    items = 0

    def scan(pos, limit, depth):
        nonlocal items
        items += 1
        if depth > 24 or items > 2048 or pos + 2 > limit:
            raise ValueError('ASN.1 complexity/truncation')
        first = data[pos]; pos += 1
        if first == 0: raise ValueError('unexpected end-of-contents')
        if first & 31 == 31:
            for _ in range(5):
                if pos >= limit: raise ValueError('truncated tag')
                octet = data[pos]; pos += 1
                if not octet & 128: break
            else: raise ValueError('oversized tag')
        length = data[pos]; pos += 1
        if length == 128:
            if not first & 32: raise ValueError('indefinite primitive')
            while True:
                if pos + 2 > limit: raise ValueError('unterminated container')
                if data[pos:pos + 2] == b'\x00\x00': return pos + 2
                pos = scan(pos, limit, depth + 1)
        if length & 128:
            count = length & 127
            if not 1 <= count <= 4 or pos + count > limit: raise ValueError('invalid length')
            length = int.from_bytes(data[pos:pos + count], 'big'); pos += count
        end = pos + length
        if end > limit: raise ValueError('truncated value')
        if first & 32:
            while pos < end: pos = scan(pos, end, depth + 1)
        return end

    try:
        if scan(0, len(data), 0) != len(data): raise ValueError('trailing ASN.1 data')
    except (ValueError, IndexError) as exc:
        raise AssertionRejected('invalid receipt ASN.1 structure') from exc


def _verified_content(encoded, root, now):
    """Validate CMS with a trusted root/time; private seam for synthetic tests."""
    _bounded_asn1(encoded)
    try:
        outer = cms.ContentInfo.load(encoded, strict=True)
        if outer['content_type'].native != 'signed_data': raise ValueError('not signedData')
        signed = outer['content']
        if signed['version'].native != 'v1' or len(signed['signer_infos']) != 1:
            raise ValueError('unsupported signer schema')
        if signed['crls'].native is not None: raise ValueError('unexpected CRLs')
        algorithms = signed['digest_algorithms']
        if len(algorithms) != 1 or algorithms[0]['algorithm'].native != 'sha256':
            raise ValueError('unsupported CMS digest')
        content = signed['encap_content_info']
        if content['content_type'].native != 'data': raise ValueError('unexpected content type')
        payload = content['content'].native
        if type(payload) is not bytes or not 1 <= len(payload) <= 16384:
            raise ValueError('missing/detached payload')
        signer = signed['signer_infos'][0]
        if (signer['version'].native != 'v1' or signer['sid'].name != 'issuer_and_serial_number' or
                signer['digest_algorithm']['algorithm'].native != 'sha256' or
                signer['signature_algorithm']['algorithm'].native != 'sha256_ecdsa' or
                signer['unsigned_attrs'].native is not None):
            raise ValueError('unsupported signer')
        for algorithm in (algorithms[0], signer['digest_algorithm'], signer['signature_algorithm']):
            if algorithm['parameters'].native is not None: raise ValueError('unexpected algorithm parameters')
        if not 2 <= len(signed['certificates']) <= 4: raise ValueError('invalid receipt chain size')
        certs, matches, seen = [], [], set()
        sid = signer['sid'].chosen
        for choice in signed['certificates']:
            if choice.name != 'certificate': raise ValueError('unexpected certificate choice')
            der = choice.chosen.dump()
            if len(der) > 8192 or der in seen: raise ValueError('invalid/duplicate certificate')
            seen.add(der)
            cert = x509.load_der_x509_certificate(der); certs.append(cert)
            if (choice.chosen.issuer.dump() == sid['issuer'].dump() and
                    choice.chosen.serial_number == sid['serial_number'].native):
                matches.append(cert)
        if len(matches) != 1: raise ValueError('ambiguous/missing signer certificate')
        leaf = matches[0]
        purpose = leaf.extensions.get_extension_for_oid(RECEIPT_PURPOSE).value
        if not isinstance(purpose, x509.UnrecognizedExtension) or purpose.value != b'\x05\x00':
            raise ValueError('not an App Attest receipt signer')
        for extension in leaf.extensions:
            if extension.critical and extension.oid not in (x509.ExtensionOID.BASIC_CONSTRAINTS, x509.ExtensionOID.KEY_USAGE):
                raise ValueError('unknown critical signer extension')
        ee_policy = (ExtensionPolicy.permit_all()
                     .require_present(x509.BasicConstraints, Criticality.CRITICAL, _leaf_constraints)
                     .require_present(x509.KeyUsage, Criticality.CRITICAL, _leaf_usage))
        verifier = (PolicyBuilder().store(Store([root])).time(now).max_chain_depth(2)
                    .extension_policies(ca_policy=ExtensionPolicy.webpki_defaults_ca(), ee_policy=ee_policy)
                    .build_client_verifier())
        chain = verifier.verify(leaf, [c for c in certs if c != leaf and c != root]).chain
        if chain[-1] != root: raise ValueError('unexpected trust anchor')
        public = leaf.public_key()
        if not isinstance(public, ec.EllipticCurvePublicKey) or not isinstance(public.curve, (ec.SECP256R1, ec.SECP384R1)):
            raise ValueError('unsupported signer key')
        signed_bytes = payload
        attrs = signer['signed_attrs']
        if attrs.native is not None:
            if not 2 <= len(attrs) <= 8: raise ValueError('invalid signed attributes')
            values = {}
            for attr in attrs:
                oid = attr['type'].dotted
                if oid in values or len(attr['values']) != 1: raise ValueError('duplicate/ambiguous signed attribute')
                values[oid] = attr['values'][0].native
            if (values.get('1.2.840.113549.1.9.3') != 'data' or
                    values.get('1.2.840.113549.1.9.4') != hashlib.sha256(payload).digest()):
                raise ValueError('signed content type/digest mismatch')
            # RFC 5652: sign DER SET OF, not the [0] implicit context tag.
            signed_bytes = attrs.untag().dump(force=True)
        signature = signer['signature'].native
        if type(signature) is not bytes or not 8 <= len(signature) <= 104: raise ValueError('invalid CMS signature')
        public.verify(signature, signed_bytes, ec.ECDSA(hashes.SHA256()))
        return payload
    except (ValueError, TypeError, KeyError, IndexError, OverflowError, RecursionError,
            InvalidSignature, UnsupportedAlgorithm, VerificationError, x509.ExtensionNotFound,
            x509.DuplicateExtension) as exc:
        raise AssertionRejected('invalid App Attest receipt signature/chain') from exc


def _fields(payload):
    _bounded_asn1(payload)
    try:
        attrs = ReceiptPayload.load(payload, strict=True)
        if not 7 <= len(attrs) <= 32: raise ValueError('invalid receipt fields')
        fields = {}
        for attr in attrs:
            number, version, value = attr['type'].native, attr['version'].native, attr['value'].native
            if number <= 0 or number in fields or version != 1 or len(value) > 8192:
                raise ValueError('duplicate/unsupported receipt field')
            fields[number] = value
        if not {2, 3, 4, 5, 6, 12, 21} <= fields.keys(): raise ValueError('missing receipt fields')
        return fields
    except (ValueError, TypeError, KeyError, IndexError, RecursionError) as exc:
        raise AssertionRejected('invalid receipt payload') from exc


def _date(value):
    text = value.decode('ascii')
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z', text) is None:
        raise ValueError('invalid receipt timestamp')
    return datetime.fromisoformat(text.replace('Z', '+00:00'))


def _verify_with_root(encoded, *, app_id, attested, client_hash, root, now):
    payload = _verified_content(encoded, root, now)
    fields = _fields(payload)
    try:
        if fields[2] != app_id.encode('ascii') or fields[6] != b'ATTEST':
            raise ValueError('receipt app/type mismatch')
        # Apple uses 'development' in the entitlement/AAGUID policy, but
        # 'sandbox' in signed receipt field 7 (verified on a real device).
        # Keep this an exact mapping: sandbox never satisfies production.
        expected_environment = {'production': b'production', 'development': b'sandbox'}.get(attested.environment)
        if expected_environment is None or fields.get(7, expected_environment) != expected_environment:
            raise ValueError('receipt environment mismatch')
        if not hmac.compare_digest(fields[4], client_hash): raise ValueError('receipt client hash mismatch')
        if not 1 <= len(fields[5]) <= 512: raise ValueError('invalid receipt token')
        created, expires = _date(fields[12]), _date(fields[21])
        if not 0 <= (now - created).total_seconds() <= 300 or expires <= now:
            raise ValueError('stale/future/expired receipt')
        credential = x509.load_der_x509_certificate(fields[3])
        if credential.public_bytes(serialization.Encoding.DER) != fields[3]:
            raise ValueError('noncanonical receipt credential')
        key = credential.public_key()
        if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
            raise ValueError('receipt key is not P-256')
        point = key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        if not hmac.compare_digest(point, attested.public_key_x962): raise ValueError('receipt key mismatch')
        if not hmac.compare_digest(hashlib.sha256(point).digest(), attested.key_id): raise ValueError('receipt key ID mismatch')
        return VerifiedReceipt(hashlib.sha256(encoded).hexdigest(), created, expires)
    except (ValueError, TypeError, UnsupportedAlgorithm) as exc:
        raise AssertionRejected('receipt not valid for this registration') from exc


def verify_registration_receipt(attested, *, app_id, challenge):
    """Use only the result of verify_attestation and the same server challenge."""
    if not isinstance(attested, AttestedKey) or type(challenge) is not bytes or len(challenge) != 32:
        raise AssertionRejected('verified attestation and challenge required')
    if type(app_id) is not str or re.fullmatch(r'[A-Z0-9]{10}\.[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+', app_id) is None:
        raise AssertionRejected('invalid trusted App ID')
    try:
        root = x509.load_pem_x509_certificate(ROOT_PATH.read_bytes())
        if root.fingerprint(hashes.SHA256()).hex() != ROOT_SHA256: raise ValueError('receipt root mismatch')
    except (OSError, ValueError) as exc:
        raise AssertionRejected('trusted receipt root unavailable') from exc
    return _verify_with_root(attested.receipt, app_id=app_id, attested=attested,
                             client_hash=hashlib.sha256(challenge).digest(), root=root,
                             now=datetime.now(timezone.utc))
