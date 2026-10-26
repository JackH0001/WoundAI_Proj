"""Bounded cutover/rollback executor with injected provider observations.

No cloud discovery, IAM, scheduling or production provider is implemented.
The provider must fence EVERY legacy writer, drain in-flight work and verify
the frozen baseline and account mapping. Tests use a synthetic provider only.
Rollback is a terminal pause: it NEVER restores legacy writers or resets usage.
"""
import copy
import hashlib
from lite_attest_state import CAS_ATTEMPTS, StateUnavailable, _encode
from lite_usage_ledger import UsageConflict, digest, _hex, _integer, _subject
from lite_usage_cutover import validate, seal_cutover, CutoverPolicy


def validate_baselines(rows, manifest):
    if type(rows) is not list or not 1<=len(rows)<=1000:
        raise UsageConflict('bounded verified baselines required')
    subjects=set()
    for r in rows:
        if type(r) is not dict or set(r)!={'subject','successes','attempts'}:
            raise UsageConflict('baseline schema')
        _subject(r['subject'])
        if r['subject'] in subjects or _integer(r['attempts'])<_integer(r['successes']):
            raise UsageConflict('ambiguous baseline')
        subjects.add(r['subject'])
    if digest(rows)!=manifest['baseline_sha256']:
        raise UsageConflict('frozen baseline digest mismatch')
    return copy.deepcopy(rows)


