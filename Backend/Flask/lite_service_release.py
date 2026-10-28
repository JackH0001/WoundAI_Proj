"""Opt-in durable service release; no deployment, scheduler or public controller.

Fresh observations authorize transitions, not every customer request. Readers
need only GET; the separate controller needs CAS and append-only audit writes.
An unavailable observer does not revoke a release. A verified contradiction is
contained by the controller; unavailable durable control ALWAYS denies new work.
"""
import copy
from lite_attest_state import StateUnavailable
from lite_usage_ledger import UsageConflict, digest, _hex, _subject
from lite_subscription_executor import key, StoredObservation, EvidenceContradiction, EvidenceUnavailable
from lite_usage_rollout import ObservationContradiction, ObservationUnavailable


def marker_key(component, scope):
    return key('service-release-required', digest([component, scope]))


def _immutable(store, k, value):
    old = store.load(k)
    if old is None:
        store.compare_exchange(k, 0, value)
        old = store.load(k)
    if old is None or old[1] != value:
        raise StateUnavailable('release audit/marker unconfirmed')


def make_manifest(runtime, rollout, *, namespace):
    """Read-only proposal. Does not infer that legacy inventory is empty."""
    _subject(namespace)
    policies = {p: runtime._policy(p, verify_admission=False)['identity'] for p in runtime.links.catalog}
    return dict(schema='lite-service-release/1', namespace=namespace,
        source=runtime.source, image=runtime.image, scope=runtime.scope,
        active_policy=runtime.links.active, policies=policies,
        usage_namespace=rollout.manifest['namespace'], usage_plan=rollout.plan_sha256,
        fence=rollout.manifest['writer_fence_sha256'])


def _validate(m):
    if (type(m) is not dict or set(m) != {'schema','namespace','source','image','scope',
            'active_policy','policies','usage_namespace','usage_plan','fence'}
            or m['schema'] != 'lite-service-release/1' or type(m['policies']) is not dict
            or not 1 <= len(m['policies']) <= 16 or m['active_policy'] not in m['policies']):
        raise UsageConflict('bounded pinned service release required')
    _subject(m['namespace']); _subject(m['usage_namespace']); _hex(m['source'], 40)
    for name in ('image','scope','active_policy','usage_plan','fence'): _hex(m[name])
    for policy, origin in m['policies'].items(): _hex(policy); _hex(origin)
    return copy.deepcopy(m)


class ServiceReleaseReader:
    """No state/observer cache and no mutation methods on the admission reader."""
    def __init__(self, *, store, audit_store, manifest):
        self.manifest = _validate(manifest)
        self.identity = digest(self.manifest)
        self.store, self.audit = store, audit_store
        self.key = key('service-release-control', self.manifest['namespace'])

    def snapshot(self):
        row = self.store.load(self.key)
        if row is None: return None
        v = row[1]
        try:
            if (type(v) is not dict or set(v) != {'v','release','epoch','phase','intent'}
                    or type(v['v']) is not int or v['v'] != 1
                    or type(v['epoch']) is not int or not 1 <= v['epoch'] < 2**53
                    or v['phase'] not in ('active','paused')):
                raise ValueError()
            _hex(v['intent']); _hex(v['release'])
        except (ValueError, TypeError, KeyError) as exc:
            raise StateUnavailable('release control corrupt or deployment differs') from exc
        return row[0], copy.deepcopy(v)

    def confirmed(self, state):
        intent = self.audit.load(key('service-release-intent', state['intent']))
        done = self.audit.load(key('service-release-confirmed', digest(state)))
        if (intent is None or digest(intent[1]) != state['intent']
                or intent[1].get('target') != {k:v for k,v in state.items() if k != 'intent'}
                or done is None or done[1] != state):
            raise StateUnavailable('release transition audit incomplete')

    def require_active(self):
        row = self.snapshot()
        if row is None or row[1]['phase'] != 'active' or row[1]['release'] != self.identity:
            raise StateUnavailable('service release absent or paused')
        self.confirmed(row[1])
        return row[1]

    def check_marker(self, store, component, scope):
        marker = store.load(marker_key(component, scope))
        if (marker is None or type(marker[1].get('v')) is not int
                or marker[1] != dict(v=1, namespace=self.manifest['namespace'])):
            raise StateUnavailable('service release binding missing or changed')

    def subscription(self, runtime, m):
        r = self.manifest
        if (runtime.source != r['source'] or runtime.image != r['image'] or runtime.scope != r['scope']
                or runtime.links.active != r['active_policy'] or set(runtime.links.catalog) != set(r['policies'])
                or m['fence'] != r['fence']):
            raise StateUnavailable('subscription release scope differs')
        self.check_marker(runtime.store, 'subscription', runtime.scope)
        return self.require_active()

    def usage(self, rollout):
        if (rollout.plan_sha256 != self.manifest['usage_plan']
                or rollout.manifest['namespace'] != self.manifest['usage_namespace']):
            raise StateUnavailable('usage release scope differs')
        self.check_marker(rollout.store, 'usage', rollout.manifest['namespace'])
        return self.require_active()


