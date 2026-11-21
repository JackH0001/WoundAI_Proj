"""Read-only, operation-specific authority for disposing cross-service research data."""
import base64
import hashlib
import json
import re
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from lite_attest_state import StateUnavailable


def digest(value):
    # Identical canonical digest to the private worker's ledger, also usable
    # in the research-only image without importing any subscription modules.
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def object_key(kind, fields):
    return bytes.fromhex(digest(["research-withdrawal-issuer/1", kind, fields]))


def verify_signature(public, role, pair, encoded):
    try:
        if type(encoded) is not str or len(encoded) != 88:
            raise ValueError()
        signature = base64.b64decode(encoded, validate=True)
        if len(signature) != 64 or base64.b64encode(signature).decode() != encoded:
            raise ValueError()
        public.verify(signature, ResearchAssociations.message(role, pair))
    except (ValueError, InvalidSignature):
        raise ValueError("invalid research association signature") from None


def validate_pair(pair):
    if type(pair) is not dict or set(pair) != {'action','scope','bucket','person','native','research','operation','registration'}:
        raise ValueError('invalid research association fields')
    if pair['action'] != 'withdraw-linked-research/1' or type(pair['bucket']) is not str or not (1 <= len(pair['bucket']) <= 222):
        raise ValueError('research association purpose or bucket differs')
    for field, length in (('scope',64),('person',64),('native',32),('research',32),('operation',32),('registration',64)):
        if type(pair[field]) is not str or not re.fullmatch('[0-9a-f]{%d}' % length,pair[field]):
            raise ValueError('invalid research association identifier')


class ResearchLinkPending(StateUnavailable):
    pass


class ResearchAssociations:
    """Both independent authorities must attest exactly the same proposed disposition.

    Verification keys are supplied by trusted private configuration, never from a
    claimant or an individual receipt. The two services authenticate the caller
    in their own domain before issuing their respective proofs. The caller
    must supply independently pinned keys when integrating those issuers.
    """
    def __init__(self, native_key, research_key, proofs):
        if type(proofs) is not list or len(proofs) > 100:
            raise ValueError('bounded research association manifest required')
        keys = {}
        for role, public in (('native', native_key), ('research', research_key)):
            if type(public) is not bytes or len(public) != 32 or native_key == research_key:
                raise ValueError('distinct Ed25519 research and native authorities required')
            keys[role] = Ed25519PublicKey.from_public_bytes(public)
        self._pairs = {}
        self._owners = {}
        anchors={role:hashlib.sha256(pub.public_bytes_raw()).hexdigest() for role,pub in keys.items()}
        for proof in proofs:
            if type(proof) is not dict or set(proof) != {'pair','native','research'}:
                raise ValueError('both independent research association proofs required')
            pair = proof['pair']
            validate_pair(pair)
            for role, public_key in keys.items():
                verify_signature(public_key, role, pair, proof[role])
            index = (pair['scope'],pair['bucket'],pair['person'],pair['native'],pair['operation'])
            owner = (pair['bucket'],pair['research'])
            if index in self._pairs or (owner in self._owners and self._owners[owner]!=(pair['person'],pair['native'])):
                raise ValueError('duplicate or conflicting research association')
            self._pairs[index] = (pair.copy(),digest(dict(anchors=anchors,evidence=proof)))
            self._owners[owner] = (pair['person'],pair['native'])

    @staticmethod
    def message(role,pair):
        return ('woundailite-research-association/1:'+role+':'+digest(pair)).encode('ascii')

    def resolve(self, scope, bucket, person, native, operation, registration):
        matched=self._pairs.get((scope,bucket,person,native,operation))
        if matched is None:
            raise ResearchLinkPending('verified link between personal service and research is pending')
        pair,proof=matched
        if pair['registration']!=registration:
            raise StateUnavailable('research link refers to another native registration')
        return pair['research'],proof
