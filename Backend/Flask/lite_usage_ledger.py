"""Durable daily inference reservations, deliberately not installed in HTTP yet.

Trusted caller supplies service identity, server-issued UTC day and a stable
semantic operation id/digest. Neither App Attest transport counters nor research
epochs are billing identities. Inject a dedicated CAS store; no clients, IAM,
credentials, deletion or automatic legacy migration are created here.

Reserved/running slots consume success capacity. Only explicitly opted-in v2
days expire unclaimed reservations; legacy histories are not reinterpreted. An
unknown write outcome NEVER authorizes inference. A claimed job never expires or
re-executes automatically. This trades availability for bounded paid work.
"""
from dataclasses import dataclass
import copy
import hashlib
import json
import math
import re
import time
from lite_attest_state import CAS_ATTEMPTS, StateUnavailable, _revision, _encode


class UsageConflict(ValueError): pass


class UsageLimit(ValueError):
    def __init__(self, reason, retry_after):
        self.reason, self.retry_after = reason, retry_after
        super().__init__(reason)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


def _hex(value, length=64):
    if type(value) is not str or re.fullmatch(r'[0-9a-f]{%d}' % length, value) is None:
        raise UsageConflict('invalid opaque identifier')
    return value


def _subject(value):
    if type(value) is not str or re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value) is None:
        raise UsageConflict('invalid verified service subject')
    return value


def _integer(value):
    if type(value) is not int or not 0 <= value < 2**53:
        raise UsageConflict('invalid integer')
    return value


@dataclass(frozen=True)
class DailyGrant:
    """Internal trusted-policy result, never deserialize from an HTTP body.

    A short snapshot bounds a refund/expiry race. In-flight work already admitted
    is honored; no claim of instantaneous revocation across distributed services.
    """
    subject: str
    limit: int
    proof: str
    issued_at: int
    valid_until: int

    def validate(self, subject, now):
        if (self.subject != _subject(subject) or type(self.limit) is not int or self.limit not in (2, 10)
                or not _integer(self.issued_at) <= now < _integer(self.valid_until)
                or self.valid_until - self.issued_at > 60):
            raise UsageConflict('invalid or expired server grant')
        _hex(self.proof)


def free_grant(subject, now):
    _subject(subject); _integer(now)
    return DailyGrant(subject, 2, digest({'policy': 'free2/v1'}), now, now+30)


@dataclass(frozen=True)
class Ticket:
    subject: str
    day: int
    operation: str
    request_digest: str


