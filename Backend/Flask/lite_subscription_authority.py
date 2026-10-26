"""Apple status -> short-lived internal quota grant; not installed in HTTP.

Requires a trusted server identity binding, current Server API response, official
SignedDataVerifier and durable proof store. A client JWS/paid flag alone is never
an entitlement. No automatic credentials lookup, account creation or network at
import time. No cached active grant is resurrected by delayed notifications.
"""
from dataclasses import dataclass
import base64
import hashlib
import re
import time
import uuid
from lite_usage_ledger import DailyGrant, UsageConflict, digest, _integer, _subject
from lite_attest_state import StateUnavailable
from audit_chain_contract import loads_json_object_strict


class SubscriptionRejected(ValueError): pass


def enum_value(value):
    return getattr(value, 'value', value)


@dataclass(frozen=True)
class ApplePolicy:
    bundle: str
    app_id: int
    product: str
    environment: str

    def validate(self, allowed_products=None):
        # Only trusted startup may extend the explicit catalog. Default behavior
        # still accepts the one existing product; no client field selects it.
        products=('com.woundai.lite.plus.monthly',) if allowed_products is None else allowed_products
        if (type(products) is not tuple or not 1<=len(products)<=8
                or any(type(p) is not str or re.fullmatch(r'com\.woundai\.lite\.[a-zA-Z0-9_.]{1,100}',p) is None for p in products)
                or len(set(products))!=len(products)):
            raise ValueError('bounded explicit server product catalog required')
        if (self.bundle != 'com.woundai.lite' or self.product not in products
                or type(self.app_id) is not int or self.app_id <= 0
                or self.environment not in ('Sandbox', 'Production')):
            raise ValueError('explicit Lite Apple policy required; Xcode/local verification forbidden')


@dataclass(frozen=True)
class ServiceBinding:
    """Read from a verified SERVER account registry, never client-selected fields.

    Registration/restore of this binding is a separate pending rollout gate.
    The research installation/epoch and receipt of consent must not create it.
    """
    subject: str
    app_account_token: str
    original_transaction: str

    def validate(self):
        _subject(self.subject)
        try:
            token = uuid.UUID(self.app_account_token)
            if str(token) != self.app_account_token or token.int == 0:
                raise ValueError()
        except (ValueError, TypeError, AttributeError) as e:
            raise SubscriptionRejected('invalid server account binding') from e
        if type(self.original_transaction) is not str or re.fullmatch(r'[0-9]{1,32}',self.original_transaction) is None:
            raise SubscriptionRejected('invalid transaction binding')


def apple_verifier(policy, root_certificates, *, allowed_products=None):
    """Deployment factory: official library with chain/online revocation checks.

    Roots must be provisioned from Apple PKI and pinned by deployment review.
    This function does not download roots, find keys or configure any service.
    Tests instantiate the SAME official verifier with synthetic roots/offline OCSP.
    """
    policy.validate(allowed_products)
    if (type(root_certificates) is not list or not root_certificates
            or any(type(v) is not bytes or not v for v in root_certificates)):
        raise ValueError('pinned Apple root certificates required')
    from appstoreserverlibrary.signed_data_verifier import SignedDataVerifier
    from appstoreserverlibrary.models.Environment import Environment
    return SignedDataVerifier(root_certificates, True, Environment(policy.environment), policy.bundle, policy.app_id)


