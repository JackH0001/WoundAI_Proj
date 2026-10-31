"""Opt-in composition of real deletion components, never a full-erasure claim.

Only the bounded internal worker calls providers or changes result payloads.
HTTP observes durable, audit-bound receipts; it never starts cleanup. Research,
version history and other retained account data still need their own executors.
"""
from pathlib import Path
from abc import ABC, abstractmethod
from lite_account_deletion import AccountDeletion
from lite_account_identity import IdentityRejected
from lite_attest_state import CAS_ATTEMPTS, StateUnavailable
from lite_grant_key_custody import AccountGrantCipher
from lite_service_disposition import ServiceResultDisposition
from lite_subscription_executor import key
from lite_subscription_runtime import SubscriptionRuntime
from lite_usage_ledger import digest

PENDING_LANES = ['historical_versions_backups', 'research_association_disposition',
                 'identity_subscription_retention', 'live_provider_device_acceptance']
CATALOG = digest(dict(v=1, service='current_slot_seal', grant_key='kms_account_version',
                      pending_lanes=PENDING_LANES))


def aliases(a, b):
    if a is b: return True
    if getattr(a, 'path', None) is not None and getattr(b, 'path', None) is not None:
        return Path(a.path).resolve() == Path(b.path).resolve()
    ab, bb = getattr(a, 'bucket', None), getattr(b, 'bucket', None)
    return (ab is not None and bb is not None and getattr(a, 'prefix', None) == getattr(b, 'prefix', None)
            and (ab is bb or getattr(ab, 'name', None) is not None and ab.name == getattr(bb, 'name', None)))


class ResearchDispositionLane(ABC):
    """Private composition contract, not an authority exposed to HTTP callers.

    Keep the general/native/key-worker packages independent of the research
    implementation. A trusted composition supplies a concrete validated lane;
    it cannot be selected by configuration strings or user request payloads.
    """
    @abstractmethod
    def advance(self, person, *, limit=20, apply=False):raise NotImplementedError

    @abstractmethod
    def status(self, person):raise NotImplementedError


