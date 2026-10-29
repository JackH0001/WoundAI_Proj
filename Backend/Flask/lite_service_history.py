"""Opt-in, exact-generation disposal of a closed account's service-result history.

Only the injected result store is touched. The current permanent CAS seal is
never deleted. Dry-run by default; no route, credentials, scheduler or startup
activation. Soft-deleted objects and external backups are never claimed erased.
"""
import base64
import re
from google.api_core.exceptions import BadRequest, NotFound
from lite_attest_state import CAS_ATTEMPTS, GCSStateStore, MAX_STATE, StateUnavailable
from lite_service_disposition import ServiceResultDisposition, validate_seal
from lite_disposition_progress import aliases
from lite_subscription_executor import key
from lite_subscription_runtime import SubscriptionRuntime
from lite_usage_ledger import digest


class ServiceResultHistory:
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

    def _bucket_policy(self):
        try:
            self.bucket.reload(timeout=10,retry=None)
            p=self.bucket._properties
            if p.get('retentionPolicy') or p.get('defaultEventBasedHold'):
                raise StateUnavailable('bucket retention/hold requires review')
            soft=(p.get('softDeletePolicy') or {}).get('retentionDurationSeconds','0')
            if type(soft) not in (str,int) or not str(soft).isdigit():raise StateUnavailable('unknown soft-delete policy')
            versioning=(p.get('versioning') or {}).get('enabled',False)
            if type(versioning) is not bool:raise StateUnavailable('unknown versioning policy')
            return dict(soft_delete_seconds=int(soft),versioning_enabled=versioning)
        except StateUnavailable:raise
        except Exception as exc:raise StateUnavailable('bucket policy unavailable') from exc

    def _list(self, bound, limit, *, soft=False):
        # Consume ONE page. Even an empty/short page can have a continuation.
        # Restart from the beginning on each bounded call: deleting objects must
        # not skip entries by reusing a cursor from a changing list.
        try:
            opts={'soft_deleted':True} if soft else {'versions':True}
            it=self.bucket.list_blobs(prefix=bound['name'],max_results=limit+1,page_size=limit+1,
                timeout=10,retry=None,**opts)
            page=next(iter(it.pages),None);items=[]
            if page is not None:
                for b in page:
                    if b.name!=bound['name']:raise StateUnavailable('unexpected object in exact-slot listing')
                    props=b._properties
                    crc=getattr(b,'crc32c',None)
                    if (type(b.generation) is not int or b.generation<=0 or type(b.size) is not int
                            or not 0<b.size<=MAX_STATE or type(crc) is not str
                            or len(base64.b64decode(crc,validate=True))!=4
                            or props.get('temporaryHold') or props.get('eventBasedHold') or props.get('retention')):
                        raise StateUnavailable('object metadata/hold requires review')
                    items.append(dict(generation=b.generation,bytes=b.size,crc32c=crc))
                    if len(items)>limit+1:raise StateUnavailable('provider exceeded requested page bound')
            gs=[x['generation'] for x in items]
            if gs!=sorted(set(gs)):raise StateUnavailable('version listing is not strictly ordered')
            if any(g>bound['seal_generation'] for g in gs):raise StateUnavailable('unexpected later result generation')
            if soft and bound['seal_generation'] in gs:raise StateUnavailable('current seal is soft-deleted')
            return dict(items=items,more=bool(it.next_page_token))
        except StateUnavailable:raise
        except BadRequest:
            if soft:return dict(items=[],more=False,unavailable=True)
            raise StateUnavailable('active history listing unavailable') from None
        except Exception as exc:raise StateUnavailable('history listing unavailable') from exc

    def _unchanged(self,person,operation,bound):
        if self._bound(person,operation)!=bound:raise StateUnavailable('current seal changed; stop history disposition')

    def _outcome(self,bound,item):
        intent=dict(v=1,bound=bound,item=item);sid=digest(intent)
        row=self.audit.load(key('service-history-outcome',sid))
        if row is None:return None
        original=self.audit.load(key('service-history-intent',sid))
        expected=dict(v=1,intent_sha256=sid,active_generation_absent=True,
                      soft_deleted_or_backup_erasure_confirmed=False)
        if original is None or original[1]!=intent or row[1]!=expected:
            raise StateUnavailable('history outcome missing or contradicting original evidence')
        return row[1]

    def _job(self,bound):
        row=self.jobs.load(key('service-history-job',digest(bound)))
        if row is None:return None
        j=row[1]
        if (type(j) is not dict or set(j)!={'v','bound','sequence','items','complete'}
                or type(j['v']) is not int or j['v']!=1 or j['bound']!=bound
                or type(j['sequence']) is not int or not 1<=j['sequence']<=1000000
                or type(j['items']) is not list or not 1<=len(j['items'])<=50 or type(j['complete']) is not bool):
            raise StateUnavailable('invalid history checkpoint; never reset')
        generations=[]
        for item in j['items']:
            if (type(item) is not dict or set(item)!={'generation','bytes','crc32c'}
                    or type(item['generation']) is not int or not 0<item['generation']<bound['seal_generation']
                    or type(item['bytes']) is not int or not 0<item['bytes']<=MAX_STATE
                    or type(item['crc32c']) is not str):raise StateUnavailable('invalid history item')
            try:
                if len(base64.b64decode(item['crc32c'],validate=True))!=4:raise ValueError()
            except Exception as exc:raise StateUnavailable('invalid history checksum') from exc
            generations.append(item['generation'])
        if generations!=sorted(set(generations)):raise StateUnavailable('invalid history item order')
        original=dict(j,complete=False)
        proof=self.audit.load(key('service-history-batch',digest(original)))
        if proof is None or proof[1]!=original:raise StateUnavailable('missing original history batch')
        if j['complete'] and any(self._outcome(bound,x) is None for x in j['items']):
            raise StateUnavailable('completed history batch lacks outcome')
        return row

    def _prepare(self,bound,candidates):
        # Persist the exact bounded list BEFORE any deletion. A lost outcome
        # write must be repaired from this list even when GCS no longer lists
        # the deleted generation. Concurrent workers join the same batch.
        for _ in range(CAS_ATTEMPTS):
            row=self._job(bound)
            if row and not row[1]['complete']:return row
            if not candidates:return row
            j=dict(v=1,bound=bound,sequence=1 if row is None else row[1]['sequence']+1,
                   items=candidates,complete=False)
            if j['sequence']>1000000:raise StateUnavailable('history batch count requires review')
            self.immutable(self.audit,key('service-history-batch',digest(j)),j)
            try:
                if self.jobs.compare_exchange(key('service-history-job',digest(bound)),0 if row is None else row[0],j):
                    return self._job(bound)
            except StateUnavailable:
                saved=self._job(bound)
                if saved and saved[1]==j:return saved
                raise
        raise StateUnavailable('history batch conflict; resume original operation')

    def _finish(self,bound,batch):
        for _ in range(CAS_ATTEMPTS):
            row=self._job(bound)
            if row is None:raise StateUnavailable('history batch vanished')
            if row[1]['sequence']>batch['sequence'] or row[1]['complete']:return
            if row[1]!=batch:raise StateUnavailable('history batch changed unexpectedly')
            if any(self._outcome(bound,x) is None for x in batch['items']):return
            try:
                if self.jobs.compare_exchange(key('service-history-job',digest(bound)),row[0],dict(batch,complete=True)):return
            except StateUnavailable:
                saved=self._job(bound)
                if saved and (saved[1]['complete'] or saved[1]['sequence']>batch['sequence']):return
                raise
        raise StateUnavailable('history completion conflict; resume original batch')

    def _delete(self,person,operation,bound,item):
        if item['generation']>=bound['seal_generation']:raise StateUnavailable('never delete the current seal')
        intent=dict(v=1,bound=bound,item=item);sid=digest(intent)
        ik=key('service-history-intent',sid);rk=key('service-history-outcome',sid)
        if self._outcome(bound,item) is not None:return
        self.immutable(self.audit,ik,intent)
        self._unchanged(person,operation,bound)
        blob=self.bucket.blob(bound['name'],generation=item['generation'])
        try:
            blob.delete(if_generation_match=item['generation'],timeout=10,retry=None)
        except NotFound:pass
        except Exception:
            # Outcome unknown: read the SAME immutable generation; never repeat
            # a delete against the latest object or infer physical erasure.
            pass
        try:
            blob.reload(if_generation_match=item['generation'],timeout=10,retry=None)
        except NotFound:
            outcome=dict(v=1,intent_sha256=sid,active_generation_absent=True,
                         soft_deleted_or_backup_erasure_confirmed=False)
            self.immutable(self.audit,rk,outcome)
            return
        except Exception as exc:raise StateUnavailable('history deletion outcome unconfirmed') from exc
        raise StateUnavailable('historical generation remains; resume original item')

    def advance(self,person,operation,*,limit=20,apply=False):
        if type(limit) is not int or not 1<=limit<=50 or type(apply) is not bool:
            raise ValueError('bounded exact-slot history request required')
        bound=self._bound(person,operation);policy=self._bucket_policy()
        before=self._list(bound,limit);soft=self._list(bound,limit,soft=True)
        job=self._job(bound)
        candidates=[x for x in before['items'] if x['generation']<bound['seal_generation']][:limit]
        if apply:
            # Record the approved namespace and immutable seal before deletes.
            self.immutable(self.audit,key('service-history-plan',digest(bound)),bound)
            job=self._prepare(bound,candidates)
            if job and not job[1]['complete']:
                candidates=[x for x in job[1]['items'] if self._outcome(bound,x) is None][:limit]
                for item in candidates:self._delete(person,operation,bound,item)
                self._finish(bound,job[1])
            after=self._list(bound,limit);soft=self._list(bound,limit,soft=True)
        else:after=before
        self._unchanged(person,operation,bound)
        old=[x for x in after['items'] if x['generation']<bound['seal_generation']]
        absent=not old and not after['more']
        # A list omission is not proof: require the known live seal in the
        # completed listing as well as the separate generation-pinned read.
        if absent and not any(x['generation']==bound['seal_generation'] for x in after['items']):
            raise StateUnavailable('complete history listing omitted current seal')
        job=self._job(bound);audit_pending=bool(job and not job[1]['complete'])
        status='active_history_pending' if not absent else 'audit_batch_pending' if audit_pending else 'soft_history_unverified' if soft.get('unavailable') else \
            'soft_history_pending' if soft['items'] or soft['more'] else 'listed_history_absent_at_check'
        result=dict(v=1,operation=operation,plan_sha256=digest(bound),apply=apply,policy=policy,
                    status=status,active_history_absent_at_check=absent,
                    versions_selected=len(candidates),soft_inventory_available=not soft.get('unavailable',False),
                    audit_batch_pending=audit_pending,
                    soft_versions_observed=len(soft['items']),soft_more=soft['more'],
                    current_seal_preserved=True,physical_backups_erased=False,account_deleted=False)
        if apply:
            # Identical idle observations deduplicate; no timestamp creates
            # endless audit growth. This is an observation, not a forever claim.
            self.immutable(self.audit,key('service-history-observation',digest(result)),result)
        return result

    def page(self,person,*,cursor=None,slots=5,versions=20,apply=False):
        if type(slots) is not int or not 1<=slots<=5:raise ValueError('bounded account history page required')
        page=self.d.life.services.page(person,cursor=cursor,limit=slots)
        results=[self.advance(person,n['operation'],limit=versions,apply=apply) for n in page['items']]
        # Callers must revisit pending operations even after paging membership.
        # A finished catalog page is never full account disposition completion.
        return dict(v=1,inventory_sha256=page['inventory_sha256'],next_cursor=page['next_cursor'],
            results=results,pending_operations=[r['operation'] for r in results if r['status']!='listed_history_absent_at_check'],
            account_deleted=False,physical_backups_erased=False)
