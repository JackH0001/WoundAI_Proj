"""Bounded internal worker for closed accounts' CURRENT service result payloads.

Permanent same-key CAS seals fence even previously missing operations. There is
no physical object deletion, ledger reset, real-media purge or public route.
Version history/backups and the other account data lanes remain separate gates.
"""
from lite_attest_state import CAS_ATTEMPTS, StateUnavailable
from lite_account_identity import IdentityRejected
from lite_subscription_executor import key
from lite_subscription_runtime import SubscriptionRuntime
from lite_usage_ledger import _hex, digest


def validate_seal(v,subject,operation):
    try:
        if (type(v) is not dict or set(v)!={'v','subject_hash','operation','disposition','prior'}
                or type(v['v']) is not int or v['v']!=3 or v['subject_hash']!=digest(subject)
                or v['operation']!=_hex(operation,32)): raise ValueError()
        d=v['disposition'];p=v['prior']
        if (type(d) is not dict or set(d)!={'scope','inventory_sha256','closure_operation','requested_at'}
                or type(d['requested_at']) is not int or d['requested_at']<0
                or type(p) is not dict or set(p)!={'revision','sha256'}
                or type(p['revision']) is not int or p['revision']<0
                or (p['sha256'] is None)!=(p['revision']==0)): raise ValueError()
        _hex(d['scope']);_hex(d['inventory_sha256']);_hex(d['closure_operation'],32)
        if p['sha256'] is not None: _hex(p['sha256'])
    except (ValueError,KeyError,TypeError): raise StateUnavailable('service disposition seal corrupt') from None


