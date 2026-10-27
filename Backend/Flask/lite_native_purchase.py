"""Opt-in App Attest -> existing verified account -> Apple purchase bridge.

No human enrollment, quota writes, research storage or startup installation.
The mapping must already be established by the trusted enrollment process.
"""
import base64
from flask import Blueprint,g,jsonify,request
from audit_chain_contract import loads_json_object_strict
from lite_attest_state import StateUnavailable
from lite_usage_ledger import UsageConflict,digest,_hex
from lite_subscription_runtime import SubscriptionRuntime
from lite_subscription_authority import SubscriptionRejected
from lite_subscription_executor import key

PREFIX='/api/v1/lite/purchase/'
PURCHASE_PATHS={PREFIX+'context',PREFIX+'confirm'}


class NativePurchaseBridge:
    def __init__(self, *, runtime, store, audit_store):
        if not isinstance(runtime,SubscriptionRuntime):raise ValueError('stable runtime required')
        self.runtime,self.store,self.audit=runtime,store,audit_store
        self.registry=runtime.registry;self.authority=runtime.links.catalog[runtime.links.active]

    def _key(self,installation,operation):
        _hex(operation,32)
        return key('native-purchase-context',digest([self.runtime.scope,installation,operation]))

    def context(self,installation,operation):
        a=self.registry.registration(installation)
        k=self._key(installation,operation);old=self.store.load(k);p=self.authority.policy
        self.runtime._policy(self.runtime.links.active)
        claim=self.runtime.store.load(self.runtime._account_key(a))
        if claim:self.runtime._batch_ready(a,claim[1])
        if old is None:
            self.runtime.ready(a)
            now=self.runtime.links.now()
            c=dict(v=1,operation=operation,installation=installation,account_sha256=digest(a['subject']),
                app_account_token=a['token'],registration_sha256=digest(a),scope_sha256=self.runtime.scope,
                product_id=p.product,environment=p.environment,created_at=now,expires_at=now+900)
            self.store.compare_exchange(k,0,c);old=self.store.load(k)
        if old is None:raise StateUnavailable('purchase context unconfirmed')
        c=old[1]
        if (set(c)!={'v','operation','installation','account_sha256','app_account_token','registration_sha256',
                    'scope_sha256','product_id','environment','created_at','expires_at'}
                or type(c['v']) is not int or c['v']!=1 or c['operation']!=operation or c['installation']!=installation
                or c['registration_sha256']!=digest(a) or c['account_sha256']!=digest(a['subject'])
                or c['app_account_token']!=a['token'] or c['scope_sha256']!=self.runtime.scope
                or c['product_id']!=p.product or c['environment']!=p.environment
                or type(c['created_at']) is not int or type(c['expires_at']) is not int
                or c['expires_at']-c['created_at']!=900 or c['created_at']>self.runtime.links.now()):
            raise UsageConflict('purchase context owner or policy differs')
        # Expired contexts may acknowledge a late/pending transaction, but the
        # native UI must obtain a fresh context before starting another purchase.
        return dict(c)

    def confirm(self,installation,operation,signed_transaction):
        if type(signed_transaction) is not str or not 1<=len(signed_transaction)<=12000:
            raise SubscriptionRejected('bounded Apple transaction required')
        if self.store.load(self._key(installation,operation)) is None:
            raise UsageConflict('saved context required')
        c=self.context(installation,operation);p=self.authority.policy
        try:
            self.authority.verifier.verify_and_decode_signed_transaction(signed_transaction)
            s=signed_transaction.split('.')[1]
            raw=loads_json_object_strict(base64.b64decode(s+'='*(-len(s)%4),altchars=b'-_',validate=True).decode())
            for k in ('transactionId','originalTransactionId','bundleId','environment','productId','appAccountToken','type','inAppOwnershipType'):
                if type(raw.get(k)) is not str:raise ValueError()
            if (raw['bundleId']!=p.bundle or raw['environment']!=p.environment or raw['productId']!=p.product
                    or raw['appAccountToken']!=c['app_account_token'] or raw['type']!='Auto-Renewable Subscription'
                    or raw['inAppOwnershipType']!='PURCHASED' or raw.get('isUpgraded',False) is not False
                    or any(not raw[k].isascii() or not raw[k].isdigit() or not 1<=len(raw[k])<=32 for k in ('transactionId','originalTransactionId'))):
                raise ValueError()
            if (any(type(raw.get(k)) is not int for k in ('purchaseDate','signedDate','expiresDate'))
                    or not 0<=raw['purchaseDate']<raw['expiresDate']
                    or not raw['purchaseDate']<=raw['signedDate']<=(self.runtime.links.now()+60)*1000):raise ValueError()
        except Exception as exc:raise SubscriptionRejected('transaction signature or account mismatch') from exc
        identity=digest([self.runtime.scope,raw['transactionId']])
        audit_key=key('native-purchase-confirmed',identity)
        previous=self.audit.load(audit_key)
        if previous is not None:
            r=previous[1]
            if (set(r)!={'v','state','account_sha256','app_account_token','transaction_id','original_transaction_id','product_id','environment','association_sha256'}
                    or type(r['v']) is not int or r['v']!=1 or r['state']!='associated'
                    or r.get('account_sha256')!=c['account_sha256'] or r.get('app_account_token')!=c['app_account_token']
                    or r.get('transaction_id')!=raw['transactionId'] or r.get('original_transaction_id')!=raw['originalTransactionId']
                    or r.get('product_id')!=p.product or r.get('environment')!=p.environment):
                raise UsageConflict('receipt already belongs to another account')
            _hex(r['association_sha256'])
            self.runtime.ready(self.registry.registration(installation));return dict(r)
        # Current Server API verification in stable transition validates ownership
        # and never grants quota from this client JWS alone. Historical restores
        # keep their old binding; fresh quota admission still queries Apple.
        association=self.runtime.transition(installation,raw['originalTransactionId'])
        a=self.registry.resolve(installation);self.runtime.ready(a)
        if a['original_transaction']!=raw['originalTransactionId']:raise StateUnavailable('association not visible')
        receipt=dict(v=1,state='associated',account_sha256=c['account_sha256'],app_account_token=c['app_account_token'],
            transaction_id=raw['transactionId'],original_transaction_id=raw['originalTransactionId'],
            product_id=p.product,environment=p.environment,association_sha256=digest(association))
        # A historical receipt is not a paid flag, balance or quota reset.
        return self.runtime.immutable(self.audit,audit_key,receipt)


