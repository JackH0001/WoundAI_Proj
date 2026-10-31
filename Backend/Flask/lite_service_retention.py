"""Explicit, single-operation service-result compaction; never startup/HTTP.

No credentials, listing, deletes, scheduler, research store or automatic retry.
Injected dedicated CAS stores only. Plans pin the exact source generation/hash;
unknown/running execution is a hard stop, not an inferred failure or refund.
Compaction is NOT GCS generation/soft-delete erasure or research withdrawal.
"""
import copy
import hashlib
from lite_attest_state import StateUnavailable, _encode, _revision
from lite_usage_ledger import UsageConflict, digest, _subject, _hex, _integer
from lite_service_operations import ServiceOperations


class RetentionBlocked(UsageConflict): pass


class ServiceResultRetention:
    PLAN_TTL=600
    def __init__(self,operations,*,audit_store,scope):
        if not isinstance(operations,ServiceOperations):raise ValueError('trusted service operations required')
        _hex(scope)
        if audit_store is operations.store or audit_store is operations.ledger.store:
            raise ValueError('separate audit store required')
        self.ops,self.audit,self.scope=operations,audit_store,scope

    def _entry(self,subject,operation):
        row=self.ops.load(subject,operation)
        if row is None:raise RetentionBlocked('missing mapping; never infer empty state')
        return row

    def _action(self,v,receipt):
        if v['v']==2:raise RetentionBlocked('already compacted; keep terminal identity')
        if self.ops.now()<v['expires']:raise RetentionBlocked('read window has not expired')
        if v['result'] is not None:
            outcome='succeeded' if v['result']['wound_polygons'] else 'empty'
            if receipt['outcome']=='running':return 'settle_and_compact'
            if receipt['outcome']==outcome and receipt['result_sha256']==v['result_sha256']:return 'compact'
            raise RetentionBlocked('saved result and ledger disagree')
        if receipt['outcome']=='reserved':return 'cancel_unstarted'
        if receipt['outcome']=='cancelled':return 'compact_cancelled'
        raise RetentionBlocked('execution/result unknown; manual evidence required')

    def plan(self,subject,operation):
        """Read only, one exact job. Never scans, settles or mutates the ledger."""
        _subject(subject);_hex(operation,32)
        rev,v=self._entry(subject,operation)
        try:r=self.ops.ledger.receipt(self.ops.ticket(subject,v))
        except UsageConflict as e:raise RetentionBlocked('no reservation; fence races before cleanup') from e
        action=self._action(v,r);now=self.ops.now()
        p=dict(v=1,scope=self.scope,usage_namespace=self.ops.ledger.namespace,subject=subject,operation=operation,
               expected_revision=rev,before_sha256=digest(v),receipt=r,action=action,created=now,deadline=now+self.PLAN_TTL)
        _encode(p)
        return p

    def _validate_plan(self,p):
        fields={'v','scope','usage_namespace','subject','operation','expected_revision','before_sha256','receipt','action','created','deadline'}
        if (type(p) is not dict or set(p)!=fields or type(p['v']) is not int or p['v']!=1
                or p['scope']!=self.scope or p['usage_namespace']!=self.ops.ledger.namespace
                or p['action'] not in ('compact','settle_and_compact','cancel_unstarted','compact_cancelled')):
            raise RetentionBlocked('plan scope/schema mismatch')
        _subject(p['subject']);_hex(p['operation'],32);_revision(p['expected_revision']);_hex(p['before_sha256'])
        start,end=_integer(p['created']),_integer(p['deadline'])
        if end-start!=self.PLAN_TTL or not start<=self.ops.now()<end:raise RetentionBlocked('plan deadline')
        r=p['receipt']
        if (type(r) is not dict or set(r)!={'day','operation','request_digest','outcome','result_sha256','grant_sha256','admitted_limit'}
                or type(r['day']) is not int or r['day']<0 or r['operation']!=p['operation']
                or r['outcome'] not in ('reserved','running','succeeded','empty','cancelled')
                or type(r['admitted_limit']) is not int or r['admitted_limit'] not in (2,10)):
            raise RetentionBlocked('invalid planned receipt')
        _hex(r['request_digest']);_hex(r['grant_sha256'])
        if r['outcome'] in ('reserved','running'):
            if r['result_sha256'] is not None:raise RetentionBlocked('unexpected planned result')
        else:_hex(r['result_sha256'])
        _encode(p)

    def audit_key(self,plan,phase):
        if phase not in ('intent','complete'):raise ValueError('audit phase')
        return hashlib.sha256(('lite/service-retention/1\0'+self.scope+'\0'+digest(plan)+'\0'+phase).encode()).digest()

    def _append(self,key,value):
        row=self.audit.load(key)
        if row is None:
            self.audit.compare_exchange(key,0,value)
            row=self.audit.load(key)
        if row is None or row[1]!=value:raise StateUnavailable('audit append/readback unconfirmed')

    def _intent(self,p):return dict(v=1,kind='service-result-compaction-intent',plan=copy.deepcopy(p))

    def _complete(self,p,v):
        expected=self._intent(p);intent=self.audit.load(self.audit_key(p,'intent'))
        if intent is None or intent[1]!=expected:raise StateUnavailable('terminal without matching audit intent')
        receipt=self.ops.ledger.receipt(self.ops.ticket(p['subject'],v))
        if receipt!=v['retention']['receipt']:raise StateUnavailable('terminal ledger differs')
        done=dict(v=1,kind='service-result-compaction-complete',plan_sha256=digest(p),
                  terminal_sha256=digest(v),receipt=receipt,compacted_at=v['retention']['compacted_at'])
        self._append(self.audit_key(p,'complete'),done)
        return done

    def execute(self,plan):
        """One bounded operation; failures stop. Reuse SAME plan to read back.

        The caller must authorize the exact plan digest at the cloud boundary;
        a plan or this method is not a public authorization API.
        """
        p=copy.deepcopy(plan);self._validate_plan(p)
        subject,operation=p['subject'],p['operation'];revision,v=self._entry(subject,operation)
        if v['v']==2:
            if v['retention']['plan_sha256']!=digest(p):raise RetentionBlocked('another plan already compacted')
            return self._complete(p,v)
        if revision!=p['expected_revision'] or digest(v)!=p['before_sha256']:
            raise RetentionBlocked('source generation/hash changed; no rebase')
        ticket=self.ops.ticket(subject,v);r=self.ops.ledger.receipt(ticket)
        cancellation=digest(['service-result-expiry/1',digest(p)])
        # A prior invocation may have settled/cancelled but lost its reply.
        recovered=(p['action']=='cancel_unstarted' and r['outcome']=='cancelled' and r['result_sha256']==cancellation)
        recovered |= (p['action']=='settle_and_compact' and v['result'] is not None
                      and r['outcome']==('succeeded' if v['result']['wound_polygons'] else 'empty')
                      and r['result_sha256']==v['result_sha256'])
        if recovered:
            if any(r[k]!=p['receipt'].get(k) for k in ('day','operation','request_digest','grant_sha256','admitted_limit')):
                raise RetentionBlocked('receipt binding changed')
        elif r!=p['receipt'] or self._action(v,r)!=p['action']:
            raise RetentionBlocked('work changed after planning')
        self._append(self.audit_key(p,'intent'),self._intent(p))
        if self.ops.now()>=p['deadline']:raise RetentionBlocked('deadline after audit')
        if p['action']=='cancel_unstarted':
            r=self.ops.ledger.cancel_unstarted(ticket,cancellation)
        elif p['action']=='settle_and_compact':
            r=self.ops.ledger.settle(ticket,v['worker'],'succeeded' if v['result']['wound_polygons'] else 'empty',v['result_sha256'])
        # Terminal state cannot later be claimed; a losing cancel/claim race
        # raises above and leaves the original result mapping untouched.
        if r['outcome'] not in ('succeeded','empty','cancelled'):raise RetentionBlocked('nonterminal work')
        if self.ops.now()>=p['deadline']:raise RetentionBlocked('deadline after ledger')
        terminal=copy.deepcopy(v);terminal.update(v=2,result=None,
            retention=dict(plan_sha256=digest(p),compacted_at=self.ops.now(),receipt=r))
        # Deliberately only one generation CAS, never retry against a newer row.
        changed=self.ops.store.compare_exchange(self.ops.key(subject,operation),revision,terminal)
        observed=self._entry(subject,operation)[1]
        if observed!=terminal:
            # Another invocation of THIS plan may have won at a different time.
            if changed or observed['v']!=2 or observed['retention']['plan_sha256']!=digest(p):
                raise StateUnavailable('compaction conflict/readback unconfirmed')
        return self._complete(p,observed)
