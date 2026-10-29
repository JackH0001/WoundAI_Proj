"""Opt-in service-only HTTP coordinator dependencies; no startup activation.

Dedicated stores and a verified installation->service account registry are
injected by trusted startup. No research store, credential discovery, public
registration, migration/reset, cloud client, or cleanup is provided here.
"""
import hashlib
import io
import json
import math
import secrets
import time
from lite_usage_ledger import UsageLedger, UsageConflict, UsageLimit, Ticket, free_grant, digest, _subject, _hex
from lite_subscription_authority import ServiceBinding
from lite_attest_state import StateUnavailable, CAS_ATTEMPTS, _encode

MAX_IMAGE=8*1024*1024
RESULT_TTL=86400


class OperationExpired(UsageConflict): pass
class OperationUnknown(UsageConflict): pass
class ImageRejected(UsageConflict): pass


def validate_retained_receipt(v,now):
    """v2 stores a terminal identity/receipt, never a still-readable payload.

    No implicit migration. Only the separately invoked retention executor writes
    this shape after expiry and verified ledger settlement/cancellation.
    """
    t=v['retention']
    if (v['result'] is not None or type(t) is not dict
            or set(t)!={'plan_sha256','compacted_at','receipt'}
            or type(t['compacted_at']) is not int or not v['expires']<=t['compacted_at']<=now):
        raise ValueError('invalid retention terminal')
    _hex(t['plan_sha256']);r=t['receipt']
    if (type(r) is not dict or set(r)!={'day','operation','request_digest','outcome','result_sha256','grant_sha256','admitted_limit'}
            or type(r['day']) is not int or r['day']!=v['day'] or r['operation']!=v['operation']
            or r['request_digest']!=v['digest'] or r['outcome'] not in ('succeeded','empty','cancelled')
            or type(r['admitted_limit']) is not int or r['admitted_limit'] not in (2,10)):
        raise ValueError('invalid terminal receipt')
    _hex(r['result_sha256']);_hex(r['grant_sha256'])
    if r['outcome']=='cancelled':
        if v['result_sha256'] is not None:raise ValueError('cancelled operation has inference result')
    elif _hex(v['result_sha256'])!=r['result_sha256']:
        raise ValueError('result hash differs from terminal receipt')


class ServiceAccounts:
    """Create-only trusted provisioning, never exposed as a user API.

    Only independently verified registration/restore may supply this mapping.
    An App Attest installation is authenticated, but alone is not a human account.
    Optional subscription links apply across verified installations of the SAME
    service account. Human login, recovery and unlinking still require rollout.
    """
    def __init__(self,store,authority=None,subscription_links=None,admission_guard=None,bootstrap=None):
        if subscription_links is not None:
            if authority is not None and authority is not subscription_links.authority:
                raise ValueError('subscription binding and quota must use the same authority')
            authority=subscription_links.authority
        self.store,self.authority,self.subscription_links=store,authority,subscription_links
        if admission_guard is not None and not callable(admission_guard):
            raise ValueError('trusted admission guard required')
        self.admission_guard=admission_guard
        if bootstrap is not None and not callable(bootstrap):raise ValueError('trusted bootstrap required')
        self.bootstrap=bootstrap
        self.lifecycle=None

    def attach_lifecycle(self,lifecycle):
        """Trusted startup composition; missing component fails closed thereafter."""
        from lite_account_lifecycle import AccountLifecycle
        if not isinstance(lifecycle,AccountLifecycle):raise ValueError('trusted account lifecycle required')
        k=hashlib.sha256(b'lite/service-accounts/lifecycle/1').digest()
        expected=dict(v=1,binding=lifecycle.binding)
        if lifecycle.service_catalog: expected.update(v=2,service_catalog='linked-operations/1')
        self.store.compare_exchange(k,0,expected)
        row=self.store.load(k)
        if row is None or row[1]!=expected:raise StateUnavailable('service lifecycle binding differs')
        self.lifecycle=lifecycle

    def require_active(self,account):
        if self.lifecycle is None:
            if self.store.load(hashlib.sha256(b'lite/service-accounts/lifecycle/1').digest()) is not None:
                raise StateUnavailable('service account lifecycle required')
            return
        subject=account['subject']
        if not subject.startswith('apple-'):raise StateUnavailable('verified Apple lifecycle account required')
        self.lifecycle.require_active(subject[6:])

    @staticmethod
    def key(installation):return hashlib.sha256(('lite/service-account/1\0'+_subject(installation)).encode()).digest()

    def provision(self,installation,subject,token,original_transaction,proof_sha256):
        _subject(subject);_hex(proof_sha256)
        ServiceBinding(subject,token,original_transaction if original_transaction is not None else '0').validate()
        value=dict(v=1,installation_hash=digest(installation),subject=subject,token=token,
                   original_transaction=original_transaction,proof=proof_sha256)
        self.require_active(value)
        self.store.compare_exchange(self.key(installation),0,value)
        readback=self.store.load(self.key(installation))
        if readback is None:raise StateUnavailable('account provisioning unconfirmed')
        if readback[1]!=value:
            raise UsageConflict('service account cannot be silently replaced')
        if self.bootstrap is not None:self.bootstrap(installation)
        self.require_active(value)

    def registration(self,installation):
        """Immutable trusted installation mapping, without a subscription overlay."""
        row=self.store.load(self.key(installation))
        if row is None:raise StateUnavailable('verified service account not provisioned')
        v=row[1]
        try:
            if (type(v) is not dict or set(v)!={'v','installation_hash','subject','token','original_transaction','proof'}
                    or type(v['v']) is not int or v['v']!=1 or v['installation_hash']!=digest(installation)):
                raise ValueError()
            _hex(v['proof']);_subject(v['subject'])
            original=v['original_transaction']
            if original is not None and (type(original) is not str or not original):raise ValueError()
            ServiceBinding(v['subject'],v['token'],original if original is not None else '0').validate()
        except (KeyError,ValueError,TypeError) as e:raise StateUnavailable('invalid service account') from e
        return v.copy()

    def resolve(self,installation):
        account=self.registration(installation)
        self.require_active(account)
        if self.subscription_links is not None:
            original=self.subscription_links.lookup(account['subject'],account['token'])
            if original is not None:
                if account['original_transaction'] not in (None,original):
                    raise UsageConflict('legacy paid binding differs; explicit reconciliation required')
                account['original_transaction']=original
        return account

    def grant(self,account,now):
        self.require_active(account)
        if self.admission_guard is not None and self.admission_guard(account) is not True:
            raise StateUnavailable('subscription cutover admission paused or unconfirmed')
        if account['original_transaction'] is None:return free_grant(account['subject'],now)
        if self.authority is None:raise StateUnavailable('paid verification unavailable')
        return self.authority.grant(ServiceBinding(account['subject'],account['token'],account['original_transaction']))


