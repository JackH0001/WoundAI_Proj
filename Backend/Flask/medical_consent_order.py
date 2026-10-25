"""Withdrawal-safe consent ordering using immutable, create-only sequence slots.

The storage CAS is put_json_immutable (GCS if_generation_match=0), not a
process lock or wall clock. Restores are conditional on a previously observed
revision; they never rebase. Withdrawals may advance over a competing restore.
Existing JSONL history is sealed on first mutation and never overwritten.
"""
import hashlib
import json
import re
import time
from store import ImmutableConflict, LocalStore

PROTOCOL = 'medical-consent/2'
ROOT = 'receipts/medical_consent'
EVENTS = 'receipts/medical_consent_events'
IMAGE_INDEX = 'receipts/medical_consent_images'
INDEX_MARKER = 'receipts/medical_consent_index/v1.json'
INDEX_SCHEMA = 'medical-consent-image-index/1'
MEDIA_OWNERS = 'receipts/medical_media_owners'
MEDIA_IMAGE_OWNERS = 'receipts/medical_media_image_owners'

def valid_image(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{16}', value) is not None

def split_images(values):
    valid, rejected = set(), []
    for value in values:
        if valid_image(value): valid.add(value)
        else:
            # Old JSONL may contain Python's non-finite JSON extension. Keep
            # only a deterministic evidence hash; it must not block withdrawal.
            raw=json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',', ':'),allow_nan=True).encode()
            rejected.append(hashlib.sha256(raw).hexdigest())
    return sorted(valid), sorted(rejected)


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
        ids = list(row.get('image_ids') or [])
        if row.get('image_id'): ids.append(row['image_id'])
        images.update(split_images(ids)[0])
        blocked = row.get('action', 'withdraw') != 'restore'
    return relevant, blocked, sorted(images)

def record_media_owner(store, code, org, image_id):
    """Call only after care token / immutable image binding verification."""
    token=subject(code)
    if not valid_image(image_id) or not isinstance(org,str) or not org:
        raise ConsentOrderError('invalid_media_owner',400)
    value=dict(schema='medical-media-owner/1',code=code,org=org,image_id=image_id)
    # Both directions precede the final admission guard and first media write.
    store.put_json_immutable(MEDIA_IMAGE_OWNERS+'/'+image_id+'/'+token+'.json',value)
    store.put_json_immutable(MEDIA_OWNERS+'/'+token+'/'+image_id+'.json',value)