class UsageLedger:
    ATTEMPT_LIMIT = 30
    TERMINAL = {'succeeded', 'empty', 'failed', 'cancelled'}

    def __init__(self, store, *, namespace, clock=time.time, cutover=None):
        if type(namespace) is not str or re.fullmatch(r'[a-z0-9_-]{1,64}', namespace) is None:
            raise ValueError('dedicated usage namespace required')
        self.store, self.namespace, self.clock = store, namespace, clock
        if cutover is not None:
            from lite_usage_cutover import CutoverPolicy
            if not isinstance(cutover, CutoverPolicy): raise ValueError('trusted cutover policy required')
        self.cutover = cutover

    def now(self):
        n = self.clock()
        if type(n) not in (int, float) or not math.isfinite(n) or not 0 <= n < 2**53:
            raise StateUnavailable('invalid server clock')
        return int(n)

    def key(self, subject, day):
        _subject(subject); _integer(day)
        return hashlib.sha256(('woundlite/usage/1\0'+self.namespace+'\0'+subject+'\0'+str(day)).encode()).digest()

    def _load(self, subject, day):
        row = self.store.load(self.key(subject, day))
        if row is None:
            raise StateUnavailable('day not initialized: verified legacy baseline required')
        revision, value = row; _revision(revision)
        try:
            fields = {'v', 'subject_hash', 'day', 'baseline', 'entries', 'updated'}
            if type(value) is dict and value.get('v') == 2: fields |= {'policy', 'fences'}
            if (type(value) is not dict or set(value) != fields
                    or type(value['v']) is not int or value['v'] not in (1, 2)
                    or value['subject_hash'] != digest(subject) or value['day'] != day
                    or type(value['day']) is not int):
                raise ValueError('identity/schema')
            _integer(value['updated'])
            b = value['baseline']
            if type(b) is not dict or set(b) != {'successes', 'attempts', 'manifest'}:
                raise ValueError('baseline')
            if _integer(b['attempts']) < _integer(b['successes']):
                raise ValueError('baseline counters')
            _hex(b['manifest'])
            if value['v'] == 2:
                if self.cutover is None: raise ValueError('v2 requires pinned cutover')
                policy = self.cutover.read(self.namespace, self.now())
                if (value['policy'] != self._policy_fields(policy) or day <= policy['day']
                        or b != self._zero_baseline(subject, day, policy)):
                    raise ValueError('day policy/baseline mismatch')
                fences = value['fences']
                if type(fences) is not dict: raise ValueError('fences')
                for op, fence in fences.items():
                    _hex(op, 32)
                    if type(fence) is not dict or set(fence) != {'digest', 'at', 'proof'}:
                        raise ValueError('fence schema')
                    _hex(fence['digest'])
                    if not (day+1)*86400 <= _integer(fence['at']) <= value['updated']:
                        raise ValueError('fence time')
                    if fence['proof'] != self._fence_proof(Ticket(subject, day, op, fence['digest'])):
                        raise ValueError('fence proof')
            entries = value['entries']
            fences = value.get('fences', {})
            if (type(entries) is not dict or len(entries) + len(fences) > self.ATTEMPT_LIMIT
                    or entries.keys() & fences.keys()):
                raise ValueError('entry bound')
            for op, e in entries.items():
                _hex(op, 32)
                if type(e) is not dict or set(e) != {'digest', 'proof', 'limit', 'worker', 'history'}:
                    raise ValueError('entry schema')
                _hex(e['digest']); _hex(e['proof'])
                if type(e['limit']) is not int or e['limit'] not in (2, 10):
                    raise ValueError('grant')
                h = e['history']
                if type(h) is not list or not 1 <= len(h) <= 3:
                    raise ValueError('history length')
                statuses = []
                previous = day*86400
                for event in h:
                    if type(event) is not list or len(event) not in (2, 3):
                        raise ValueError('event')
                    at = _integer(event[0]); status = event[1]
                    if type(status) is not str or not previous <= at <= value['updated']:
                        raise ValueError('event ordering')
                    previous = at; statuses.append(status)
                    if status in self.TERMINAL:
                        if len(event) != 3: raise ValueError('receipt missing')
                        _hex(event[2])
                    elif len(event) != 2:
                        raise ValueError('unexpected receipt')
                allowed = [['reserved'], ['reserved', 'running'], ['reserved', 'cancelled']]
                allowed += [['reserved', 'running', s] for s in ('succeeded', 'empty', 'failed')]
                if statuses not in allowed or h[0][0] // 86400 != day:
                    raise ValueError('state transition')
                if 'running' in statuses:
                    _hex(e['worker'], 32)
                elif e['worker'] is not None:
                    raise ValueError('unexpected worker')
            _encode(value)  # same 16 KiB limit as the actual GCS/SQLite adapter
        except (KeyError, ValueError, TypeError, IndexError) as exc:
            raise StateUnavailable('usage state corrupt; no reset or inferred balance') from exc
        if value['updated'] > self.now():
            raise StateUnavailable('server clock moved backwards')
        return revision, copy.deepcopy(value)

    @staticmethod
    def _policy_fields(policy):
        return dict(sha256=digest(policy), reservation_seconds=policy['reservation_seconds'])

    @staticmethod
    def _zero_baseline(subject, day, policy):
        return dict(successes=0, attempts=0, manifest=digest(['verified-post-cutover-day/1',
                    digest(policy), subject, day]))

    def _ensure_current_day(self, subject, day):
        """Called only after trusted service-account/grant resolution, never by HTTP input."""
        if self.cutover is None or self.store.load(self.key(subject, day)) is not None: return
        now = self.now(); policy = self.cutover.read(self.namespace, now, admission=True)
        if day != now//86400 or day <= policy['day']:
            raise StateUnavailable('verified cutover-day baseline required')
        value = dict(v=2, subject_hash=digest(subject), day=day,
            baseline=self._zero_baseline(subject, day, policy), entries={}, updated=now,
            policy=self._policy_fields(policy), fences={})
        self.store.compare_exchange(self.key(subject, day), 0, value)
        self._load(subject, day)  # uncertain write/readback never authorizes work

    def _active(self, subject, day):
        """Expire only unclaimed v2 reservations, on the same row as claim CAS."""
        for _ in range(CAS_ATTEMPTS):
            rev, state = self._load(subject, day)
            if state['v'] == 1: return rev, state
            now = self.now(); changed = False
            for op, e in state['entries'].items():
                deadline = e['history'][0][0] + state['policy']['reservation_seconds']
                if e['history'][-1][1] == 'reserved' and now >= deadline:
                    proof = digest(['unstarted-lease-expiry/1', self.namespace, subject, day,
                                    op, e['digest'], deadline, state['policy']['sha256']])
                    e['history'].append([now, 'cancelled', proof]); changed = True
            if not changed: return rev, state
            state['updated'] = now
            if self.store.compare_exchange(self.key(subject, day), rev, state):
                continue  # return actual persisted revision; do not fabricate one
        raise StateUnavailable('lease contention; no inferred release')

    def _admission(self):
        if self.cutover is not None: self.cutover.read(self.namespace, self.now(), admission=True)

    def _fence_proof(self, ticket):
        return digest(['missing-reservation-fence/1', self.namespace, ticket.subject,
                       ticket.day, ticket.operation, ticket.request_digest])

    def fence_missing(self, ticket):
        """Backend recovery primitive; NOT an API or permission to create a successor.

        The caller must separately pin the durable operation/image and persist a
        unique successor mapping before exposing any retry. A competing old
        reservation either wins first (return None; recover it) or loses this CAS.
        v1 histories/missing daily rows are never silently upgraded or fabricated.
        """
        _subject(ticket.subject); _integer(ticket.day); _hex(ticket.operation, 32); _hex(ticket.request_digest)
        if ticket.day >= self.now()//86400: raise UsageConflict('only retired day may be fenced')
        for _ in range(CAS_ATTEMPTS):
            rev, state = self._load(ticket.subject, ticket.day)
            if ticket.operation in state['entries']:
                self._entry(state, ticket); return None
            if state['v'] != 2: raise StateUnavailable('legacy day requires explicit reconciliation')
            old = state['fences'].get(ticket.operation)
            if old is not None:
                if old['digest'] != ticket.request_digest: raise UsageConflict('fenced input changed')
                return old['proof']
            if len(state['entries']) + len(state['fences']) >= self.ATTEMPT_LIMIT:
                raise StateUnavailable('bounded fence capacity exhausted; manual reconciliation required')
            now = self.now(); proof = self._fence_proof(ticket)
            state['fences'][ticket.operation] = dict(digest=ticket.request_digest, at=now, proof=proof)
            state['updated'] = now
            if self.store.compare_exchange(self.key(ticket.subject, ticket.day), rev, state): return proof
        raise StateUnavailable('fence contention; do not authorize a successor')

    def initialize_day(self, subject, day, *, baseline_successes, baseline_attempts, manifest_sha256):
        """Trusted cutover/control operation, never an end-user reset endpoint.

        The caller must pause legacy writers and verify their baseline, even if
        zero. Existing rows are never replaced, and old days cannot be created.
        """
        now = self.now(); _integer(day)
        if day != now // 86400 or _integer(baseline_attempts) < _integer(baseline_successes):
            raise UsageConflict('invalid current-day migration baseline')
        _hex(manifest_sha256)
        baseline = dict(successes=baseline_successes, attempts=baseline_attempts, manifest=manifest_sha256)
        value = dict(v=1, subject_hash=digest(_subject(subject)), day=day, baseline=baseline, entries={}, updated=now)
        self.store.compare_exchange(self.key(subject, day), 0, value)
        _, observed = self._load(subject, day)
        if observed['baseline'] != baseline:
            raise UsageConflict('existing baseline cannot be changed')

    @staticmethod
    def _counts(value):
        entries = value['entries'].values()
        success = value['baseline']['successes'] + sum(e['history'][-1][1] == 'succeeded' for e in entries)
        pending = sum(e['history'][-1][1] in ('reserved', 'running') for e in entries)
        return success, pending, value['baseline']['attempts'] + len(value['entries']) + len(value.get('fences', {}))

    def snapshot(self, subject, day, grant):
        grant.validate(subject, self.now())
        self._ensure_current_day(subject, day)
        _, value = self._active(subject, day)
        used, pending, attempts = self._counts(value)
        return dict(day=day, used=used, pending=pending, attempts=attempts, limit=grant.limit,
                    remaining=max(0, grant.limit-used-pending), attempt_limit=self.ATTEMPT_LIMIT)

    def reserve(self, subject, day, operation, request_digest, grant):
        _hex(operation, 32); _hex(request_digest)
        ticket = Ticket(_subject(subject), _integer(day), operation, request_digest)
        for _ in range(CAS_ATTEMPTS):
            rev, state = self._active(subject, day)
            if operation in state['entries']:
                self._entry(state, ticket)  # same id with changed input is a conflict
                return ticket  # terminal/pending readback is allowed after expiry/downgrade
            if operation in state.get('fences', {}): raise UsageConflict('retired operation fenced')
            now = self.now()
            if day != now // 86400:
                raise UsageConflict('new work requires current server-issued UTC day')
            if not isinstance(grant, DailyGrant):
                raise UsageConflict('trusted server grant required')
            grant.validate(subject, now)
            self._admission()
            # The trusted fence read can take time. Recheck the day/grant before
            # proposing the write; a later concurrent fence still wins via CAS.
            now = self.now()
            if day != now//86400 or now < state['updated']:
                raise UsageConflict('day/clock changed during admission')
            grant.validate(subject, now)
            used, pending, attempts = self._counts(state)
            retry = max(1, (day+1)*86400-now)
            if attempts >= self.ATTEMPT_LIMIT:
                raise UsageLimit('attempt_quota', retry)
            if used + pending >= grant.limit:
                raise UsageLimit('success_quota', retry)
            state['entries'][operation] = dict(digest=request_digest, proof=grant.proof,
                limit=grant.limit, worker=None, history=[[now, 'reserved']])
            state['updated'] = now
            if self.store.compare_exchange(self.key(subject, day), rev, state):
                return ticket
        raise StateUnavailable('usage contention; retry the same operation')

    @staticmethod
    def _entry(state, ticket):
        _hex(ticket.operation, 32); _hex(ticket.request_digest)
        e = state['entries'].get(ticket.operation)
        if e is None or e['digest'] != ticket.request_digest:
            raise UsageConflict('operation missing or changed')
        return e

    def claim(self, ticket, worker):
        """True is the ONLY authorization to run once. False must never run.

        Even the same worker retry after a lost CAS response returns False.
        There is no timeout takeover because the previous computation may live.
        """
        _hex(worker, 32)
        for _ in range(CAS_ATTEMPTS):
            rev, state = self._active(ticket.subject, ticket.day); e = self._entry(state, ticket)
            if e['history'][-1][1] != 'reserved': return False
            self._admission()
            now = self.now()
            if now < state['updated']: raise StateUnavailable('server clock moved backwards')
            if state['v'] == 2 and now >= e['history'][0][0] + state['policy']['reservation_seconds']:
                continue
            e['worker'] = worker; e['history'].append([now, 'running'])
            state['updated'] = now
            if self.store.compare_exchange(self.key(ticket.subject, ticket.day), rev, state): return True
        raise StateUnavailable('claim contention')

    def settle(self, ticket, worker, outcome, receipt_sha256):
        if outcome not in ('succeeded', 'empty', 'failed'):
            raise UsageConflict('explicit known execution result required')
        _hex(worker, 32); _hex(receipt_sha256)
        for _ in range(CAS_ATTEMPTS):
            rev, state = self._load(ticket.subject, ticket.day); e = self._entry(state, ticket)
            last = e['history'][-1]
            if e['worker'] != worker:
                raise UsageConflict('different executor')
            if last[1] in self.TERMINAL:
                if last[1:] != [outcome, receipt_sha256]:
                    raise UsageConflict('terminal receipt cannot change')
                return self.receipt(ticket)
            if last[1] != 'running': raise UsageConflict('execution not claimed')
            now = self.now(); e['history'].append([now, outcome, receipt_sha256]); state['updated'] = now
            if self.store.compare_exchange(self.key(ticket.subject, ticket.day), rev, state):
                return self.receipt(ticket)
        raise StateUnavailable('settlement contention')

    def cancel_unstarted(self, ticket, receipt_sha256):
        _hex(receipt_sha256)
        for _ in range(CAS_ATTEMPTS):
            rev, state = self._load(ticket.subject, ticket.day); e = self._entry(state, ticket)
            last = e['history'][-1]
            if last[1] == 'cancelled' and last[2] == receipt_sha256: return self.receipt(ticket)
            if last[1] != 'reserved': raise UsageConflict('started or completed work cannot be cancelled')
            now = self.now(); e['history'].append([now, 'cancelled', receipt_sha256]); state['updated'] = now
            if self.store.compare_exchange(self.key(ticket.subject, ticket.day), rev, state): return self.receipt(ticket)
        raise StateUnavailable('cancellation contention')

    def receipt(self, ticket):
        _, state = self._load(ticket.subject, ticket.day); e = self._entry(state, ticket); last = e['history'][-1]
        return dict(day=ticket.day, operation=ticket.operation, request_digest=e['digest'],
                    outcome=last[1], result_sha256=last[2] if len(last)==3 else None,
                    grant_sha256=e['proof'], admitted_limit=e['limit'])

    def active_receipt(self, ticket):
        self._active(ticket.subject, ticket.day)
        return self.receipt(ticket)

    def expired_reservation_proof(self, ticket):
        """Recognize the existing immutable lease receipt; never infer from age alone."""
        _, state = self._active(ticket.subject, ticket.day)
        e = self._entry(state, ticket)
        if state['v'] != 2 or e['history'][-1][1] != 'cancelled': return None
        deadline = e['history'][0][0] + state['policy']['reservation_seconds']
        proof = digest(['unstarted-lease-expiry/1', self.namespace, ticket.subject, ticket.day,
                        ticket.operation, e['digest'], deadline, state['policy']['sha256']])
        if e['history'][-1][0] < deadline or e['history'][-1][2] != proof: return None
        return proof
