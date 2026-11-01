"""Opt-in App-Attest-bound session families; not a client bearer refresh token.

All renewals retain the original Apple authorization time and login operation.
Generation CAS + durable receipt prevents replay from extending a newer lease.
No registry, quota, subscription, record or research mutation is performed here.
"""
from lite_account_identity import IdentityRejected
from lite_attest_state import StateUnavailable, CAS_ATTEMPTS
from lite_subscription_executor import key
from lite_subscription_runtime import SubscriptionRuntime
from lite_usage_ledger import digest, _hex


class SessionRenewals:
    immutable=staticmethod(SubscriptionRuntime.immutable)
    def __init__(self, bridge):
        self.b=bridge
        self.policy=dict(v=1,binding=bridge.grants.binding,lease_seconds=900,
            family_seconds=30*86400,apple_validation_seconds=86400)

    def _key(self, installation, operation):
        return key('human-session-family',digest([self.b.namespace,installation,operation]))

    def _row(self, installation, c):
        k=self._key(installation,c['operation']);row=self.b.store.load(k)
        if row is None:
            initial=dict(v=1,operation=c['operation'],account_sha256=digest(c['account']),
                renewal_generation=0,renewed_at=c['created_at'],expires_at=c['session_expires_at'],
                family_expires_at=c['created_at']+self.policy['family_seconds'],renewal_operation=None)
            try:self.b.store.compare_exchange(k,0,initial)
            except StateUnavailable:pass
            row=self.b.store.load(k)
        if row is None: raise StateUnavailable('session family creation unconfirmed')
        r=row[1]
        try:
            if (set(r)!={'v','operation','account_sha256','renewal_generation','renewed_at','expires_at','family_expires_at','renewal_operation'}
                    or type(r['v']) is not int or r['v']!=1 or r['operation']!=c['operation']
                    or r['account_sha256']!=digest(c['account'])
                    or any(type(r[f]) is not int for f in ('renewal_generation','renewed_at','expires_at','family_expires_at'))
                    or not 0<=r['renewal_generation']<=10000
                    or not c['created_at']<=r['renewed_at']<=self.b.now()
                    or r['family_expires_at']!=c['created_at']+self.policy['family_seconds']
                    or not r['renewed_at']<r['expires_at']<=min(r['family_expires_at'],r['renewed_at']+1200)):
                raise ValueError()
            if r['renewal_generation']==0:
                if r['renewal_operation'] is not None or r['expires_at']!=c['session_expires_at'] or r['renewed_at']!=c['created_at']:raise ValueError()
            else:
                _hex(r['renewal_operation'],32)
                if r['expires_at']>r['renewed_at']+900:raise ValueError()
        except (ValueError,TypeError,KeyError):raise StateUnavailable('session family corrupt; never reset') from None
        return row

    def _audit(self, installation, r):
        if r['renewal_generation']==0:return
        self.immutable(self.b.audit,key('human-session-renewed',digest([self.b.namespace,installation,r['operation'],r['renewal_operation']])),r)

    def effective(self, installation, c):
        r=self._row(installation,c)[1]
        # Repair only a committed rotation's missing receipt. No re-rotation.
        self._audit(installation,r)
        until=self.b.grants.fresh_until(installation,c['operation'],c['account']['identity_sha256'])
        if self.b.now()>=min(until,r['family_expires_at']):raise IdentityRejected('session renewal required')
        return dict(c,session_expires_at=r['expires_at'],**{f:r[f] for f in ('renewal_generation','renewed_at','family_expires_at')})

    def _live(self, installation, operation):
        c=self.b._live_session(installation)
        if c['operation']!=operation:raise IdentityRejected('renewal belongs to another login')
        return c

    def renew(self, installation, operation, renewal_operation, expected_generation):
        _hex(operation,32);_hex(renewal_operation,32)
        if type(expected_generation) is not int or not 0<=expected_generation<10000:
            raise IdentityRejected('bounded saved renewal generation required')
        c=self._live(installation,operation)
        r=self._row(installation,c)[1]
        if self.b.now()>=r['family_expires_at']:raise IdentityRejected('explicit sign-in required after family expiry')
        intent=dict(v=1,operation=operation,renewal_operation=renewal_operation,expected_generation=expected_generation)
        self.immutable(self.b.audit,key('human-session-renew-intent',digest([self.b.namespace,installation,renewal_operation])),intent)
        if r['renewal_operation']==renewal_operation:
            if r['renewal_generation']!=expected_generation+1:raise IdentityRejected('renewal generation differs')
            self._audit(installation,r)
            self._live(installation,operation)
            # Return exact saved receipt even if expired, never extend on retry.
            return self._public(installation,c,r)
        if r['renewal_generation']!=expected_generation:raise IdentityRejected('saved renewal is stale')
        if self.b.now()<r['expires_at']-60:raise IdentityRejected('renewal not due')
        self.b.grants.validate_for_session(installation,operation,c['account']['identity_sha256'],c['nonce'])
        for _ in range(CAS_ATTEMPTS):
            c=self._live(installation,operation);rev,r=self._row(installation,c)
            if r['renewal_operation']==renewal_operation:
                self._audit(installation,r);return self._public(installation,c,r)
            if r['renewal_generation']!=expected_generation:raise IdentityRejected('concurrent renewal won')
            n=self.b.now()
            if n>=r['family_expires_at']:raise IdentityRejected('session family expired')
            updated=dict(r,renewal_generation=expected_generation+1,renewed_at=n,
                expires_at=min(n+900,r['family_expires_at'],self.b.grants.fresh_until(installation,operation,c['account']['identity_sha256'])),renewal_operation=renewal_operation)
            try:committed=self.b.store.compare_exchange(self._key(installation,operation),rev,updated)
            except StateUnavailable:committed=False
            if committed or self._row(installation,c)[1]==updated:
                self._audit(installation,updated)
                self._live(installation,operation)
                return self._public(installation,c,updated)
        raise StateUnavailable('renewal not confirmed; retain saved request')

    @staticmethod
    def _public(installation,c,r):
        return dict(v=1,state='signed_in',installation=installation,operation=c['operation'],
            account_sha256=digest(c['account']['subject']),app_account_token=c['account']['token'],
            expires_at=r['expires_at'],**{f:r[f] for f in ('renewal_generation','renewed_at','family_expires_at')})
