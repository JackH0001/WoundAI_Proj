"""Offline assertion verification; deliberately not an HTTP authorization gate.

Inputs must come from verified registration and server challenge/request state.
The caller must atomically consume the challenge AND advance the stored counter
before performing any side effect. A successful return alone is not admission.
Apple certificate attestation/registration is a separate, still-required step.
"""
from dataclasses import dataclass
import hashlib
import hmac
import re

import cbor2
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

MAX_ASSERTION = 4096
CATEGORY = 'apple_validation_category_01'
VERSION = 'apple_bundle_version_01'


class AssertionRejected(ValueError):
    """Malformed, incompatible or cryptographically invalid assertion."""


def _bounded_cbor(data, *, max_size=MAX_ASSERTION, credential=False, prefix=False):
    """Preflight the limited assertion grammar before library decoding.

    No tags (including shared references), floats or indefinite lengths
    are needed. Credential mode also permits short arrays and negative COSE
    integers; assertion mode rejects them. Reject other types before a
    generic decoder can construct cyclic structures or invoke semantic codecs.
    cbor2 supplies UTF-8 validation and duplicate-key rejection.
    """
    if type(data) is not bytes or not 1 <= len(data) <= max_size:
        raise AssertionRejected('invalid CBOR size')

    def scan(pos, depth):
        if depth > 4 or pos >= len(data):
            raise AssertionRejected('invalid CBOR structure')
        first = data[pos]
        pos += 1
        major, info = first >> 5, first & 31
        if major not in ((0, 1, 2, 3, 4, 5) if credential else (0, 2, 3, 5)) or info > 27:
            raise AssertionRejected('unsupported CBOR type')
        if info < 24:
            length = info
        else:
            width = 1 << (info - 24)
            if pos + width > len(data):
                raise AssertionRejected('truncated CBOR length')
            length = int.from_bytes(data[pos:pos + width], 'big')
            pos += width
        if major in (2, 3):
            pos += length
            if pos > len(data):
                raise AssertionRejected('truncated CBOR string')
        elif major in (4, 5):
            if length > 8:
                raise AssertionRejected('oversized CBOR map')
            for _ in range(length * (2 if major == 5 else 1)):
                pos = scan(pos, depth + 1)
        return pos

    end = scan(0, 0)
    if not prefix and end != len(data):
        raise AssertionRejected('trailing CBOR data')
    try:
        value = cbor2.loads(data[:end], max_depth=5, allow_duplicate_keys=False,
                            allow_indefinite=False)
        return (value, end) if prefix else value
    except (ValueError, TypeError, cbor2.CBORDecodeError) as exc:
        raise AssertionRejected('invalid CBOR encoding') from exc


@dataclass(frozen=True)
class AssertionPolicy:
    app_id: str
    allowed_categories: frozenset[int]
    allowed_bundle_versions: frozenset[str]
    # Fail closed by default. Legacy support must be a trusted server decision,
    # never a client-supplied iOS version or a retry after verification failure.
    require_extensions: bool = True

    def __post_init__(self):
        if (type(self.app_id) is not str or
                re.fullmatch(r'[A-Z0-9]{10}\.[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+', self.app_id) is None):
            raise ValueError('invalid configured App ID')
        if (type(self.allowed_categories) is not frozenset or not self.allowed_categories or
                any(type(x) is not int or x not in (2, 3, 4, 5) for x in self.allowed_categories)):
            raise ValueError('invalid configured validation categories')
        if (type(self.allowed_bundle_versions) is not frozenset or not self.allowed_bundle_versions or
                any(type(x) is not str or re.fullmatch(r'[A-Za-z0-9.-]{1,64}', x) is None
                    for x in self.allowed_bundle_versions)):
            raise ValueError('invalid configured bundle versions')
        if type(self.require_extensions) is not bool:
            raise ValueError('invalid extension policy')


@dataclass(frozen=True)
class VerifiedAssertion:
    counter: int
    validation_category: int | None
    bundle_version: str | None


