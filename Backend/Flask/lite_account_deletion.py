"""Opt-in account deletion intake and bounded Apple-revocation worker.

HTTP never calls Apple or claims data erasure. A trusted worker advances frozen
grant inventory with durable checkpoints. Data disposition is a separate lane;
until its reviewed executor exists, account_deleted always remains False.
"""
import secrets
from flask import Blueprint, g, jsonify, request
from audit_chain_contract import loads_json_object_strict
from lite_account_identity import AccountIdentityBridge, IdentityRejected
from lite_apple_oauth import GrantUncertain
from lite_attest_state import CAS_ATTEMPTS, StateUnavailable
from lite_subscription_executor import key
from lite_subscription_runtime import SubscriptionRuntime
from lite_usage_ledger import digest, _hex
from lite_deletion_identity import DeletionIdentity, DeletionIdentityExpired

PREFIX = '/api/v1/lite/account/deletion/'
DELETION_ACTIONS = ('request','status','reauth/challenge','reauth/confirm')
DELETION_PATHS = frozenset(PREFIX + s for s in DELETION_ACTIONS)


class DeletionBusy(StateUnavailable): pass


class AccountDeletion:
    immutable = staticmethod(SubscriptionRuntime.immutable)
    LEASE_SECONDS = 120

    def __init__(self, *, bridge, store, audit_store):
        if (not isinstance(bridge, AccountIdentityBridge) or bridge.lifecycle is None
                or bridge.grants is None or bridge.grants.lifecycle is not bridge.lifecycle):
            raise ValueError('matching indexed identity and grant authorities required')
        if store is audit_store or (getattr(store, 'path', None) is not None
                and getattr(store, 'path', None) == getattr(audit_store, 'path', None)):
            raise ValueError('deletion progress and audit must be separate')
        self.bridge, self.lifecycle, self.grants = bridge, bridge.lifecycle, bridge.grants
        self.store, self.audit = store, audit_store
        self.disposition = None
        self.scope = self.lifecycle.scope
        self.reauthentication = DeletionIdentity(self)
        self.immutable(store, key('account-deletion-config', bridge.namespace),
                       dict(v=1, scope=self.scope, lease_seconds=self.LEASE_SECONDS))

    def _job_key(self, person):
        _hex(person)
        return key('account-deletion-job', digest([self.scope, person]))

    def attach_disposition(self, disposition):
        # Explicit candidate composition only; keep legacy startup import-safe.
        from lite_disposition_progress import DispositionProgress
        if not isinstance(disposition, DispositionProgress) or disposition.deletion is not self:
            raise ValueError('matching disposition progress required')
        if self.disposition is not None and self.disposition is not disposition:
            raise ValueError('disposition already attached')
        self.disposition = disposition

    def _plan(self, person):
        row = self.lifecycle._read(person)
        if row is None or row[1]['phase'] != 'closing': raise IdentityRejected('account closure required')
        c = row[1]
        intent = self.lifecycle.progress(person, c['closure']['requester'], c['closure']['operation'])
        return dict(v=1, scope=self.scope, person=person, operation=intent['operation'],
                    requester=intent['requester'], requested_at=intent['requested_at'],
                    inventory_sha256=intent['inventory_sha256'], head=c['head'], total=c['count'])

    def _read(self, person, plan):
        row = self.store.load(self._job_key(person))
        if row is None: return None
        c = row[1]
        try:
            if (type(c) is not dict or set(c) != {'v','plan','phase','cursor','remaining','revoked','scan','claim'}
                    or type(c['v']) is not int or c['v'] != 1 or c['plan'] != plan
                    or c['phase'] not in ('ready','running','pending','revoked')
                    or any(type(c[s]) is not int for s in ('remaining','revoked','scan'))
                    or not 0 <= c['remaining'] <= plan['total']
                    or not 0 <= c['revoked'] <= plan['total'] - c['remaining']
                    or not 1 <= c['scan'] <= 1000000
                    or (c['cursor'] is None) != (c['remaining'] == 0)):
                raise ValueError()
            if c['cursor'] is not None: _hex(c['cursor'])
            if c['phase'] in ('pending', 'revoked'):
                if c['remaining'] != 0: raise ValueError()
                if (c['revoked'] == plan['total']) != (c['phase'] == 'revoked'): raise ValueError()
            if c['phase'] == 'running':
                q = c['claim']
                if (type(q) is not dict or set(q) != {'id','started_at','expires_at'}
                        or type(q['started_at']) is not int or type(q['expires_at']) is not int
                        or not 0 <= q['started_at'] <= self.lifecycle.now()
                        or q['expires_at'] != q['started_at'] + self.LEASE_SECONDS): raise ValueError()
                _hex(q['id'],32)
            elif c['claim'] is not None: raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise StateUnavailable('deletion progress corrupt; never reset') from None
        return row

    def _ensure(self, person):
        plan = self._plan(person)
        self.immutable(self.audit, key('account-deletion-plan', digest(plan)), plan)
        row = self._read(person, plan)
        if row is None:
            initial = dict(v=1, plan=plan, phase='ready', cursor=plan['head'],
                           remaining=plan['total'], revoked=0, scan=1, claim=None)
            try: self.store.compare_exchange(self._job_key(person), 0, initial)
            except StateUnavailable: pass
            row = self._read(person, plan)
        if row is None: raise StateUnavailable('deletion checkpoint unconfirmed')
        return plan, row

    def _owner(self, installation, operation):
        _hex(operation,32)
        account = self.bridge.registry.registration(installation)
        if not account['subject'].startswith('apple-'): raise IdentityRejected('verified Apple account required')
        person = account['subject'][6:]
        self.lifecycle.progress(person, installation, operation)
        return person

    def begin(self, installation, operation):
        if not self.reauthentication.resume(installation,operation):
            self.bridge.begin_account_closure(installation, operation)
        return self.status(installation, operation)

    def status(self, installation, operation):
        # An already-closed request must remain readable without renewing an
        # active session. HTTP still requires the same device's App Attest.
        person = self._owner(installation, operation)
        _, row = self._ensure(person)
        self._record_scan(row[1])
        base = self._public(row[1])
        return self.disposition.decorate(person, base) if self.disposition is not None else base

    def _record_scan(self, c):
        if c['phase'] in ('pending','revoked'):
            evidence = dict(v=1, plan_sha256=digest(c['plan']), scan=c['scan'], total=c['plan']['total'],
                            revoked=c['revoked'], apple_revocation='confirmed' if c['phase']=='revoked' else 'pending')
            self.immutable(self.audit, key('account-deletion-scan', digest(evidence)), evidence)

    @staticmethod
    def _public(c):
        p = c['plan']
        return dict(v=1, state='account_deletion_pending', operation=p['operation'],
                    installation=p['requester'], requested_at=p['requested_at'],
                    inventory_sha256=p['inventory_sha256'], grant_total=p['total'],
                    revocation_scan=c['scan'], grants_examined=p['total']-c['remaining'], grants_revoked=c['revoked'],
                    apple_revocation=('confirmed' if c['phase']=='revoked' else
                        'pending' if c['phase']=='pending' else 'in_progress' if c['phase']=='running'
                        or c['remaining'] < p['total'] else 'not_started'),
                    data_disposition='not_started', account_deleted=False,
                    subscription_cancelled=False, local_records_preserved=True)

    def advance(self, person, *, limit=20):
        """Trusted worker only. At most limit grants; no data cleanup or scheduler.

        Revocation is externally idempotent. Lease recovery may repeat an Apple
        revoke if a response was lost, but never a code exchange, refresh, model
        call, or quota debit. A stale worker cannot advance the new checkpoint.
        """
        if type(limit) is not int or not 1 <= limit <= 100: raise ValueError('bounded revocation batch required')
        plan, row = self._ensure(person)
        for _ in range(CAS_ATTEMPTS):
            rev, c = row
            if c['phase'] == 'revoked':
                self._record_scan(c)
                return self._public(c)
            now = self.lifecycle.now()
            if c['phase'] == 'running' and now < c['claim']['expires_at']:
                raise DeletionBusy('deletion worker still active')
            if c['phase'] == 'pending':
                if c['scan'] == 1000000: raise StateUnavailable('deletion recovery requires review')
                c = dict(c, cursor=plan['head'], remaining=plan['total'], revoked=0, scan=c['scan']+1)
            claimed = dict(c, phase='running', claim=dict(id=secrets.token_hex(16),started_at=now,
                                                        expires_at=now+self.LEASE_SECONDS))
            # Uncertain claim ownership must never dispatch an external call.
            if self.store.compare_exchange(self._job_key(person), rev, claimed): break
            row = self._read(person, plan)
            if row is None: raise StateUnavailable('deletion job disappeared')
        else: raise DeletionBusy('deletion worker contention')
        owned = self._read(person, plan)
        if owned is None or owned[1] != claimed: raise DeletionBusy('deletion claim superseded')
        cursor, remaining, revoked = c['cursor'], c['remaining'], c['revoked']
        for _ in range(min(limit, remaining)):
            live = self._read(person, plan)
            if live is None or live[1] != claimed or self.lifecycle.now() >= claimed['claim']['expires_at']:
                raise DeletionBusy('deletion lease expired; resume saved checkpoint')
            n = self.lifecycle._node(person, cursor)
            if n['position'] != remaining: raise StateUnavailable('frozen grant sequence differs')
            # No caller-provided grant or cursor can skip inventory entries.
            try:
                receipt = self.grants.revoke(n['installation'], n['operation'])
                if receipt != dict(v=1,state='apple_token_revoked',grant_id=n['grant_id']):
                    raise StateUnavailable('revocation receipt mismatch')
                outcome = 'revoked'
            except GrantUncertain: outcome = 'pending'
            except IdentityRejected: outcome = 'rejected'
            except StateUnavailable: outcome = 'unavailable'
            evidence = dict(v=1, plan_sha256=digest(plan), grant_id=n['grant_id'],
                            scan=c['scan'], claim=claimed['claim']['id'], outcome=outcome)
            self.immutable(self.audit, key('account-deletion-grant-attempt', digest(evidence)), evidence)
            if outcome == 'revoked': revoked += 1
            cursor, remaining = n['previous'], remaining-1
        if (cursor is None) != (remaining == 0): raise StateUnavailable('frozen grant sequence incomplete')
        phase = 'ready' if remaining else 'revoked' if revoked == plan['total'] else 'pending'
        updated = dict(claimed, phase=phase, cursor=cursor, remaining=remaining, revoked=revoked, claim=None)
        if self.lifecycle.now() >= claimed['claim']['expires_at']:
            raise DeletionBusy('deletion lease expired after provider response')
        try: self.store.compare_exchange(self._job_key(person), owned[0], updated)
        except StateUnavailable: pass
        saved = self._read(person, plan)
        if saved is None or saved[1] != updated: raise DeletionBusy('deletion checkpoint unconfirmed')
        self._record_scan(updated)
        return self._public(updated)


