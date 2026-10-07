"""Conservative global request admission, not a billing or per-person balance.

Counts failed attempts too. One CAS reserves minute and UTC-day capacity. This
bounds admitted work across key replacement; it does not replace edge DDoS limits
or bound the cost of reading this limiter itself. Use a separate store namespace.
"""
import hashlib
import time
from lite_attest_state import CAS_ATTEMPTS, StateUnavailable, _revision


class BudgetExceeded(ValueError):
    def __init__(self, retry_after):
        self.retry_after = retry_after
        super().__init__('Lite service request budget exceeded')


class RequestBudget:
    def __init__(self, store, *, minute_limit, day_limit, clock=time.time):
        if any(type(n) is not int or n < 1 for n in (minute_limit, day_limit)):
            raise ValueError('positive trusted budget limits required')
        self.store, self.minute_limit, self.day_limit, self.clock = store, minute_limit, day_limit, clock
        # The store passed here MUST use a budget-only namespace/database.
        self.key = hashlib.sha256(b'woundlite/global-request-budget/1').digest()

    def reserve(self, *, scope='general'):
        if scope not in ('general', 'withdraw'):
            raise ValueError('unknown budget scope')
        # Independent, bounded privacy capacity. Each lane has the configured
        # minute/day limits; total admitted requests can be twice those limits.
        key = self.key if scope == 'general' else hashlib.sha256(b'woundlite/withdrawal-budget/1').digest()
        for _ in range(CAS_ATTEMPTS):
            now = self.clock()
            if type(now) not in (int, float) or not 0 <= now < 2**53:
                raise StateUnavailable('invalid budget clock')
            now = int(now); minute, day = now // 60, now // 86400
            row = self.store.load(key)
            rev = 0
            state = dict(v=1, minute=minute, day=day, minute_used=0, day_used=0)
            if row is not None:
                rev, state = row; _revision(rev)
                if (type(state) is not dict or set(state) != {'v', 'minute', 'day', 'minute_used', 'day_used'} or
                        any(type(v) is not int or v < 0 for v in state.values()) or state['v'] != 1 or
                        state['day'] != state['minute'] // 1440 or
                        state['minute_used'] > state['day_used']):
                    raise StateUnavailable('invalid request budget state')
                if minute < state['minute'] or day < state['day']:
                    raise StateUnavailable('request budget clock moved backwards')
                if minute != state['minute']:
                    state['minute'], state['minute_used'] = minute, 0
                if day != state['day']:
                    state['day'], state['day_used'] = day, 0
            if state['day_used'] >= self.day_limit:
                raise BudgetExceeded((day + 1) * 86400 - now)
            if state['minute_used'] >= self.minute_limit:
                raise BudgetExceeded((minute + 1) * 60 - now)
            state['minute_used'] += 1; state['day_used'] += 1
            if self.store.compare_exchange(key, rev, state):
                return
        raise StateUnavailable('request budget contention')
