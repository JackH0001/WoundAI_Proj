"""Purpose-separated provenance for a research capture, not research consent.

The native issuer route authenticates the service session; the research receiver
independently verifies its App Attest owner, consent epoch and writer fence.
Both routes require explicit trusted injection and are off by default. The claim
binds the image hash; the receiver independently freezes exact depth bytes.
"""
import base64
import hashlib
import json
import math
import re
import time
import uuid
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from lite_reconsent import epoch
from lite_result_contract import validate_result

TTL = 600
CONSENTS = frozenset({'2026-10-16.1'})
FIELDS = {'schema', 'scope', 'bucket', 'native_installation', 'account_sha256',
          'registration_sha256', 'research_installation', 'consent_epoch',
          'consent_version', 'capture_id', 'image_sha256', 'source', 'operation',
          'model', 'result', 'result_sha256'}


class SourceRejected(ValueError): pass
class SourceExpired(SourceRejected): pass


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(value): return hashlib.sha256(encoded(value)).hexdigest()


def hexid(value, length):
    if type(value) is not str or re.fullmatch('[0-9a-f]{'+str(length)+'}', value) is None:
        raise SourceRejected('invalid source identifier')


def validate_claim(p):
    if type(p) is not dict or set(p) != FIELDS or p['schema'] != 'lite.research-source/1':
        raise SourceRejected('invalid source claim')
    for k in ('scope','account_sha256','registration_sha256','image_sha256'): hexid(p[k],64)
    for k in ('native_installation','research_installation'): hexid(p[k],32)
    if (type(p['bucket']) is not str or re.fullmatch(r'[a-z0-9][a-z0-9-]{1,61}[a-z0-9]',p['bucket']) is None
            or type(p['capture_id']) is not str or str(uuid.UUID(p['capture_id']))!=p['capture_id']
            or type(p['consent_version']) is not str or p['consent_version'] not in CONSENTS):
        raise SourceRejected('invalid target or capture')
    epoch(p['consent_epoch'])
    if p['source']=='service':
        hexid(p['operation'],32); hexid(p['model'],64); hexid(p['result_sha256'],64)
        validate_result(p['result'])
        if sha(p['result']) != p['result_sha256']: raise SourceRejected('result digest differs')
    elif p['source']=='manual':
        if any(p[k] is not None for k in ('operation','model','result','result_sha256')):
            raise SourceRejected('manual source cannot claim model output')
    else: raise SourceRejected('unknown source')
    if len(encoded(p))>12288: raise SourceRejected('source claim too large')
    return p


def message(p): return b'woundailite/research-source/1\0'+encoded(p)
def ticket_message(p, issued, expires):
    return b'woundailite/research-source-ticket/1\0'+encoded(dict(sha256=sha(p),issued=issued,expires=expires))


def signature(value):
    if type(value) is not str or len(value)!=88: raise SourceRejected('invalid signature size')
    raw=base64.b64decode(value,validate=True)
    if len(raw)!=64 or base64.b64encode(raw).decode()!=value: raise SourceRejected('invalid signature')
    return raw


class ResearchSourceVerifier:
    def __init__(self, *, scope, bucket, public_key, clock=time.time):
        hexid(scope,64)
        if type(bucket) is not str or re.fullmatch(r'[a-z0-9][a-z0-9-]{1,61}[a-z0-9]',bucket) is None:
            raise ValueError('explicit research bucket required')
        self.key=Ed25519PublicKey.from_public_bytes(public_key)
        if not callable(clock): raise ValueError('trusted clock required')
        self.scope,self.bucket,self.clock=scope,bucket,clock

    def verify(self, ticket, *, owner, consent_epoch, allow_expired=False):
        if type(allow_expired) is not bool: raise SourceRejected('explicit expiry policy required')
        hexid(owner,32); epoch(consent_epoch)
        if type(ticket) is not dict or set(ticket)!={'claim','signature','issued','expires','ticket_signature'}:
            raise SourceRejected('exact source ticket required')
        p=validate_claim(ticket['claim'])
        if (p['scope'],p['bucket'],p['research_installation'],p['consent_epoch'])!=(self.scope,self.bucket,owner,consent_epoch):
            raise SourceRejected('source destination differs')
        self.key.verify(signature(ticket['signature']),message(p))
        start,end=ticket['issued'],ticket['expires']; now=self.clock()
        if (type(start) is not int or type(end) is not int or type(now) not in (int,float)
                or not math.isfinite(now) or not 0<=start<=now<2**53
                or end!=start+TTL): raise SourceRejected('source interval differs')
        self.key.verify(signature(ticket['ticket_signature']),ticket_message(p,start,end))
        if now>=end and not allow_expired: raise SourceExpired('renew source ticket without another inference')
        return p
