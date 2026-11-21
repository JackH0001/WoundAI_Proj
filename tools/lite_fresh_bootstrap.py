"""Private, bounded first-install coordinator over the existing CAS adapters.

No fixture import, cloud discovery, credentials, traffic/IAM change or HTTP route.
The operator supplies independently pinned components and inventory coverage.
Each invocation advances ONE signed step. Uncertain attempts require a separate
signed recovery and a fresh observation proving the prior executor drained.
"""
import copy
from pathlib import Path
from lite_attest_state import StateUnavailable, SQLiteStateStore, GCSStateStore, _encode
from lite_usage_ledger import UsageConflict, digest, _hex, _subject
from lite_subscription_executor import SignedEvidence, key, canonical, EvidenceContradiction
from lite_subscription_runtime import SubscriptionRuntime
from lite_usage_rollout import UsageRollout
from lite_usage_cutover import FIRST_INSTALL
from lite_service_release import ServiceReleaseReader, ServiceReleaseController

STAGES = ('subscription', 'quota', 'release')


def _overlap(a, b):
    if a is b: return True
    if isinstance(a, SQLiteStateStore) and isinstance(b, SQLiteStateStore):
        return Path(a.path).resolve() == Path(b.path).resolve()
    if isinstance(a, GCSStateStore) and isinstance(b, GCSStateStore):
        same_bucket = a.bucket is b.bucket or (getattr(a.bucket, 'name', None) is not None
                      and a.bucket.name == getattr(b.bucket, 'name', None))
        return same_bucket and (a.prefix == b.prefix or a.prefix.startswith(b.prefix+'/')
                                or b.prefix.startswith(a.prefix+'/'))
    return False


def inventory(document, *, sources, writers):
    """Coverage comes from reviewed configuration, not the inventory's own list.

    Snapshot hashes reference external frozen evidence. This validator does not
    create that evidence or infer emptiness from a missing bucket or account.
    """
    if (type(document) is not dict or set(document) != {'schema','sources','writers'}
            or document['schema'] != 'lite-fresh-inventory/1'
            or type(sources) is not list or not 3 <= len(sources) <= 64
            or type(writers) is not list or not 1 <= len(writers) <= 32
            or document['writers'] != writers or len(set(writers)) != len(writers)
            or type(document['sources']) is not list or len(document['sources']) != len(sources)):
        raise UsageConflict('explicit complete frozen inventory coverage required')
    for writer in writers: _subject(writer)
    seen, roles = set(), set()
    for expected, row in zip(sources, document['sources']):
        if (type(expected) is not dict or set(expected) != {'id','role','location'}
                or type(row) is not dict or set(row) != {'id','role','location','count','snapshot'}
                or any(row[k] != v for k,v in expected.items())
                or row['role'] not in ('accounts','links','quota')
                or type(row['count']) is not int or row['count'] != 0):
            raise UsageConflict('first installation has unknown, nonempty or different sources')
        _subject(row['id']); _hex(row['location']); _hex(row['snapshot'])
        if row['id'] in seen: raise UsageConflict('duplicate inventory source')
        seen.add(row['id']); roles.add(row['role'])
    if roles != {'accounts','links','quota'}: raise UsageConflict('missing inventory class')
    return copy.deepcopy(document)