def validate_result(result):
    if (type(result) is not dict or set(result)!={'image_w','image_h','wound_polygons','confidence','route'}
            or any(type(result[k]) is not int or not 1<=result[k]<=8192 for k in ('image_w','image_h'))
            or result['image_w']*result['image_h']>16000000
            or type(result['confidence']) not in (int,float) or not math.isfinite(result['confidence'])
            or not 0<=result['confidence']<=1
            or type(result['route']) is not str or not 1<=len(result['route'])<=64):
        raise StateUnavailable('invalid bounded inference result')
    polys=result['wound_polygons']
    if type(polys) is not list or len(polys)>32:raise StateUnavailable('polygon bound')
    total=0
    for poly in polys:
        if type(poly) is not list or not 3<=len(poly)<=512:raise StateUnavailable('polygon points')
        total+=len(poly)
        for point in poly:
            if (type(point) is not list or len(point)!=2 or
                    any(type(v) not in (int,float) or not math.isfinite(v) for v in point)
                    or not 0<=point[0]<result['image_w'] or not 0<=point[1]<result['image_h']):
                raise StateUnavailable('polygon coordinate')
    if total>512 or len(_encode(result))>8192:raise StateUnavailable('result size')
    return result


class RasterInference:
    """Same RGB model and contour extraction as Lite, no storage or rate appends."""
    def __init__(self,model,face_check):
        if not callable(model) or not callable(face_check):raise ValueError('model and privacy check required')
        self.model,self.face_check=model,face_check

    def decode(self,raw):
        from PIL import Image
        import cv2,numpy as np
        if type(raw) is not bytes or not 1<=len(raw)<=MAX_IMAGE:raise ImageRejected('bounded JPEG required')
        try:
            with Image.open(io.BytesIO(raw)) as im:
                if (im.format!='JPEG' or min(im.size)<1 or max(im.size)>8192 or im.width*im.height>16000000
                        or im.getexif().get(274,1)!=1):raise ValueError()
                im.verify()
            bgr=cv2.imdecode(np.frombuffer(raw,np.uint8),cv2.IMREAD_COLOR)
            if bgr is None:raise ValueError()
        except Exception as e:raise ImageRejected('invalid or unnormalized image') from e
        found,_=self.face_check(bgr)
        if found is True:raise ImageRejected('face_detected')
        if found is not False:raise StateUnavailable('privacy check unavailable')
        return cv2.cvtColor(bgr,cv2.COLOR_BGR2RGB)

    def infer(self,rgb):
        import numpy as np
        from api_lite import _polygons_from_mask
        output=self.model(rgb);mask,info=output if isinstance(output,tuple) else (output,{})
        h,w=rgb.shape[:2]
        if mask is None:polys=[];confidence=0.
        else:
            a=np.asarray(mask)
            if a.shape!=(h,w) or not np.isfinite(a).all() or not np.isin(a,[0,1]).all():
                raise StateUnavailable('model mask contract')
            polys=_polygons_from_mask(a);confidence=float(a.mean()) if polys else 0.
        return validate_result(dict(image_w=w,image_h=h,wound_polygons=polys,
                                    confidence=confidence,route=info.get('route') or 'student'))


