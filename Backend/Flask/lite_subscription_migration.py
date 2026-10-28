"""Explicit, opt-in subscription policy migration. No startup/HTTP activation.

Trusted control supplies a complete legacy-policy inventory and independently
verified account authorization/writer-fence evidence. Hashes link to that
evidence; they do NOT authenticate a person or fence old deployed processes.
No ledger, research, image, deletion, scheduler or cloud client is reachable.
"""
from dataclasses import asdict
import copy
import hashlib
import time
from lite_attest_state import StateUnavailable
from lite_service_subscription_links import (ServiceSubscriptionLinks, SubscriptionLinkConflict,
    SubscriptionMigrationRequired, stable_subscription_key)
from lite_subscription_authority import ServiceBinding
from lite_usage_ledger import DailyGrant, digest, _hex, _integer


def scope(policy):
    return {k:v for k,v in asdict(policy).items() if k!='product'}


class StableSubscriptionLinks:
    """Stable account reader plus bounded, audited control-only transitions.

    The object is also ServiceAccounts' authority router. A supported previous
    product remains on its old authority until an explicit verified transition.
    Unknown policy, absent bootstrap or incomplete completion never means free.
    """
    def __init__(self, *, store, audit_store, authorities, active_policy, legacy_policies,
                 authorize, writer_fence, clock=time.time):
        if type(authorities) is not tuple or not 1<=len(authorities)<=8:
            raise ValueError('bounded explicit authority catalog required')
        self.catalog={digest(asdict(a.policy)):a for a in authorities}
        if len(self.catalog)!=len(authorities) or active_policy not in self.catalog:
            raise ValueError('unique known active policy required')
        self.policy=self.catalog[active_policy].policy;self.scope=scope(self.policy)
        for a in authorities:
            a.policy.validate(getattr(a,'allowed_products',None))
            if scope(a.policy)!=self.scope:raise ValueError('cross-app/environment migration forbidden')
            if a.proofs is not authorities[0].proofs:raise ValueError('one shared subscription ownership proof store required')
        if (type(legacy_policies) is not tuple or len(set(legacy_policies))!=len(legacy_policies)
                or any(p not in self.catalog for p in legacy_policies)
                or not callable(authorize) or not callable(writer_fence)):
            raise ValueError('explicit inventory and control verifiers required')
        self.legacy=legacy_policies;self.active=active_policy
        self.store,self.audit,self.clock=store,audit_store,clock
        self.authorize,self.writer_fence=authorize,writer_fence
        self.authority=self

    def now(self):
        n=self.clock()
        if type(n) not in (int,float) or not 0<=n<2**53:raise StateUnavailable('invalid migration clock')
        return int(n)

    def key(self,subject):return stable_subscription_key(self.policy,subject)

    @staticmethod
    def audit_key(kind,identity):
        _hex(identity)
        return hashlib.sha256(('lite/subscription-migration/1\0'+kind+'\0'+identity).encode()).digest()

    def immutable(self,key,value):
        old=self.audit.load(key)
        if old is None:
            self.audit.compare_exchange(key,0,value);old=self.audit.load(key)
        if old is None or digest(old[1])!=digest(value):raise StateUnavailable('migration audit not confirmed')

    @staticmethod
    def record(intent):
        p=intent['plan']
        return dict(v=1,scope=p['scope'],subject_hash=p['subject_hash'],token_hash=p['token_hash'],
                    original_transaction=p['original_transaction'],policy=p['target_policy'],
                    plan=digest(p),intent=digest(intent))

    @staticmethod
    def receipt(intent):
        return dict(v=1,kind='subscription-policy-transition-confirmed',plan=digest(intent['plan']),
                    intent=digest(intent),record_sha256=digest(StableSubscriptionLinks.record(intent)))

    def _validate_plan(self,p):
        try:
            keys={'v','kind','operation','scope','subject_hash','token_hash','registration_sha256',
                  'policies','legacy_sources','expected_revision','before','target_policy',
                  'original_transaction','inventory_sha256','writer_fence_sha256'}
            if (type(p) is not dict or set(p)!=keys or type(p['v']) is not int or p['v']!=1
                    or p['kind']!='subscription-policy-transition' or digest(p['scope'])!=digest(self.scope)
                    or type(p['expected_revision']) is not int or p['expected_revision']<0):raise ValueError()
            for k in ('operation','subject_hash','token_hash','registration_sha256','inventory_sha256','writer_fence_sha256'):_hex(p[k])
            if (type(p['policies']) is not list or not 1<=len(p['policies'])<=8
                    or any(type(x) is not str for x in p['policies'])
                    or p['policies']!=sorted(set(p['policies']))):raise ValueError()
            for x in p['policies']:_hex(x)
            if type(p['legacy_sources']) is not dict or len(p['legacy_sources'])>8:raise ValueError()
            for k,v in p['legacy_sources'].items():
                _hex(k)
                if k not in p['policies']:raise ValueError()
                if v is not None:_hex(v)
            if (p['before'] is None)!=(p['expected_revision']==0):raise ValueError()
            if p['before'] is not None:
                self._validate_record_shape(p['before'])
                if any(digest(p['before'][k])!=digest(p[k]) for k in ('scope','subject_hash','token_hash')):raise ValueError()
                if p['before']['original_transaction'] not in (None,p['original_transaction']):raise ValueError()
            if p['original_transaction'] is None:
                if p['target_policy'] is not None:raise ValueError()
            elif (type(p['original_transaction']) is not str or not p['original_transaction'].isascii()
                    or not p['original_transaction'].isdigit() or not 1<=len(p['original_transaction'])<=32
                    or p['target_policy'] not in p['policies']):raise ValueError()
        except (TypeError,ValueError,KeyError) as e:raise StateUnavailable('invalid migration plan') from e

    def _validate_record_shape(self,r):
        if (type(r) is not dict or set(r)!={'v','scope','subject_hash','token_hash','original_transaction','policy','plan','intent'}
                or type(r['v']) is not int or r['v']!=1 or digest(r['scope'])!=digest(self.scope)):
            raise StateUnavailable('invalid stable subscription record')
        for k in ('subject_hash','token_hash','plan','intent'):_hex(r[k])

    def _intent(self,identity):
        row=self.audit.load(self.audit_key('intent',identity))
        if row is None:return None
        v=row[1]
        try:
            if (type(v) is not dict or set(v)!={'v','kind','plan','authorization','apple_proof','verified_at','valid_until','observed_limit'}
                    or type(v['v']) is not int or v['v']!=1 or v['kind']!='subscription-policy-transition-intent'):
                raise ValueError()
            self._validate_plan(v['plan']);_hex(v['authorization'])
            if digest(v['plan'])!=identity:raise ValueError()
            if v['plan']['original_transaction'] is None:
                if any(v[k] is not None for k in ('apple_proof','verified_at','valid_until','observed_limit')):raise ValueError()
            else:
                _hex(v['apple_proof']);start=_integer(v['verified_at']);end=_integer(v['valid_until'])
                if (not 0<end-start<=60 or start>self.now()
                        or type(v['observed_limit']) is not int or v['observed_limit'] not in (2,10)):raise ValueError()
        except (TypeError,ValueError,KeyError) as e:raise StateUnavailable('invalid migration intent') from e
        return v

    def _complete(self,intent):
        result=self.receipt(intent)
        self.immutable(self.audit_key('complete',digest(intent['plan'])),result)
        return result

    def _read(self,subject,token,*,complete=True,require_policy=True):
        row=self.store.load(self.key(subject))
        if row is None:return None
        try:
            r=row[1];self._validate_record_shape(r)
            if r['subject_hash']!=digest(subject) or r['token_hash']!=digest(token):raise ValueError()
            intent=self._intent(r['plan'])
            if intent is None or digest(self.record(intent))!=digest(r):raise ValueError()
            if require_policy and r['policy'] is not None and r['policy'] not in self.catalog:
                raise SubscriptionMigrationRequired('unsupported subscription policy; retain stable reader')
            guard=dict(v=1,scope=self.scope,subject_hash=digest(subject),token_hash=digest(token))
            g=self.store.load(stable_subscription_key(self.policy,subject,'guard'))
            if g is None or digest(g[1])!=digest(guard):raise ValueError()
            if complete:
                done=self.audit.load(self.audit_key('complete',r['plan']))
                if done is None or digest(done[1])!=digest(self.receipt(intent)):raise ValueError()
        except (TypeError,ValueError,KeyError) as e:raise StateUnavailable('stable association incomplete or corrupt') from e
        return row

    def lookup(self,subject,token):
        ServiceBinding(subject,token,'0').validate()
        row=self._read(subject,token,require_policy=False)
        if row is None:raise SubscriptionMigrationRequired('account inventory/bootstrap required; no free fallback')
        return row[1]['original_transaction']

    def grant(self,binding):
        binding.validate();row=self._read(binding.subject,binding.app_account_token)
        if row is None or row[1]['original_transaction']!=binding.original_transaction:
            raise SubscriptionMigrationRequired('stable binding required before entitlement')
        return self.catalog[row[1]['policy']].grant(binding)

    def _sources(self,subject,token):
        sources={};originals=set()
        for p in self.legacy:
            reader=ServiceSubscriptionLinks(store=self.store,audit_store=self.audit,authority=self.catalog[p],clock=self.clock)
            link=reader._read(subject,token)  # bounded legacy read; intentionally not a runtime fallback
            if link is None:sources[p]=None;continue
            done=self.audit.load(reader.audit_key('complete',link['intent']))
            if done is None or digest(done[1])!=digest(reader.receipt(link)):
                raise StateUnavailable('legacy completion missing')
            sources[p]=digest(link);originals.add(link['original_transaction'])
        if len(originals)>1:raise SubscriptionLinkConflict('legacy policies disagree; manual reconciliation required')
        return sources,next(iter(originals),None)

    def plan(self,registry,installation,*,operation,inventory_sha256,writer_fence_sha256,original_transaction=None):
        if registry.subscription_links is not self:raise ValueError('matching stable registry required')
        a=registry.registration(installation);s,t=a['subject'],a['token']
        row=self._read(s,t);sources,legacy=self._sources(s,t)
        previous=row[1]['original_transaction'] if row else legacy
        originals={x for x in (a['original_transaction'],previous,original_transaction) if x is not None}
        if len(originals)>1:raise SubscriptionLinkConflict('account transfer/replacement forbidden')
        original=next(iter(originals),None)
        p=dict(v=1,kind='subscription-policy-transition',operation=operation,scope=copy.deepcopy(self.scope),
            subject_hash=digest(s),token_hash=digest(t),registration_sha256=digest(a),
            policies=sorted(self.catalog),legacy_sources=sources,expected_revision=row[0] if row else 0,
            before=row[1] if row else None,target_policy=self.active if original else None,
            original_transaction=original,inventory_sha256=inventory_sha256,writer_fence_sha256=writer_fence_sha256)
        self._validate_plan(p);return p

    def execute(self,p,registry,installation,*,authorization_sha256):
        """Explicit approved plan only. Lost responses use that SAME plan.

        No automatic rollback: pause admissions, preserve reader/receipts. A new
        policy never remaps account, token, original transaction or usage day.
        """
        p=copy.deepcopy(p)
        self._validate_plan(p);_hex(authorization_sha256)
        if registry.subscription_links is not self:raise ValueError('matching stable registry required')
        a=registry.registration(installation);s,t=a['subject'],a['token'];identity=digest(p)
        if (digest(a)!=p['registration_sha256'] or digest(s)!=p['subject_hash'] or digest(t)!=p['token_hash']):
            raise SubscriptionLinkConflict('registration differs from approved plan')
        intent=self._intent(identity)
        if intent is not None and intent['authorization']!=authorization_sha256:
            raise SubscriptionLinkConflict('authorization differs from retained intent')
        # A historical completion may be read after later migrations, without
        # touching the current pointer or calling Apple again.
        def committed_intent():
            # A concurrent identical operation can publish its intent after our
            # first read but before the completion/pointer reads below. Reload
            # that immutable identity once; never infer or replace missing data.
            current=intent if intent is not None else self._intent(identity)
            if current is None:raise StateUnavailable('missing committed intent')
            if current['authorization']!=authorization_sha256:
                raise SubscriptionLinkConflict('authorization differs from retained intent')
            return current
        done=self.audit.load(self.audit_key('complete',identity))
        if done is not None:
            intent=committed_intent()
            if digest(done[1])!=digest(self.receipt(intent)):raise StateUnavailable('completion differs')
            return done[1]
        row=self._read(s,t,complete=False)
        if row is not None and row[1]['plan']==identity:
            return self._complete(committed_intent())
        if (p['policies']!=sorted(self.catalog) or p['target_policy']!=(self.active if p['original_transaction'] else None)
                or p['expected_revision']!=(row[0] if row else 0)
                or digest(p['before'])!=digest(row[1] if row else None)):
            raise SubscriptionLinkConflict('stale plan or changed catalog; explicit new review required')
        if row is not None:self._read(s,t)  # cannot leapfrog another incomplete transition
        sources,_=self._sources(s,t)
        if sources!=p['legacy_sources']:raise SubscriptionLinkConflict('legacy inventory changed')
        def authorized():
            if self.authorize(copy.deepcopy(p),authorization_sha256) is not True or self.writer_fence(p['writer_fence_sha256']) is not True:
                raise StateUnavailable('control authorization or legacy writer fence unconfirmed')
        authorized()
        if intent is None:
            grant=None
            if p['original_transaction'] is not None:
                grant=self.catalog[self.active].grant(ServiceBinding(s,t,p['original_transaction']))
                if type(grant) is not DailyGrant:raise StateUnavailable('verified target policy grant required')
                grant.validate(s,self.now())
            candidate=dict(v=1,kind='subscription-policy-transition-intent',plan=p,authorization=authorization_sha256,
                apple_proof=grant.proof if grant else None,verified_at=grant.issued_at if grant else None,
                valid_until=grant.valid_until if grant else None,observed_limit=grant.limit if grant else None)
            self.audit.compare_exchange(self.audit_key('intent',identity),0,candidate)
            intent=self._intent(identity)
            if intent is None or intent['authorization']!=authorization_sha256:raise StateUnavailable('intent not confirmed')
        def fresh():
            if intent['apple_proof'] is not None:
                DailyGrant(s,intent['observed_limit'],intent['apple_proof'],intent['verified_at'],intent['valid_until']).validate(s,self.now())
        fresh()
        guard=dict(v=1,scope=self.scope,subject_hash=digest(s),token_hash=digest(t))
        key=stable_subscription_key(self.policy,s,'guard');g=self.store.load(key)
        if g is None:self.store.compare_exchange(key,0,guard);g=self.store.load(key)
        if g is None or digest(g[1])!=digest(guard):raise StateUnavailable('legacy reader guard unconfirmed')
        authorized();fresh()
        # Recheck inventory after the control/audit I/O as well as at planning.
        if self._sources(s,t)[0]!=p['legacy_sources']:raise SubscriptionLinkConflict('late legacy writer detected')
        self.store.compare_exchange(self.key(s),p['expected_revision'],self.record(intent))
        row=self._read(s,t,complete=False)
        if row is None:raise StateUnavailable('stable subscription write unconfirmed')
        if row[1]['plan']!=identity:raise SubscriptionLinkConflict('another transition won; preserve both intents')
        return self._complete(intent)