class ServiceResultDisposition:
    immutable=staticmethod(SubscriptionRuntime.immutable)

    def __init__(self,*,operations,lifecycle,store,audit_store):
        from lite_service_operations import ServiceOperations
        from lite_account_lifecycle import AccountLifecycle
        if (not isinstance(operations,ServiceOperations) or not isinstance(lifecycle,AccountLifecycle)
                or not lifecycle.service_catalog or operations.accounts.lifecycle is not lifecycle):
            raise ValueError('matching opt-in service catalog and operations required')
        all_stores=(store,audit_store,operations.store,lifecycle.store,lifecycle.audit,operations.ledger.store)
        for i,a in enumerate(all_stores):
            for b in all_stores[i+1:]:
                same_gcs = (getattr(a,'bucket',None) is not None and getattr(b,'bucket',None) is not None
                    and getattr(a,'prefix',None)==getattr(b,'prefix',None)
                    and (a.bucket is b.bucket or (getattr(a.bucket,'name',None) is not None
                         and a.bucket.name==getattr(b.bucket,'name',None))))
                if a is b or same_gcs or (getattr(a,'path',None) is not None and getattr(a,'path',None)==getattr(b,'path',None)):
                    raise ValueError('disposition, result, ledger and audit stores must be separate')
        self.ops,self.life,self.store,self.audit=operations,lifecycle,store,audit_store

    def plan(self,person):
        row=self.life._read(person)
        if row is None or row[1]['phase']!='closing': raise IdentityRejected('account must be closed before disposition')
        root=row[1];c=root['closure']
        return dict(v=1,scope=self.life.scope,person=person,closure_operation=c['operation'],
                    requested_at=c['requested_at'],inventory_sha256=c['inventory_sha256'],
                    head=root['service_head'],total=root['service_count'])

    def _job(self,plan):
        k=key('account-service-disposition',digest([plan['scope'],plan['person']]))
        intent_key=key('account-service-disposition-intent',digest(plan))
        row=self.store.load(k)
        if row is not None and self.audit.load(intent_key) is None:
            raise StateUnavailable('existing disposition missing original audit intent')
        self.immutable(self.audit,intent_key,plan)
        if row is None:
            initial=dict(v=1,plan=plan,cursor=plan['head'],remaining=plan['total'],processed=0)
            self.store.compare_exchange(k,0,initial);row=self.store.load(k)
        if row is None: raise StateUnavailable('disposition job unconfirmed')
        j=row[1]
        if (type(j) is not dict or set(j)!={'v','plan','cursor','remaining','processed'}
                or type(j['v']) is not int or j['v']!=1 or j['plan']!=plan
                or type(j['remaining']) is not int or type(j['processed']) is not int
                or min(j['remaining'],j['processed'])<0 or j['remaining']+j['processed']!=plan['total']
                or (j['cursor'] is None)!=(j['remaining']==0)):
            raise StateUnavailable('disposition checkpoint corrupt; never reset')
        if j['cursor'] is not None:
            n=self.life.services.node(plan['person'],j['cursor'])
            if n['position']!=j['remaining']: raise StateUnavailable('disposition checkpoint sequence differs')
        return k,row

    def _seal(self,plan,node):
        subject='apple-'+plan['person'];op=node['operation'];k=self.ops.key(subject,op)
        binding={name:plan[name] for name in ('scope','inventory_sha256','closure_operation','requested_at')}
        intent=dict(v=1,plan_sha256=digest(plan),node_sha256=digest(node),operation=op)
        intent_key=key('account-service-seal-intent',digest(intent))
        initial=self.ops.load(subject,op)
        if initial is not None and initial[1]['v']==3 and self.audit.load(intent_key) is None:
            raise StateUnavailable('existing seal missing original audit intent')
        self.immutable(self.audit,intent_key,intent)
        for _ in range(CAS_ATTEMPTS):
            row=self.ops.load(subject,op)
            if row is not None and row[1]['v']==3:
                sealed=row[1]
                if sealed['disposition']!=binding: raise StateUnavailable('service sealed by different disposition')
                receipt=dict(v=1,intent_sha256=digest(intent),seal_sha256=digest(sealed),prior=sealed['prior'])
                self.immutable(self.audit,key('account-service-seal-receipt',digest(intent)),receipt)
                return
            rev=0 if row is None else row[0]
            prior=dict(revision=rev,sha256=None if row is None else digest(row[1]))
            before=dict(v=1,intent_sha256=digest(intent),prior=prior)
            self.immutable(self.audit,key('account-service-seal-before',digest(before)),before)
            marker=dict(v=3,subject_hash=digest(subject),operation=op,disposition=binding,prior=prior)
            validate_seal(marker,subject,op)
            try: self.ops.store.compare_exchange(k,rev,marker)
            except StateUnavailable: pass  # Resolve lost acknowledgment, never delete/reset.
        raise StateUnavailable('service sealing contention; resume original deletion')

    def advance(self,person,*,limit=50):
        if type(limit) is not int or not 1<=limit<=100: raise ValueError('bounded disposition batch required')
        plan=self.plan(person);k,(rev,j)=self._job(plan)
        cursor=j['cursor'];remaining=j['remaining'];processed=j['processed']
        for _ in range(min(limit,remaining)):
            n=self.life.services.node(person,cursor)
            if n['position']!=remaining: raise StateUnavailable('disposition inventory gap')
            self._seal(plan,n)
            cursor=n['previous'];remaining-=1;processed+=1
        if processed!=j['processed']:
            self.store.compare_exchange(k,rev,dict(j,cursor=cursor,remaining=remaining,processed=processed))
        # Concurrent workers may win the checkpoint. Read that durable progress;
        # never overwrite it backwards or count an unacknowledged receipt.
        _,(_,j)=self._job(plan)
        receipt=dict(v=1,scope=plan['scope'],inventory_sha256=plan['inventory_sha256'],
            closure_operation=plan['closure_operation'],total=plan['total'],sealed=j['processed'],
            service_result_payloads='current_sealed' if j['remaining']==0 else 'pending',
            account_deleted=False,quota_reset=False,research_data_changed=False,
            historical_versions_erased=False,physical_backups_erased=False)
        if j['remaining']==0:
            self.immutable(self.audit,key('account-service-disposition-receipt',digest(plan)),receipt)
        return receipt