def blueprint(coordinator):
    if not isinstance(coordinator, AccountDeletion): raise ValueError('trusted deletion coordinator required')
    bp = Blueprint('lite_account_deletion', __name__)
    def action(kind):
        try:
            admission = getattr(g,'lite_admission',None)
            if admission is None: return jsonify(error='lite_attestation_required'),401
            b = loads_json_object_strict(request.get_data().decode())
            fields = {'anon_id','operation'} | ({'confirmation'} if kind=='request' else set())
            if kind.startswith('reauth/'):
                fields |= {'reauth_operation','account_sha256'}
                fields |= {'confirmation'} if kind=='reauth/challenge' else {'identity_token','state'}
            if (request.mimetype != 'application/json' or set(b) != fields
                    or kind == 'request' and b['confirmation'] != 'delete_account'
                    or kind == 'reauth/challenge' and b['confirmation'] != 'continue_deletion'):
                raise ValueError('explicit deletion confirmation required')
            if b['anon_id'] != admission.installation: return jsonify(error='lite_owner_mismatch'),403
            if kind.startswith('reauth/'):
                args = (admission.installation,b['operation'],b['reauth_operation'],b['account_sha256'])
                if kind == 'reauth/challenge':
                    return jsonify(coordinator.reauthentication.challenge(*args)),200
                return jsonify(coordinator.reauthentication.confirm(*args,b['identity_token'],b['state'])),202
            result = (coordinator.begin if kind=='request' else coordinator.status)(admission.installation,b['operation'])
            return jsonify(result),202
        except DeletionIdentityExpired: return jsonify(error='lite_deletion_reauth_expired',keep_local=True),410
        except IdentityRejected: return jsonify(error='lite_deletion_identity_required',keep_local=True),401
        except (ValueError,TypeError,KeyError): return jsonify(error='lite_deletion_conflict',keep_local=True),409
        except Exception: return jsonify(error='lite_deletion_unconfirmed',keep_local=True,retry_saved_request=True),503
    for kind in DELETION_ACTIONS:
        bp.add_url_rule(PREFIX+kind,kind.replace('/','_'),lambda kind=kind:action(kind),methods=['POST'])
    @bp.after_request
    def no_cache(response):
        response.headers['Cache-Control']='no-store'
        response.headers['Retry-After']='5'
        return response
    return bp
