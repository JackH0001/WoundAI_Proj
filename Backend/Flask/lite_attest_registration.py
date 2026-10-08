"""App Attest certificate/key validation, not a registration endpoint.

No network fetch, system CA trust, client-selected root or time override in the
public function. A caller still needs receipt validation, challenge consumption,
unique key/owner registration and durable assertion counters before admission.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
from pathlib import Path

from cryptography import x509
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.verification import (Criticality, ExtensionPolicy,
                                           PolicyBuilder, Store, VerificationError)

from lite_attest_assertion import (AssertionPolicy, AssertionRejected, CATEGORY,
                                  VERSION, _bounded_cbor, _category_number)

# Retrieved 2026-10-04 from Apple's Private PKI (public certificate, not a key):
# https://www.apple.com/certificateauthority/Apple_App_Attestation_Root_CA.pem
ROOT_SHA256 = '1cb9823ba28ba6ad2d33a006941de2ae4f513ef1d4e831b9f7e0fa7b6242c932'
ROOT_PATH = Path(__file__).with_name('certificates') / 'Apple_App_Attestation_Root_CA.pem'
NONCE_OID = x509.ObjectIdentifier('1.2.840.113635.100.8.2')
ATTEST_EKU = x509.ObjectIdentifier('1.2.840.113635.100.4.24')
MAX_ATTESTATION = 65536


@dataclass(frozen=True)
class AttestedKey:
    key_id: bytes
    public_key_x962: bytes
    environment: str
    validation_category: int | None
    bundle_version: str | None
    # Opaque CMS receipt: a separate verifier must check it before registration.
    receipt: bytes


def _leaf_constraints(policy, cert, value):
    if value.ca:
        raise ValueError('credential cannot be a CA')


def _leaf_usage(policy, cert, value):
    if not value.digital_signature or value.key_cert_sign or value.crl_sign:
        raise ValueError('invalid credential key usage')


def _leaf_eku(policy, cert, value):
    if set(value) != {ATTEST_EKU}:
        raise ValueError('not an App Attest credential')


def _verify_chain(cert_bytes, root, now):
    if type(cert_bytes) is not list or len(cert_bytes) != 2:
        raise AssertionRejected('expected credential and intermediate certificates')
    certs = []
    try:
        for data in cert_bytes:
            if type(data) is not bytes or not 1 <= len(data) <= 8192:
                raise ValueError('invalid certificate size')
            cert = x509.load_der_x509_certificate(data)
            if cert.public_bytes(serialization.Encoding.DER) != data:
                raise ValueError('noncanonical certificate DER')
            certs.append(cert)
        # permit_all avoids imposing TLS clientAuth/SAN rules on Apple attestation
        # certificates. These explicit constraints replace those TLS rules.
        ee_policy = (ExtensionPolicy.permit_all()
                     .require_present(x509.BasicConstraints, Criticality.CRITICAL, _leaf_constraints)
                     .require_present(x509.KeyUsage, Criticality.CRITICAL, _leaf_usage)
                     .require_present(x509.ExtendedKeyUsage, Criticality.AGNOSTIC, _leaf_eku))
        for ext in certs[0].extensions:
            if ext.critical and ext.oid not in (x509.ExtensionOID.BASIC_CONSTRAINTS,
                                                x509.ExtensionOID.KEY_USAGE,
                                                x509.ExtensionOID.EXTENDED_KEY_USAGE):
                raise ValueError('unsupported critical credential extension')
        verifier = (PolicyBuilder().store(Store([root])).time(now).max_chain_depth(1)
                    .extension_policies(ca_policy=ExtensionPolicy.webpki_defaults_ca(), ee_policy=ee_policy)
                    .build_client_verifier())
        chain = verifier.verify(certs[0], [certs[1]]).chain
        if len(chain) != 3 or chain[1] != certs[1] or chain[-1] != root:
            raise ValueError('unexpected certificate path')
        return certs[0]
    except (ValueError, TypeError, VerificationError, x509.DuplicateExtension, UnsupportedAlgorithm) as exc:
        raise AssertionRejected('invalid App Attest certificate chain') from exc


def _verify_with_root(encoded, *, expected_key_id, challenge, policy, environment, root, now):
    """Private test seam. Never expose root or now as request/config parameters."""
    if not isinstance(policy, AssertionPolicy) or environment not in ('production', 'development'):
        raise AssertionRejected('invalid registration policy')
    categories = {2, 4} if environment == 'production' else {3}
    if not policy.allowed_categories <= categories:
        raise AssertionRejected('category policy conflicts with attestation environment')
    if (type(expected_key_id) is not bytes or len(expected_key_id) != 32 or
            type(challenge) is not bytes or len(challenge) != 32):
        raise AssertionRejected('invalid registration key/challenge')
    obj = _bounded_cbor(encoded, max_size=MAX_ATTESTATION, credential=True)
    if type(obj) is not dict or set(obj) != {'fmt', 'attStmt', 'authData'} or obj['fmt'] != 'apple-appattest':
        raise AssertionRejected('invalid attestation envelope')
    stmt = obj['attStmt']
    if type(stmt) is not dict or set(stmt) != {'x5c', 'receipt'}:
        raise AssertionRejected('invalid attestation statement')
    receipt = stmt['receipt']
    if type(receipt) is not bytes or not 1 <= len(receipt) <= 32768:
        raise AssertionRejected('invalid receipt size')
    cert = _verify_chain(stmt['x5c'], root, now)
    try:
        key = cert.public_key()
    except (ValueError, UnsupportedAlgorithm) as exc:
        raise AssertionRejected('unsupported credential public key') from exc
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
        raise AssertionRejected('credential key must be P-256')
    point = key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    if not hmac.compare_digest(hashlib.sha256(point).digest(), expected_key_id):
        raise AssertionRejected('attestation key ID mismatch')
    auth = obj['authData']
    if type(auth) is not bytes or not 88 <= len(auth) <= 2048:
        raise AssertionRejected('invalid attestation authenticator data')
    if not hmac.compare_digest(auth[:32], hashlib.sha256(policy.app_id.encode('ascii')).digest()):
        raise AssertionRejected('attestation App ID mismatch')
    flags = auth[32]
    if not flags & 0x40 or flags & ~0xc1:
        raise AssertionRejected('invalid attestation flags')
    if int.from_bytes(auth[33:37], 'big') != 0:
        raise AssertionRejected('attestation counter must be zero')
    aaguid = b'appattest' + b'\x00' * 7 if environment == 'production' else b'appattestdevelop'
    if not hmac.compare_digest(auth[37:53], aaguid):
        raise AssertionRejected('attestation environment mismatch')
    if int.from_bytes(auth[53:55], 'big') != 32 or not hmac.compare_digest(auth[55:87], expected_key_id):
        raise AssertionRejected('credential ID mismatch')
    cose, used = _bounded_cbor(auth[87:], credential=True, prefix=True)
    if (type(cose) is not dict or set(cose) != {1, 3, -1, -2, -3} or
            any(type(cose[k]) is not int or cose[k] != v for k, v in ((1, 2), (3, -7), (-1, 1))) or
            type(cose[-2]) is not bytes or len(cose[-2]) != 32 or
            type(cose[-3]) is not bytes or len(cose[-3]) != 32 or
            not hmac.compare_digest(b'\x04' + cose[-2] + cose[-3], point)):
        raise AssertionRejected('COSE credential does not match certificate key')
    tail = auth[87 + used:]
    category = version = None
    if tail or flags & 0x80:
        ext = _bounded_cbor(tail)
        if type(ext) is not dict or set(ext) != {CATEGORY, VERSION}:
            raise AssertionRejected('invalid attestation extensions')
        category, version = _category_number(ext[CATEGORY]), ext[VERSION]
        if type(category) is not int or category not in policy.allowed_categories:
            raise AssertionRejected('attestation validation category not allowed')
        if type(version) is not str or version not in policy.allowed_bundle_versions:
            raise AssertionRejected('attestation bundle version not allowed')
    elif policy.require_extensions:
        raise AssertionRejected('attestation extensions required')
    nonce = hashlib.sha256(auth + hashlib.sha256(challenge).digest()).digest()
    try:
        value = cert.extensions.get_extension_for_oid(NONCE_OID).value
        # Canonical DER SEQUENCE { [1] EXPLICIT OCTET STRING (32 bytes) }.
        # Exact encoding prevents accepting an extra/ambiguous nonce field.
        if not isinstance(value, x509.UnrecognizedExtension) or not hmac.compare_digest(
                value.value, b'\x30\x24\xa1\x22\x04\x20' + nonce):
            raise AssertionRejected('attestation nonce mismatch')
    except x509.ExtensionNotFound as exc:
        raise AssertionRejected('attestation nonce missing') from exc
    return AttestedKey(expected_key_id, point, environment, category, version, receipt)


def verify_attestation(encoded, *, expected_key_id, challenge, policy, environment):
    """Validate against the bundled Apple root and current UTC server time."""
    try:
        root = x509.load_pem_x509_certificate(ROOT_PATH.read_bytes())
        if not hmac.compare_digest(root.fingerprint(hashes.SHA256()).hex(), ROOT_SHA256):
            raise ValueError('root fingerprint mismatch')
    except (OSError, ValueError) as exc:
        raise AssertionRejected('trusted Apple root unavailable') from exc
    return _verify_with_root(encoded, expected_key_id=expected_key_id, challenge=challenge,
                             policy=policy, environment=environment, root=root,
                             now=datetime.now(timezone.utc))
