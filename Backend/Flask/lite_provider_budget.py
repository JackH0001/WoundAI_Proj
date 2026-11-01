"""Finite durable provider allocations; no transport, route or default activation.

Reserve a whole attempt before provider work. Lost allocation acknowledgements
burn that slice: durable evidence never restores a live caller's authority. A
transport must reserve response capacity before sending and enforce that bound
while reading. Counters cover payload, not TLS overhead or budget-journal I/O.
"""
import copy
import math
import threading
import time
import uuid
from lite_attest_state import StateUnavailable
from lite_store_locations import LimitedStateStore, location, overlaps
from lite_subscription_executor import key
from lite_usage_ledger import digest, _hex, _subject


class BudgetUnavailable(StateUnavailable): pass


def stamp(clock):
    n = clock()
    if type(n) not in (int, float) or not math.isfinite(n) or not 0 <= n < 2**53:
        raise BudgetUnavailable('provider budget clock unavailable')
    return n


def validate_policy(policy, expected):
    _hex(expected)
    p = copy.deepcopy(policy)
    if (type(p) is not dict or set(p) != {'v','namespace','manifest','source','image','not_before',
            'expires_at','max_allocations','requests','send_bytes','receive_bytes','seconds'}
            or type(p['v']) is not int or p['v'] != 1 or digest(p) != expected):
        raise ValueError('pinned provider budget policy required')
    _subject(p['namespace']); _hex(p['manifest']); _hex(p['source'], 40); _hex(p['image'])
    names = ('not_before','expires_at','max_allocations','requests','send_bytes','receive_bytes','seconds')
    if (any(type(p[n]) is not int for n in names)
            or not 0 <= p['not_before'] < p['expires_at'] <= p['not_before']+3600
            or not 1 <= p['max_allocations'] <= 20 or not 1 <= p['requests'] <= 10000
            or p['max_allocations']*p['requests'] > 100000
            or not 1 <= p['send_bytes'] <= 32*1024**2 or not 1 <= p['receive_bytes'] <= 512*1024**2
            or p['max_allocations']*p['send_bytes'] > 256*1024**2
            or p['max_allocations']*p['receive_bytes'] > 4*1024**3
            or not 1 <= p['seconds'] <= 300):
        raise ValueError('finite provider allocations required')
    return p


class ProviderBudget:
    """A CAS journal for an explicitly reviewed finite execution plan.

    The journal needs separately bounded control-plane access. No automatic
    retries of allocation, refill, refund, renewal, or old-lease recovery exist.
    """
    def __init__(self, *, policy, expected, store, audit_store, clock=time.time, monotonic=time.monotonic):
        self.policy = validate_policy(policy, expected)
        if not callable(clock) or not callable(monotonic) or overlaps(location(store), location(audit_store)):
            raise ValueError('separate budget state/audit and trusted clocks required')
        self.identity, self.store, self.clock, self.monotonic = expected, store, clock, monotonic
        self.audit = (audit_store if isinstance(audit_store, LimitedStateStore)
                      else LimitedStateStore(audit_store, mode='create'))
        if self.audit.mode != 'create': raise ValueError('create-only budget audit required')
        self.namespace = self.policy['namespace']
        self._read()

    def _key(self, name): return key('provider-budget-'+name, self.namespace)

    def _immutable(self, store, name, value):
        row = store.load(name)
        if row is None:
            try: store.compare_exchange(name, 0, value)
            except StateUnavailable: pass
            row = store.load(name)
        if row is None or row[1] != value:
            raise BudgetUnavailable('original provider budget evidence unavailable')

    def _read(self):
        binding = self.store.load(self._key('binding'))
        row = self.store.load(self._key('state'))
        if row is None:
            if binding is not None: raise BudgetUnavailable('bound provider budget missing; never reset')
            return None
        j = row[1]
        if (binding is None or binding[1] != dict(v=1, policy=self.identity)
                or type(j) is not dict or set(j) != {'v','policy','allocations'}
                or type(j['v']) is not int or j['v'] != 1 or j['policy'] != self.identity
                or type(j['allocations']) is not dict
                or not 1 <= len(j['allocations']) <= self.policy['max_allocations']):
            raise BudgetUnavailable('provider budget state corrupt')
        for name, a in j['allocations'].items():
            _hex(name, 32)
            if (type(a) is not dict or set(a) != {'issued_at','expires_at'}
                    or any(type(a[n]) is not int for n in a)
                    or not self.policy['not_before'] <= a['issued_at'] < self.policy['expires_at']
                    or a['expires_at'] != min(a['issued_at']+self.policy['seconds'], self.policy['expires_at'])):
                raise BudgetUnavailable('provider allocation corrupt')
        for name, value in (('policy', self.policy), ('checkpoint', j)):
            proof = self.audit.load(key('provider-budget-'+name, digest(value)))
            if proof is None or proof[1] != value:
                raise BudgetUnavailable('original provider budget audit missing')
        return row

    def status(self):
        row = self._read()
        n = 0 if row is None else len(row[1]['allocations'])
        return dict(v=1, policy=self.identity, allocated_attempts=n,
            remaining_attempts=self.policy['max_allocations']-n,
            reserved={k:n*self.policy[k] for k in ('requests','send_bytes','receive_bytes')},
            refills_allowed=False, payload_only=True, control_plane_included=False)

    def allocate(self):
        now = stamp(self.clock)
        if not self.policy['not_before'] <= now < self.policy['expires_at']:
            raise BudgetUnavailable('provider allocation window closed')
        row = self._read()
        allocations = {} if row is None else copy.deepcopy(row[1]['allocations'])
        if len(allocations) >= self.policy['max_allocations']:
            raise BudgetUnavailable('provider allocation budget exhausted')
        name = uuid.uuid4().hex
        value = dict(issued_at=int(now), expires_at=min(int(now)+self.policy['seconds'],self.policy['expires_at']))
        allocations[name] = value
        j = dict(v=1, policy=self.identity, allocations=allocations)
        self._immutable(self.audit,key('provider-budget-policy',self.identity),self.policy)
        self._immutable(self.audit,key('provider-budget-checkpoint',digest(j)),j)
        self._immutable(self.store,self._key('binding'),dict(v=1,policy=self.identity))
        try: won = self.store.compare_exchange(self._key('state'),0 if row is None else row[0],j)
        except StateUnavailable: won = False
        if won is not True:
            raise BudgetUnavailable('provider allocation not exclusively acknowledged; do not dispatch')
        confirmed = self._read()
        if confirmed is None or confirmed[1]['allocations'].get(name) != value:
            raise BudgetUnavailable('provider allocation readback unavailable')
        # Readback alone never grants authority. This path requires our own
        # acknowledged CAS; there is deliberately no recover(name) operation.
        return Allocation(self.policy, name, value, clock=self.clock, monotonic=self.monotonic)


