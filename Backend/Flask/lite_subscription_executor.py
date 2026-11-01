"""Explicit policy-migration batch executor; no startup, cloud discovery or CLI activation.

Uses the existing generation-CAS store (SQLite locally, GCS when independently
provisioned). Operator approval and fresh observer evidence have distinct pinned
Ed25519 keys. An observation attests an EXTERNAL writer fence; it does not create
one. No IAM, routing, ledger, research, deletion or purchase capability is exposed.
"""
import base64
import copy
import hashlib
import json
import time
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from lite_attest_state import StateUnavailable
from lite_usage_ledger import UsageConflict, digest, _hex, _integer, _subject
from lite_subscription_migration import StableSubscriptionLinks

MAX_ACCOUNTS = 100
MAX_SECONDS = 900


class EvidenceUnavailable(StateUnavailable): pass
class EvidenceContradiction(StateUnavailable): pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def key(kind, identity):
    return hashlib.sha256(('lite/subscription-executor/1\0'+kind+'\0'+identity).encode()).digest()


class SignedEvidence:
    """Public verification keys are supplied by trusted startup, never in a plan."""
    def __init__(self, approval_keys, observer_keys, clock=time.time):
        def checked(keys):
            if type(keys) is not dict or not 1 <= len(keys) <= 4:
                raise ValueError('bounded pinned public keys required')
            for name, value in keys.items():
                _subject(name)
                if type(value) is not bytes or len(value) != 32: raise ValueError('Ed25519 key required')
            return dict(keys)
        self.approvers, self.observers = checked(approval_keys), checked(observer_keys)
        if set(self.approvers.values()) & set(self.observers.values()):
            raise ValueError('independent approval and observer keys required')
        self.clock = clock

    def now(self):
        n = self.clock()
        if type(n) not in (int, float) or not 0 <= n < 2**53:
            raise EvidenceUnavailable('invalid control clock')
        return int(n)

    def verify(self, envelope, role):
        if role not in ('approval','observation'):raise EvidenceUnavailable('unknown evidence role')
        keys = self.approvers if role == 'approval' else self.observers
        try:
            if (type(envelope) is not dict or set(envelope) != {'key_id','payload','signature'}
                    or type(envelope['payload']) is not dict or len(canonical(envelope)) > 16384):
                raise ValueError()
            signature = base64.b64decode(envelope['signature'], validate=True)
            Ed25519PublicKey.from_public_bytes(keys[envelope['key_id']]).verify(signature, canonical(envelope['payload']))
            p = copy.deepcopy(envelope['payload'])
            if p.get('kind') != 'lite-migration-'+role+'/1': raise ValueError()
            start, end = _integer(p['issued_at']), _integer(p['expires_at'])
            if not start <= self.now() < end or end-start > (MAX_SECONDS if role == 'approval' else 30):
                raise ValueError()
            return p
        except Exception as exc:
            raise EvidenceUnavailable('invalid, unknown or expired signed '+role) from exc


class StoredObservation:
    """GET-only view of a dedicated, independently written observer namespace."""
    def __init__(self, store, evidence, namespace):
        self.store, self.evidence, self.namespace = store, evidence, _subject(namespace)

    def observe(self, manifest, *, drained=False, manifest_digest=None):
        try:
            row = self.store.load(key('observation', self.namespace))
            if row is None: raise EvidenceUnavailable('observer record absent')
            p = self.evidence.verify(row[1], 'observation')
            fields = {'kind','namespace','manifest','source','image','inventory','fence','legacy_disabled',
                      'legacy_inflight','migration_inflight','stable_reader','issued_at','expires_at'}
            if (set(p) != fields or any(type(p[k]) is not bool for k in ('legacy_disabled','stable_reader'))
                    or any(type(p[k]) is not int or p[k] < 0 for k in ('legacy_inflight','migration_inflight'))):
                raise EvidenceUnavailable('malformed observer record')
        except EvidenceUnavailable: raise
        except Exception as exc: raise EvidenceUnavailable('observer read unavailable') from exc
        if manifest_digest is not None:_hex(manifest_digest)
        expected = dict(namespace=manifest['namespace'], manifest=manifest_digest or digest(manifest), source=manifest['source'],
                        image=manifest['image'], inventory=manifest['inventory'], fence=manifest['fence'])
        if (any(p[k] != v for k,v in expected.items()) or not p['legacy_disabled'] or p['legacy_inflight']
                or not p['stable_reader'] or (drained and p['migration_inflight'])):
            error = EvidenceContradiction('observer state contradicts approved scope or writer drain')
            error.observation = copy.deepcopy(row[1])  # exact verified envelope for containment audit
            raise error
        return p


