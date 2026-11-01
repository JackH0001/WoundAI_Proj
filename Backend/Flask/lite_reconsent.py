"""Explicit renewed consent, same App Attest identity, separate immutable epoch.

Never reopen a withdrawn write fence or delete a tombstone. Old requests and
even delayed DELETEs remain confined to the old namespace. No cloud clients.
The HTTP caller must first verify the signed body and installation ownership.
"""
import hashlib
import json
import re
import time
from lite_attest_state import CAS_ATTEMPTS, StateUnavailable
from lite_fenced_store import FencedPrivacy

DISCLOSURE = '2026-10-21.rejoin.1'
CONSENT = '2026-10-16.1'

class ReconsentRejected(ValueError): pass

def epoch(value):
    if value != 'legacy' and (type(value) is not str or re.fullmatch(r'[0-9a-f]{32}', value) is None):
        raise ReconsentRejected('invalid consent epoch')
    return value

def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()

class Reconsent:
    def __init__(self, privacy, clock=time.time):
        if not isinstance(privacy, FencedPrivacy) or privacy.manifest.epoch != 'legacy':
            raise StateUnavailable('root fenced coordinator required')
        self.privacy, self.store, self.clock = privacy, privacy.manifest.store, clock

    def key(self, owner, suffix='active'):
        self.privacy.manifest._key(owner)  # strict identity validation
        return hashlib.sha256(b'woundlite/reconsent/1\0' + self.privacy.objects.bucket.name.encode()
            + b'\0' + owner.encode() + b'\0' + suffix.encode()).digest()

    def _state(self, owner):
        row = self.store.load(self.key(owner))
        if row is None: return 0, None, None, None
        generation, state = row
        if (type(state) is not dict or type(state.get('v')) is not int or state['v'] not in (1,2)
                or set(state) != ({'v','owner','epoch','audit_sha256'} |
                    ({'retirement_sha256'} if state['v']==2 else set())) or state['owner'] != owner):
            raise StateUnavailable('invalid consent state')
        current = epoch(state['epoch'])
        retirement = None
        if state['v']==2:
            h=state['retirement_sha256']
            if type(h) is not str or re.fullmatch(r'[0-9a-f]{64}',h) is None:
                raise StateUnavailable('invalid retirement proof')
            proof=self.store.load(self.key(owner,'retired.'+h))
            retirement=None if proof is None else proof[1]
            previous=None if current=='legacy' else dict(v=1,owner=owner,epoch=current,audit_sha256=state['audit_sha256'])
            if (type(retirement) is not dict or set(retirement)!={'v','action','owner','bucket',
                    'closure_sha256','association_sha256','previous'} or type(retirement['v']) is not int
                    or retirement['v']!=1 or retirement['action']!='account_deletion'
                    or retirement['owner']!=owner or retirement['bucket']!=self.privacy.objects.bucket.name
                    or retirement['previous']!=previous
                    or any(type(retirement[n]) is not str or re.fullmatch(r'[0-9a-f]{64}',retirement[n]) is None
                           for n in ('closure_sha256','association_sha256'))
                    or hashlib.sha256(encoded(retirement)).hexdigest()!=h):
                raise StateUnavailable('missing or inconsistent retirement evidence')
        if current=='legacy':
            if state['v']!=2 or state['audit_sha256'] is not None:
                raise StateUnavailable('invalid retired legacy epoch')
            return generation,state,None,retirement
        audit = self.store.load(self.key(owner, state['epoch']))
        if audit is None or hashlib.sha256(encoded(audit[1])).hexdigest() != state['audit_sha256']:
            raise StateUnavailable('missing consent audit')
        event = audit[1]
        if event.get('owner') != owner or event.get('new_epoch') != state['epoch']:
            raise StateUnavailable('consent audit identity mismatch')
        return generation,state,event,retirement

    def load(self, owner):
        generation,state,event,_=self._state(owner)
        return generation,'legacy' if state is None else state['epoch'],event

    def retirement(self, owner):
        return self._state(owner)[3]

    def retire(self, owner, *, closure_sha256, association_sha256):
        """Trusted account-deletion worker only; no HTTP action or payload deletion.

        Retirement and reconsent contend on the SAME generation. A losing
        reconsent cannot publish a new epoch. An earlier winning reconsent is
        included in the next retirement attempt. Unknown audit/CAS results are
        read back; original consent events remain immutable and readable.
        """
        for h in (closure_sha256,association_sha256):
            if type(h) is not str or re.fullmatch(r'[0-9a-f]{64}',h) is None:
                raise ValueError('exact closure and association references required')
        for _ in range(CAS_ATTEMPTS):
            revision,state,_,retired=self._state(owner)
            if retired is not None:
                if (retired['closure_sha256'],retired['association_sha256'])!=(closure_sha256,association_sha256):
                    raise StateUnavailable('installation retired under a different closure')
                return retired
            event=dict(v=1,action='account_deletion',owner=owner,bucket=self.privacy.objects.bucket.name,
                closure_sha256=closure_sha256,association_sha256=association_sha256,previous=state)
            h=hashlib.sha256(encoded(event)).hexdigest();ak=self.key(owner,'retired.'+h)
            proof=self.store.load(ak)
            if proof is None:
                try:self.store.compare_exchange(ak,0,event)
                except StateUnavailable:pass
                proof=self.store.load(ak)
            if proof is None or proof[1]!=event:raise StateUnavailable('retirement intent unconfirmed')
            value=dict(v=2,owner=owner,epoch='legacy' if state is None else state['epoch'],
                audit_sha256=None if state is None else state['audit_sha256'],retirement_sha256=h)
            try:
                if self.store.compare_exchange(self.key(owner),revision,value):return event
            except StateUnavailable:
                saved=self._state(owner)[3]
                if saved==event:return saved
                raise StateUnavailable('retirement checkpoint unconfirmed') from None
        raise StateUnavailable('retirement contention; resume original closure')

    def resolve(self, owner, supplied):
        supplied = epoch(supplied)
        _,state,_,retired=self._state(owner)
        if retired is not None:raise ReconsentRejected('installation retired by account deletion')
        if ('legacy' if state is None else state['epoch']) != supplied:
            raise ReconsentRejected('consent epoch changed')
        return self.privacy.scoped(supplied)

    def resume(self, owner, request):
        required = {'anon_id', 'research_consent', 'previous_epoch', 'new_epoch', 'consent_version', 'disclosure_version'}
        if (type(request) is not dict or set(request) != required or request['anon_id'] != owner or
                request['research_consent'] is not True or
                any(type(request[k]) is not str or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,95}', request[k]) is None
                    for k in ('consent_version', 'disclosure_version'))):
            raise ReconsentRejected('invalid consent request')
        previous, new = epoch(request['previous_epoch']), epoch(request['new_epoch'])
        if new == 'legacy' or new == previous: raise ReconsentRejected('new epoch required')
        revision,state,existing,retired=self._state(owner)
        if retired is not None:raise ReconsentRejected('installation retired by account deletion')
        active='legacy' if state is None else state['epoch']
        request_sha = hashlib.sha256(encoded(request)).hexdigest()
        if active == new:
            if existing['request_sha256'] != request_sha or self.privacy.scoped(new).is_withdrawn(owner):
                raise ReconsentRejected('renewal retired or conflicting')
            if self._state(owner)[3] is not None:
                raise ReconsentRejected('installation retired by account deletion')
            return self.receipt(owner, new, existing)
        if request['consent_version'] != CONSENT or request['disclosure_version'] != DISCLOSURE:
            raise ReconsentRejected('explicit current consent required')
        if active != previous: raise ReconsentRejected('previous epoch changed')
        old = self.privacy.scoped(previous)
        generation, fence = old.manifest._load(owner)
        if generation == 0 or not fence['withdrawn'] or fence['writers']:
            raise ReconsentRejected('withdrawal not completed')
        refs = old.objects.list_refs(owner)
        for ref in refs:
            snapshot = old.objects._snapshot(ref)
            if snapshot is None or snapshot.state != 'sealed' or old.objects._read(ref, snapshot) != b'':
                raise ReconsentRejected('live payload remains')
        # Old namespace stays permanently withdrawn; concurrent old cleanup
        # may continue, but cannot touch any object in the new namespace.
        fresh = self.privacy.scoped(new)
        if fresh.manifest._load(owner)[0] != 0 or fresh.objects.list_refs(owner):
            raise ReconsentRejected('epoch already used')
        event = dict(v=1, action='reconsent_future_only', owner=owner, previous_epoch=previous, new_epoch=new,
            request_sha256=request_sha, consent_version=CONSENT, disclosure_version=DISCLOSURE,
            previous_fence_generation=generation, previous_fence=fence,
            sealed_markers=len(refs), previous_audit_sha256=(hashlib.sha256(encoded(existing)).hexdigest() if existing else None),
            at=int(self.clock()))
        audit_key = self.key(owner, new)
        audit = self.store.load(audit_key)
        if audit is None:
            self.store.compare_exchange(audit_key, 0, event)
            audit = self.store.load(audit_key)
        if (audit is None or audit[1].get('request_sha256') != request_sha or audit[1].get('previous_fence') != fence or
                any(audit[1].get(k) != event[k] for k in ('v', 'action', 'owner', 'previous_epoch', 'new_epoch',
                    'consent_version', 'disclosure_version', 'previous_audit_sha256', 'sealed_markers'))):
            raise StateUnavailable('consent audit not confirmed')
        event = audit[1]
        state = dict(v=1, owner=owner, epoch=new, audit_sha256=hashlib.sha256(encoded(event)).hexdigest())
        if not self.store.compare_exchange(self.key(owner), revision, state):
            raise StateUnavailable('consent changed; retry the same renewal')
        return self.receipt(owner, new, event)

    @staticmethod
    def receipt(owner, new, event):
        return dict(status='reconsented', anon_id=owner, consent_epoch=new, previous_epoch=event['previous_epoch'],
            consent_version=event['consent_version'], disclosure_version=event['disclosure_version'],
            audit_sha256=hashlib.sha256(encoded(event)).hexdigest(), scope='future_captures_only',
            historical_media_restored=False)
