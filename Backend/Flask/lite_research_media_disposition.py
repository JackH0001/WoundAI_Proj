"""Private, explicit bounded cleanup of verified closed-account research epochs.

Only current payloads are sealed. Permanent markers, consent events, original
audit, historical generations and backups remain. Not an HTTP handler, startup
activation or full-account deletion claim. No original payload enters audit.
"""
import hashlib
import re
from dataclasses import asdict
from lite_account_research_closure import AccountResearchClosure
from lite_disposition_progress import ResearchDispositionLane
from lite_attest_state import StateUnavailable
from lite_fenced_objects import ObjectRef
from lite_reconsent import encoded, epoch
from lite_store_locations import location, overlaps
from lite_subscription_executor import key
from lite_subscription_runtime import SubscriptionRuntime
from lite_usage_ledger import digest


def hex64(x):return type(x) is str and re.fullmatch(r'[0-9a-f]{64}',x) is not None


class ResearchMediaDisposition(ResearchDispositionLane):
    immutable=staticmethod(SubscriptionRuntime.immutable)

    def __init__(self, *, closure, store, audit_store):
        if not isinstance(closure,AccountResearchClosure):raise ValueError('verified account research closure required')
        self.c,self.r,self.d=closure,closure.r,closure.d
        peers=(closure.store,closure.audit,self.r.store,self.d.store,self.d.audit,
            self.d.lifecycle.store,self.d.lifecycle.audit,self.d.bridge.store,self.d.bridge.audit,
            self.d.bridge.registry.store,self.d.grants.store,self.d.grants.audit)
        if overlaps(location(store),location(audit_store)) or any(overlaps(location(s),location(p))
                for s in (store,audit_store) for p in peers):raise ValueError('separate media job and audit required')
        self.store,self.audit=store,audit_store
        self.config=dict(v=1,scope=self.d.scope,bucket=self.r.privacy.objects.bucket.name,
            action='seal_current_research_epochs',history_erasure=False)
        self.immutable(store,key('research-media-config',self.d.scope),self.config)

    def _read(self,kind,p):
        row=self.store.load(key('research-media-'+kind,digest(p)))
        if row is None:return None
        j=row[1]
        if type(j) is not dict or j.get('plan')!=p or type(j.get('step')) is not int or not 0<=j['step']<=1000000:
            raise StateUnavailable('research disposition checkpoint corrupt')
        proof=self.audit.load(key('research-media-checkpoint',digest(j)))
        intent=self.audit.load(key('research-media-intent',digest(p)))
        if proof is None or proof[1]!=j or intent is None or intent[1]!=p:
            raise StateUnavailable('original disposition evidence missing; never recreate')
        return row

    def _save(self,kind,p,old,j):
        if j['step']>1000000:raise StateUnavailable('research batch count requires review')
        self.immutable(self.audit,key('research-media-checkpoint',digest(j)),j)
        try:self.store.compare_exchange(key('research-media-'+kind,digest(p)),0 if old is None else old[0],j)
        except StateUnavailable:pass
        saved=self._read(kind,p)
        if saved is None or saved[1]['step']<j['step'] or (saved[1]['step']==j['step'] and saved[1]!=j):
            raise StateUnavailable('research disposition checkpoint unconfirmed')
        return saved

    def _account(self,p):
        row=self._read('account',p)
        if row is None:return None
        j=row[1]
        if (set(j)!={'plan','step','cursor','remaining','linked','unresolved'}
                or any(type(j[k]) is not int or j[k]<0 for k in ('remaining','linked','unresolved'))
                or sum(j[k] for k in ('remaining','linked','unresolved'))!=p['closure']['total']
                or (j['cursor'] is None)!=(j['remaining']==0)):
            raise StateUnavailable('invalid account media cursor')
        if j['cursor'] is not None and self.d.lifecycle._node(p['closure']['person'],j['cursor'])['position']!=j['remaining']:
            raise StateUnavailable('account media grant position differs')
        return row

    def _bound(self,p,owner):
        generation,state,_,retired=self.r._state(owner)
        if retired is None or retired['closure_sha256']!=digest(p['closure']):
            raise StateUnavailable('installation is not retired by this closure')
        return dict(v=1,config=self.config,account_plan_sha256=digest(p),owner=owner,
            retirement_generation=generation,retirement_sha256=state['retirement_sha256'],
            initial_epoch=state['epoch'],initial_audit_sha256=state['audit_sha256'])

    def _owner(self,b):
        row=self._read('owner',b)
        if row is None:return None
        j=row[1]
        if (set(j)!={'plan','step','epoch','audit_sha256','phase','token','after','epochs','objects','plans_sha256'}
                or j['phase'] not in ('plans','objects','done')
                or any(type(j[k]) is not int or j[k]<0 for k in ('epochs','objects'))
                or j['epochs']>1000000 or (j['audit_sha256'] is None)!=(j['epoch']=='legacy')
                or (j['audit_sha256'] is not None and not hex64(j['audit_sha256']))
                or (j['plans_sha256'] is not None and not hex64(j['plans_sha256']))
                or (j['phase']!='plans' and j['plans_sha256'] is None)
                or (j['token'] is not None and (type(j['token']) is not str or not 1<=len(j['token'])<=8192))
                or (j['phase']!='objects' and (j['token'] is not None or j['after'] is not None))):
            raise StateUnavailable('invalid research epoch cursor')
        try:epoch(j['epoch'])
        except ValueError:raise StateUnavailable('invalid saved research epoch') from None
        if j['after'] is not None:
            prefix=self.r.privacy.scoped(j['epoch']).objects.name(ObjectRef(b['owner'],'image','0'*32)).rsplit('/',2)[0]+'/'
            if type(j['after']) is not str or not j['after'].startswith(prefix):raise StateUnavailable('invalid media name cursor')
        if j['plans_sha256'] is not None:self._plans(b,j)
        return row

    def _plans(self,b,j):
        row=self.audit.load(key('research-media-frozen-plans',j['plans_sha256']))
        value=None if row is None else row[1]
        if (type(value) is not dict or set(value)!={'bound_sha256','epoch','generation','manifest'}
                or digest(value)!=j['plans_sha256'] or value['bound_sha256']!=digest(b)
                or value['epoch']!=j['epoch'] or type(value['generation']) is not int or value['generation']<=0
                or type(value['manifest']) is not dict
                or value['manifest'].get('withdrawn') is not True or value['manifest'].get('owner')!=b['owner']):
            raise StateUnavailable('original pending write inventory missing')
        return value['manifest']

    def _event(self,b,j):
        if j['epoch']=='legacy':return None
        row=self.r.store.load(self.r.key(b['owner'],j['epoch']))
        e=None if row is None else row[1]
        if (type(e) is not dict or hashlib.sha256(encoded(e)).hexdigest()!=j['audit_sha256']
                or type(e.get('v')) is not int or e['v']!=1 or e.get('action')!='reconsent_future_only'
                or e.get('owner')!=b['owner'] or e.get('new_epoch')!=j['epoch']):
            raise StateUnavailable('original consent chain evidence differs')
        try:previous=epoch(e.get('previous_epoch'))
        except ValueError:raise StateUnavailable('invalid prior consent epoch') from None
        if (previous==j['epoch'] or (e.get('previous_audit_sha256') is None)!=(previous=='legacy')
                or (previous!='legacy' and not hex64(e.get('previous_audit_sha256')))):
            raise StateUnavailable('invalid prior consent audit reference')
        return e

    def _seal(self,b,current,ref):
        objects=self.r.privacy.scoped(current).objects
        identity=dict(v=1,bound_sha256=digest(b),epoch=current,kind=ref.kind,identifier=ref.identifier)
        sid=digest(identity);ik=key('research-media-object-intent',sid);ok=key('research-media-object-outcome',sid)
        intent=self.audit.load(ik);out=self.audit.load(ok)
        if intent is None:
            if out is not None:raise StateUnavailable('seal outcome missing original intent')
            snapshot=objects._snapshot(ref)
            if snapshot is None:raise StateUnavailable('listed research object vanished')
            value=dict(v=1,identity=identity,prior=asdict(snapshot))
            self.immutable(self.audit,ik,value);intent=self.audit.load(ik)
        if (intent is None or type(intent[1]) is not dict or set(intent[1])!={'v','identity','prior'}
                or intent[1]['v']!=1 or intent[1]['identity']!=identity):
            raise StateUnavailable('research object intent differs')
        prior=intent[1]['prior']
        if (type(prior) is not dict or set(prior)!={'generation','size','state','sha256'}
                or type(prior['generation']) is not int or prior['generation']<=0
                or type(prior['size']) is not int or not 0<=prior['size']<=24*1024*1024
                or prior['state'] not in ('sealed','live') or not hex64(prior['sha256'])):
            raise StateUnavailable('invalid original object observation')
        if out is not None:
            if (type(out[1]) is not dict or set(out[1])!={'v','intent_sha256','seal_generation','current_payload_absent','history_erased'}
                    or out[1]['v']!=1 or out[1]['intent_sha256']!=digest(intent[1])
                    or out[1]['current_payload_absent'] is not True or out[1]['history_erased'] is not False
                    or type(out[1]['seal_generation']) is not int or out[1]['seal_generation']<prior['generation']):
                raise StateUnavailable('invalid original seal outcome')
            s=objects._snapshot(ref)
            if s is None or s.generation!=out[1]['seal_generation'] or s.state!='sealed' or objects._read(ref,s)!=b'':
                raise StateUnavailable('previously confirmed seal changed')
            return
        generation=objects.seal(ref)
        if generation<prior['generation']:raise StateUnavailable('research object generation regressed')
        self.immutable(self.audit,ok,dict(v=1,intent_sha256=digest(intent[1]),seal_generation=generation,
            current_payload_absent=True,history_erased=False))

    def _advance_owner(self,p,owner,limit):
        b=self._bound(p,owner);row=self._owner(b)
        if row is None:
            self.immutable(self.audit,key('research-media-intent',digest(b)),b)
            row=self._save('owner',b,None,dict(plan=b,step=0,epoch=b['initial_epoch'],audit_sha256=b['initial_audit_sha256'],
                phase='plans',token=None,after=None,epochs=0,objects=0,plans_sha256=None))
        j=row[1];self._owner(b)
        if j['phase']=='done':return j
        e=self._event(b,j);privacy=self.r.privacy.scoped(j['epoch'])
        visit=dict(bound_sha256=digest(b),epoch=j['epoch'],ordinal=j['epochs'],audit_sha256=j['audit_sha256'])
        self.immutable(self.audit,key('research-media-epoch',digest([b,j['epoch']])),visit)
        if j['phase']=='plans':
            privacy.manifest.withdraw(owner)
            generation,fence=privacy.manifest._load(owner)
            if j['plans_sha256'] is None:
                frozen=dict(bound_sha256=digest(b),epoch=j['epoch'],generation=generation,manifest=fence)
                h=digest(frozen);self.immutable(self.audit,key('research-media-frozen-plans',h),frozen)
                return self._save('owner',b,row,dict(j,step=j['step']+1,plans_sha256=h))[1]
            frozen=self._plans(b,j)
            for ticket,plans in fence['writers'].items():
                original=frozen['writers'].get(ticket,{})
                if any(name not in original or original[name][0]!=pin[0] for name,pin in plans.items()):
                    raise StateUnavailable('new plans appeared after frozen withdrawal')
            batch=privacy.manifest.seal_pending_batch(owner,privacy.objects,limit=limit)
            if self._bound(p,owner)!=b:raise StateUnavailable('retirement changed during plan sealing')
            if batch['remaining_writers']:return j
            return self._save('owner',b,row,dict(j,step=j['step']+1,phase='objects'))[1]
        if not privacy.is_withdrawn(owner) or privacy.manifest._load(owner)[1]['writers']:
            raise StateUnavailable('admission or pending plans changed during paging')
        page_intent=dict(v=1,bound_sha256=digest(b),epoch=j['epoch'],step=j['step'],token=j['token'],after=j['after'])
        self.immutable(self.audit,key('research-media-page',digest([b,j['epoch'],j['token']])),page_intent)
        refs,following,last=privacy.objects.page_refs(owner,token=j['token'],after=j['after'],limit=limit)
        for ref in refs:
            if self._bound(p,owner)!=b:raise StateUnavailable('retirement changed before media disposition')
            self._seal(b,j['epoch'],ref)
        if self._bound(p,owner)!=b:raise StateUnavailable('retirement changed during media disposition')
        if following is not None:
            value=dict(j,step=j['step']+1,token=following,after=last,objects=j['objects']+len(refs))
        elif e is None:
            value=dict(j,step=j['step']+1,phase='done',token=None,after=None,
                objects=j['objects']+len(refs),epochs=j['epochs']+1)
        else:
            value=dict(j,step=j['step']+1,phase='plans',token=None,after=None,
                epoch=e['previous_epoch'],audit_sha256=e['previous_audit_sha256'],
                objects=j['objects']+len(refs),epochs=j['epochs']+1,plans_sha256=None)
        saved=self._save('owner',b,row,value);self._owner(b)
        return saved[1]

    @staticmethod
    def _public(j):
        return dict(v=1,plan_sha256=digest(j['plan']),total_grants=j['plan']['closure']['total'],
            examined_grants=j['linked']+j['unresolved'],linked_grants=j['linked'],unresolved_grants=j['unresolved'],
            scan_finished=j['remaining']==0,live_media='linked_epochs_sealed' if j['remaining']==0 and j['unresolved']==0 else 'pending',
            historical_versions_erased=False,physical_backups_erased=False,consent_and_audit_preserved=True,
            writers_drained=False,account_deleted=False)

    def advance(self,person,*,limit=20,apply=False):
        """Five admission grants OR one media grant/plan batch/page per call.

        Explicit private worker only. A saved account deletion and verified
        registration association authorize this namespace; no prefix guessing.
        """
        if apply is not True:raise ValueError('explicit current-media disposition required')
        if type(limit) is not int or not 1<=limit<=20:raise ValueError('one to twenty objects required')
        p=dict(v=1,config=self.config,closure=self.c._plan(person));row=self._account(p)
        if row is None:
            self.immutable(self.audit,key('research-media-intent',digest(p)),p)
            row=self._save('account',p,None,dict(plan=p,step=0,cursor=p['closure']['head'],
                remaining=p['closure']['total'],linked=0,unresolved=0))
        j=row[1]
        if j['remaining']==0:return self._public(j)
        closed=self.c._read(p['closure'])
        if closed is None or closed[1]['remaining']:
            # Close every verified installation before spending calls on large
            # media inventories. Do not let another device keep adding work.
            self.c.advance(person,limit=5)
            return self._public(j)
        n=self.d.lifecycle._node(person,j['cursor'])
        if n['position']!=j['remaining']:raise StateUnavailable('frozen grant order changed')
        outcome=self.c._node(p['closure'],n)
        linked=outcome['state']=='linked_admission_closed'
        if linked and self._advance_owner(p,n['installation'],limit)['phase']!='done':return self._public(j)
        value=dict(j,step=j['step']+1,cursor=n['previous'],remaining=j['remaining']-1,
            linked=j['linked']+int(linked),unresolved=j['unresolved']+int(not linked))
        saved=self._save('account',p,row,value);self._account(p)
        return self._public(saved[1])

    def status(self,person):
        p=dict(v=1,config=self.config,closure=self.c._plan(person));row=self._account(p)
        if row is None:raise StateUnavailable('research media disposition not started')
        return self._public(row[1])