def blueprint(bridge):
    if not isinstance(bridge,NativePurchaseBridge):raise ValueError('trusted purchase bridge required')
    bp=Blueprint('lite_native_purchase',__name__)
    def action(kind):
        try:
            admission=getattr(g,'lite_admission',None)
            if admission is None:return jsonify(error='lite_attestation_required'),401
            body=loads_json_object_strict(request.get_data().decode())
            fields={'anon_id','operation'}|({'signed_transaction'} if kind=='confirm' else set())
            if request.mimetype!='application/json' or set(body)!=fields:raise UsageConflict('exact purchase fields required')
            if body['anon_id']!=admission.installation:return jsonify(error='lite_owner_mismatch'),403
            r=(bridge.context(admission.installation,body['operation']) if kind=='context' else
               bridge.confirm(admission.installation,body['operation'],body['signed_transaction']))
            return jsonify(r)
        except SubscriptionRejected:return jsonify(error='lite_purchase_owner_or_transaction_rejected',keep_local=True),409
        except (UsageConflict,ValueError,TypeError,KeyError):return jsonify(error='lite_purchase_conflict',keep_local=True),409
        except Exception:return jsonify(error='lite_purchase_unconfirmed',keep_local=True),503
    for kind in ('context','confirm'):bp.add_url_rule(PREFIX+kind,kind,lambda kind=kind:action(kind),methods=['POST'])
    @bp.after_request
    def no_cache(r):r.headers['Cache-Control']='no-store';return r
    return bp
