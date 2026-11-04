"""Private, explicitly applied account-disposition composition.

One durable round-robin step invokes one bounded component. A failed lane does
not block unrelated work. No route, scheduler, provider credentials or startup
activation. Deadlines are cooperative dispatch limits, not hard RPC deadlines.
"""
import math
import time
from lite_account_identity import IdentityRejected
from lite_attest_state import StateUnavailable
from lite_disposition_progress import DispositionProgress
from lite_research_history import ResearchMediaHistory
from lite_service_history import ServiceResultHistory
from lite_store_locations import location, overlaps
from lite_subscription_executor import key
from lite_subscription_runtime import SubscriptionRuntime
from lite_usage_ledger import digest

LANES=('service_current','apple_revocation','grant_key','research_current','service_history','research_history')
HISTORY=('service_history','research_history')
PUBLIC_LANES = {'service_current':'service_results', 'apple_revocation':'apple_revocation', 'grant_key':'grant_key'}
PENDING=('orphan_or_unlinked_data','soft_delete_and_backups','identity_subscription_retention','live_provider_device_acceptance')


class AccountDispositionWorker:
    immutable=staticmethod(SubscriptionRuntime.immutable)

    def __init__(self,*,progress,service_history,research_history,store,audit_store,clock=time.monotonic):
        if (not isinstance(progress,DispositionProgress) or not isinstance(service_history,ServiceResultHistory)
                or not isinstance(research_history,ResearchMediaHistory) or service_history.d is not progress.results
                or research_history.w.d is not progress.deletion or progress.deletion.disposition is not progress
                or progress.research not in (None,research_history.w) or not callable(clock)):
            raise ValueError('matching real disposition components required')
        self.p=progress;self.s=service_history;self.r=research_history
        self.store=store;self.audit=audit_store;self.clock=clock
        d=progress.deletion;w=research_history.w
        peers=(progress.store,progress.audit,d.store,d.audit,d.bridge.store,d.bridge.audit,d.bridge.registry.store,
            d.lifecycle.store,d.lifecycle.audit,d.grants.store,d.grants.audit,progress.cipher.store,progress.cipher.audit,
            progress.results.store,progress.results.audit,progress.results.ops.store,progress.results.ops.ledger.store,
            self.s.jobs,self.s.audit,w.store,w.audit,w.c.store,w.c.audit,w.r.store,self.r.jobs,self.r.audit)
        guarded=(store,audit_store,self.s.jobs,self.s.audit,self.r.jobs,self.r.audit)
        others=[s for s in peers if not any(s is g for g in guarded)]
        if any(overlaps(location(a),location(b)) for i,a in enumerate(guarded) for b in guarded[i+1:]) or any(
                overlaps(location(a),location(b)) for a in guarded for b in others):
            raise ValueError('independent worker checkpoint and audit required')
        for a in guarded+(progress.results.ops.store,):
            for prefix in research_history.policy['prefixes']:
                if overlaps(location(a),('gcs',research_history.bucket.name,prefix.rstrip('/'))):
                    raise ValueError('worker state cannot overlap media')

    def _plan(self,person):
        p=self.p._plan(person)
        # Validate existing public observations before dispatch, never repair
        # missing evidence by fabricating a new progress intent.
        self.p._read(p)
        return dict(v=1,progress=p,research=self.r.w.c.inventory_plan(person),
                    research_config=self.r.w.config,service_policy=self.s.policy,research_policy=self.r.policy)

    @staticmethod
    def _initial(p):
        return dict(v=1,plan=p,step=0,next=0,states={n:'not_started' for n in LANES},
                    receipts={n:None for n in LANES},round_targets={n:None for n in HISTORY})

    def _outcome(self,p,lane,r):
        if type(r) is not dict or r.get('account_deleted') is not False:
            raise StateUnavailable('component cannot claim account deletion')
        if lane=='service_current':
            self.p._validate_service(p['progress'],r)
            done=r['service_result_payloads']=='current_sealed'
        elif lane=='grant_key':
            self.p._validate_key(p['progress'],r);done=self.p.key_observed(r)
        elif lane=='apple_revocation':
            d=p['progress']['deletion']
            if (r.get('operation')!=d['operation'] or r.get('installation')!=d['requester']
                    or r.get('inventory_sha256')!=d['inventory_sha256'] or r.get('grant_total')!=d['total']
                    or r.get('apple_revocation') not in ('not_started','in_progress','pending','confirmed')
                    or type(r.get('grants_revoked')) is not int or not 0<=r['grants_revoked']<=d['total']):
                raise StateUnavailable('Apple disposition binding differs')
            done=r['apple_revocation']=='confirmed'
            if done and (r['grants_revoked']!=d['total'] or r.get('grants_examined')!=d['total']):
                raise StateUnavailable('Apple disposition incomplete')
        else:
            if r.get('physical_backups_erased') is not False or type(r.get('scan_finished')) is not bool:
                raise StateUnavailable('incomplete media observation required')
            if lane=='research_current':
                expected=digest(dict(v=1,config=p['research_config'],closure=p['research']))
                fields=('examined_grants','linked_grants','unresolved_grants')
                if (r.get('plan_sha256')!=expected or r.get('total_grants')!=p['research']['total']
                        or any(type(r.get(k)) is not int or r[k]<0 for k in fields)
                        or r['linked_grants']+r['unresolved_grants']!=r['examined_grants']
                        or r['examined_grants']>r['total_grants']
                        or r['scan_finished']!=(r['examined_grants']==r['total_grants'])):
                    raise StateUnavailable('research current observation differs')
            else:
                expected=(dict(v=1,policy=p['service_policy'],closure=p['progress']['service']) if lane=='service_history'
                    else dict(v=1,policy=p['research_policy'],media=dict(v=1,config=p['research_config'],closure=p['research'])))
                counts=(('operations_checked','soft_pending_operations','soft_unverified_operations') if lane=='service_history'
                        else ('objects_checked','soft_pending_slots','soft_unverified_slots','linked_grants','unresolved_grants'))
                if (r.get('plan_sha256')!=digest(expected) or r.get('apply') is not True
                        or type(r.get('round')) is not int or not 1<=r['round']<=1000000
                        or any(type(r.get(k)) is not int or not 0<=r[k]<=1000000 for k in counts)
                        or r.get('current_fences_preserved') is not True or r.get('observed_scope_only') is not True
                        or r.get('active_history_absent_in_verified_scope') is not r['scan_finished']):
                    raise StateUnavailable('history observation binding differs')
            done=r['scan_finished']
        return 'observed' if done else 'pending'

    def _read(self,p):
        row=self.store.load(key('account-disposition-worker',digest(p)))
        if row is None:return None
        j=row[1]
        if (type(j) is not dict or set(j)!={'v','plan','step','next','states','receipts','round_targets'}
                or type(j['v']) is not int or j['v']!=1 or j['plan']!=p
                or type(j['step']) is not int or not 0<=j['step']<=1000000
                or type(j['next']) is not int or not 0<=j['next']<len(LANES)
                or type(j['states']) is not dict or set(j['states'])!=set(LANES)
                or type(j['receipts']) is not dict or set(j['receipts'])!=set(LANES)
                or type(j['round_targets']) is not dict or set(j['round_targets'])!=set(HISTORY)):
            raise StateUnavailable('worker checkpoint corrupt; never reset')
        for lane in LANES:
            state=j['states'][lane];r=j['receipts'][lane];target=j['round_targets'].get(lane)
            if state not in ('not_started','pending','blocked','observed') or (state=='not_started' and r is not None):
                raise StateUnavailable('worker state differs')
            if target is not None and (type(target) is not int or not 2<=target<=1000000 or state not in ('pending','blocked')):
                raise StateUnavailable('rescan target differs')
            if r is not None:
                outcome=self._outcome(p,lane,r)
                if state=='observed' and outcome!='observed':raise StateUnavailable('false observed disposition')
            elif state in ('pending','observed'):raise StateUnavailable('worker observation missing')
        for kind,value in (('plan',p),('checkpoint',j)):
            proof=self.audit.load(key('disposition-worker-'+kind,digest(value)))
            if proof is None or proof[1]!=value:raise StateUnavailable('original worker audit missing')
        return row

    def _save(self,p,old,j):
        if j['step']>1000000:raise StateUnavailable('worker revision needs review')
        self.immutable(self.audit,key('disposition-worker-checkpoint',digest(j)),j)
        try:self.store.compare_exchange(key('account-disposition-worker',digest(p)),0 if old is None else old[0],j)
        except StateUnavailable:pass
        row=self._read(p)
        if row is None or row[1]['step']<j['step']:raise StateUnavailable('worker checkpoint unconfirmed')
        # A concurrent step may win; never overwrite that step or its evidence.
        return row

    def _control(self,p):
        row=self.store.load(key('disposition-worker-control',digest(p)))
        anchor=self.audit.load(key('disposition-worker-control-anchor',digest(p)))
        initial=dict(v=1,plan_sha256=digest(p),revision=0,paused=False)
        if row is None:
            if anchor is not None or self.store.load(key('account-disposition-worker',digest(p))) is not None:
                raise StateUnavailable('original worker control missing; never default to active')
            return None,initial
        if anchor is None or anchor[1]!=initial:raise StateUnavailable('worker control anchor missing')
        c=row[1]
        if (type(c) is not dict or set(c)!={'v','plan_sha256','revision','paused'} or type(c['v']) is not int or c['v']!=1
                or c['plan_sha256']!=digest(p) or type(c['revision']) is not int or not 0<=c['revision']<=1000000
                or type(c['paused']) is not bool):raise StateUnavailable('worker control corrupt')
        proof=self.audit.load(key('disposition-worker-control',digest(c)))
        if proof is None or proof[1]!=c:raise StateUnavailable('worker control audit missing')
        return row,c

    def _start_control(self,p):
        row,c=self._control(p)
        if row is not None:return row,c
        self.immutable(self.audit,key('disposition-worker-control-anchor',digest(p)),c)
        self.immutable(self.audit,key('disposition-worker-control',digest(c)),c)
        try:self.store.compare_exchange(key('disposition-worker-control',digest(p)),0,c)
        except StateUnavailable:pass
        return self._control(p)

    def set_paused(self,person,*,paused,expected_revision):
        """Trusted operator CAS; pause survives restart and never reopens account."""
        if type(paused) is not bool or type(expected_revision) is not int or not 0<=expected_revision<1000000:
            raise ValueError('explicit pause and observed revision required')
        p=self._plan(person);self._read(p);row,c=self._control(p)
        if c['revision']!=expected_revision:raise StateUnavailable('pause control changed; read before updating')
        if c['paused']==paused:return c
        if row is None:row,c=self._start_control(p)
        value=dict(c,revision=c['revision']+1,paused=paused)
        self.immutable(self.audit,key('disposition-worker-control',digest(value)),value)
        try:self.store.compare_exchange(key('disposition-worker-control',digest(p)),0 if row is None else row[0],value)
        except StateUnavailable:pass
        _,actual=self._control(p)
        if actual!=value:raise StateUnavailable('worker control unconfirmed')
        return actual

    def status(self,person):
        """Private checkpoint view; no Apple/KMS/media listing or mutation."""
        p=self._plan(person);row=self._read(p);j=self._initial(p) if row is None else row[1];_,c=self._control(p)
        return dict(v=1,plan_sha256=digest(p),revision=j['step'],paused=c['paused'],control_revision=c['revision'],
            states=dict(j['states']),observations=dict(j['receipts']),pending_lanes=list(PENDING)+(['shared_key_retirement'] if self.p.shared else []),
            account_deleted=False,physical_backups_erased=False,observed_scope_only=True)

    def request_rescan(self,person,*,lane):
        if lane not in HISTORY:raise ValueError('only completed history observations can be rescanned')
        p=self._plan(person);row=self._read(p)
        if row is None:raise StateUnavailable('worker not started')
        j=row[1]
        if j['round_targets'][lane] is not None:return self.status(person)
        r=j['receipts'][lane]
        if r is None or not r['scan_finished']:raise StateUnavailable('active scan cannot be reset')
        target=r['round']+1
        if target>1000000:raise StateUnavailable('history rounds require review')
        value=dict(j,step=j['step']+1,states=dict(j['states'],**{lane:'pending'}),
                   round_targets=dict(j['round_targets'],**{lane:target}))
        self._save(p,row,value)
        return self.status(person)

    def _invoke(self,p,j,lane,limit):
        person=p['progress']['deletion']['person']
        if lane=='service_current':
            r=self.p.results.advance(person,limit=limit)
        elif lane=='apple_revocation':r=self.p.deletion.advance(person,limit=limit)
        elif lane=='grant_key':r=self.p.cipher.destroy_after_revocation(self.p.deletion,person)
        elif lane=='research_current':r=self.r.w.advance(person,limit=limit,apply=True)
        else:
            h=self.s if lane=='service_history' else self.r
            target=j['round_targets'][lane];rescan=False
            if target is not None:
                current=h.advance_account(person,apply=False)
                if current['round']==target-1 and current['scan_finished']:rescan=True
                elif current['round']!=target:raise StateUnavailable('history rescan round differs')
            r=h.advance_account(person,versions=limit,apply=True,rescan=rescan)
        self._outcome(p,lane,r)
        return r

    def _publish_progress(self,p):
        def observe(old):
            row=self._read(p)
            if row is None: raise StateUnavailable('missing original worker checkpoint')
            j=row[1]
            blocked=set(old['blocked'] if old else [])
            for lane, name in PUBLIC_LANES.items():
                state=j['states'][lane]
                if state=='blocked': blocked.add(name)
                elif state!='not_started': blocked.discard(name)
            return j['receipts']['service_current'], j['receipts']['grant_key'], sorted(blocked)

        public=p['progress']
        self.p.immutable(self.p.audit,key('disposition-progress-intent',digest(public)),public)
        self.p._record_observation(public,observe)

    def run(self,person,*,apply=False,max_steps=12,max_seconds=30,limit=5,cancelled=lambda:False):
        """Bound dispatches; blocked lanes are tried at most once per invocation.

        Deadline/cancellation checked before dispatch and after each return. An
        already-running component/RPC is not killed. Production supervision and
        RPC deadline propagation remain separate deployment acceptance gates.
        """
        if (type(apply) is not bool or type(max_steps) is not int or not 1<=max_steps<=100
                or type(limit) is not int or not 1<=limit<=20 or type(max_seconds) not in (int,float)
                or not math.isfinite(max_seconds) or not 0<max_seconds<=300 or not callable(cancelled)):
            raise ValueError('bounded explicit worker request required')
        started=self.clock();deadline=started+max_seconds;dispatched=0;blocked=set();reason='step_limit'
        attempts={n:0 for n in LANES}
        if not apply:return dict(self.status(person),dispatches=0,stop_reason='read_only',deadline_is_cooperative=True)
        for _ in range(max_steps):
            if cancelled():reason='cancelled';break
            if self.clock()>=deadline:reason='deadline';break
            p=self._plan(person);row=self._read(p);_,control=self._control(p)
            # Retry only the receipt publication if the previous invocation
            # stopped between saving the private checkpoint and updating the app.
            if dispatched==0 and row and any(row[1]['states'][n]!='not_started' for n in PUBLIC_LANES):
                self._publish_progress(p)
            if control['paused']:reason='paused';break
            j=self._initial(p) if row is None else row[1]
            choices=[(j['next']+n)%len(LANES) for n in range(len(LANES))]
            eligible=[n for n in choices if j['states'][LANES[n]]!='observed' and LANES[n] not in blocked and attempts[LANES[n]]<3]
            if not eligible:
                reason=('blocked' if blocked else 'retry_budget' if any(s!='observed' for s in j['states'].values())
                        else 'observations_complete');break
            self._start_control(p)
            self.immutable(self.audit,key('disposition-worker-plan',digest(p)),p)
            if row is None:row=self._save(p,None,j);j=row[1]
            index=eligible[0];lane=LANES[index]
            intent=dict(v=1,plan_sha256=digest(p),step=j['step'],lane=lane,limit=limit,control_revision=control['revision'])
            self.immutable(self.audit,key('disposition-worker-dispatch',digest(intent)),intent)
            # A pause during evidence reads/writes prevents new dispatch.
            if cancelled():reason='cancelled';break
            if self.clock()>=deadline:reason='deadline';break
            if self._control(p)[1]['paused']:reason='paused';break
            receipts=dict(j['receipts']);targets=dict(j['round_targets']);states=dict(j['states'])
            try:
                r=self._invoke(p,j,lane,limit);receipts[lane]=r;states[lane]=self._outcome(p,lane,r)
                if lane in HISTORY and targets[lane] is not None:
                    if r['round']!=targets[lane]:raise StateUnavailable('rescan outcome differs')
                    targets[lane]=None
            except (StateUnavailable,IdentityRejected):
                states[lane]='blocked';blocked.add(lane)
            dispatched+=1
            attempts[lane]+=1
            value=dict(j,step=j['step']+1,next=(index+1)%len(LANES),states=states,receipts=receipts,round_targets=targets)
            self._save(p,row,value)
            if lane in PUBLIC_LANES:
                self._publish_progress(p)
        return dict(self.status(person),dispatches=dispatched,stop_reason=reason,deadline_is_cooperative=True)