def _category_number(value):
    # Apple's validation-guide sample carries UInt32 as four little-endian
    # bytes. Also accept an unsigned CBOR integer, as described in the prose.
    if type(value) is bytes and len(value) == 4:
        return int.from_bytes(value, 'little')
    return value


def verify_assertion(encoded, *, public_key_x962, expected_key_id,
                     client_data, previous_counter, policy):
    """Verify against trusted state, returning facts for an atomic commit.

    client_data must be rebuilt with lite_attest_request.client_data from the
    issued challenge and actual HTTP bytes; do not pass client-provided digests.
    public_key_x962/key ID must be loaded from a verified registration, not the
    request. This function neither registers keys nor persists counters.
    """
    if not isinstance(policy, AssertionPolicy):
        raise AssertionRejected('missing trusted policy')
    if type(previous_counter) is not int or not 0 <= previous_counter <= 0xffffffff:
        raise AssertionRejected('invalid stored counter')
    if type(client_data) is not bytes or not 1 <= len(client_data) <= 4096:
        raise AssertionRejected('invalid client data')
    if (type(public_key_x962) is not bytes or len(public_key_x962) != 65 or public_key_x962[0] != 4 or
            type(expected_key_id) is not bytes or len(expected_key_id) != 32 or
            not hmac.compare_digest(hashlib.sha256(public_key_x962).digest(), expected_key_id)):
        raise AssertionRejected('registered key mismatch')
    try:
        key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), public_key_x962)
    except ValueError as exc:
        raise AssertionRejected('invalid registered public key') from exc
    envelope = _bounded_cbor(encoded)
    if type(envelope) is not dict or set(envelope) != {'signature', 'authenticatorData'}:
        raise AssertionRejected('invalid assertion envelope')
    signature, auth = envelope['signature'], envelope['authenticatorData']
    if type(signature) is not bytes or not 8 <= len(signature) <= 72:
        raise AssertionRejected('invalid signature encoding')
    if type(auth) is not bytes or not 37 <= len(auth) <= 1024:
        raise AssertionRejected('invalid authenticator data')
    if not hmac.compare_digest(auth[:32], hashlib.sha256(policy.app_id.encode('ascii')).digest()):
        raise AssertionRejected('App ID mismatch')
    flags = auth[32]
    # Apple device assertions with signed version/category extensions have
    # been observed with 0xc0 despite containing no credential section. Accept
    # that exact form and still parse the entire tail as the strict extension
    # map below; never skip bytes based on the WebAuthn AT bit. Do not generally
    # allow AT, UV, backup or reserved bits. UP is not consent evidence here.
    if flags != 0xc0 and flags & ~0x81:
        raise AssertionRejected('unsupported assertion flags')
    counter = int.from_bytes(auth[33:37], 'big')
    if counter <= previous_counter:
        raise AssertionRejected('counter did not advance')
    category = version = None
    # Apple's attestation example includes a signed extension map without ED.
    # Always validate a present tail, rather than ignore it based on that bit.
    if len(auth) > 37 or flags & 0x80:
        extensions = _bounded_cbor(auth[37:])
        if type(extensions) is not dict or set(extensions) != {CATEGORY, VERSION}:
            raise AssertionRejected('unsupported assertion extensions')
        category, version = _category_number(extensions[CATEGORY]), extensions[VERSION]
        if type(category) is not int or category not in policy.allowed_categories:
            raise AssertionRejected('validation category not allowed')
        if type(version) is not str or version not in policy.allowed_bundle_versions:
            raise AssertionRejected('bundle version not allowed')
    elif policy.require_extensions:
        raise AssertionRejected('missing assertion extensions')
    nonce = hashlib.sha256(auth + hashlib.sha256(client_data).digest()).digest()
    try:
        # Apple signs the nonce as an ECDSA/SHA-256 message. The nonce itself
        # is SHA-256(authenticatorData || clientDataHash); Prehashed(nonce)
        # rejects genuine device assertions. Verified with real Apple replies.
        key.verify(signature, nonce, ec.ECDSA(hashes.SHA256()))
    except (InvalidSignature, ValueError) as exc:
        raise AssertionRejected('invalid assertion signature') from exc
    return VerifiedAssertion(counter, category, version)
