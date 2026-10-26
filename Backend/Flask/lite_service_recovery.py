"""Opt-in G3 recovery; immutable authorization before exposing a new operation.

The parent must still be in its read window and have no reservation/result.
An original-day CAS fence defeats paused old reservations. All authorization,
operation and daily-ledger rows are retained; no data/receipt is deleted.
"""
import copy
import hashlib
import secrets
from lite_attest_state import StateUnavailable, _encode
from lite_usage_ledger import UsageConflict, digest, _hex, _subject
from lite_service_operations import RESULT_TTL


def _new_id():return secrets.token_hex(16)


class ServiceRecovery:
    def __init__(self, operations, store):
        if operations.ledger.cutover is None:
            raise ValueError('recovery requires pinned cutover policy')
        self.ops, self.store = operations, store

    @staticmethod
    def key(subject, parent):
        return hashlib.sha256(('lite/service-recovery/1\0'+_subject(subject)+'\0'+_hex(parent,32)).encode()).digest()

    def _read(self, subject, parent):
        row=self.store.load(self.key(subject,parent['operation']))
        if row is None:return None
        v=row[1]
        try:
            if (type(v) is not dict or set(v)!={'v','subject_hash','parent','fence','successor'}
                    or type(v['v']) is not int or v['v']!=1
                    or v['subject_hash']!=digest(subject)
                    or v['parent']!={k:parent[k] for k in ('operation','day','digest','expires','image_sha256','model')}):
                raise ValueError('parent mismatch')
            _hex(v['fence'])
            new=v['successor']
            expected={'v','subject_hash','operation','day','created','expires','image_sha256','model','digest','worker','result','result_sha256'}
            if (type(new) is not dict or set(new)!=expected or type(new['v']) is not int or new['v']!=1
                    or new['subject_hash']!=digest(subject)
                    or new['image_sha256']!=parent['image_sha256'] or new['model']!=parent['model']
                    or new['digest']!=parent['digest'] or new['operation']==parent['operation']
                    or any(type(new[k]) is not int or new[k]<0 for k in ('day','created','expires'))
                    or new['created']//86400!=new['day'] or new['day']<=parent['day']
                    or not parent['created']<=new['created']<parent['expires']
                    or new['created']>self.ops.now() or new['expires']!=new['created']+RESULT_TTL
                    or new['result'] is not None or new['result_sha256'] is not None):
                raise ValueError('successor mismatch')
            _hex(new['operation'],32);_hex(new['worker'],32)
            if v['fence']!=self.ops.ledger._fence_proof(self.ops.ticket(subject,parent)):
                raise ValueError('fence mismatch')
            _encode(v)
        except (ValueError,TypeError,KeyError) as exc:
            raise StateUnavailable('recovery authorization corrupt; never replace') from exc
        return copy.deepcopy(v)

    def offer(self, subject, parent):
        # Status does not mutate or infer absence. The recover endpoint must win
        # the original-day fence, or return the original ticket if a writer won.
        if parent['result'] is not None:
            raise StateUnavailable('result without reservation; do not recover')
        existing=self._read(subject,parent)
        if existing is not None:
            return self._reply(subject,parent,existing)
        return dict(state='recovery_available',operation=parent['operation'],day=parent['day'],
                    retry_action='recover',research_stored=False)

    def _reply(self, subject, parent, value):
        # Read back the durable fence, including on replay. Missing/corrupt
        # ledger/control cannot authorize another inference.
        proof=self.ops.ledger.fence_missing(self.ops.ticket(subject,parent))
        if proof is None or proof!=value['fence']:
            raise StateUnavailable('parent fence no longer confirmed')
        new=value['successor']
        self.ops.store.compare_exchange(self.ops.key(subject,new['operation']),0,new)
        observed=self.ops.load(subject,new['operation'])
        if observed is None:raise StateUnavailable('successor creation unconfirmed')
        # Results may have progressed; immutable input/worker/time cannot change.
        if any(observed[1][k]!=new[k] for k in new if k not in ('v','result','result_sha256')):
            raise StateUnavailable('successor conflicts with retained operation')
        auth=dict(version=1,parent_operation=parent['operation'],parent_day=parent['day'],
            parent_request_digest=parent['digest'],image_sha256=parent['image_sha256'],
            fence_sha256=proof,successor_operation=new['operation'],successor_day=new['day'],
            authorization_sha256=digest(value))
        return dict(state='new_operation_required',operation=parent['operation'],day=parent['day'],
                    retry_action='recover',research_stored=False,recovery=auth)

    def recover(self, installation, operation, image_sha256):
        _hex(image_sha256)
        account=self.ops.accounts.resolve(installation);subject=account['subject']
        _,parent=self.ops._require(subject,operation)
        if parent['image_sha256']!=image_sha256:raise UsageConflict('recovery image changed')
        if parent['day']>=self.ops.now()//86400:raise UsageConflict('only an earlier day can recover')
        if parent['result'] is not None:return self.ops.status(installation,operation)
        ticket=self.ops.ticket(subject,parent)
        # Never cancel/replace an existing reservation, even when its reply was
        # lost. Pending, completed, lease-cancelled and uncertain stay original.
        fence=self.ops.ledger.fence_missing(ticket)
        if fence is None:return self.ops.status(installation,operation)
        value=self._read(subject,parent)
        if value is None:
            self.ops.ledger._admission()
            if parent['model']!=self.ops.model_version:
                raise UsageConflict('model changed; explicit reconciliation required')
            now=self.ops.now()
            if now>=parent['expires']:
                from lite_service_operations import OperationExpired
                raise OperationExpired('parent read window elapsed')
            new=dict(v=1,subject_hash=digest(subject),operation=_new_id(),
                day=now//86400,created=now,expires=now+RESULT_TTL,
                image_sha256=parent['image_sha256'],model=parent['model'],digest=parent['digest'],
                worker=_new_id(),result=None,result_sha256=None)
            value=dict(v=1,subject_hash=digest(subject),
                parent={k:parent[k] for k in ('operation','day','digest','expires','image_sha256','model')},
                fence=fence,successor=new)
            self.store.compare_exchange(self.key(subject,operation),0,value)
            value=self._read(subject,parent)
            if value is None:raise StateUnavailable('recovery write unconfirmed')
        return self._reply(subject,parent,value)