class ServiceOperations:
    def __init__(self,*,ledger,accounts,store,inference,model_version,clock=time.time,recovery_store=None):
        if not isinstance(ledger,UsageLedger) or not isinstance(accounts,ServiceAccounts) or not isinstance(inference,RasterInference):
            raise ValueError('trusted service components required')
        _hex(model_version)
        self.ledger,self.accounts,self.store,self.inference,self.model_version,self.clock=ledger,accounts,store,inference,model_version,clock
        self.recovery=None
        if recovery_store is not None:
            from lite_service_recovery import ServiceRecovery
            self.recovery=ServiceRecovery(self,recovery_store)

    def now(self):
        n=self.clock()
        if type(n) not in (int,float) or not math.isfinite(n) or not 0<=n<2**53:raise StateUnavailable('invalid clock')
        return int(n)

    @staticmethod
    def key(subject,operation):
        return hashlib.sha256(('lite/service-op/1\0'+_subject(subject)+'\0'+_hex(operation,32)).encode()).digest()

    def admit_operation(self,subject,operation):
        """Catalog slot wins the SAME account CAS as closure, before any result write."""
        self.accounts.require_active(dict(subject=subject))
        life=self.accounts.lifecycle
        if life is not None and life.service_catalog:
            life.services.reserve(subject[6:],operation)

    def load(self,subject,operation):
        row=self.store.load(self.key(subject,operation))
        if row is None:return None
        revision,v=row
        if type(v) is dict and type(v.get('v')) is int and v['v']==3:
            from lite_service_disposition import validate_seal
            validate_seal(v,subject,operation)
            return row
        try:
            fields={'v','subject_hash','operation','day','created','expires','image_sha256','model','digest','worker','result','result_sha256'}
            if (type(v) is not dict or type(v.get('v')) is not int or v['v'] not in (1,2)
                    or set(v)!=(fields if v['v']==1 else fields|{'retention'})
                    or v['subject_hash']!=digest(subject) or v['operation']!=operation
                    or any(type(v[k]) is not int or v[k]<0 for k in ('day','created','expires'))
                    or v['day']!=v['created']//86400 or v['expires']!=v['created']+RESULT_TTL
                    or v['created']>self.now()):raise ValueError()
            _hex(v['image_sha256']);_hex(v['model']);_hex(v['digest']);_hex(v['worker'],32)
            if v['digest']!=digest(['service-only/v1',v['image_sha256'],v['model']]):raise ValueError()
            if v['v']==2:
                validate_retained_receipt(v,self.now())
            elif v['result'] is None:
                if v['result_sha256'] is not None:raise ValueError()
            elif digest(validate_result(v['result']))!=_hex(v['result_sha256']):raise ValueError()
            _encode(v)
        except (ValueError,TypeError,KeyError) as e:raise StateUnavailable('operation state invalid; never reset') from e
        return revision,v

    @staticmethod
    def ticket(subject,v):return Ticket(subject,v['day'],v['operation'],v['digest'])

    def _require(self,subject,operation):
        row=self.load(subject,operation)
        if row is None:raise OperationUnknown('unknown operation')
        if row[1]['v'] in (2,3) or self.now()>=row[1]['expires']:raise OperationExpired('result read window expired; do not silently rerun')
        life=self.accounts.lifecycle
        if life is not None and life.service_catalog:
            if not subject.startswith('apple-') or life.services.member(subject[6:],operation) is None:
                raise StateUnavailable('existing service operation lacks verified catalog membership')
        return row

    def prepare(self,installation,operation,image_sha256):
        _hex(image_sha256);account=self.accounts.resolve(installation);subject=account['subject']
        grant=None
        row=self.load(subject,operation)
        if row is None:
            now=self.now();grant=self.accounts.grant(account,now)
            quota=self.ledger.snapshot(subject,now//86400,grant)
            if quota['attempts']>=quota['attempt_limit']:raise UsageLimit('attempt_quota',(now//86400+1)*86400-now)
            if quota['remaining']==0:raise UsageLimit('success_quota',(now//86400+1)*86400-now)
            self.admit_operation(subject,operation)
            value=dict(v=1,subject_hash=digest(subject),operation=operation,day=now//86400,created=now,
                expires=now+RESULT_TTL,image_sha256=image_sha256,model=self.model_version,
                digest=digest(['service-only/v1',image_sha256,self.model_version]),worker=secrets.token_hex(16),
                result=None,result_sha256=None)
            self.store.compare_exchange(self.key(subject,operation),0,value)
        _,v=self._require(subject,operation)
        if v['image_sha256']!=image_sha256:raise UsageConflict('operation input changed')
        # Recovery can durably create the operation before its new-day ledger.
        # Initialization still requires the verified account and pinned cutover;
        # existing/corrupt/legacy rows cannot be reset by this retry.
        if v['day']==self.now()//86400:self.ledger._ensure_current_day(subject,v['day'])
        ticket=self.ticket(subject,v)
        try:self.ledger.receipt(ticket)
        except UsageConflict:
            if self.recovery is not None and v['day'] < self.now()//86400:
                return self.status(installation,operation)
            # Reuse only this request's still-valid verified snapshot. A slow
            # preflight must refresh it; a retry must never reuse a stored grant.
            if grant is not None:
                try:grant.validate(subject,self.now())
                except UsageConflict:grant=None
            if grant is None:grant=self.accounts.grant(account,self.now())
            self.ledger.reserve(subject,v['day'],operation,v['digest'],grant)
        return self.status(installation,operation)

    def status(self,installation,operation):
        account=self.accounts.resolve(installation);subject=account['subject'];_,v=self._require(subject,operation)
        ticket=self.ticket(subject,v)
        try:receipt=self.ledger.active_receipt(ticket)
        except UsageConflict:
            if self.recovery is not None and v['day'] < self.now()//86400:
                return self.recovery.offer(subject,v)
            return dict(state='awaiting_reservation',operation=operation,day=v['day'],retry_action='prepare',research_stored=False)
        if v['result'] is not None:
            outcome='succeeded' if v['result']['wound_polygons'] else 'empty'
            receipt=self.ledger.settle(ticket,v['worker'],outcome,v['result_sha256'])
        elif receipt['outcome'] in ('succeeded','empty','failed'):
            raise StateUnavailable('result receipt mismatch')
        state={'reserved':'ready','running':'pending','succeeded':'completed','empty':'completed','cancelled':'cancelled'}[receipt['outcome']]
        reply = dict(state=state,operation=operation,day=v['day'],receipt=receipt,result=v['result'],
                    result_json=None if v['result'] is None else json.dumps(v['result'],sort_keys=True,separators=(',', ':'),allow_nan=False),
                    retry_action='run' if state=='ready' else 'status' if state=='pending' else 'none',
                    research_stored=False,service_result_stored=v['result'] is not None,result_expires=v['expires'])
        if state == 'cancelled':
            reply['cancel_reason'] = ('reservation_expired' if self.ledger.expired_reservation_proof(ticket)
                else 'preflight_rejected' if receipt['result_sha256']==digest(['preflight_rejected',v['image_sha256']])
                else 'cancelled_unknown')
        self.accounts.require_active(account)
        return reply

    def run(self,installation,operation,raw):
        account=self.accounts.resolve(installation);subject=account['subject'];_,v=self._require(subject,operation)
        if type(raw) is not bytes or hashlib.sha256(raw).hexdigest()!=v['image_sha256']:raise UsageConflict('image hash mismatch')
        ticket=self.ticket(subject,v)
        if v['result'] is not None:return self.status(installation,operation)
        try:receipt=self.ledger.active_receipt(ticket)
        except UsageConflict:
            if self.recovery is not None and v['day'] < self.now()//86400:
                return self.status(installation,operation)
            raise
        if receipt['outcome']!='reserved':return self.status(installation,operation)
        if v['model']!=self.model_version:raise UsageConflict('prepared model changed; explicit reconciliation required')
        try:rgb=self.inference.decode(raw)
        except ImageRejected:
            self.ledger.cancel_unstarted(ticket,digest(['preflight_rejected',v['image_sha256']]))
            raise
        if not self.ledger.claim(ticket,v['worker']):return self.status(installation,operation)
        # Any unexpected model or persistence exception remains pending. No
        # second executor, automatic timeout release or guessed failure outcome.
        self.accounts.require_active(account)
        result=self.inference.infer(rgb);result_hash=digest(result)
        self.accounts.require_active(account)
        for _ in range(CAS_ATTEMPTS):
            revision,current=self._require(subject,operation)
            if current['result'] is not None:
                if current['result_sha256']!=result_hash:raise StateUnavailable('result conflict')
                break
            current['result']=result;current['result_sha256']=result_hash
            if self.store.compare_exchange(self.key(subject,operation),revision,current):break
        else:raise StateUnavailable('result commit contention')
        return self.status(installation,operation)