class ConsentOrder:
    def __init__(self, store, legacy, now=None):
        self.store, self.legacy = store, legacy
        self.now = now or time.time

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
        if (not isinstance(root, dict) or root.get('protocol') != PROTOCOL or root.get('code') != code
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
                    or event.get('action') not in {'restore','withdraw','purge'}
                    or not self._images_valid(event.get('image_ids'))
                    or ('committed_at' in event and (type(event['committed_at']) is not int or event['committed_at']<0))
                    or (event.get('action') == 'purge' and (event.get('expected_revision') != event.get('parent') or type(event.get('committed_at')) is not int))
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
            status='restored' if last['action']=='restore' else 'withdrawn',
            image_ids=sorted({i for e in chain for i in e['image_ids']}),
            purged_image_ids=sorted({i for e in chain if e['action']=='purge' for i in e['image_ids']}),
            committed_at=last.get('committed_at'))

    def ensure_index(self, create=False):
        marker = self.store.get_json(INDEX_MARKER)
        if marker is not None:
            if marker != {'schema': INDEX_SCHEMA}:
                raise ConsentOrderError('consent_index_invalid', 503)
            return
        if self._keys(ROOT):
            # Existing v2 candidates need an offline, writes-paused backfill.
            # Never infer "no references" from an index that was not completed.
            raise ConsentOrderError('consent_index_migration_required', 503)
        if create:
            self.store.put_json_immutable(INDEX_MARKER, {'schema': INDEX_SCHEMA})

    def index_images(self, code, org, images):
        for iid in images:
            self.store.put_json_immutable(IMAGE_INDEX+'/'+iid+'/'+subject(code)+'.json',
                                         dict(code=code, org=org, image_id=iid))

    def image_subjects(self, iid):
        if not valid_image(iid): return []
        self.ensure_index()
        out=[]
        for prefix in (IMAGE_INDEX,MEDIA_IMAGE_OWNERS):
            for key in self._keys(prefix+'/'+iid):
                row=self.store.get_json(key)
                if (not isinstance(row,dict) or row.get('image_id')!=iid
                        or not isinstance(row.get('org'),str)
                        or key!=prefix+'/'+iid+'/'+subject(row.get('code'))+'.json'):
                    raise ConsentOrderError('consent_image_index_invalid',503)
                out.append(dict(row,consent_root_required=prefix==IMAGE_INDEX))
        return out

    def owned_images(self, code, org, tolerate_faults=False):
        images, faults = set(), []
        prefix=MEDIA_OWNERS+'/'+subject(code)
        for key in self._keys(prefix):
            try:
                row=self.store.get_json(key)
                if (not isinstance(row,dict) or row.get('schema')!='medical-media-owner/1'
                        or row.get('code')!=code or row.get('org')!=org
                        or not valid_image(row.get('image_id'))
                        or key!=prefix+'/'+row['image_id']+'.json'):
                    raise ConsentOrderError('media_owner_invalid',503)
                images.add(row['image_id'])
            except (ValueError,TypeError,KeyError,IOError):
                if not tolerate_faults: raise
                faults.append(digest(key))
        return images,faults

    def safe_state(self, code, org=None, require_root=False):
        """Fail closed for this code, without poisoning unrelated subjects."""
        try:
            root = self.store.get_json(ROOT+'/'+subject(code)+'.json')
            if root is None and require_root:
                raise ConsentOrderError('consent_referenced_root_missing',503)
            scope = org or (root.get('org') if isinstance(root,dict) else 'default')
            state=self.state(code, scope)
            if require_root and state['sequence']==0:
                raise ConsentOrderError('consent_index_uncommitted',503)
            return state
        except (ConsentOrderError, ValueError, TypeError, KeyError, IOError) as exc:
            return dict(code=code, org=org, status='fault', image_ids=[],
                        purged_image_ids=[], fault=getattr(exc,'code','consent_history_unreadable'))

    def all_states(self, known_codes=()):
        # Bulk export only. A malformed root filename cannot identify a subject;
        # its hash is still matched to queue/legacy codes for exclusion.
        known = {subject(code):code for code in known_codes if isinstance(code,str) and re.fullmatch(r'[A-Za-z0-9_-]{1,80}',code)}
        states=[]
        for key in self._keys(ROOT):
            token=key.rsplit('/',1)[-1].removesuffix('.json')
            try:
                root=self.store.get_json(key)
                code=root.get('code') if isinstance(root,dict) else None
                if key!=ROOT+'/'+subject(code)+'.json':
                    raise ConsentOrderError('consent_history_invalid',503)
                states.append(self.safe_state(code,root.get('org'),require_root=True))
            except (ConsentOrderError,ValueError,TypeError,KeyError,IOError):
                states.append(dict(code=known.get(token),subject_hash=token,status='fault',
                                   image_ids=[],purged_image_ids=[],fault='consent_root_unreadable'))
        seen={subject(s['code']) for s in states if s.get('code')}
        seen.update(s['subject_hash'] for s in states if s.get('subject_hash'))
        # Root-loss recovery must not release exports merely because the root
        # is no longer in a listing. Index filenames preserve its affected IDs.
        names=self._keys(IMAGE_INDEX)
        if isinstance(self.store,LocalStore):
            names=[leaf for directory in names for leaf in self._keys(directory)]
        for name in names:
            match=re.fullmatch(re.escape(IMAGE_INDEX)+r'/([0-9a-f]{16})/([0-9a-f]{64})\.json',name)
            if match and match[2] not in seen:
                states.append(dict(code=known.get(match[2]),subject_hash=match[2],status='fault',
                    image_ids=[match[1]],purged_image_ids=[],fault='consent_index_orphan'))
        return states

    def apply(self, code, org, actor, action, intent_id, expected_revision, images, requested_image=None, rejected_image_digests=()):
        images, invalid = split_images(images)
        rejected_image_digests = sorted(set(rejected_image_digests) | set(invalid))
        if invalid and action != 'withdraw':
            raise ConsentOrderError('invalid_consent_images',400)
        self.ensure_index(create=True)
        if action not in {'withdraw','restore','purge'} or not isinstance(intent_id,str) or not re.fullmatch(r'[A-Za-z0-9-]{16,80}',intent_id):
            raise ConsentOrderError('invalid_consent_intent',400)
        if not isinstance(actor,str) or not actor.startswith(org+':'):
            raise ConsentOrderError('consent_scope_conflict',403)
        if action in {'restore','purge'} and (not isinstance(expected_revision,str) or not re.fullmatch(r'[0-9a-f]{64}',expected_revision)):
            raise ConsentOrderError('consent_revision_required',428)
        binding=dict(actor=actor, action=action, intent_id=intent_id,
                     expected_revision=expected_revision if action in {'restore','purge'} else None,
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
            if action in {'restore','purge'} and parent!=expected_revision:
                raise ConsentOrderError('consent_revision_conflict',409)
            if action=='purge' and chain[-1]['action']!='withdraw':
                raise ConsentOrderError('consent_purge_not_withdrawn',409)
            owned, owner_faults=self.owned_images(code,org,tolerate_faults=action=='withdraw')
            images=sorted(set(images)|owned)
            rejected_image_digests=sorted(set(rejected_image_digests)|set(owner_faults))
            root=chain[0]
            # Publish reverse references before the state event. A failed writer
            # may leave an extra reference, never a missing committed exclusion.
            self.index_images(code,org,sorted(set(images)|set(self._state(chain)['image_ids'])))
            try: self.store.put_json_immutable(root_key,root)
            except ImmutableConflict: continue
            event=dict(protocol=PROTOCOL,code=code,org=org,seq=len(chain),parent=parent,
                       image_ids=sorted(set(images)|set(self._state(chain)['image_ids'])),
                       committed_at=int(self.now()), rejected_image_digests=rejected_image_digests, **binding)
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