def make_manifest(links, registry, installations, *, namespace, source, image, inventory, fence, operation):
    """Read-only exact account plans. No signing, mutation or inferred inventory."""
    _subject(namespace); _hex(source,40)
    for value in (image,inventory,fence,operation): _hex(value)
    if type(installations) is not list or not 1 <= len(installations) <= MAX_ACCOUNTS:
        raise ValueError('bounded explicit installation list required')
    entries=[]; subjects=set()
    for installation in installations:
        _subject(installation)
        p=links.plan(registry,installation,operation=digest([operation,len(entries)]),
                     inventory_sha256=inventory,writer_fence_sha256=fence)
        if p['subject_hash'] in subjects: raise UsageConflict('duplicate service account in batch')
        subjects.add(p['subject_hash']);entries.append(dict(installation=installation,plan=p))
    return dict(v=1,namespace=namespace,source=source,image=image,inventory=inventory,fence=fence,
                operation=operation,entries=entries)


class SubscriptionMigrationExecutor:
    """One exact batch per namespace. Explicit start/resume; active inspect is read-only.

    Rollback means durable admission pause, not reversal of committed account rows.
    After a crash, do not resume until the independently observed old executor is
    drained. Uncommitted expired Apple intents require a newly reviewed account
    plan; this executor NEVER silently rotates an operation ID.
    """
    def __init__(self, *, manifest, links, registry, state_store, audit_store, observation, evidence,
                 expected_source, expected_image):
        m=copy.deepcopy(manifest)
        if (type(m) is not dict or set(m) != {'v','namespace','source','image','inventory','fence','operation','entries'}
                or type(m['v']) is not int or m['v'] != 1 or m['source'] != expected_source
                or m['image'] != expected_image or type(m['entries']) is not list
                or not 1 <= len(m['entries']) <= MAX_ACCOUNTS or len(canonical(m)) > 512*1024):
            raise UsageConflict('exact bounded migration manifest required')
        _subject(m['namespace']);_hex(m['source'],40)
        for field in ('image','inventory','fence','operation'): _hex(m[field])
        from lite_subscription_runtime import SubscriptionRuntime
        prior=getattr(registry,'subscription_runtime',None)
        if (not isinstance(links,StableSubscriptionLinks) or registry.subscription_links is not links
                or (registry.admission_guard is not None and not isinstance(prior,SubscriptionRuntime))
                or not isinstance(evidence,SignedEvidence) or not isinstance(observation,StoredObservation)
                or observation.evidence is not evidence or observation.namespace != m['namespace']):
            raise ValueError('matched trusted components required')
        subjects=set()
        for e in m['entries']:
            if type(e) is not dict or set(e) != {'installation','plan'}: raise UsageConflict('entry schema')
            _subject(e['installation']);links._validate_plan(e['plan']);p=e['plan']
            if (p['subject_hash'] in subjects or p['inventory_sha256'] != m['inventory']
                    or p['writer_fence_sha256'] != m['fence']): raise UsageConflict('ambiguous account scope')
            subjects.add(p['subject_hash'])
        runtime=prior or SubscriptionRuntime(links=links,registry=registry,state_store=state_store,
            audit_store=audit_store,observation_store=observation.store,evidence=evidence,
            expected_source=expected_source,expected_image=expected_image)
        if (runtime.store is not state_store or runtime.audit is not audit_store or runtime.evidence is not evidence
                or runtime.observations is not observation.store or runtime.source!=expected_source or runtime.image!=expected_image):
            raise ValueError('runtime differs across batches')
        registry.subscription_runtime=runtime
        registry.admission_guard=runtime.allowed
        registry.bootstrap=runtime.bootstrap
        # Batch control callbacks are private to each executor. Runtime readers
        # and a second batch must never replace the first executor's callbacks.
        from lite_service_operations import ServiceAccounts
        control_links=runtime.clone()
        control_registry=ServiceAccounts(registry.store,subscription_links=control_links)
        self.manifest,self.links,self.registry=m,control_links,control_registry
        self.runtime=runtime
        self.store,self.audit,self.observation,self.evidence=state_store,audit_store,observation,evidence
        self.identity=digest(m);self.control_key=key('control',m['namespace']);self.subjects=subjects
        self._context=None
        # Bind the core's previously abstract callbacks to exact persisted command
        # state, cryptographic approval and independently signed current evidence.
        control_links.authorize=self._authorize_account
        control_links.writer_fence=lambda proof: self._fence(proof)

    def _read(self):
        row=self.store.load(self.control_key)
        if row is None:return None
        r=row[1]
        if (type(r) is not dict or set(r) != {'v','manifest','phase','next','attempt','transitions'}
                or type(r['v']) is not int or r['v'] != 1 or r['manifest'] != self.identity
                or r['phase'] not in ('running','sealed','active','paused')
                or type(r['next']) is not int or not 0 <= r['next'] <= len(self.manifest['entries'])
                or type(r['transitions']) is not int or not 1 <= r['transitions'] <= 32
                or (r['phase'] in ('sealed','active') and r['next'] != len(self.manifest['entries']))):
            raise StateUnavailable('migration state corrupt or different batch; never replace')
        _hex(r['attempt']);return row

    def state_digest(self):
        row=self._read();return digest(None if row is None else row[1])

    def _immutable(self,kind,value):
        payload=dict(v=1,manifest=self.identity,kind=kind,value=copy.deepcopy(value))
        k=key('audit',digest(payload));self.audit.compare_exchange(k,0,payload)
        row=self.audit.load(k)
        if row is None or row[1] != payload:raise StateUnavailable('executor audit unconfirmed')
        return digest(payload)

    def _move(self,old,phase,index,attempt):
        revision=old[0] if old else 0
        value=dict(v=1,manifest=self.identity,phase=phase,next=index,attempt=attempt,
                   transitions=(old[1]['transitions']+1 if old else 1))
        # Progress may exceed 32 accounts; transitions counts control phases only.
        if old and phase == old[1]['phase']:value['transitions']=old[1]['transitions']
        if value['transitions'] > 32:raise UsageConflict('control transition budget exhausted')
        self._immutable('state-intent',dict(before=None if old is None else old[1],after=value))
        if not self.store.compare_exchange(self.control_key,revision,value):raise UsageConflict('another command won; inspect first')
        observed=self._read()
        if observed is None or observed[1] != value:raise StateUnavailable('state write unconfirmed')
        self._immutable('state-confirmed',value);return observed

    def _approval(self,envelope,action,expected):
        p=self.evidence.verify(envelope,'approval')
        if (set(p) != {'kind','manifest','action','expected_state','issued_at','expires_at','nonce'}
                or p['manifest'] != self.identity or p['action'] != action or p['expected_state'] != expected):
            raise EvidenceContradiction('approval does not match exact batch/action/state')
        _hex(p['nonce']);return digest(envelope)

    def _fence(self,proof):
        if proof != self.manifest['fence']:return False
        self.observation.observe(self.manifest);return True

    def _authorize_account(self,p,authorization):
        if self._context is None:return False
        envelope,action,expected,attempt=self._context
        if self._approval(envelope,action,expected) != attempt or authorization != self._account_authorization(p):return False
        row=self._read()
        if row is None or row[1]['phase'] != 'running' or row[1]['attempt'] != attempt:return False
        index=row[1]['next']
        return index < len(self.manifest['entries']) and digest(p) == digest(self.manifest['entries'][index]['plan'])

    def _account_authorization(self,p):
        # Stable reference, not a bearer credential. Each execution/resume still
        # requires a fresh signed command, persisted control state and observation.
        # Retained core intents must keep this reference across lost responses.
        return digest(dict(kind='executor-account-authority/1',manifest=self.identity,plan=digest(p)))

    def inspect(self):
        row=self._read()
        if row is None:return dict(phase='absent',observation='not_requested',next=0)
        try:self.observation.observe(self.manifest);status='confirmed'
        except EvidenceUnavailable:status='unavailable'
        except EvidenceContradiction:status='contradictory'
        return dict(phase=row[1]['phase'],observation=status,next=row[1]['next'])

    def admission_allowed(self,account):
        return self.runtime.allowed(account)

    def _contain(self,attempt):
        row=self._read()
        if row is None or row[1]['attempt'] != attempt:
            raise StateUnavailable('closure needs current command reconciliation')
        if row[1]['phase'] != 'paused':row=self._move(row,'paused',row[1]['next'],attempt)
        self._immutable('pause-confirmed',row[1])
        return row[1]

    def pause(self,envelope):
        row=self._read()
        if row is None:raise UsageConflict('no batch to pause')
        auth=self._approval(envelope,'pause',digest(row[1]))
        self._immutable('approved-pause',dict(approval=envelope,before=row[1]))
        # A verified explicit pause does not depend on live observation availability.
        return self._move(row,'paused',row[1]['next'],auth)[1]

    def advance(self,envelope=None,*,resume=False):
        if self._context is not None:raise UsageConflict('executor already advancing')
        old=self._read()
        if old and old[1]['phase'] == 'active':
            self.observation.observe(self.manifest);return copy.deepcopy(old[1])
        action='resume' if resume else 'start';expected=digest(None if old is None else old[1])
        if (resume and (old is None or old[1]['phase'] != 'paused')) or (not resume and old is not None):
            raise UsageConflict('explicit pause/drain/resume required; no automatic restart')
        attempt=self._approval(envelope,action,expected)
        if old and old[1]['transitions'] > 27:raise UsageConflict('insufficient control budget for resume/containment')
        self.observation.observe(self.manifest,drained=True)
        self._immutable('approved-command',dict(approval=envelope,before=None if old is None else old[1]))
        self._context=(copy.deepcopy(envelope),action,expected,attempt)
        try:
            row=self._move(old,'running',old[1]['next'] if old else 0,attempt)
            self.runtime.claim(self.manifest)
            while row[1]['next'] < len(self.manifest['entries']):
                i=row[1]['next'];entry=self.manifest['entries'][i]
                authority=self._account_authorization(entry['plan'])
                if not self._authorize_account(entry['plan'],authority):raise StateUnavailable('command no longer authorized')
                self.observation.observe(self.manifest)
                receipt=self.links.execute(entry['plan'],self.registry,entry['installation'],authorization_sha256=authority)
                self._immutable('account-confirmed',dict(index=i,plan=digest(entry['plan']),receipt=receipt))
                current=self._read()
                if current != row:raise UsageConflict('command paused or superseded')
                row=self._move(row,'running',i+1,attempt)
            self._approval(envelope,action,expected);self.observation.observe(self.manifest)
            # All exact core completions and current pointers must agree before activation.
            for entry in self.manifest['entries']:
                a=self.registry.registration(entry['installation'])
                current=self.links._read(a['subject'],a['token'])
                if current is None or current[1]['plan'] != digest(entry['plan']):
                    raise StateUnavailable('account changed before activation')
            row=self._move(row,'sealed',row[1]['next'],attempt)
            self._approval(envelope,action,expected);self.observation.observe(self.manifest)
            row=self._move(row,'active',row[1]['next'],attempt)
            self.runtime.activate_batch(self.manifest,row[1])
            return copy.deepcopy(row[1])
        except Exception:
            try:self._contain(attempt)
            except Exception as closure:raise StateUnavailable('pause or audit unconfirmed; independently stop admission and reconcile control state') from closure
            raise
        finally:self._context=None
