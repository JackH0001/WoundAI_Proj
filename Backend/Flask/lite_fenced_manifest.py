"""Durable write plans for candidate public HTTP routes; not deployed.

Order: pin object generation -> register plan under owner CAS -> conditional
write -> acknowledge -> finish. A lost acknowledgement retains the plan. Closing
admission freezes the plan set; every remaining object can then be sealed even
if the worker crashed. No clock lease or unproven 'worker dead' deletion exists.

Finishing pending plans is not whole-owner withdrawal: completed objects still
require a fresh owner inventory and seals, and legacy namespaces need migration.
"""
from dataclasses import dataclass
import hashlib
import re
import secrets
from lite_attest_state import CAS_ATTEMPTS, StateUnavailable, _revision
from lite_fenced_objects import ObjectRef, PinnedWrite, FencedObjects, GenerationConflict
from lite_privacy_state import OwnerWithdrawn, WriterLimit


class PendingWritePlans(StateUnavailable): pass


@dataclass(frozen=True)
class FenceTicket:
    owner: str
    identifier: str


class FencedManifest:
    MAX_WRITERS = 8
    MAX_PLANS = 10

    def __init__(self, store, *, bucket):
        if type(bucket) is not str or re.fullmatch(r'[a-z0-9][a-z0-9-]{1,61}[a-z0-9]', bucket) is None:
            raise ValueError('explicit bucket binding required')
        self.store, self.bucket = store, bucket

    def _key(self, owner):
        if type(owner) is not str or re.fullmatch(r'[0-9a-f]{32}', owner) is None:
            raise StateUnavailable('invalid owner')
        return hashlib.sha256(b'woundlite/write-plans/1\0' + self.bucket.encode() + b'\0' + owner.encode()).digest()

    def _load(self, owner):
        row = self.store.load(self._key(owner))
        if row is None:
            return 0, dict(v=1, owner=owner, bucket=self.bucket, withdrawn=False, writers={})
        revision, state = row; _revision(revision)
        try:
            if (type(state) is not dict or set(state) != {'v', 'owner', 'bucket', 'withdrawn', 'writers'} or
                    type(state['v']) is not int or state['v'] != 1 or state['owner'] != owner or
                    state['bucket'] != self.bucket or type(state['withdrawn']) is not bool or
                    type(state['writers']) is not dict or len(state['writers']) > self.MAX_WRITERS):
                raise ValueError()
            writers = {}
            for token, plans in state['writers'].items():
                if (type(token) is not str or re.fullmatch(r'[0-9a-f]{32}', token) is None or
                        type(plans) is not dict or len(plans) > self.MAX_PLANS):
                    raise ValueError()
                copied = {}
                for name, values in plans.items():
                    kind, identifier = name.split(':')
                    ObjectRef(owner, kind, identifier).name
                    if (type(values) is not list or len(values) != 2 or type(values[0]) is not int or
                            values[0] < 0 or type(values[1]) is not bool):
                        raise ValueError()
                    copied[name] = list(values)
                writers[token] = copied
            return revision, dict(state, writers=writers)
        except (ValueError, TypeError, AttributeError):
            raise StateUnavailable('invalid durable write plans') from None

    def _ticket(self, ticket):
        if not isinstance(ticket, FenceTicket) or re.fullmatch(r'[0-9a-f]{32}', ticket.identifier) is None:
            raise StateUnavailable('invalid fence ticket')
        self._key(ticket.owner)

    def begin(self, owner):
        token = secrets.token_hex(16)
        for _ in range(CAS_ATTEMPTS):
            revision, state = self._load(owner)
            if state['withdrawn']: raise OwnerWithdrawn('research withdrawn')
            if len(state['writers']) >= self.MAX_WRITERS: raise WriterLimit('writers busy')
            if token in state['writers']: raise StateUnavailable('ticket collision')
            state['writers'][token] = {}
            if self.store.compare_exchange(self._key(owner), revision, state): return FenceTicket(owner, token)
        raise StateUnavailable('write-plan admission contention')

    def plan(self, ticket, pin):
        self._ticket(ticket)
        if (not isinstance(pin, PinnedWrite) or pin.bucket != self.bucket or pin.ref.owner != ticket.owner or
                type(pin.generation) is not int or pin.generation < 0):
            raise StateUnavailable('write plan scope mismatch')
        pin.ref.name
        name = pin.ref.kind + ':' + pin.ref.identifier
        for _ in range(CAS_ATTEMPTS):
            revision, state = self._load(ticket.owner)
            if state['withdrawn']: raise OwnerWithdrawn('research withdrawn')
            plans = state['writers'].get(ticket.identifier)
            if plans is None: raise StateUnavailable('ticket no longer active')
            if name in plans:
                if plans[name][0] != pin.generation: raise StateUnavailable('cannot rebase registered write plan')
                return
            if len(plans) >= self.MAX_PLANS: raise StateUnavailable('write plan capacity exceeded')
            plans[name] = [pin.generation, False]
            if self.store.compare_exchange(self._key(ticket.owner), revision, state): return
        raise StateUnavailable('write plan contention')

    def acknowledge(self, ticket, pin):
        self._ticket(ticket)
        if (not isinstance(pin, PinnedWrite) or pin.bucket != self.bucket or pin.ref.owner != ticket.owner or
                type(pin.generation) is not int or pin.generation < 0):
            raise StateUnavailable('acknowledgement scope mismatch')
        name = pin.ref.kind + ':' + pin.ref.identifier
        for _ in range(CAS_ATTEMPTS):
            revision, state = self._load(ticket.owner)
            plans = state['writers'].get(ticket.identifier)
            if plans is None: raise StateUnavailable('ticket no longer active')
            if name not in plans or plans[name][0] != pin.generation: raise StateUnavailable('write not registered')
            plans[name][1] = True
            if self.store.compare_exchange(self._key(ticket.owner), revision, state): return
        raise StateUnavailable('write acknowledgement outcome unknown')

    def finish(self, ticket):
        self._ticket(ticket)
        for _ in range(CAS_ATTEMPTS):
            revision, state = self._load(ticket.owner)
            plans = state['writers'].get(ticket.identifier)
            if revision == 0: raise StateUnavailable('writer manifest disappeared')
            if plans is None: return
            if any(not value[1] for value in plans.values()): raise PendingWritePlans('unconfirmed writes retain their plans')
            del state['writers'][ticket.identifier]
            if self.store.compare_exchange(self._key(ticket.owner), revision, state): return
        raise StateUnavailable('writer completion outcome unknown')

    def is_withdrawn(self, owner):
        return self._load(owner)[1]["withdrawn"]

    def withdraw(self, owner):
        for _ in range(CAS_ATTEMPTS):
            revision, state = self._load(owner)
            if state['withdrawn']: return len(state['writers'])
            state['withdrawn'] = True
            if self.store.compare_exchange(self._key(owner), revision, state): return len(state['writers'])
        raise StateUnavailable('write plan withdrawal contention')

    def write_registered(self, ticket, objects, ref, data):
        """Single integration entry point: no payload write before durable intent."""
        self._ticket(ticket)
        if (not isinstance(objects, FencedObjects) or objects.bucket.name != self.bucket or
                not isinstance(ref, ObjectRef) or ref.owner != ticket.owner):
            raise StateUnavailable('registered write scope mismatch')
        pin = objects.pin(ref)
        self.plan(ticket, pin)
        try:
            generation = objects.write(pin, data)
        except GenerationConflict:
            # A definitive 412 means this one-shot write did not commit. An
            # unknown timeout is deliberately NOT handled here and retains intent.
            self.acknowledge(ticket, pin)
            raise
        self.acknowledge(ticket, pin)
        return generation

    def seal_pending(self, owner, objects):
        if not isinstance(objects, FencedObjects) or objects.bucket.name != self.bucket:
            raise StateUnavailable('recovery bucket mismatch')
        for _ in range(CAS_ATTEMPTS):
            revision, state = self._load(owner)
            if not state['withdrawn']: raise StateUnavailable('must close admission before sealing')
            if not state['writers']: return 0
            # No new plan can join after withdrawal. Seal acknowledged plans too;
            # an interrupted writer may have persisted only part of its payload.
            for plans in state['writers'].values():
                for name in plans:
                    kind, identifier = name.split(':')
                    objects.seal(ObjectRef(owner, kind, identifier))
            state['writers'] = {}
            if self.store.compare_exchange(self._key(owner), revision, state): return 0
            # Acknowledge/finish races only remove work or change acknowledgement
            # flags; a fresh pass still verifies each remaining object's seal.
        raise StateUnavailable('sealed plan confirmation contention')
