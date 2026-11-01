"""Explicit, bounded account-to-research admission closure; never payload erasure.

Only frozen verified installation associations may retire research participation.
No route, scheduler, discovery or default startup. All original media, consent
events and audit remain. Ordinary research withdrawal/reconsent stays separate.
"""
import re
from lite_account_deletion import AccountDeletion
from lite_account_identity import IdentityRejected
from lite_attest_state import StateUnavailable
from lite_research_association import ResearchAssociations, ResearchLinkPending
from lite_reconsent import Reconsent
from lite_store_locations import location, overlaps
from lite_subscription_executor import key
from lite_subscription_runtime import SubscriptionRuntime
from lite_usage_ledger import digest


class AccountResearchClosure:
    immutable=staticmethod(SubscriptionRuntime.immutable)

    def __init__(self, *, deletion, research, store, audit_store, associations=None):
        if not isinstance(deletion,AccountDeletion) or not isinstance(research,Reconsent):
            raise ValueError('actual closed-account and independent research authorities required')
        if associations is not None and not isinstance(associations,ResearchAssociations):
            raise ValueError('verified cross-service research associations required')
        self.associations=associations
        b=deletion.bridge
        peers=(deletion.store,deletion.audit,b.store,b.audit,b.lifecycle.store,b.lifecycle.audit,
            b.registry.store,b.grants.store,b.grants.audit,research.store)
        if any(overlaps(location(research.store),location(p)) for p in peers[:-1]):
            raise ValueError('research and account scopes must remain separate')
        if overlaps(location(store),location(audit_store)) or any(
                overlaps(location(s),location(p)) for s in (store,audit_store) for p in peers):
            raise ValueError('separate research closure checkpoint and audit required')
        self.d,self.r,self.store,self.audit=deletion,research,store,audit_store
        self.config=dict(v=2,scope=deletion.scope,research_bucket=research.privacy.objects.bucket.name,
            action='retire_linked_research_admission',media_disposition='not_started')
        self.immutable(store,key('account-research-config-v2',deletion.scope),self.config)

    def inventory_plan(self,person):
        # Read original closure evidence; do not repair a missing consent audit.
        life=self.d.lifecycle;row=life._read(person)
        if row is None or row[1]['phase']!='closing':raise IdentityRejected('saved account deletion required')
        c=row[1];d=c['closure']
        expected=dict(v=1,state='account_closing',**d,grant_count=c['count'],
            data_disposition='not_started',apple_revocation='not_completed',account_deleted=False)
        proof=life.audit.load(key('account-closure-intent',digest([life.scope,person])))
        if proof is None or proof[1]!=expected:raise StateUnavailable('original account closure evidence missing')
        return dict(v=1,config=self.config,person=person,closure=d,head=c['head'],total=c['count'])

    def _plan(self, person):
        plan = self.inventory_plan(person)
        request = plan['closure']
        if not self.d.research_confirmation(person, request['requester'], request['operation']):
            raise StateUnavailable('explicit linked research withdrawal not confirmed')
        return plan

    def _key(self,p):return key('account-research-job',digest(p))

    def _read(self,p):
        row=self.store.load(self._key(p))
        if row is None:return None
        j=row[1]
        if (type(j) is not dict or set(j)!={'v','plan','cursor','remaining','linked','unresolved'}
                or type(j['v']) is not int or j['v']!=1 or j['plan']!=p
                or any(type(j[n]) is not int for n in ('remaining','linked','unresolved'))
                or not 0<=j['remaining']<=p['total'] or min(j['linked'],j['unresolved'])<0
                or j['remaining']+j['linked']+j['unresolved']!=p['total']
                or (j['cursor'] is None)!=(j['remaining']==0)):
            raise StateUnavailable('research closure checkpoint corrupt; never reset')
        if j['cursor'] is not None:
            n=self.d.lifecycle._node(p['person'],j['cursor'])
            if n['position']!=j['remaining']:raise StateUnavailable('research closure cursor mismatch')
        for kind,value in (('intent',p),('checkpoint',j)):
            proof=self.audit.load(key('account-research-'+kind,digest(value)))
            if proof is None or proof[1]!=value:raise StateUnavailable('original research closure audit missing')
        return row

    def _record(self,p,prior,j):
        self.immutable(self.audit,key('account-research-checkpoint',digest(j)),j)
        try:self.store.compare_exchange(self._key(p),0 if prior is None else prior[0],j)
        except StateUnavailable:pass
        saved=self._read(p)
        if saved is None:raise StateUnavailable('research closure checkpoint unconfirmed')
        # Another worker may be ahead. Never rewind or reset its cursor.
        if saved[1]['remaining']>j['remaining']:raise StateUnavailable('research closure needs resume')
        return saved

    def _node(self,p,n):
        life=self.d.lifecycle;owner=n['installation'];b=self.d.bridge
        if life.member(p['person'],n['grant_id'])!=n:
            raise StateUnavailable('frozen grant membership evidence missing')
        intent=dict(v=1,plan_sha256=digest(p),node_sha256=digest(n))
        self.immutable(self.audit,key('account-research-node-intent',digest(intent)),intent)
        research_owner=None
        proof=None
        if re.fullmatch(r'[0-9a-f]{32}',owner) is None:
            state='unresolved_installation_format';retirement=None
        elif b.registry.store.load(b.registry.key(owner)) is None:
            state='unresolved_registration';retirement=None
        else:
            account=b.registry.registration(owner)
            human=b.store.load(key('human-account',p['person']))
            if (account['subject']!='apple-'+p['person'] or human is None
                    or human[1].get('identity_sha256')!=p['person']
                    or human[1].get('subject')!=account['subject'] or human[1].get('token')!=account['token']):
                raise StateUnavailable('research installation association differs')
            # Stable per-installation reference, not a login-specific grant: a
            # second historic login joins the same retirement, never another one.
            if self.associations is None:
                raise ResearchLinkPending('verified link between personal service and research is pending')
            research_owner,proof=self.associations.resolve(self.d.scope,self.r.privacy.objects.bucket.name,
                p['person'],owner,p['closure']['operation'],account['proof'])
            self.immutable(self.audit,key('account-research-verified-pair',digest([p,owner])),
                dict(research_owner=research_owner,association_sha256=proof))
            retirement=self.r.retire(research_owner,closure_sha256=digest(p),association_sha256=proof)
            current=self.r.load(research_owner)[1]
            fence=self.r.privacy.scoped(current).manifest
            fence.withdraw(research_owner)
            if not fence.is_withdrawn(research_owner):raise StateUnavailable('research fence unconfirmed')
            state='linked_admission_closed'
        outcome=dict(v=2,intent_sha256=digest(intent),state=state,research_owner=research_owner,association_sha256=proof,
            retirement_sha256=None if retirement is None else digest(retirement),
            media_disposition='not_started',account_deleted=False)
        self.immutable(self.audit,key('account-research-node-outcome',digest(intent)),outcome)
        return outcome

    @staticmethod
    def _public(j):
        p=j['plan']
        return dict(v=1,closure_sha256=digest(p),total_grants=p['total'],
            examined_grants=p['total']-j['remaining'],linked_grants=j['linked'],
            unresolved_grants=j['unresolved'],scan_finished=j['remaining']==0,
            research_admission=('no_verified_research_installations' if not p['total'] else
                ('linked_installations_closed' if j['remaining']==0 and j['unresolved']==0 else 'pending')),
            media_disposition='not_started',historical_epochs_verified=False,
            original_media_preserved=True,account_deleted=False)

    def advance(self,person,*,limit=5):
        """Internal only; at most five frozen grants, independent of total size.

        A saved deletion request authorizes stopping linked admission, not
        deleting unlinked anonymous data or asserting completed erasure.
        """
        if type(limit) is not int or not 1<=limit<=5:raise ValueError('one to five grants required')
        p=self._plan(person);row=self._read(p)
        if row is None:
            self.immutable(self.audit,key('account-research-intent',digest(p)),p)
            row=self._record(p,None,dict(v=1,plan=p,cursor=p['head'],remaining=p['total'],linked=0,unresolved=0))
        for _ in range(limit):
            j=row[1]
            if j['remaining']==0:break
            n=self.d.lifecycle._node(person,j['cursor'])
            if n['position']!=j['remaining']:raise StateUnavailable('research grant sequence differs')
            try:
                outcome=self._node(p,n)
            except ResearchLinkPending:
                self.immutable(self.audit,key('account-research-awaiting-link',digest([p,n['grant_id']])),
                    dict(closure_sha256=digest(p),grant=n['grant_id'],reason='verified_research_link_required'))
                return dict(self._public(j),waiting_for='verified_research_link_required')
            linked=outcome['state']=='linked_admission_closed'
            next_j=dict(j,cursor=n['previous'],remaining=j['remaining']-1,
                linked=j['linked']+int(linked),unresolved=j['unresolved']+int(not linked))
            row=self._record(p,row,next_j)
        return self._public(row[1])

    def status(self,person):
        p=self._plan(person);row=self._read(p)
        if row is None:raise StateUnavailable('research closure has not started')
        j=row[1];result=self._public(j)
        if j['cursor'] is not None:
            node=self.d.lifecycle._node(person,j['cursor'])
            waiting=self.audit.load(key('account-research-awaiting-link',digest([p,node['grant_id']])))
            if waiting is not None:
                expected=dict(closure_sha256=digest(p),grant=node['grant_id'],reason='verified_research_link_required')
                if waiting[1]!=expected:raise StateUnavailable('research link pause audit differs')
                result['waiting_for']=expected['reason']
        return result
