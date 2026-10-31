"""Opt-in, exact-generation disposal of a closed account's service-result history.

Only the injected result store is touched. The current permanent CAS seal is
never deleted. Dry-run by default; no route, credentials, scheduler or startup
activation. Soft-deleted objects and external backups are never claimed erased.
"""
import re
from lite_attest_state import GCSStateStore, StateUnavailable
from lite_generation_history import ExactGenerationHistory
from lite_service_disposition import ServiceResultDisposition, validate_seal
from lite_disposition_progress import aliases
from lite_subscription_executor import key
from lite_subscription_runtime import SubscriptionRuntime
from lite_usage_ledger import digest


class ServiceResultHistory(ExactGenerationHistory):
    immutable=staticmethod(SubscriptionRuntime.immutable)

    def __init__(self, *, disposition, store, audit_store, policy):
        if not isinstance(disposition,ServiceResultDisposition) or not isinstance(disposition.ops.store,GCSStateStore):
            raise ValueError('indexed disposition and exact GCS result store required')
        self.d=disposition;self.store=disposition.ops.store;self.bucket=self.store.bucket;self.audit=audit_store;self.jobs=store
        if (type(policy) is not dict or set(policy)!={'v','id','bucket','prefix'} or type(policy['v']) is not int
                or policy['v']!=1 or type(policy['id']) is not str or not re.fullmatch(r'[a-z0-9_-]{1,80}',policy['id'])
                or type(policy['bucket']) is not str or not re.fullmatch(r'[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]',policy['bucket'])
                or policy['bucket']!=getattr(self.bucket,'name',None) or policy['prefix']!=self.store.prefix):
            raise ValueError('explicit exact result namespace policy required')
        peers=(self.store,self.d.store,self.d.audit,self.d.life.store,self.d.life.audit,self.d.ops.ledger.store,
               self.d.ops.accounts.store)
        if aliases(store,audit_store) or any(aliases(a,s) for a in (store,audit_store) for s in peers):
            raise ValueError('independent history checkpoint and audit required')
        self.policy=dict(policy)

    def _bound(self, person, operation):
        plan=self.d.plan(person);n=self.d.life.services.member(person,operation)
        if n is None or n['position']>plan['total']:raise StateUnavailable('operation outside frozen inventory')
        subject='apple-'+person;k=self.d.ops.key(subject,operation);row=self.d.ops.load(subject,operation)
        if row is None or row[1]['v']!=3:raise StateUnavailable('current result must be sealed first')
        rev,seal=row;validate_seal(seal,subject,operation)
        if seal['disposition']!={x:plan[x] for x in ('scope','inventory_sha256','closure_operation','requested_at')}:
            raise StateUnavailable('current seal is from another closure')
        intent=dict(v=1,plan_sha256=digest(plan),node_sha256=digest(n),operation=operation)
        a=self.d.audit.load(key('account-service-seal-intent',digest(intent)))
        b=self.d.audit.load(key('account-service-seal-receipt',digest(intent)))
        expected=dict(v=1,intent_sha256=digest(intent),seal_sha256=digest(seal),prior=seal['prior'])
        if a is None or a[1]!=intent or b is None or b[1]!=expected:
            raise StateUnavailable('original service seal evidence required')
        return dict(v=1,policy=self.policy,plan_sha256=digest(plan),operation=operation,
                    name=self.store._name(k),seal_generation=rev,seal_sha256=digest(seal))

    def page(self,person,*,cursor=None,slots=5,versions=20,apply=False):
        if type(slots) is not int or not 1<=slots<=5:raise ValueError('bounded account history page required')
        page=self.d.life.services.page(person,cursor=cursor,limit=slots)
        results=[self.advance(person,n['operation'],limit=versions,apply=apply) for n in page['items']]
        # Callers must revisit pending operations even after paging membership.
        # A finished catalog page is never full account disposition completion.
        return dict(v=1,inventory_sha256=page['inventory_sha256'],next_cursor=page['next_cursor'],
            results=results,pending_operations=[r['operation'] for r in results if r['status']!='listed_history_absent_at_check'],
            account_deleted=False,physical_backups_erased=False)