class Allocation:
    """Thread-safe, fail-closed payload meter for one acknowledged attempt."""
    def __init__(self, policy, name, value, *, clock, monotonic):
        self.name, self.clock, self.monotonic = name, clock, monotonic
        self.limits = {n:policy[n] for n in ('requests','send_bytes','receive_bytes')}
        self.used = dict.fromkeys(self.limits, 0)
        self.expires = value['expires_at']
        self.last_wall = stamp(clock); self.last_mono = stamp(monotonic)
        self.mono_end = self.last_mono+max(0, self.expires-self.last_wall)
        self.closed = None; self.lock = threading.Lock(); self.completed = 0; self.received = 0
        self.by_provider = dict(gcs=0, kms=0, apple=0, metadata=0)
        self._check()

    def _fail(self, reason):
        self.closed = reason
        raise BudgetUnavailable('provider allocation '+reason)

    def _check(self):
        if self.closed is not None: self._fail(self.closed)
        try: wall, mono = stamp(self.clock), stamp(self.monotonic)
        except StateUnavailable: self._fail('clock_unavailable')
        if wall < self.last_wall or mono < self.last_mono: self._fail('clock_regressed')
        self.last_wall, self.last_mono = wall, mono
        if wall >= self.expires or mono >= self.mono_end: self._fail('expired')

    def begin(self, *, provider, method, send_bytes, response_limit):
        if (provider not in self.by_provider or method not in ('GET','POST','PUT','DELETE','PATCH')
                or type(send_bytes) is not int or not 0 <= send_bytes <= 1024**2
                or type(response_limit) is not int or not 1 <= response_limit <= 1024**2):
            raise ValueError('bounded provider payload reservation required')
        with self.lock:
            self._check()
            charge = dict(requests=1,send_bytes=send_bytes,receive_bytes=response_limit)
            if any(self.used[n]+v > self.limits[n] for n,v in charge.items()): self._fail('exhausted')
            for n,v in charge.items(): self.used[n] += v
            self.by_provider[provider] += 1
            # Response capacity is reserved BEFORE transport and never refunded,
            # including network errors and HTTP refusal. No silent SDK retries.
            return ResponseReservation(self, response_limit)

    def snapshot(self):
        with self.lock:
            return dict(used=dict(self.used),limits=dict(self.limits),providers=dict(self.by_provider),
                response_bytes_observed=self.received,completed_responses=self.completed,
                closed=self.closed,refunded=0,payload_only=True)


class ResponseReservation:
    def __init__(self, allocation, limit):
        self.allocation, self.limit, self.received, self.ended = allocation, limit, 0, False

    def timeout(self, maximum=10):
        if type(maximum) not in (int,float) or not math.isfinite(maximum) or maximum <= 0:
            raise ValueError('finite timeout required')
        with self.allocation.lock:
            self.allocation._check()
            if self.ended: raise BudgetUnavailable('response already closed')
            return min(maximum,self.allocation.expires-self.allocation.last_wall,
                       self.allocation.mono_end-self.allocation.last_mono)

    def read_size(self, maximum=4096):
        if type(maximum) is not int or maximum < 1: raise ValueError('positive read bound required')
        with self.allocation.lock:
            self.allocation._check()
            if self.ended: raise BudgetUnavailable('response already closed')
            remaining = self.limit-self.received
            if remaining == 0: self.allocation._fail('response_limit')
            return min(maximum,remaining)

    def received_bytes(self, count):
        with self.allocation.lock:
            self.allocation._check()
            if self.ended: raise BudgetUnavailable('response already closed')
            if type(count) is not int or not 0 <= count <= self.limit-self.received:
                self.allocation._fail('response_limit')
            self.received += count; self.allocation.received += count

    def finish(self):
        with self.allocation.lock:
            self.allocation._check()
            if self.ended: raise BudgetUnavailable('response already closed')
            self.ended = True; self.allocation.completed += 1