class UsageRollout:
    PHASES=('prepared','frozen','sealed','active','paused')

    def __init__(self, *, store, audit_store, control_store, ledger, provider, manifest, baselines):
        self.manifest=validate(manifest);self.rows=validate_baselines(baselines,self.manifest)
        if ledger.namespace!=self.manifest['namespace']:raise UsageConflict('scope mismatch')
        if any(not callable(getattr(provider,n,None)) for n in ('freeze_legacy','enable_new','disable_new','observe')):
            raise ValueError('explicit trusted provider required')
        self.store,self.audit,self.control,self.ledger,self.provider=store,audit_store,control_store,ledger,provider
        self.plan_sha256=digest(self.manifest)
        self.key=hashlib.sha256(('lite/usage-rollout/1\0'+self.manifest['namespace']).encode()).digest()

    def _audit(self, kind, value):
        payload=dict(v=1,plan_sha256=self.plan_sha256,kind=kind,value=value)
        key=hashlib.sha256(('lite/usage-rollout-audit/1\0'+digest(payload)).encode()).digest()
        self.audit.compare_exchange(key,0,payload)
        row=self.audit.load(key)
        if row is None or row[1]!=payload:raise StateUnavailable('rollout audit unconfirmed')

    def _read(self):
        row=self.store.load(self.key)
        if row is None:return None
        try:
            v=row[1];h=v['history']
            if (type(v) is not dict or set(v)!={'v','plan_sha256','phase','history'}
                    or type(v['v']) is not int or v['v']!=1 or v['plan_sha256']!=self.plan_sha256
                    or v['phase'] not in self.PHASES or type(h) is not list or not 1<=len(h)<=5):
                raise ValueError()
            phases=[]
            for item in h:
                if type(item) is not list or len(item)!=2 or item[0] not in self.PHASES:raise ValueError()
                _integer(item[1]);phases.append(item[0])
            normal=list(self.PHASES[:-1])
            if phases[-1]=='paused':normal=normal[:len(phases)-1]+['paused']
            else:normal=normal[:len(phases)]
            if phases!=normal or phases[-1]!=v['phase'] or any(h[i][1]>h[i+1][1] for i in range(len(h)-1)):
                raise ValueError()
            if h[-1][1]>self.ledger.now():raise ValueError()
            _encode(v)
        except (ValueError,TypeError,KeyError) as exc:raise StateUnavailable('rollout control corrupt; never replace') from exc
        return row[0],copy.deepcopy(v)

    def _move(self, phase):
        for _ in range(CAS_ATTEMPTS):
            row=self._read()
            if row is None:
                if phase!='prepared':raise StateUnavailable('rollout intent missing')
                revision=0;v=dict(v=1,plan_sha256=self.plan_sha256,phase=phase,history=[[phase,self.ledger.now()]])
            else:
                revision,old=row
                if old['phase']==phase:return old
                if old['phase']=='paused':raise UsageConflict('rollback is terminal; no automatic reactivation')
                if phase!='paused' and self.PHASES.index(phase)!=self.PHASES.index(old['phase'])+1:
                    raise UsageConflict('rollout phase order')
                v=copy.deepcopy(old);v['phase']=phase;v['history'].append([phase,self.ledger.now()])
            self._audit('transition-intent',v)
            if self.store.compare_exchange(self.key,revision,v):
                observed=self._read()
                if observed is None or observed[1]['history'][:len(v['history'])]!=v['history']:
                    raise StateUnavailable('rollout phase write unconfirmed')
                self._audit('transition-complete',v)
                return v
        raise StateUnavailable('rollout contention')

    def _observe(self, *, enabled=None):
        v=self.provider.observe(copy.deepcopy(self.manifest))
        expected={'namespace','writer_fence_sha256','baseline_sha256','account_map_sha256',
                  'legacy_disabled','inflight_legacy','new_enabled'}
        if (type(v) is not dict or set(v)!=expected
                or any(v[k]!=self.manifest[k] for k in ('namespace','writer_fence_sha256','baseline_sha256','account_map_sha256'))
                or v['legacy_disabled'] is not True or type(v['inflight_legacy']) is not int or v['inflight_legacy']!=0
                or type(v['new_enabled']) is not bool or (enabled is not None and v['new_enabled'] is not enabled)):
            raise StateUnavailable('provider fence/baseline/account observations unconfirmed')
        return v

    def admission_allowed(self, manifest):
        try:
            row=self._read()
            return (manifest==self.manifest and row is not None and row[1]['phase']=='active'
                    and self._observe(enabled=True)['new_enabled'] is True)
        except Exception:return False

    def policy(self):
        return CutoverPolicy(self.control,namespace=self.manifest['namespace'],
            expected_sha256=self.plan_sha256,verify_writer_fence=self.admission_allowed)

    def rollback(self):
        # Try both closure mechanisms even if audit/provider is unavailable.
        # Never return success unless the provider AND durable pause read back.
        errors=[]
        for action in (lambda:self._audit('rollback-intent',{'policy':'pause-retain-fence/v1'}),
                       lambda:self.provider.disable_new(copy.deepcopy(self.manifest)),
                       lambda:self._move('paused'),
                       lambda:self._observe(enabled=False)):
            try:action()
            except Exception as exc:errors.append(type(exc).__name__)
        if errors:raise StateUnavailable('rollback closure or audit unconfirmed; manual reconciliation required')
        self._audit('rollback-complete',{'policy':'pause-retain-fence/v1'})
        return self._read()[1]

    def execute(self):
        self._move('prepared') if self._read() is None else None
        try:
            row=self._read()[1]
            if row['phase']=='paused':raise UsageConflict('rollout previously paused')
            if row['phase']=='active':
                self._observe(enabled=True);return row
            if self.ledger.now()//86400!=self.manifest['day']:
                raise StateUnavailable('cutover day ended; no guessed baseline')
            if row['phase']=='prepared':
                self.provider.freeze_legacy(copy.deepcopy(self.manifest))
                self._observe(enabled=False);self._move('frozen')
            if self._read()[1]['phase']=='frozen':
                self._observe(enabled=False)
                for r in self.rows:
                    self.ledger.initialize_day(r['subject'],self.manifest['day'],
                        baseline_successes=r['successes'],baseline_attempts=r['attempts'],
                        manifest_sha256=self.manifest['baseline_sha256'])
                self._observe(enabled=False)
                seal_cutover(self.control,self.manifest,verify_frozen_inputs=lambda _:bool(self._observe(enabled=False)))
                self._move('sealed')
            if self._read()[1]['phase']=='sealed':
                # Recheck all frozen inputs before enabling new routes. Runtime
                # admission additionally requires the durable active phase.
                self._observe(enabled=False)
                self.provider.enable_new(copy.deepcopy(self.manifest))
                self._observe(enabled=True);self._move('active')
            return self._read()[1]
        except Exception:
            self.rollback()
            raise

