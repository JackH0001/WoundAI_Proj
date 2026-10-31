"""Private research history disposal, bound to original retired consent epochs.

Explicit apply only; no HTTP/startup/scheduler binding. Current zero-byte fences,
consent and audit are retained. Soft-deleted versions and backups stay pending.
"""
import hashlib
from lite_attest_state import StateUnavailable
from lite_generation_history import ExactGenerationHistory
from lite_research_media_disposition import ResearchMediaDisposition, hex64
from lite_fenced_objects import ObjectRef, MAX_BYTES
from lite_reconsent import epoch
from lite_store_locations import location, overlaps
from lite_subscription_executor import key
from lite_usage_ledger import digest


class ResearchMediaHistory(ExactGenerationHistory):
    namespace='research-history'
    min_bytes=0
    max_bytes=MAX_BYTES

    def __init__(self, *, media, store, audit_store, policy):
        if not isinstance(media,ResearchMediaDisposition):raise ValueError('verified research media worker required')
        self.w=media;self.bucket=media.r.privacy.objects.bucket;self.jobs=store;self.audit=audit_store
        expected=dict(v=1,id=policy.get('id') if type(policy) is dict else None,bucket=self.bucket.name,
                      prefixes=['lite_fenced/v1/','lite_epochs/v1/'])
        if (type(policy) is not dict or policy!=expected or type(policy.get('v')) is not int
                or type(policy.get('id')) is not str or not 1<=len(policy['id'])<=80
                or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789_-' for c in policy['id'])):
            raise ValueError('explicit research-only bucket policy required')
        d=media.d
        peers=(media.store,media.audit,media.c.store,media.c.audit,media.r.store,d.store,d.audit,
               d.lifecycle.store,d.lifecycle.audit,d.bridge.store,d.bridge.audit,d.bridge.registry.store,
               d.grants.store,d.grants.audit)
        if overlaps(location(store),location(audit_store)) or any(overlaps(location(s),location(p))
                for s in (store,audit_store) for p in peers):raise ValueError('independent history jobs and audit required')
        if any(overlaps(location(s),('gcs',self.bucket.name,p.rstrip('/')))
                for s in (store,audit_store) for p in expected['prefixes']):
            raise ValueError('history state cannot overlap original media namespaces')
        self.policy=dict(policy)

    def _plan(self,person):
        m=dict(v=1,config=self.w.config,closure=self.w.c._plan(person))
        row=self.w._account(m)
        if row is None or row[1]['remaining']:raise StateUnavailable('current media inventory must finish first')
        return dict(v=1,policy=self.policy,media=m)

    def _member(self,p,grant):
        if not hex64(grant):raise StateUnavailable('invalid frozen grant reference')
        c=p['media']['closure'];life=self.w.d.lifecycle;n=life._node(c['person'],grant)
        if n['position']>c['total'] or life.member(c['person'],n['grant_id'])!=n:
            raise StateUnavailable('grant outside frozen account inventory')
        intent=dict(v=1,plan_sha256=digest(c),node_sha256=digest(n))
        a=self.w.c.audit.load(key('account-research-node-intent',digest(intent)))
        b=self.w.c.audit.load(key('account-research-node-outcome',digest(intent)))
        out=None if b is None else b[1]
        if (a is None or a[1]!=intent or type(out) is not dict
                or set(out)!={'v','intent_sha256','state','retirement_sha256','media_disposition','account_deleted'}
                or type(out['v']) is not int or out['v']!=1 or out['intent_sha256']!=digest(intent)
                or out['media_disposition']!='not_started' or out['account_deleted'] is not False
                or out['state'] not in ('linked_admission_closed','unresolved_installation_format','unresolved_registration')):
            raise StateUnavailable('original research association proof required')
        if out['state']!='linked_admission_closed':
            if out['retirement_sha256'] is not None:raise StateUnavailable('unresolved retirement contradiction')
            return n,None
        bound=self.w._bound(p['media'],n['installation']);owner=self.w._owner(bound)
        retired=self.w.r._state(n['installation'])[3]
        if owner is None or owner[1]['phase']!='done' or digest(retired)!=out['retirement_sha256']:
            raise StateUnavailable('original completed media closure required')
        return n,bound

    def _scope(self,p,grant,current):
        epoch(current);n,b=self._member(p,grant)
        if b is None:raise StateUnavailable('unknown installation cannot authorize history')
        v=self.w.audit.load(key('research-media-epoch',digest([b,current])))
        visit=None if v is None else v[1];j=self.w._owner(b)[1]
        if (type(visit) is not dict or set(visit)!={'bound_sha256','epoch','ordinal','audit_sha256'}
                or visit['bound_sha256']!=digest(b) or visit['epoch']!=current
                or type(visit['ordinal']) is not int or not 0<=visit['ordinal']<j['epochs']
                or (visit['audit_sha256'] is None)!=(current=='legacy')
                or (visit['audit_sha256'] is not None and not hex64(visit['audit_sha256']))
                or (visit['ordinal']==0 and (current,visit['audit_sha256'])!=(b['initial_epoch'],b['initial_audit_sha256']))):
            raise StateUnavailable('original consent epoch traversal proof required')
        event=self.w._event(b,dict(epoch=current,audit_sha256=visit['audit_sha256']))
        privacy=self.w.r.privacy.scoped(current)
        if not privacy.is_withdrawn(n['installation']) or privacy.manifest._load(n['installation'])[1]['writers']:
            raise StateUnavailable('research admission or pending writers changed')
        return n,b,event

    def _bound(self,person,operation):
        if type(operation) is not dict or set(operation)!={'grant','epoch','kind','identifier'}:
            raise StateUnavailable('exact research slot required')
        p=self._plan(person);n,b,_=self._scope(p,operation['grant'],operation['epoch'])
        ref=ObjectRef(n['installation'],operation['kind'],operation['identifier'])
        objects=self.w.r.privacy.scoped(operation['epoch']).objects;name=objects.name(ref)
        identity=dict(v=1,bound_sha256=digest(b),epoch=operation['epoch'],kind=ref.kind,identifier=ref.identifier)
        a=self.w.audit.load(key('research-media-object-intent',digest(identity)))
        z=self.w.audit.load(key('research-media-object-outcome',digest(identity)))
        intent=None if a is None else a[1];out=None if z is None else z[1]
        if (type(intent) is not dict or set(intent)!={'v','identity','prior'} or type(intent['v']) is not int
                or intent['v']!=1 or intent['identity']!=identity):raise StateUnavailable('original object intent required')
        prior=intent['prior']
        if (type(prior) is not dict or set(prior)!={'generation','size','state','sha256'}
                or type(prior['generation']) is not int or prior['generation']<=0
                or type(prior['size']) is not int or not 0<=prior['size']<=MAX_BYTES
                or prior['state'] not in ('live','sealed') or not hex64(prior['sha256'])):
            raise StateUnavailable('original object observation invalid')
        if (type(out) is not dict or set(out)!={'v','intent_sha256','seal_generation','current_payload_absent','history_erased'}
                or type(out['v']) is not int or out['v']!=1 or out['intent_sha256']!=digest(intent)
                or out['current_payload_absent'] is not True or out['history_erased'] is not False
                or type(out['seal_generation']) is not int or out['seal_generation']<prior['generation']):
            raise StateUnavailable('original sealed generation outcome required')
        s=objects._snapshot(ref)
        if s is None or s.state!='sealed' or s.generation!=out['seal_generation'] or objects._read(ref,s)!=b'':
            raise StateUnavailable('current permanent fence changed')
        return dict(v=1,policy=self.policy,plan_sha256=digest(p),operation=dict(operation),name=name,
                    seal_generation=s.generation,seal_sha256=hashlib.sha256(b'').hexdigest())

    def _scan_read(self,p):
        row=self.jobs.load(key('research-history-scan',digest(p)))
        if row is None:return None
        j=row[1];fields={'v','plan','step','round','phase','grant','remaining','linked','unresolved','epoch',
                          'token','after','pending','objects','soft_pending','soft_unverified'}
        if (type(j) is not dict or set(j)!=fields or type(j['v']) is not int or j['v']!=1 or j['plan']!=p
                or any(type(j[k]) is not int or not 0<=j[k]<=1000000 for k in
                       ('step','round','remaining','linked','unresolved','objects','soft_pending','soft_unverified'))
                or j['round']<1 or j['remaining']+j['linked']+j['unresolved']!=p['media']['closure']['total']
                or j['phase'] not in ('grant','select','history','done')
                or (j['grant'] is None)!=(j['remaining']==0) or (j['phase']=='done')!=(j['remaining']==0)):
            raise StateUnavailable('history scan checkpoint invalid; never reset')
        if j['grant'] is not None:
            n,_=self._member(p,j['grant'])
            if n['position']!=j['remaining']:raise StateUnavailable('history scan grant order changed')
        if j['phase'] in ('select','history'):self._scope(p,j['grant'],j['epoch'])
        elif any(j[k] is not None for k in ('epoch','token','after','pending')):
            raise StateUnavailable('history scan phase differs')
        if j['token'] is not None and (type(j['token']) is not str or not 1<=len(j['token'])<=8192):
            raise StateUnavailable('invalid history scan token')
        if (j['pending'] is None)!=(j['phase']!='history'):raise StateUnavailable('history slot missing')
        if j['pending'] is not None:
            pending=j['pending']
            if (type(pending) is not dict or set(pending)!={'operation','following','last'}
                    or type(pending['operation']) is not dict
                    or pending['operation'].get('grant')!=j['grant'] or pending['operation'].get('epoch')!=j['epoch']):
                raise StateUnavailable('history slot scope differs')
            bound=self._bound(p['media']['closure']['person'],pending['operation'])
            if pending['last']!=bound['name']:raise StateUnavailable('history slot cursor differs')
            t=pending['following']
            if t is not None and (type(t) is not str or not 1<=len(t)<=8192 or t==j['token']):
                raise StateUnavailable('history slot continuation differs')
        for kind,value in (('scan-plan',p),('scan-checkpoint',j)):
            proof=self.audit.load(key('research-history-'+kind,digest(value)))
            if proof is None or proof[1]!=value:raise StateUnavailable('original history scan audit required')
        return row

    def _scan_save(self,p,old,j):
        if j['step']>1000000 or j['round']>1000000:raise StateUnavailable('history scan limit requires review')
        self.immutable(self.audit,key('research-history-scan-checkpoint',digest(j)),j)
        try:self.jobs.compare_exchange(key('research-history-scan',digest(p)),0 if old is None else old[0],j)
        except StateUnavailable:pass
        saved=self._scan_read(p)
        if saved is None or saved[1]['step']<j['step'] or (saved[1]['step']==j['step'] and saved[1]!=j):
            raise StateUnavailable('history scan checkpoint unconfirmed')
        return saved

    def _initial(self,p,*,step=0,round=1):
        c=p['media']['closure']
        return dict(v=1,plan=p,step=step,round=round,phase='grant' if c['total'] else 'done',
                    grant=c['head'],remaining=c['total'],linked=0,unresolved=0,epoch=None,token=None,
                    after=None,pending=None,objects=0,soft_pending=0,soft_unverified=0)

    @staticmethod
    def _receipt(j,apply):
        return dict(v=1,plan_sha256=digest(j['plan']),round=j['round'],apply=apply,scan_finished=j['phase']=='done',
                    linked_grants=j['linked'],unresolved_grants=j['unresolved'],objects_checked=j['objects'],
                    active_history_absent_in_verified_scope=j['phase']=='done',
                    soft_pending_slots=j['soft_pending'],soft_unverified_slots=j['soft_unverified'],
                    observed_scope_only=True,current_fences_preserved=True,consent_and_audit_preserved=True,
                    account_deleted=False,physical_backups_erased=False)

    def _end_epoch(self,p,j):
        n,_,event=self._scope(p,j['grant'],j['epoch'])
        if event is not None:return dict(j,phase='select',epoch=event['previous_epoch'],token=None,after=None,pending=None)
        return dict(j,phase='grant' if n['previous'] is not None else 'done',grant=n['previous'],
                    remaining=j['remaining']-1,linked=j['linked']+1,epoch=None,token=None,after=None,pending=None)

    def advance_account(self,person,*,versions=20,apply=False,rescan=False):
        """One selected object or catalog transition per call. No overall-erasure claim.

        A completed observation is retained; rescan=True explicitly starts a new
        round after provider retention/soft expiry. Never rewinds an active scan.
        Dry-run reads the checkpoint only and does not construct evidence.
        """
        if type(versions) is not int or not 1<=versions<=50 or type(apply) is not bool or type(rescan) is not bool:
            raise ValueError('bounded explicit history request required')
        p=self._plan(person);row=self._scan_read(p);j=self._initial(p) if row is None else row[1]
        if not apply:return self._receipt(j,False)
        self.immutable(self.audit,key('research-history-scan-plan',digest(p)),p)
        if row is None:return self._receipt(self._scan_save(p,None,j)[1],True)
        if j['phase']=='done':
            if rescan:return self._receipt(self._scan_save(p,row,self._initial(p,step=j['step']+1,round=j['round']+1))[1],True)
            return self._receipt(j,True)
        if rescan:raise StateUnavailable('active scan must finish before explicit rescan')
        value=dict(j,step=j['step']+1)
        if j['phase']=='grant':
            n,b=self._member(p,j['grant'])
            if b is None:
                value.update(phase='grant' if n['previous'] is not None else 'done',grant=n['previous'],
                             remaining=j['remaining']-1,unresolved=j['unresolved']+1)
            else:value.update(phase='select',epoch=b['initial_epoch'])
        elif j['phase']=='select':
            n,_,_=self._scope(p,j['grant'],j['epoch'])
            page=dict(plan_sha256=digest(p),round=j['round'],grant=j['grant'],epoch=j['epoch'],token=j['token'],step=j['step'])
            self.immutable(self.audit,key('research-history-page',digest([p,j['round'],j['grant'],j['epoch'],j['token']])),page)
            refs,following,last=self.w.r.privacy.scoped(j['epoch']).objects.page_refs(
                n['installation'],token=j['token'],after=j['after'],limit=1)
            if refs:
                ref=refs[0];op=dict(grant=j['grant'],epoch=j['epoch'],kind=ref.kind,identifier=ref.identifier)
                self._bound(person,op)
                value.update(phase='history',pending=dict(operation=op,following=following,last=last))
            elif following is not None:value.update(token=following,after=last)
            else:value=self._end_epoch(p,value)
        else:
            r=self.advance(person,j['pending']['operation'],limit=versions,apply=True)
            if r['active_history_absent_at_check'] and not r['audit_batch_pending']:
                value.update(objects=j['objects']+1,soft_pending=j['soft_pending']+int(r['soft_versions_observed']>0 or r['soft_more']),
                             soft_unverified=j['soft_unverified']+int(not r['soft_inventory_available']))
                if j['pending']['following'] is None:value=self._end_epoch(p,value)
                else:value.update(phase='select',token=j['pending']['following'],after=j['pending']['last'],pending=None)
        return self._receipt(self._scan_save(p,row,value)[1],True)
