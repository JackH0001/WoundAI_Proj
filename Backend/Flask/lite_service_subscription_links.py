"""Opt-in, create-only subscription association for an ALREADY verified account.

No human authentication, public registration, entitlement cache, account transfer,
quota mutation, research write, startup wiring or cloud client is supplied here.
The control caller must independently authenticate the account and provide the
audit proof of that decision. A digest is evidence linkage, NOT authentication.
"""
from dataclasses import asdict
import hashlib
import time
from lite_attest_state import StateUnavailable
from lite_subscription_authority import ServiceBinding
from lite_usage_ledger import DailyGrant, UsageConflict, digest, _hex, _integer, _subject


class SubscriptionLinkConflict(UsageConflict): pass


class SubscriptionMigrationRequired(StateUnavailable): pass


def stable_subscription_key(policy, subject, kind='account'):
    # app_id is validated in the row, so an accidental app_id change cannot
    # select an empty slot and silently treat an existing account as free.
    return hashlib.sha256(('lite/service-subscription-stable/1\0'+policy.bundle+'\0'
        +policy.environment+'\0'+kind+'\0'+_subject(subject)).encode()).digest()


class ServiceSubscriptionLinks:
    def __init__(self, *, store, audit_store, authority, clock=time.time):
        authority.policy.validate(getattr(authority,'allowed_products',None))
        self.store,self.audit,self.authority,self.clock=store,audit_store,authority,clock
        self.policy=digest(asdict(authority.policy))

    def now(self):
        n=self.clock()
        if type(n) not in (int,float) or not 0<=n<2**53:
            raise StateUnavailable('invalid binding clock')
        return int(n)

    def key(self,subject):
        return hashlib.sha256(('lite/service-subscription/1\0'+self.policy+'\0'+_subject(subject)).encode()).digest()

    def audit_key(self,kind,identity):
        _hex(identity)
        return hashlib.sha256(('lite/service-subscription-audit/1\0'+self.policy+'\0'+kind+'\0'+identity).encode()).digest()

    def immutable(self,key,value):
        # Read first: completed retries must not generate redundant writes.
        row=self.audit.load(key)
        if row is None:
            self.audit.compare_exchange(key,0,value)
            row=self.audit.load(key)
        if row is None or digest(row[1])!=digest(value):
            raise StateUnavailable('subscription audit absent or conflicting')

    def _read(self,subject,token):
        row=self.store.load(self.key(subject))
        if row is None:return None
        link=row[1]
        try:
            if (type(link) is not dict or set(link)!={'v','policy','subject_hash','token_hash','original_transaction','intent'}
                    or type(link['v']) is not int or link['v']!=1 or link['policy']!=self.policy
                    or link['subject_hash']!=digest(subject) or link['token_hash']!=digest(token)):
                raise ValueError('binding schema or owner')
            ServiceBinding(subject,token,link['original_transaction']).validate();_hex(link['intent'])
            audit=self.audit.load(self.audit_key('intent',link['intent']))
            if audit is None:raise ValueError('missing intent')
            intent=audit[1]
            if (type(intent) is not dict or set(intent)!={'v','kind','policy','subject_hash','token_hash',
                    'original_transaction','installation_hash','registration_proof','authentication_proof',
                    'apple_proof','verified_at','valid_until','observed_limit'}
                    or type(intent['v']) is not int or intent['v']!=1
                    or intent['kind']!='verified-subscription-link' or digest(intent)!=link['intent']
                    or any(intent[k]!=link[k] for k in ('policy','subject_hash','token_hash','original_transaction'))):
                raise ValueError('intent binding')
            for k in ('installation_hash','registration_proof','authentication_proof','apple_proof'):_hex(intent[k])
            if (type(intent['observed_limit']) is not int or intent['observed_limit'] not in (2,10)
                    or not 0<_integer(intent['valid_until'])-_integer(intent['verified_at'])<=60
                    or intent['verified_at']>self.now()):raise ValueError('historical proof chronology')
        except (ValueError,KeyError,TypeError) as e:
            raise StateUnavailable('invalid subscription association; no fallback or reset') from e
        return link.copy()

    @staticmethod
    def receipt(link):
        # This is a binding acknowledgment, NEVER a current paid entitlement.
        return dict(v=1,kind='subscription-association-confirmed',intent=link['intent'],link_sha256=digest(link))

    def _complete(self,link):
        receipt=self.receipt(link)
        self.immutable(self.audit_key('complete',link['intent']),receipt)
        return receipt

    def lookup(self,subject,token):
        """Read-only overlay. Incomplete audit blocks until explicit attach retry."""
        ServiceBinding(subject,token,'0').validate()
        self._guard(subject)
        link=self._read(subject,token)
        if link is None:return None
        row=self.audit.load(self.audit_key('complete',link['intent']))
        if row is None or digest(row[1])!=digest(self.receipt(link)):
            raise StateUnavailable('subscription association awaits completion readback')
        self._guard(subject)
        return link['original_transaction']

    def _guard(self,subject):
        if self.store.load(stable_subscription_key(self.authority.policy,subject,'guard')) is not None:
            raise SubscriptionMigrationRequired('stable subscription reader required; no legacy fallback')

    def attach(self,registry,installation,original_transaction,*,authentication_proof_sha256):
        """Trusted control operation; no HTTP route accepts these arguments.

        A previously associated transaction is only read back, even after expiry
        or refund. Every new quota grant still queries the authority separately.
        New account/device enrollment and alternative-original transfers are NOT
        implemented. Repeated association does not initialize/reset a usage day.
        """
        if registry.subscription_links is not self or registry.authority is not self.authority:
            raise ValueError('explicit matching registry/authority required')
        _hex(authentication_proof_sha256)
        account=registry.registration(installation)
        subject,token=account['subject'],account['token']
        self._guard(subject)
        binding=ServiceBinding(subject,token,original_transaction);binding.validate()
        if account['original_transaction'] not in (None,original_transaction):
            raise SubscriptionLinkConflict('legacy transaction differs')
        link=self._read(subject,token)
        if link is not None:
            if link['original_transaction']!=original_transaction:
                raise SubscriptionLinkConflict('account already associated; no silent replacement')
            return self._complete(link)
        grant=self.authority.grant(binding)
        if type(grant) is not DailyGrant:raise StateUnavailable('verified authority grant required')
        grant.validate(subject,self.now())
        intent=dict(v=1,kind='verified-subscription-link',policy=self.policy,
            subject_hash=digest(subject),token_hash=digest(token),original_transaction=original_transaction,
            installation_hash=digest(installation),registration_proof=account['proof'],
            authentication_proof=authentication_proof_sha256,apple_proof=grant.proof,
            verified_at=grant.issued_at,valid_until=grant.valid_until,observed_limit=grant.limit)
        identity=digest(intent)
        self.immutable(self.audit_key('intent',identity),intent)
        # An audit write can stall past the short verification window.
        grant.validate(subject,self.now())
        candidate=dict(v=1,policy=self.policy,subject_hash=digest(subject),token_hash=digest(token),
                       original_transaction=original_transaction,intent=identity)
        self._guard(subject)
        self.store.compare_exchange(self.key(subject),0,candidate)
        link=self._read(subject,token)
        if link is None:raise StateUnavailable('subscription association unconfirmed')
        if link['original_transaction']!=original_transaction:
            raise SubscriptionLinkConflict('concurrent different subscription won; preserve both proofs')
        self._guard(subject)
        return self._complete(link)
