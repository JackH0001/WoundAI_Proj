"""Withdrawal-safe consent ordering using immutable, create-only sequence slots.

The storage CAS is put_json_immutable (GCS if_generation_match=0), not a
process lock or wall clock. Restores are conditional on a previously observed
revision; they never rebase. Withdrawals may advance over a competing restore.
Existing JSONL history is sealed on first mutation and never overwritten.
"""
import hashlib
import json
import re
from store import ImmutableConflict, LocalStore

PROTOCOL = 'medical-consent/2'
ROOT = 'receipts/medical_consent'
EVENTS = 'receipts/medical_consent_events'

class ConsentOrderError(ValueError):
    def __init__(self, code, status=409):
        self.code, self.status = code, status
        super().__init__(code)

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()

def subject(code):
    if not isinstance(code, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', code):
        raise ConsentOrderError('invalid_consent_code', 400)
    return hashlib.sha256(code.encode()).hexdigest()

def legacy_state(rows, code):
    relevant = [r for r in rows if r.get('code') == code]
    blocked, images = False, set()
    for row in relevant:
        ids = set(row.get('image_ids') or [])
        if row.get('image_id'): ids.add(row['image_id'])
        images |= ids
        blocked = row.get('action', 'withdraw') != 'restore'
    return relevant, blocked, sorted(images)

class ConsentOrder:
    def __init__(self, store, legacy):
        self.store, self.legacy = store, legacy

    def _keys(self, prefix):
        keys = self.store.list_keys(prefix)
        # LocalStore publishes a flushed .immutable-* temporary via hard link.
        # Only published names are ledger entries; GCS has no temporary objects.
        return [k for k in keys if not (isinstance(self.store,LocalStore) and k.rsplit('/',1)[-1].startswith('.immutable-'))]

    def _root(self, code, org):
        key = ROOT + '/' + subject(code) + '.json'
        rows, blocked, images = legacy_state(self.legacy(), code)
        root = self.store.get_json(key)
        if root is None:
            root = dict(protocol=PROTOCOL, code=code, org=org, seq=0,
                action='withdraw' if blocked else 'restore', image_ids=images,
                legacy_digest=digest(rows))
        if (root.get('protocol') != PROTOCOL or root.get('code') != code
                or root.get('org') != org or type(root.get('seq')) is not int or root.get('seq') != 0
                or root.get('action') not in {'restore','withdraw'}
                or not self._images_valid(root.get('image_ids'))):
            raise ConsentOrderError('consent_scope_conflict', 409)
        # Mixed old/new backend writers or manual legacy edits are not silently
        # merged. A late legacy restore must never release a v2 withdrawal.
        if root.get('legacy_digest') != digest(rows):
            raise ConsentOrderError('consent_legacy_history_changed', 503)
        return key, root

    def history(self, code, org):
        key, root = self._root(code, org)
        chain = [root]
        prefix = EVENTS + '/' + subject(code)
        names = sorted(self._keys(prefix))
        if len(names) > 4096:
            raise ConsentOrderError('consent_history_limit', 503)
        for seq, name in enumerate(names, 1):
            if name != self._slot(code, seq):
                raise ConsentOrderError('consent_history_gap', 503)
            event = self.store.get_json(name)
            if (not isinstance(event,dict) or event.get('protocol') != PROTOCOL or event.get('code') != code
                    or event.get('org') != org or type(event.get('seq')) is not int or event.get('seq') != seq
                    or event.get('parent') != digest(chain[-1])
                    or event.get('action') not in {'restore','withdraw'}
                    or not self._images_valid(event.get('image_ids'))
                    or not isinstance(event.get('intent_id'), str)
                    or not re.fullmatch(r'[A-Za-z0-9-]{16,80}',event['intent_id'])
                    or not isinstance(event.get('actor'),str) or not event['actor'].startswith(org+':')):
                raise ConsentOrderError('consent_history_invalid', 503)
            chain.append(event)
        return key, chain

    @staticmethod
    def _images_valid(images):
        return (isinstance(images,list) and all(isinstance(i,str) and re.fullmatch(r'[0-9a-f]{16}',i) for i in images)
                and images == sorted(set(images)))

    @staticmethod
    def _slot(code, seq):
        return EVENTS + '/' + subject(code) + '/%020d.json' % seq

    def state(self, code, org):
        _, chain = self.history(code, org)
        return self._state(chain)

    @staticmethod
    def _state(chain):
        last = chain[-1]
        return dict(protocol=PROTOCOL, code=last['code'], org=last['org'],
            revision=digest(last), sequence=last['seq'],
            status='withdrawn' if last['action']=='withdraw' else 'restored',
            image_ids=sorted({i for e in chain for i in e['image_ids']}))

    def all_states(self):
        states = []
        for key in self._keys(ROOT):
            root = self.store.get_json(key)
            if root is None or not isinstance(root, dict):
                raise ConsentOrderError('consent_history_missing', 503)
            if key != ROOT + '/' + subject(root.get('code')) + '.json':
                raise ConsentOrderError('consent_history_invalid', 503)
            states.append(self.state(root['code'], root['org']))
        return states

    def apply(self, code, org, actor, action, intent_id, expected_revision, images, requested_image=None):
        if not self._images_valid(sorted(set(images))):
            raise ConsentOrderError('invalid_consent_images',400)
        if action not in {'withdraw','restore'} or not isinstance(intent_id,str) or not re.fullmatch(r'[A-Za-z0-9-]{16,80}',intent_id):
            raise ConsentOrderError('invalid_consent_intent',400)
        if not isinstance(actor,str) or not actor.startswith(org+':'):
            raise ConsentOrderError('consent_scope_conflict',403)
        if action=='restore' and (not isinstance(expected_revision,str) or not re.fullmatch(r'[0-9a-f]{64}',expected_revision)):
            raise ConsentOrderError('consent_revision_required',428)
        binding=dict(actor=actor, action=action, intent_id=intent_id,
                     expected_revision=expected_revision if action=='restore' else None,
                     requested_image=requested_image)
        for _ in range(16):
            root_key, chain = self.history(code,org)
            for old in chain[1:]:
                if old['intent_id']==intent_id:
                    if any(old.get(k)!=v for k,v in binding.items()):
                        raise ConsentOrderError('consent_intent_reused',409)
                    # Replay is read-only. Never execute media side effects again.
                    state=self._state(chain)
                    if state['revision']!=digest(old):
                        raise ConsentOrderError('consent_intent_superseded',409)
                    return dict(state, intent_id=intent_id, replayed=True)
            parent=digest(chain[-1])
            if action=='restore' and parent!=expected_revision:
                raise ConsentOrderError('consent_revision_conflict',409)
            root=chain[0]
            try: self.store.put_json_immutable(root_key,root)
            except ImmutableConflict: continue
            event=dict(protocol=PROTOCOL,code=code,org=org,seq=len(chain),parent=parent,
                       image_ids=sorted(set(images)|set(self._state(chain)['image_ids'])),**binding)
            try: self.store.put_json_immutable(self._slot(code,len(chain)),event)
            except ImmutableConflict:
                if action=='restore':
                    # Re-enter once to recognize a same-intent response-lost retry;
                    # the revision check above rejects any other winning intent.
                    continue
                continue
            state=self.state(code,org)
            if state['revision']!=digest(event):
                raise ConsentOrderError('consent_intent_superseded',409)
            return dict(state,intent_id=intent_id,replayed=False)
        raise ConsentOrderError('consent_order_busy',503)