class DispositionProgress:
    immutable = staticmethod(SubscriptionRuntime.immutable)

    def __init__(self, *, deletion, results, cipher, store, audit_store):
        if (not isinstance(deletion, AccountDeletion) or not isinstance(results, ServiceResultDisposition)
                or not isinstance(cipher, AccountGrantCipher) or results.life is not deletion.lifecycle
                or cipher.lifecycle is not deletion.lifecycle or deletion.grants.cipher is not cipher):
            raise ValueError('matching indexed deletion, results and account keys required')
        peers = (deletion.store, deletion.audit, deletion.lifecycle.store, deletion.lifecycle.audit,
                 deletion.grants.store, deletion.grants.audit, results.store, results.audit,
                 results.ops.store, results.ops.ledger.store, cipher.store, cipher.audit)
        if aliases(store, audit_store) or any(aliases(s, p) for s in (store, audit_store) for p in peers):
            raise ValueError('disposition progress and audit require independent stores')
        self.deletion, self.results, self.cipher = deletion, results, cipher
        self.store, self.audit = store, audit_store
        self.research = None
        self.immutable(store, key('disposition-progress-config', deletion.scope),
                       dict(v=1, scope=deletion.scope, catalog=CATALOG))

    def attach_research(self, worker):
        """Explicit private-worker composition; public routes still only observe.

        v2 intentionally keeps research/history pending until full historical
        retention and provider acceptance exist. Current-media sealing alone
        cannot remove that outward pending lane.
        """
        from lite_store_locations import location, overlaps
        if not isinstance(worker,ResearchDispositionLane) or worker.d is not self.deletion:
            raise ValueError('matching account research media worker required')
        if self.research is not None and self.research is not worker:
            raise ValueError('research worker already attached')
        peers=(self.store,self.audit,self.results.store,self.results.audit,self.results.ops.store,
            self.results.ops.ledger.store,self.cipher.store,self.cipher.audit)
        if any(overlaps(location(s),location(p)) for s in
                (worker.store,worker.audit,worker.c.store,worker.c.audit,worker.r.store) for p in peers):
            raise ValueError('research worker cannot share service or progress scopes')
        self.research=worker

    def _plan(self, person):
        p = self.deletion._plan(person)
        s = self.results.plan(person)
        if (p['scope'], p['operation'], p['inventory_sha256']) != (s['scope'], s['closure_operation'], s['inventory_sha256']):
            raise StateUnavailable('disposition inventories differ')
        return dict(v=1, catalog=CATALOG, deletion=p, service=s)

    def _key(self, plan): return key('disposition-progress', digest(plan))

    def _validate_service(self, p, r):
        s = p['service']
        if (type(r) is not dict or set(r) != {'v','scope','inventory_sha256','closure_operation','total','sealed',
                'service_result_payloads','account_deleted','quota_reset','research_data_changed',
                'historical_versions_erased','physical_backups_erased'}
                or type(r['v']) is not int or r['v'] != 1
                or any(r[n] != s[n] for n in ('scope','inventory_sha256','closure_operation','total'))
                or type(r['total']) is not int or type(r['sealed']) is not int or not 0 <= r['sealed'] <= r['total']
                or r['service_result_payloads'] != ('current_sealed' if r['sealed']==r['total'] else 'pending')
                or any(r[n] is not False for n in ('account_deleted','quota_reset','research_data_changed',
                                                  'historical_versions_erased','physical_backups_erased'))):
            raise StateUnavailable('service disposition receipt differs')

    def _validate_key(self, p, r):
        d = p['deletion']
        if (type(r) is not dict or set(r) != {'v','scope','person','closure','version','kms_state',
                'grant_key_unrecoverable','physical_backup_deletion_confirmed','account_deleted','data_disposition'}
                or type(r['v']) is not int or r['v'] != 1 or r['scope'] != d['scope'] or r['person'] != d['person']
                or r['closure'] != digest(d) or r['version'] != self.cipher.provider.version(d['scope'], d['person'])
                or r['kms_state'] not in ('DESTROY_SCHEDULED','DESTROYED')
                or r['grant_key_unrecoverable'] is not (r['kms_state']=='DESTROYED')
                or r['physical_backup_deletion_confirmed'] is not False or r['account_deleted'] is not False
                or r['data_disposition'] != 'pending'):
            raise StateUnavailable('grant key receipt differs')

    def _read(self, p):
        row = self.store.load(self._key(p))
        if row is None: return None
        j = row[1]
        if (type(j) is not dict or set(j) != {'v','plan','sequence','service','grant_key','blocked'}
                or type(j['v']) is not int or j['v'] != 1 or j['plan'] != p
                or type(j['sequence']) is not int or not 1 <= j['sequence'] <= 1000000
                or type(j['blocked']) is not list or any(type(x) is not str for x in j['blocked'])
                or j['blocked'] != sorted(set(j['blocked']))
                or any(x not in ('apple_revocation','service_results','grant_key') for x in j['blocked'])):
            raise StateUnavailable('disposition progress corrupt; never reset')
        if j['service'] is not None: self._validate_service(p, j['service'])
        if j['grant_key'] is not None: self._validate_key(p, j['grant_key'])
        intent = self.audit.load(key('disposition-progress-intent', digest(p)))
        if intent is None or intent[1] != p: raise StateUnavailable('missing original disposition intent')
        proof = self.audit.load(key('disposition-progress-observation', digest(j)))
        if proof is None or proof[1] != j:
            raise StateUnavailable('disposition progress missing original observation')
        return row

    def _record(self, p, service, grant_key, blocked):
        if service is not None: self._validate_service(p, service)
        if grant_key is not None: self._validate_key(p, grant_key)
        if type(blocked) is not list or any(type(x) is not str or x not in
                ('apple_revocation','service_results','grant_key') for x in blocked):
            raise StateUnavailable('unknown blocked disposition lane')
        for _ in range(CAS_ATTEMPTS):
            row = self._read(p)
            old = None if row is None else row[1]
            s, k = service, grant_key
            if old:
                if old['service'] is not None and (s is None or s['sealed'] <= old['service']['sealed']): s = old['service']
                if old['grant_key'] is not None and (k is None or old['grant_key']['kms_state']=='DESTROYED'): k = old['grant_key']
            b = sorted(set(blocked))
            value = dict(v=1, plan=p, sequence=1 if old is None else old['sequence']+1,
                         service=s, grant_key=k, blocked=b)
            if old is not None and dict(value, sequence=old['sequence']) == old: return
            if value['sequence'] > 1000000: raise StateUnavailable('progress revision requires review')
            self.immutable(self.audit, key('disposition-progress-observation', digest(value)), value)
            try:
                if self.store.compare_exchange(self._key(p), 0 if row is None else row[0], value): return
            except StateUnavailable: pass
            saved = self._read(p)
            if saved is not None and saved[1] == value: return
        raise StateUnavailable('disposition checkpoint unconfirmed; resume original operation')

    def decorate(self, person, base):
        p = self._plan(person); d = p['deletion']
        if any(base[n] != d[m] for n,m in (('operation','operation'),('installation','requester'),
                ('inventory_sha256','inventory_sha256'),('grant_total','total'),('requested_at','requested_at'))):
            raise StateUnavailable('public deletion receipt differs')
        row = self._read(p); j = None if row is None else row[1]
        s, k = (None, None) if j is None else (j['service'], j['grant_key'])
        if k is not None and base['apple_revocation'] != 'confirmed':
            raise StateUnavailable('key disposition contradicts Apple revocation')
        progress = dict(v=1, catalog_sha256=CATALOG, revision=0 if j is None else j['sequence'],
            service_total=p['service']['total'], service_sealed=0 if s is None else s['sealed'],
            service_state='not_started' if s is None else s['service_result_payloads'],
            grant_key_state='not_started' if k is None else 'unrecoverable' if k['kms_state']=='DESTROYED' else 'destroy_scheduled',
            blocked_lanes=[] if j is None else j['blocked'], pending_lanes=PENDING_LANES.copy())
        return dict(base, v=2, data_disposition='pending', disposition=progress)

    def advance(self, person, *, limit=20):
        """Trusted worker, not an HTTP action. Each lane is bounded and resumable.

        Service masks can be fenced even if Apple is temporarily unavailable.
        Keys cannot be destroyed before all revocations are confirmed. Every
        outward claim is backed by the original component's validated receipt.
        """
        if type(limit) is not int or not 1 <= limit <= 100: raise ValueError('bounded disposition batch required')
        if self.deletion.disposition is not self: raise ValueError('explicit disposition composition required')
        p = self._plan(person)
        # Detect a missing/corrupt prior observation before dispatching work.
        self._read(p)
        self.immutable(self.audit, key('disposition-progress-intent', digest(p)), p)
        if self.research is not None:
            # A failure leaves the original research checkpoint resumable and
            # does not dispatch unbounded cleanup from a status request.
            self.research.advance(person,limit=min(limit,20),apply=True)
        service = grant_key = None; blocked = []
        try: service = self.results.advance(person, limit=limit)
        except (StateUnavailable, IdentityRejected): blocked.append('service_results')
        try: self.deletion.advance(person, limit=limit)
        except (StateUnavailable, IdentityRejected): blocked.append('apple_revocation')
        _, job = self.deletion._ensure(person)
        self.deletion._record_scan(job[1]); base = self.deletion._public(job[1])
        if base['apple_revocation']=='confirmed':
            try: grant_key = self.cipher.destroy_after_revocation(self.deletion, person)
            except (StateUnavailable, IdentityRejected): blocked.append('grant_key')
        self._record(p, service, grant_key, blocked)
        return self.deletion.status(p['deletion']['requester'], p['deletion']['operation'])
