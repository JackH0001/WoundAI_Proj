"""Default-off signed Apple account events, durable per-human authorization fence.

No research withdrawal, quota reset, media cleanup or account unlink occurs here.
Apple account deletion disables service identity, but does not claim data deletion.
"""
import hashlib
import hmac
from flask import Blueprint, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge
from audit_chain_contract import loads_json_object_strict
from lite_account_identity import AppleIdentityVerifier, IdentityRejected
from lite_attest_state import StateUnavailable, CAS_ATTEMPTS
from lite_usage_ledger import digest, _hex, _subject, UsageConflict
from lite_subscription_executor import key
from lite_subscription_runtime import SubscriptionRuntime

EVENT_PATH='/api/v1/lite-apple/account-events'


class AppleAccountEvents:
    def __init__(self, *, verifier, store, audit_store, namespace, identity_key):
        if not isinstance(verifier,AppleIdentityVerifier) or type(identity_key) is not bytes or len(identity_key)!=32:
            raise ValueError('trusted matching Apple identity authority required')
        _subject(namespace)
        self.verifier,self.store,self.audit=verifier,store,audit_store
        self.namespace,self.secret=namespace,identity_key
        self.binding=dict(namespace=namespace,issuer=verifier.issuer,audience=verifier.audience,
            identity_key_sha256=hashlib.sha256(identity_key).hexdigest())
        SubscriptionRuntime.immutable(store,key('apple-events-config',namespace),self.binding)

    def identity(self, sub):
        return hmac.new(self.secret,(self.namespace+'\0'+self.verifier.issuer+'\0'+sub).encode(),hashlib.sha256).hexdigest()

    def _read(self, identity):
        _hex(identity)
        row=self.store.load(key('apple-event-fence',identity))
        if row is None:return None
        p=row[1]
        if (set(p)!={'v','identity_sha256','revoked_through','deleted','event_sha256'} or type(p['v']) is not int or p['v']!=1
                or p['identity_sha256']!=identity or type(p['revoked_through']) is not int or p['revoked_through']<0
                or type(p['deleted']) is not bool): raise StateUnavailable('Apple event fence corrupt; never reset')
        try:_hex(p['event_sha256'])
        except ValueError as exc:raise StateUnavailable('Apple event fence evidence corrupt') from exc
        return row

    def require_identity(self, identity, issued):
        if type(issued) is not int or issued<0:raise StateUnavailable('identity issued-at proof required')
        row=self._read(identity)
        if row and (row[1]['deleted'] or issued<=row[1]['revoked_through']):
            raise IdentityRejected('Apple authorization ended; explicit new authorization required')

    def accept(self, signed):
        c=self.verifier.event(signed);identity=self.identity(c['sub'])
        # No email, raw subject, token or payload is persisted or returned.
        event=dict(v=1,event_sha256=digest([self.namespace,self.verifier.issuer,c['jti']]),
            payload_sha256=digest(signed),identity_sha256=identity,type=c['type'],
            event_time=c['event_time'],issued_at=c['issued'])
        event_key=key('apple-event',event['event_sha256'])
        existing=self.audit.load(event_key)
        if existing is not None and existing[1]!=event:raise UsageConflict('Apple event identity reused with different payload')
        # Bind jti before changing authorization. Concurrent signed payloads
        # reusing an event ID must not both alter the fence before audit wins.
        SubscriptionRuntime.immutable(self.store,key('apple-event-claim',event['event_sha256']),event)
        if c['type'] in ('consent-revoked','account-deleted'):
            for _ in range(CAS_ATTEMPTS):
                row=self._read(identity)
                old=row[1] if row else dict(revoked_through=0,deleted=False,event_sha256='0'*64)
                event_hash=(event['event_sha256'] if c['event_time']>old['revoked_through'] else
                    max(old['event_sha256'],event['event_sha256']) if c['event_time']==old['revoked_through'] else old['event_sha256'])
                updated=dict(v=1,identity_sha256=identity,revoked_through=max(old['revoked_through'],c['event_time']),
                    deleted=old['deleted'] or c['type']=='account-deleted',event_sha256=event_hash)
                if row and updated==old:break
                if self.store.compare_exchange(key('apple-event-fence',identity),row[0] if row else 0,updated):break
            else:raise StateUnavailable('Apple event fence raced; retry same payload')
        # A durable fence precedes the audit acknowledgment: audit failure must
        # not leave an already observed revocation authorized. Same signed event
        # retries complete audit/receipt without undoing the fence.
        SubscriptionRuntime.immutable(self.audit,event_key,event)
        receipt=dict(v=1,event_sha256=event['event_sha256'],state='applied',type=c['type'])
        SubscriptionRuntime.immutable(self.audit,key('apple-event-applied',event['event_sha256']),receipt)
        return receipt


def blueprint(events):
    if not isinstance(events,AppleAccountEvents):raise ValueError('trusted event processor required')
    bp=Blueprint('lite_apple_account_events',__name__)
    @bp.post(EVENT_PATH)
    def receive():
        try:
            raw_uri=request.environ.get('RAW_URI',request.path)
            if request.query_string or '%' in raw_uri or '\\' in raw_uri or request.headers.get('Content-Encoding','identity')!='identity':
                raise IdentityRejected('canonical Apple event target required')
            request.max_content_length=20000
            if request.mimetype!='application/json' or request.content_length is None or not 1<=request.content_length<=20000:
                raise IdentityRejected('bounded Apple event required')
            raw=request.get_data(cache=False)
            if len(raw)!=request.content_length:raise IdentityRejected('incomplete event')
            data=loads_json_object_strict(raw.decode('utf-8'))
            if set(data)!={'payload'}:raise IdentityRejected('exact Apple event fields required')
            return jsonify(events.accept(data['payload']))
        except RequestEntityTooLarge:return jsonify(error='apple_event_too_large'),413
        except (IdentityRejected,ValueError,TypeError,KeyError,UsageConflict):return jsonify(error='apple_event_rejected'),400
        except Exception:return jsonify(error='apple_event_unconfirmed'),503
    @bp.after_request
    def no_cache(response):response.headers['Cache-Control']='no-store';return response
    return bp
