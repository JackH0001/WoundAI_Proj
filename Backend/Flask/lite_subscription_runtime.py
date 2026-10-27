"""Opt-in stable-account runtime. Batch size never defines the customer population.

Trusted provisioning remains external (App Attest is not human enrollment).
No cloud discovery, quota reset, research access, or automatic activation.
"""
import copy
from lite_attest_state import StateUnavailable
from lite_usage_ledger import UsageConflict, digest, _hex, _subject
from lite_subscription_migration import StableSubscriptionLinks
from lite_subscription_executor import key, StoredObservation, EvidenceContradiction


class SubscriptionRuntime:
    def __init__(self, *, links, registry, state_store, audit_store, observation_store,
                 evidence, expected_source, expected_image):
        if not isinstance(links, StableSubscriptionLinks) or registry.subscription_links is not links:
            raise ValueError('matching stable registry required')
        self.links,self.registry,self.store,self.audit=links,registry,state_store,audit_store
        self.observations,self.evidence=observation_store,evidence
        self.source,self.image=expected_source,expected_image
        self.scope=digest(links.scope)
        if registry.admission_guard is not None or registry.bootstrap is not None:
            raise ValueError('runtime already installed')
        registry.admission_guard=self.allowed;registry.bootstrap=self.bootstrap
        registry.subscription_runtime=self

    def clone(self):
        return StableSubscriptionLinks(store=self.links.store,audit_store=self.links.audit,
            authorities=tuple(self.links.catalog.values()),active_policy=self.links.active,
            legacy_policies=self.links.legacy,authorize=lambda *_:False,writer_fence=lambda _:False,
            clock=self.links.clock)

    @staticmethod
    def immutable(store,k,value):
        old=store.load(k)
        if old is None:store.compare_exchange(k,0,value);old=store.load(k)
        if old is None or digest(old[1])!=digest(value):raise StateUnavailable('runtime evidence unconfirmed')
        return old[1]

    def manifest(self,m):
        if (type(m) is not dict or set(m)!={'v','namespace','source','image','inventory','fence','operation','entries'}
                or type(m['v']) is not int or m['v']!=1 or type(m['entries']) is not list
                or len(m['entries'])>100 or m['source']!=self.source or m['image']!=self.image or
                any(digest(e['plan']['scope'])!=self.scope for e in m['entries'])):
            raise UsageConflict('runtime deployment/scope differs')
        _subject(m['namespace']);_hex(m['source'],40)
        for k in ('image','inventory','fence','operation'):_hex(m[k])
        # Store bounded header/digests, not a 100-account manifest in one 16KiB
        # CAS row. Exact plans remain in per-account immutable core intents.
        for index, entry in enumerate(m['entries']):
            self.immutable(self.audit,key('runtime-manifest-entry',digest([digest(m),index])),
                dict(manifest=digest(m),index=index,entry=copy.deepcopy(entry)))
        summary={k:copy.deepcopy(v) for k,v in m.items() if k!='entries'}
        summary.update(identity=digest(m),entries_sha256=digest(m['entries']),accounts=len(m['entries']))
        return self.immutable(self.store,key('runtime-manifest',digest(m)),summary)

    def _manifest(self,identity):
        row=self.store.load(key('runtime-manifest',identity))
        if row is None:raise StateUnavailable('manifest missing')
        m=row[1]
        if (set(m)!={'v','namespace','source','image','inventory','fence','operation','identity','entries_sha256','accounts'}
                or m['identity']!=identity or type(m['v']) is not int or m['v']!=1
                or type(m['accounts']) is not int or not 0<=m['accounts']<=100
                or m['source']!=self.source or m['image']!=self.image):raise StateUnavailable('deployment activation required')
        for k in ('inventory','fence','operation','identity','entries_sha256'):_hex(m[k])
        return m

    def observe(self,m):
        return StoredObservation(self.observations,self.evidence,m['namespace']).observe(m,manifest_digest=m.get('identity'))

    def _policy_key(self,policy):
        return key('runtime-policy',digest([self.scope,policy,self.source,self.image]))

    def _policy(self,policy):
        row=self.store.load(self._policy_key(policy))
        if row is None:raise StateUnavailable('policy not activated')
        r=row[1]
        if (set(r)!={'v','scope','policy','manifest','evidence'} or type(r['v']) is not int or r['v']!=1
                or r['scope']!=self.scope or r['policy']!=policy or policy not in self.links.catalog):
            raise StateUnavailable('policy activation corrupt')
        a=self.audit.load(key('runtime-activation',r['evidence']))
        if a is None or digest(a[1])!=r['evidence'] or a[1]['record']!=dict((k,v) for k,v in r.items() if k!='evidence'):
            raise StateUnavailable('policy audit absent or changed')
        m=self._manifest(r['manifest']);self.observe(m);return m

    def _activate(self,m,proof):
        self.manifest(m)
        # Activate only policies actually validated by this catalog. This does
        # not change an existing account's product, original transaction or day.
        for policy in self.links.catalog:
            r=dict(v=1,scope=self.scope,policy=policy,manifest=digest(m))
            a=dict(record=r,proof=copy.deepcopy(proof));a_hash=digest(a)
            self.immutable(self.audit,key('runtime-activation',a_hash),a)
            k=self._policy_key(policy)
            old=self.store.load(k)
            if old is None:self.store.compare_exchange(k,0,dict(r,evidence=a_hash))
            # Another successful batch may have activated it first. Keep its
            # immutable origin; all batches use independent observer records.
            self._policy(policy)

    def activate_batch(self,m,state):
        if state['phase']!='active' or state['manifest']!=digest(m):raise UsageConflict('batch incomplete')
        confirmed=dict(v=1,manifest=digest(m),kind='state-confirmed',value=state)
        audit=self.audit.load(key('audit',digest(confirmed)))
        control=self.store.load(key('control',m['namespace']))
        if audit is None or audit[1]!=confirmed or control is None or control[1]!=state:
            raise StateUnavailable('batch activation not confirmed')
        self._activate(m,dict(kind='completed-batch',state=state,audit=digest(confirmed)))

    def activate_fresh(self,m,approval):
        """Explicit empty-inventory launch only; signed external proof, no scan.

        Must be authorized after a real inventory/writer-fence review. Never
        infer an empty deployment from a missing individual account row.
        """
        if m.get('entries')!=[]:raise UsageConflict('fresh activation requires empty reviewed inventory')
        p=self.evidence.verify(approval,'approval')
        if (set(p)!={'kind','manifest','action','expected_state','issued_at','expires_at','nonce'}
                or p['manifest']!=digest(m) or p['action']!='activate-fresh'
                or p['expected_state']!=digest(None)):
            raise EvidenceContradiction('fresh activation approval differs')
        _hex(p['nonce'])
        if p['kind']!='lite-migration-approval/1':raise EvidenceContradiction('approval kind differs')
        self.observe(m);self._activate(m,dict(kind='fresh-stable-launch',approval=approval))

    def _account_key(self,a):return key('runtime-account-batch',digest([self.scope,digest(a['subject'])]))

    def claim(self,m):
        self.manifest(m)
        for e in m['entries']:
            a=self.registry.registration(e['installation']);p=e['plan'];k=self._account_key(a)
            if digest(a)!=p['registration_sha256']:raise UsageConflict('registration differs from plan')
            target=dict(v=1,subject_hash=digest(a['subject']),token_hash=digest(a['token']),
                        manifest=digest(m),namespace=m['namespace'])
            old=self.store.load(k)
            if old and old[1]==target:
                self.immutable(self.audit,key('runtime-claim',digest(target)),target)
                continue
            if old:self._batch_ready(a,old[1])
            current=self.links._read(a['subject'],a['token'],complete=False)
            if (current[0] if current else 0)!=p['expected_revision'] or digest(current[1] if current else None)!=digest(p['before']):
                raise UsageConflict('account changed before batch claim')
            if not self.store.compare_exchange(k,old[0] if old else 0,target):raise UsageConflict('concurrent account batch')
            self.immutable(self.audit,key('runtime-claim',digest(target)),target)

    def _batch_ready(self,a,r):
        if (set(r)!={'v','subject_hash','token_hash','manifest','namespace'} or type(r['v']) is not int or r['v']!=1
                or r['subject_hash']!=digest(a['subject']) or r['token_hash']!=digest(a['token'])):
            raise StateUnavailable('account claim corrupt')
        m=self._manifest(r['manifest'])
        if r['namespace']!=m['namespace']:raise StateUnavailable('account claim namespace differs')
        c=self.store.load(key('control',r['namespace']))
        if c is None or c[1]['phase']!='active' or c[1]['manifest']!=r['manifest']:
            raise StateUnavailable('this account migration paused or incomplete')
        confirmed=dict(v=1,manifest=r['manifest'],kind='state-confirmed',value=c[1])
        done=self.audit.load(key('audit',digest(confirmed)))
        claim=self.audit.load(key('runtime-claim',digest(r)))
        if done is None or done[1]!=confirmed or claim is None or claim[1]!=r:raise StateUnavailable('account audit incomplete')
        self.observe(m)

    def ready(self,a,*,require_record=True):
        row=self.links._read(a['subject'],a['token'])
        if require_record and row is None:raise StateUnavailable('stable account missing')
        claim=self.store.load(self._account_key(a))
        if claim:self._batch_ready(a,claim[1])
        policy=row[1]['policy'] if row and row[1]['policy'] else self.links.active
        return self._policy(policy)

    def allowed(self,account):
        try:self.ready(account);return True
        except Exception:return False

    def bootstrap(self,installation):
        a=self.registry.registration(installation)
        row=self.links._read(a['subject'],a['token'],complete=False)
        if row is not None:
            try:self.ready(a);return row[1]
            except StateUnavailable:
                # Only a previously retained free-bootstrap plan may finish its
                # own lost completion below; never replace a paid/migrated row.
                if row[1]['original_transaction'] is not None:raise
        if a['original_transaction'] is not None:raise UsageConflict('legacy paid account needs explicit migration')
        if any(v is not None for v in self.links._sources(a['subject'],a['token'])[0].values()):
            raise UsageConflict('legacy account needs explicit migration')
        return self.transition(installation,None)

    def transition(self,installation,original):
        """Trusted mapped installation only; native route supplies no subject/token.

        A first subscription still passes the real Apple authority inside execute.
        No account creation, product replacement, automatic retry ID rotation or
        quota mutation is possible. Reuse the retained plan after lost responses.
        """
        a=self.registry.registration(installation);m=self._policy(self.links.active)
        def authorized_runtime():
            self._policy(self.links.active)
            claim=self.store.load(self._account_key(a))
            if claim:self._batch_ready(a,claim[1])
            return True
        authorized_runtime()
        if original is not None:
            current=self.links._read(a['subject'],a['token'],complete=False)
            if current is None:raise StateUnavailable('free bootstrap must be confirmed first')
            if current[1]['original_transaction']==original:
                # Let the core validate/create a missing completion, preserving
                # the old intent without another Apple query.
                intent=self.links._intent(current[1]['plan'])
                return self.links._complete(intent)
        identity=digest([self.scope,self.links.active,a['subject'],a['token'],original])
        plan_key=key('runtime-native-plan',identity);stored=self.store.load(plan_key)
        control=self.clone()
        from lite_service_operations import ServiceAccounts
        registry=ServiceAccounts(self.registry.store,subscription_links=control)
        if stored is None:
            p=control.plan(registry,installation,operation=identity,
                inventory_sha256=m['inventory'],writer_fence_sha256=m['fence'],original_transaction=original)
            self.store.compare_exchange(plan_key,0,dict(v=1,installation=installation,plan=p));stored=self.store.load(plan_key)
        if stored is None:raise StateUnavailable('native plan not saved')
        wrapper=stored[1]
        if set(wrapper)!={'v','installation','plan'} or type(wrapper['v']) is not int or wrapper['v']!=1:
            raise StateUnavailable('native plan wrapper corrupt')
        retained_installation=wrapper['installation'];retained=self.registry.registration(retained_installation)
        if retained['subject']!=a['subject'] or retained['token']!=a['token']:
            raise UsageConflict('retained plan belongs to another account')
        p=wrapper['plan']
        if p['operation']!=identity or p['original_transaction']!=original:raise UsageConflict('native plan differs')
        authority=digest(dict(kind='verified-installation-native-action',registration=digest(retained),plan=digest(p)))
        # This is not a new human login. The server-side registration is an
        # immutable proof of a previously verified account/device relationship.
        control.authorize=lambda plan,proof: (digest(plan)==digest(p) and proof==authority
            and digest(self.registry.registration(installation))==digest(a)
            and digest(self.registry.registration(retained_installation))==digest(retained)
            and authorized_runtime())
        control.writer_fence=lambda proof: proof==m['fence'] and bool(self.observe(m))
        return control.execute(p,registry,retained_installation,authorization_sha256=authority)