class AppleSubscriptionAuthority:
    def __init__(self, *, policy, verifier, client, proof_store, clock=time.time, allowed_products=None):
        policy.validate(allowed_products)
        self.allowed_products=allowed_products
        self.policy, self.verifier, self.client, self.proofs, self.clock = policy, verifier, client, proof_store, clock

    def _now(self):
        n = self.clock()
        if type(n) not in (float,int) or not 0 <= n < 2**53:
            raise StateUnavailable('invalid subscription clock')
        return int(n)

    def _grace(self, row, binding, expires, began, revoked):
        """Bind Apple's signed renewal to the verified transaction/status query.

        Renewal JWS verification checks its chain and environment, not our
        account/product binding. No configured number of grace days substitutes
        for the signed expiry. Auto-renew-off describes the NEXT renewal, not
        withdrawal of an already granted grace period.
        """
        signed=getattr(row,'signedRenewalInfo',None)
        if signed is None:
            raise StateUnavailable('billing grace renewal proof unavailable; refresh')
        if type(signed) is not str or not 1<=len(signed)<=12000:
            raise SubscriptionRejected('bounded signed renewal required')
        try:
            renewal=self.verifier.verify_and_decode_renewal_info(signed)
            encoded=signed.split('.')[1]
            raw=loads_json_object_strict(base64.b64decode(encoded+'='*(-len(encoded)%4),
                altchars=b'-_',validate=True).decode('utf-8'))
            if (any(type(raw.get(k)) is not str for k in ('originalTransactionId','productId','environment'))
                    or any(type(raw.get(k)) is not int for k in ('gracePeriodExpiresDate','signedDate'))
                    or any(k in raw and type(raw[k]) is not str for k in ('appAccountToken','autoRenewProductId'))
                    or ('isInBillingRetryPeriod' in raw and type(raw['isInBillingRetryPeriod']) is not bool)
                    or ('autoRenewStatus' in raw and (type(raw['autoRenewStatus']) is not int or raw['autoRenewStatus'] not in (0,1)))):
                raise ValueError('strict signed renewal claim types')
        except Exception as exc:
            raise SubscriptionRejected('Apple renewal verification failed') from exc
        ended=self._now()
        if not began<=ended<=began+30:
            raise StateUnavailable('renewal verification deadline')
        if (getattr(renewal,'originalTransactionId',None)!=binding.original_transaction
                or getattr(renewal,'productId',None)!=self.policy.product
                or enum_value(getattr(renewal,'environment',None))!=self.policy.environment
                or ('appAccountToken' in raw and raw['appAccountToken']!=binding.app_account_token)):
            raise SubscriptionRejected('renewal product/owner/environment mismatch')
        # The token is optional in renewal info; the transaction token is always
        # required and verified above. A future autoRenewProductId is not the
        # current entitlement and cannot authorize a different product here.
        grace=raw['gracePeriodExpiresDate'];signed_at=raw['signedDate']
        if not (0<=expires<=signed_at<2**53 and signed_at<=(ended+60)*1000
                and expires<grace<2**53):
            raise SubscriptionRejected('renewal timestamps')
        if (revoked is not None or expires>ended*1000 or grace<=ended*1000
                or raw.get('isInBillingRetryPeriod') is False):
            raise StateUnavailable('contradictory grace status; refresh instead of downgrade')
        until=min(grace//1000,ended+30)
        if until<=ended:raise StateUnavailable('grace expiry boundary; refresh')
        return signed,grace,ended,until

    def grant(self, binding):
        if not isinstance(binding, ServiceBinding):
            raise SubscriptionRejected('server binding required')
        binding.validate(); p=self.policy; began=self._now()
        # Do not filter ACTIVE in the query: revoked/expired must be observable.
        try:
            response = self.client.get_all_subscription_statuses(binding.original_transaction)
        except Exception as exc:
            raise StateUnavailable('Apple status unavailable; no invented free or paid balance') from exc
        ended=self._now()
        if not began <= ended <= began+30:
            raise StateUnavailable('status query clock/deadline')
        if (getattr(response,'bundleId',None) != p.bundle or getattr(response,'appAppleId',None) != p.app_id
                or enum_value(getattr(response,'environment',None)) != p.environment):
            raise SubscriptionRejected('status application/environment mismatch')
        groups=getattr(response,'data',None)
        if type(groups) is not list or not 1 <= len(groups) <= 100:
            raise SubscriptionRejected('bounded subscription status required')
        matches=[]
        for group in groups:
            rows=getattr(group,'lastTransactions',None)
            if type(rows) is not list or len(rows)>100:
                raise SubscriptionRejected('malformed status group')
            matches += [r for r in rows if getattr(r,'originalTransactionId',None)==binding.original_transaction]
        if len(matches)!=1:
            raise SubscriptionRejected('missing or ambiguous bound subscription')
        row=matches[0]; signed=getattr(row,'signedTransactionInfo',None)
        if type(signed) is not str or not 1 <= len(signed) <= 12000:
            raise SubscriptionRejected('bounded signed transaction required')
        try:
            # Name is pinned to Apple library 3.1.2; upstream main uses another name.
            transaction=self.verifier.verify_and_decode_signed_transaction(signed)
            # Official model conversion can coerce booleans/numbers into expected
            # types. Inspect the SAME already signature-verified payload to reject
            # such coercion and duplicate JSON keys; this is not JWS verification.
            encoded=signed.split('.')[1]
            raw=loads_json_object_strict(base64.b64decode(encoded+'='*(-len(encoded)%4),
                altchars=b'-_',validate=True).decode('utf-8'))
            strings=('transactionId','originalTransactionId','bundleId','environment','productId',
                     'appAccountToken','type','inAppOwnershipType')
            if (any(type(raw.get(k)) is not str for k in strings)
                    or any(type(raw.get(k)) is not int for k in ('purchaseDate','expiresDate','signedDate'))
                    or ('revocationDate' in raw and type(raw['revocationDate']) is not int)
                    or ('isUpgraded' in raw and type(raw['isUpgraded']) is not bool)):
                raise ValueError('strict signed claim types')
        except Exception as exc:
            raise SubscriptionRejected('Apple transaction verification failed') from exc
        ended=self._now()
        if not began <= ended <= began+30:
            raise StateUnavailable('verification deadline')
        checks=(getattr(transaction,'bundleId',None)==p.bundle,
                enum_value(getattr(transaction,'environment',None))==p.environment,
                getattr(transaction,'productId',None)==p.product,
                getattr(transaction,'originalTransactionId',None)==binding.original_transaction,
                getattr(transaction,'appAccountToken',None)==binding.app_account_token,
                enum_value(getattr(transaction,'type',None))=='Auto-Renewable Subscription',
                enum_value(getattr(transaction,'inAppOwnershipType',None))=='PURCHASED',
                getattr(transaction,'isUpgraded',False) in (None,False))
        if not all(checks):
            raise SubscriptionRejected('product/owner/type mismatch; no cross-account restoration')
        transaction_id=getattr(transaction,'transactionId',None)
        if type(transaction_id) is not str or re.fullmatch(r'[0-9]{1,32}',transaction_id) is None:
            raise SubscriptionRejected('verified transaction identifier required')
        purchased=getattr(transaction,'purchaseDate',None)
        expires=getattr(transaction,'expiresDate',None)
        signed_at=getattr(transaction,'signedDate',None)
        if (any(type(v) is not int or v<0 for v in (purchased,expires,signed_at))
                or not purchased < expires or purchased>ended*1000
                or not purchased <= signed_at <= (ended+60)*1000):
            raise SubscriptionRejected('transaction timestamps')
        status=enum_value(getattr(row,'status',None))
        if type(status) is not int or status not in (1,2,3,4,5):
            raise SubscriptionRejected('unknown subscription status')
        revoked=getattr(transaction,'revocationDate',None)
        if revoked is not None and (type(revoked) is not int or revoked<0 or revoked>(ended+60)*1000):
            raise SubscriptionRejected('invalid revocation')
        if status==4:
            renewal,grace,ended,until=self._grace(row,binding,expires,began,revoked)
            limit=10
        elif status==1:
            if revoked is not None or expires<=ended*1000:
                raise StateUnavailable('contradictory active status; refresh instead of grant')
            limit=10; until=min(expires//1000,ended+30)
            if until<=ended: raise StateUnavailable('expiry boundary; refresh')
        else:
            if status in (2,3) and expires>ended*1000:
                raise StateUnavailable('contradictory expired/retry status')
            limit=2;until=ended+30
        evidence=dict(v=1,kind='apple-status-query',subject_hash=digest(binding.subject),
            account_binding_hash=digest([binding.app_account_token,binding.original_transaction]),
            bundle=p.bundle,app_id=p.app_id,environment=p.environment,product=p.product,
            query_started=began,verified_at=ended,status=status,signed_transaction=signed,
            admitted_limit=limit,valid_until=until)
        if status==4:
            evidence.update(v=2,entitlement_basis='billing-grace',signed_renewal=renewal,
                            grace_expires_ms=grace)
        proof=digest(evidence)
        # One verified original subscription maps to exactly one service subject.
        # Restoring on another device must resolve to that SAME account, not
        # create a second allowance. Account transfers need a separate process.
        owner_key=hashlib.sha256(('woundlite/subscription-owner/1\0'+p.bundle+'\0'
                                  +p.environment+'\0'+binding.original_transaction).encode()).digest()
        owner=dict(v=1,subject_hash=digest(binding.subject),token_hash=digest(binding.app_account_token))
        self.proofs.compare_exchange(owner_key,0,owner)
        bound=self.proofs.load(owner_key)
        if bound is None: raise StateUnavailable('subscription ownership not confirmed')
        if bound[1]!=owner: raise SubscriptionRejected('subscription already bound to another service account')
        key=hashlib.sha256(('woundlite/subscription-proof/1\0'+proof).encode()).digest()
        # Immutable by application behavior: create-only, read back, no update/delete.
        # IAM/retention/real WORM policy remain separate deployment responsibilities.
        self.proofs.compare_exchange(key,0,evidence)
        stored=self.proofs.load(key)
        if stored is None or stored[1]!=evidence:
            raise StateUnavailable('subscription proof not confirmed')
        return DailyGrant(binding.subject,limit,proof,ended,until)