class ServiceReleaseController:
    """Trusted operational API only. Never mount this object on a customer route.

    Operator signatures pin the before-state, manifest and action. Uncertain CAS
    is inspected, never retried with a new identity. Evidence/records are retained.
    """
    def __init__(self, *, reader, runtime, rollout, evidence):
        self.reader, self.runtime, self.rollout, self.evidence = reader, runtime, rollout, evidence
        if make_manifest(runtime, rollout, namespace=reader.manifest['namespace']) != reader.manifest:
            raise UsageConflict('release proposal differs from installed components')

    def _fresh(self):
        m = self.reader.manifest; r = self.runtime
        proofs, errors = {}, []
        for policy, identity in m['policies'].items():
            try:
                p = r._policy(policy, verify_admission=False)
                if p['identity'] != identity or p['fence'] != m['fence']:
                    raise EvidenceContradiction('policy origin or writer fence changed')
                proofs[policy] = StoredObservation(r.observations, r.evidence, p['namespace']).observe(
                    p, drained=True, manifest_digest=identity)
            except (EvidenceUnavailable, EvidenceContradiction) as exc:
                errors.append(exc)
        self.rollout.confirmed_active()
        self.rollout.policy().read(m['usage_namespace'], self.rollout.ledger.now())
        quota = None
        try: quota = self.rollout._observe(enabled=True)
        except (ObservationUnavailable, ObservationContradiction) as exc: errors.append(exc)
        # An unavailable observation must not mask a separate confirmed drift.
        for error in errors:
            if isinstance(error, (EvidenceContradiction, ObservationContradiction)): raise error
        if errors: raise errors[0]
        return dict(subscription=proofs, usage=quota)

    def _markers(self):
        m = self.reader.manifest; value = dict(v=1, namespace=m['namespace'])
        _immutable(self.runtime.store, marker_key('subscription', self.runtime.scope), value)
        _immutable(self.rollout.store, marker_key('usage', m['usage_namespace']), value)

    def _confirm(self, state):
        # Never confirm an overwritten state or invent evidence for a lost intent.
        row = self.reader.snapshot()
        intent = self.reader.audit.load(key('service-release-intent', state['intent']))
        if row is None or row[1] != state or intent is None or digest(intent[1]) != state['intent']:
            raise StateUnavailable('release intent/state unconfirmed')
        _immutable(self.reader.audit, key('service-release-confirmed', digest(state)), state)
        latest = self.reader.snapshot()
        if latest is None or latest[1] != state:
            raise UsageConflict('release changed during confirmation')
        self.reader.confirmed(state)
        return copy.deepcopy(state)

    def _move(self, row, phase, command, proof):
        before = row[1] if row else None
        target = dict(v=1, release=self.reader.identity, epoch=(before['epoch'] + 1 if before else 1), phase=phase)
        intent = dict(target=target, before=digest(before), command=copy.deepcopy(command), proof=proof)
        state = dict(target, intent=digest(intent))
        _immutable(self.reader.audit, key('service-release-intent', state['intent']), intent)
        if not self.reader.store.compare_exchange(self.reader.key, row[0] if row else 0, state):
            raise UsageConflict('release changed; inspect before retry')
        return self._confirm(state)

    def apply(self, envelope):
        p = self.evidence.verify(envelope, 'approval')
        if (set(p) != {'kind','manifest','action','expected_state','issued_at','expires_at','nonce'}
                or p['manifest'] != self.reader.identity
                or p['action'] not in ('service-release-publish','service-release-pause',
                                       'service-release-resume','service-release-confirm','service-release-upgrade')):
            raise EvidenceContradiction('release approval differs')
        _hex(p['nonce']); _hex(p['expected_state'])
        row = self.reader.snapshot(); current = row[1] if row else None
        if current and current['release'] == self.reader.identity:
            prior = self.reader.audit.load(key('service-release-intent', current['intent']))
            if prior and prior[1].get('command') == envelope:
                # Same unexpired command recovers its own lost receipt only.
                try:
                    self.reader.confirmed(current)
                    return current
                except StateUnavailable:
                    if current['phase'] == 'active': self._fresh()
                    return self._confirm(current)
        if p['expected_state'] != digest(current):
            raise EvidenceContradiction('release approval has stale before-state')
        action = p['action']
        if action == 'service-release-confirm':
            if current is None or current['release'] != self.reader.identity: raise UsageConflict('no matching release to confirm')
            proof = self._fresh() if current['phase'] == 'active' else {'paused':True}
            _immutable(self.reader.audit, key('service-release-recovery', digest(envelope)),
                       dict(command=envelope, state=current, proof=proof))
            return self._confirm(current)
        if action == 'service-release-pause':
            if current is None or current['phase'] != 'active' or current['release'] != self.reader.identity: raise UsageConflict('matching active release required')
            return self._move(row, 'paused', envelope, {'reason':'operator-pause'})
        if ((action == 'service-release-publish' and current is not None)
                or (action == 'service-release-resume' and (current is None or current['phase'] != 'paused' or current['release'] != self.reader.identity))
                or (action == 'service-release-upgrade' and (current is None or current['phase'] != 'paused' or current['release'] == self.reader.identity))):
            raise UsageConflict('explicit publication or paused-generation resume required')
        if current is not None:self.reader.confirmed(current)
        proof = self._fresh()
        self._markers()  # Partial binding denies affected readers; never remove on failure.
        proof = self._fresh()  # Check again after marker writes; no TTL extension.
        return self._move(row, 'active', envelope, proof)

    def health(self):
        """Monitor availability without mutation; contain a verified contradiction.

        A controller/storage failure raises and requires external route containment.
        No claim of automatic cloud availability or scheduling is made here.
        """
        row = self.reader.snapshot()
        if row is None or row[1]['phase'] != 'active' or row[1]['release'] != self.reader.identity: return {'status':'inactive'}
        self.reader.confirmed(row[1])
        try: self._fresh()
        except (EvidenceUnavailable, ObservationUnavailable): return {'status':'observation-unavailable'}
        except (EvidenceContradiction, ObservationContradiction) as exc:
            self._move(row, 'paused', {'controller':'verified-contradiction'},
                {'error':type(exc).__name__, 'observation':getattr(exc,'observation',None)})
            return {'status':'paused-contradiction'}
        return {'status':'confirmed'}
