"""Durable per-owner writer drain before withdrawal inventory.

No expiring lease: a paused process can resume after a clock deadline. An
unreleased ticket therefore remains pending, even after restart. Operators must
prove the writer is terminal before any future recovery operation removes it.
This trades liveness after a crash for never falsely claiming cleanup complete.
Use a dedicated CAS namespace; admission is not itself owner authentication.
"""
from dataclasses import dataclass
import hashlib
import re
import secrets
from lite_attest_state import CAS_ATTEMPTS, StateUnavailable, _revision


class OwnerWithdrawn(ValueError): pass
class WriterLimit(ValueError): pass


@dataclass(frozen=True)
class WriterTicket:
    owner: str
    identifier: str


class PrivacyState:
    MAX_WRITERS = 8

    def __init__(self, store):
        self.store = store

    def _key(self, owner):
        if type(owner) is not str or re.fullmatch(r'[A-Za-z0-9_-]{1,128}', owner) is None:
            raise StateUnavailable('invalid privacy owner')
        return hashlib.sha256(b'woundlite/privacy/1\0' + owner.encode()).digest()

    def _load(self, owner):
        row = self.store.load(self._key(owner))
        if row is None:
            return 0, dict(v=1, owner=owner, withdrawn=False, writers=[])
        revision, state = row; _revision(revision)
        if (type(state) is not dict or set(state) != {'v', 'owner', 'withdrawn', 'writers'} or
                type(state['v']) is not int or state['v'] != 1 or state['owner'] != owner or
                type(state['withdrawn']) is not bool or type(state['writers']) is not list or
                len(state['writers']) > self.MAX_WRITERS or
                any(type(v) is not str or re.fullmatch(r'[0-9a-f]{32}', v) is None for v in state['writers']) or
                len(state['writers']) != len(set(state['writers']))):
            raise StateUnavailable('invalid privacy state')
        # No mutation of state shared by a store adapter or another caller.
        return revision, dict(state, writers=list(state['writers']))

    def begin_write(self, owner):
        token = secrets.token_hex(16)
        for _ in range(CAS_ATTEMPTS):
            revision, state = self._load(owner)
            if state['withdrawn']:
                raise OwnerWithdrawn('owner withdrew research data')
            if len(state['writers']) >= self.MAX_WRITERS:
                raise WriterLimit('too many in-flight writers')
            if token in state['writers']:
                raise StateUnavailable('writer ticket collision')
            state['writers'].append(token)
            if self.store.compare_exchange(self._key(owner), revision, state):
                return WriterTicket(owner, token)
        raise StateUnavailable('writer admission contention')

    def finish_write(self, ticket):
        if not isinstance(ticket, WriterTicket):
            raise StateUnavailable('invalid writer ticket')
        for _ in range(CAS_ATTEMPTS):
            revision, state = self._load(ticket.owner)
            if revision == 0:
                raise StateUnavailable('writer state disappeared')
            if ticket.identifier not in state['writers']:
                return  # Idempotent release after a lost acknowledgement.
            state['writers'].remove(ticket.identifier)
            if self.store.compare_exchange(self._key(ticket.owner), revision, state):
                return
        raise StateUnavailable('writer release contention')

    def withdraw(self, owner):
        """Permanently close admission, returning remaining active writers."""
        for _ in range(CAS_ATTEMPTS):
            revision, state = self._load(owner)
            if state['withdrawn']:
                return len(state['writers'])
            state['withdrawn'] = True
            if self.store.compare_exchange(self._key(owner), revision, state):
                return len(state['writers'])
        raise StateUnavailable('withdrawal contention')

    def is_withdrawn(self, owner):
        return self._load(owner)[1]['withdrawn']