class FreshBootstrapExecutor:
    """Trusted operational API, deliberately absent from all application images.

    `store` and `audit` are dedicated control lanes, never account or quota data.
    All envelopes use independently pinned operator/observer public keys.
    No rollback deletes state or reopens an old writer; recovery retains intents.
    """
    def __init__(self, *, plan, expected_plan, expected_config, sources, writers,
                 runtime, rollout, reader, evidence, observer, store, audit):
        _hex(expected_plan); _hex(expected_config)
        fields = {'schema','namespace','config','inventory','subscription','usage','release'}
        if (type(plan) is not dict or set(plan) != fields
                or plan['schema'] != 'lite-fresh-bootstrap/1' or len(canonical(plan)) > 8192
                or digest(plan) != expected_plan or plan['config'] != expected_config
                or not isinstance(runtime, SubscriptionRuntime) or not isinstance(rollout, UsageRollout)
                or not isinstance(reader, ServiceReleaseReader) or not isinstance(evidence, SignedEvidence)
                or runtime.evidence is not evidence):
            raise UsageConflict('pinned bootstrap components required')
        _subject(plan['namespace'])
        inv = inventory(plan['inventory'], sources=sources, writers=writers)
        m, u, r = plan['subscription'], rollout.manifest, reader.manifest
        if (type(m) is not dict or set(m) != {'v','namespace','source','image','inventory','fence','operation','entries'}
                or type(m['v']) is not int or m['v'] != 1 or m['entries'] != []
                or type(m['entries']) is not list or m['inventory'] != digest(inv)
                or m['source'] != runtime.source or m['image'] != runtime.image
                or plan['usage'] != u or plan['release'] != r or u['schema'] != FIRST_INSTALL
                or rollout.rows != [] or u['fresh_inventory_sha256'] != digest(inv)
                or m['fence'] != u['writer_fence_sha256'] or m['fence'] != r['fence']
                or len(runtime.links.catalog) != 1 or set(r['policies']) != set(runtime.links.catalog)
                or set(r['policies'].values()) != {digest(m)}
                or r['source'] != runtime.source or r['image'] != runtime.image
                or r['scope'] != runtime.scope or r['active_policy'] != runtime.links.active
                or r['usage_plan'] != digest(u) or r['usage_namespace'] != u['namespace']
                or runtime.service_release is not reader or rollout.service_release is not reader):
            raise UsageConflict('first-install subscription, usage and release disagree')
        _subject(m['namespace']); _hex(m['source'], 40)
        for field in ('image','inventory','fence','operation'): _hex(m[field])
        # Reject known object and physical path aliases. Deployment must also
        # prove the complete location map and effective inherited IAM boundaries.
        peers = (runtime.store, runtime.audit, runtime.observations, runtime.registry.store,
                 runtime.links.store, runtime.links.audit, rollout.store, rollout.audit,
                 rollout.control, rollout.ledger.store, reader.store, reader.audit)
        if (_overlap(store, audit) or _overlap(store, observer) or _overlap(audit, observer)
                or any(_overlap(x, y) for x in peers for y in (store, audit, observer))):
            raise UsageConflict('separate operational control and audit required')
        self.plan, self.identity = copy.deepcopy(plan), expected_plan
        self.runtime, self.rollout, self.reader = runtime, rollout, reader
        self.evidence, self.observer, self.store, self.audit = evidence, observer, store, audit
        self.key = key('fresh-bootstrap-control', plan['namespace'])

    def _immutable(self, kind, value):
        _encode(value)  # Same size/type bound as the production GCS CAS adapter.
        identity = digest(value); k = key('fresh-bootstrap-'+kind, identity)
        old = self.audit.load(k)
        if old is None:
            self.audit.compare_exchange(k, 0, value); old = self.audit.load(k)
        if old is None or old[1] != value: raise StateUnavailable('bootstrap audit unconfirmed')
        return identity

    def _read(self):
        row = self.store.load(self.key)
        if row is None: return None
        v = row[1]
        if (type(v) is not dict or set(v) != {'v','plan','stage','status','attempt','started_at','intent','result'}
                or type(v['v']) is not int or v['v'] != 1 or v['plan'] != self.identity
                or v['stage'] not in STAGES or v['status'] not in ('running','complete')
                or type(v['attempt']) is not int or not 1 <= v['attempt'] <= 8
                or type(v['started_at']) is not int or not 0 <= v['started_at'] <= self.evidence.now()):
            raise StateUnavailable('bootstrap control changed or corrupt; preserve it')
        _hex(v['intent'])
        intent = self.audit.load(key('fresh-bootstrap-intent', v['intent']))
        if (intent is None or type(intent[1]) is not dict
                or set(intent[1]) != {'plan','target','before','command','inner','observation'}
                or digest(intent[1]) != v['intent']
                or intent[1].get('target') != {k:x for k,x in v.items() if k not in ('intent','result','status')}
                or intent[1].get('plan') != self.plan):
            raise StateUnavailable('bootstrap intent missing or inconsistent')
        if v['status'] == 'running':
            if v['result'] is not None: raise StateUnavailable('running step has a result')
        else:
            _hex(v['result'])
            done = self.audit.load(key('fresh-bootstrap-result', v['result']))
            if (done is None or type(done[1]) is not dict
                    or set(done[1]) != {'plan','intent','stage','result'}
                    or done[1]['plan'] != self.identity or type(done[1]['result']) is not dict
                    or digest(done[1]) != v['result']
                    or done[1].get('intent') != v['intent'] or done[1].get('stage') != v['stage']):
                raise StateUnavailable('bootstrap completion evidence missing')
        return row[0], copy.deepcopy(v)

    def inspect(self):
        """Read-only; no observer refresh, activation or implicit retry."""
        row = self._read()
        return copy.deepcopy(row[1]) if row else None

    def _observation(self, *, after=None):
        row = self.observer.load(key('bootstrap-observation', self.plan['namespace']))
        if row is None: raise StateUnavailable('bootstrap observer absent')
        p = self.evidence.verify(row[1], 'observation')
        if (set(p) != {'kind','namespace','manifest','inventory','writer_fence','route_closed',
                       'legacy_disabled','legacy_inflight','bootstrap_inflight','issued_at','expires_at'}
                or p['namespace'] != self.plan['namespace'] or p['manifest'] != self.identity
                or p['inventory'] != digest(self.plan['inventory'])
                or p['writer_fence'] != self.plan['subscription']['fence']
                or p['route_closed'] is not True or p['legacy_disabled'] is not True
                or type(p['legacy_inflight']) is not int or p['legacy_inflight'] != 0
                or type(p['bootstrap_inflight']) is not int or p['bootstrap_inflight'] != 0
                or (after is not None and p['issued_at'] <= after)):
            raise EvidenceContradiction('independent closed-route and drained-writer proof required')
        return row[1]

    def _same_day(self):
        if self.evidence.now()//86400 != self.plan['usage']['day']:
            raise UsageConflict('bootstrap crossed UTC day; reconcile, do not reset')

    def _ready(self, stage):
        if stage == 'subscription':
            for policy in self.runtime.links.catalog:
                row = self.runtime.store.load(self.runtime._policy_key(policy))
                if row is None: return None
                if self.runtime._policy(policy, verify_admission=False)['identity'] != digest(self.plan['subscription']):
                    raise StateUnavailable('another subscription origin exists')
            return {'manifest': digest(self.plan['subscription'])}
        if stage == 'quota':
            row = self.rollout._read()
            if row is None or row[1]['phase'] != 'active': return None
            result = self.rollout.confirmed_active()
            self.rollout.policy().read(self.rollout.manifest['namespace'], self.rollout.ledger.now())
            return {'state': result}
        row = self.reader.snapshot()
        if row is None: return None
        if row[1]['release'] != self.reader.identity or row[1]['phase'] != 'active':
            raise StateUnavailable('release changed or paused; bootstrap cannot override')
        try: self.reader.confirmed(row[1])
        except StateUnavailable: return None  # Separate signed confirm may repair a lost acknowledgement.
        return {'state': row[1]}

    def _dispatch(self, stage, inner, authorization):
        # Check already committed steps without replaying expired inner commands.
        ready = self._ready(stage)
        if ready is not None: return ready
        if stage == 'subscription': self.runtime.activate_fresh(self.plan['subscription'], inner)
        elif stage == 'quota':
            if inner is not None:
                # An earlier rollback may have paused durable control while
                # provider containment was unavailable. Re-close preparation
                # under this new signed recovery before the pause is resumed.
                self.rollout.provider.disable_new(copy.deepcopy(self.rollout.manifest))
                self.rollout.resume_paused(expected_pause_sha256=inner['expected_pause_sha256'],
                                          authorization_sha256=authorization)
            self.rollout.advance()
        else:
            ServiceReleaseController(reader=self.reader, runtime=self.runtime,
                rollout=self.rollout, evidence=self.evidence).apply(inner)
        ready = self._ready(stage)
        if ready is None: raise StateUnavailable('bootstrap step not confirmed')
        return ready

    def _inner(self, stage, inner):
        # A valid outer operator signature cannot broaden a bootstrap into a
        # release upgrade/resume. Reject malformed inner commands before claim.
        if stage == 'quota' or self._ready(stage) is not None: return
        p = self.evidence.verify(inner, 'approval')
        current = self.reader.snapshot() if stage == 'release' else None
        expected = digest(current[1] if current else None)
        action = ('activate-fresh' if stage == 'subscription' else
                  'service-release-confirm' if current else 'service-release-publish')
        if (set(p) != {'kind','manifest','action','expected_state','issued_at','expires_at','nonce'}
                or p['action'] != action or p['expected_state'] != expected
                or p['manifest'] != (digest(self.plan['subscription']) if stage == 'subscription' else self.reader.identity)):
            raise EvidenceContradiction('inner action is outside this bootstrap step')
        _hex(p['nonce'])

    def execute(self, envelope, *, inner=None):
        """One signed step, or an explicitly signed recovery of a drained attempt.

        A duplicate of a completed command is read-only even after expiry. It
        reports historical completion, never re-enables a now-paused service.
        """
        row = self._read(); before = row[1] if row else None
        if before and before['status'] == 'complete':
            intent = self.audit.load(key('fresh-bootstrap-intent', before['intent']))[1]
            if intent['command'] == envelope and intent['inner'] == inner: return before
        p = self.evidence.verify(envelope, 'approval')
        if (set(p) != {'kind','manifest','action','stage','expected_state','inner_sha256','issued_at','expires_at','nonce'}
                or p['manifest'] != self.identity or p['stage'] not in STAGES
                or p['action'] not in ('bootstrap-step','bootstrap-recover')
                or p['expected_state'] != digest(before) or p['inner_sha256'] != digest(inner)):
            raise EvidenceContradiction('bootstrap command or prior state differs')
        _hex(p['nonce']); stage = p['stage']; recover = p['action'] == 'bootstrap-recover'
        if stage != 'quota' and inner is None: raise UsageConflict('step requires its exact inner command')
        if stage == 'quota' and inner is not None:
            paused = self.rollout._read()
            if (not recover or type(inner) is not dict or set(inner) != {'action','expected_pause_sha256'}
                    or inner['action'] != 'resume-paused' or paused is None or paused[1]['phase'] != 'paused'
                    or inner['expected_pause_sha256'] != digest(paused[1])):
                raise UsageConflict('quota resume requires exact independently approved pause')
        if recover:
            if before is None or before['status'] != 'running' or before['stage'] != stage or before['attempt'] >= 8:
                raise UsageConflict('bounded recovery requires exact running attempt')
        elif (before is not None and before['status'] != 'complete') or STAGES.index(stage) != (STAGES.index(before['stage'])+1 if before else 0):
            raise UsageConflict('ordered step or explicit drained recovery required')
        self._same_day()
        observation = self._observation(after=before['started_at'] if recover else None)
        # Detect contradictory persisted origins before adding a new intent.
        for earlier in STAGES[:STAGES.index(stage)]:
            if self._ready(earlier) is None: raise StateUnavailable('earlier component no longer confirmed')
        self._ready(stage)
        self._inner(stage, inner)
        target = dict(v=1,plan=self.identity,stage=stage,attempt=before['attempt']+1 if recover else 1,
                      started_at=self.evidence.now())
        intent = dict(plan=self.plan,target=target,before=before,command=copy.deepcopy(envelope),
                      inner=copy.deepcopy(inner),observation=observation)
        identity = self._immutable('intent', intent)
        running = dict(target,status='running',intent=identity,result=None)
        self._same_day()
        if not self.store.compare_exchange(self.key, row[0] if row else 0, running):
            raise UsageConflict('another bootstrap step won; inspect only')
        owned = self._read()
        if owned is None or owned[1] != running: raise StateUnavailable('bootstrap claim unconfirmed')
        # No automatic retry or rollback after this claim. Keep an uncertain
        # core result and require a new independent drain proof for recovery.
        result = self._dispatch(stage, inner, digest(envelope))
        self._same_day()
        done_hash = self._immutable('result', dict(plan=self.identity,intent=identity,stage=stage,result=result))
        complete = dict(running,status='complete',result=done_hash)
        if not self.store.compare_exchange(self.key, owned[0], complete):
            raise UsageConflict('bootstrap attempt superseded; do not overwrite')
        observed = self._read()
        if observed is None or observed[1] != complete: raise StateUnavailable('bootstrap completion unconfirmed')
        return complete
