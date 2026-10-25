"""Read-only migration planning and additive, explicitly quiesced index apply.

Plans contain sensitive subject mappings: store them with the protected audit,
never in a public review bundle. No media or historical receipt is rewritten.
"""
import hashlib
import hmac
import re
from medical_consent_order import (ROOT, EVENTS, IMAGE_INDEX, INDEX_MARKER,
    INDEX_SCHEMA, MEDIA_OWNERS, MEDIA_IMAGE_OWNERS, digest, subject,
    record_media_owner, valid_image)
from medical_media_cleanup import MEDIA
from store import LocalStore, GcsStore

MIGRATIONS = 'receipts/medical_consent_migrations'
INPUTS = (ROOT, EVENTS, 'staging_meta/bind', 'receipts/care', 'receipts/promotion')


def leaves(store, prefix):
    if isinstance(store, LocalStore):
        from pathlib import Path
        base = Path(store._p(prefix))
        if base.is_symlink(): raise ValueError('migration source symlink refused')
        if not base.exists(): return []
        paths = sorted(base.rglob('*'))
        if any(p.is_symlink() for p in paths):
            raise ValueError('migration source symlink refused')
        return [str(p.relative_to(store.root)) for p in paths if p.is_file()
                and not p.name.startswith('.immutable-')]
    return sorted(store.list_keys(prefix))


def snapshot(order):
    store = order.store
    source = []
    for prefix in INPUTS:
        for key in leaves(store, prefix):
            data = store.get_blob(key)
            if data is None: raise ValueError('migration source disappeared')
            source.append({'key': key, 'sha256': hashlib.sha256(data).hexdigest()})
    media = []
    if isinstance(store, LocalStore):
        for prefix in MEDIA:
            for key in leaves(store, prefix):
                data = store.get_blob(key)
                if data is None: raise ValueError('migration media disappeared')
                media.append(dict(key=key, bytes=len(data), sha256=hashlib.sha256(data).hexdigest()))
    elif isinstance(store, GcsStore):
        # GCS generations identify immutable bytes. Include noncurrent and
        # recoverable generations too; do not download all research media.
        for prefix in MEDIA:
            _, bucket = store._target(prefix)
            for soft in (False, True):
                opts = {'soft_deleted': True} if soft else {'versions': True}
                for blob in store._client.list_blobs(bucket, prefix=store._k(prefix)+'/', **opts):
                    if type(blob.generation) is not int or blob.generation <= 0:
                        raise ValueError('missing migration media generation')
                    key = blob.name[len(store.prefix)+1:] if store.prefix else blob.name
                    media.append(dict(key=key, generation=blob.generation, bytes=blob.size, soft_deleted=soft))
    else: raise TypeError('unsupported migration store')
    return {'objects': source, 'legacy_sha256': digest(order.legacy()),
            'media_keys': sorted({x['key'] for x in media}),
            'media_versions': sorted(media, key=lambda x:(x['key'], x.get('generation', 0)))}


def verify_existing_indexes(order, states, owners):
    """Only exact, independently supported references may survive migration.

    Generated outputs are excluded from the input digest so interrupted applies
    can resume. Validate all of them instead, including unexpected extra rows.
    """
    expected = {}
    for state in states:
        for iid in state['image_ids']:
            expected[IMAGE_INDEX+'/'+iid+'/'+subject(state['code'])+'.json'] = dict(
                code=state['code'], org=state['org'], image_id=iid)
    for row in owners:
        value = dict(schema='medical-media-owner/1', **row)
        expected[MEDIA_OWNERS+'/'+subject(row['code'])+'/'+row['image_id']+'.json'] = value
        expected[MEDIA_IMAGE_OWNERS+'/'+row['image_id']+'/'+subject(row['code'])+'.json'] = value
    for prefix in (IMAGE_INDEX, MEDIA_OWNERS, MEDIA_IMAGE_OWNERS):
        for key in leaves(order.store, prefix):
            if key not in expected or order.store.get_json(key) != expected[key]:
                raise ValueError('unsupported or conflicting existing index: '+digest(key))
    marker = order.store.get_json(INDEX_MARKER)
    if marker is not None and marker != {'schema': INDEX_SCHEMA}:
        raise ValueError('invalid existing completion marker')


def plan_migration(order, care_keys=None):
    """Resolve ownership from sealed histories or independently verified care.

    Unknown media and expired/missing care keys block completion. No queue
    guesses, manual override, timestamp invention or audit reset is offered.
    """
    from consent_staging import (CARE_SCHEMA, BIND_SCHEMA, valid_promotion,
                                CODE, ORG)
    before = snapshot(order)
    rows = {x['key']: order.store.get_json(x['key']) for x in before['objects']}
    states, owners, errors, scopes = [], {}, [], {}

    def own(code, org, iid):
        subject(code)
        if not ORG.fullmatch(str(org)) or not valid_image(iid):
            raise ValueError('invalid migration owner')
        if code in scopes and scopes[code] != org:
            raise ValueError('conflicting subject institution')
        scopes[code] = org
        owners[(code, org, iid)] = {'code': code, 'org': org, 'image_id': iid}

    roots = set()
    for key in sorted(k for k in rows if k.startswith(ROOT+'/')):
        root = rows[key]
        if not isinstance(root, dict) or key != ROOT+'/'+subject(root.get('code'))+'.json':
            raise ValueError('invalid migration root')
        state = order.state(root['code'], root['org'])
        if state['sequence'] == 0: raise ValueError('uncommitted root requires investigation')
        if not ORG.fullmatch(str(root['org'])): raise ValueError('invalid root institution')
        # A root without image IDs still fixes the subject's institution. A
        # staging receipt for another institution must not silently fill it.
        scopes[root['code']] = root['org']
        roots.add(subject(root['code'])); states.append(state)
        for iid in state['image_ids']: own(root['code'], root['org'], iid)
    for key in sorted(k for k in rows if k.startswith(EVENTS+'/')):
        if key.split('/')[-2] not in roots:
            raise ValueError('orphan consent event requires investigation')
    # Do not label a deployment complete while historical subjects have no
    # sealed v2 chain. Their institution/timestamp must not be guessed here.
    for row in order.legacy():
        if not isinstance(row, dict) or subject(row.get('code')) not in roots:
            raise ValueError('unsealed legacy subject requires reviewed migration')
    for key in sorted(k for k in rows if k.startswith('receipts/promotion/')):
        iid = key.rsplit('/', 1)[-1].removesuffix('.json'); row = rows[key]
        if not valid_promotion(row, iid): raise ValueError('invalid promotion evidence')
        own(row['code'], row['org'], iid)
    for key in sorted(k for k in rows if k.startswith('staging_meta/bind/')):
        b = rows[key]; iid = key.rsplit('/', 1)[-1].removesuffix('.json')
        try:
            if (not isinstance(b, dict) or b.get('schema') != BIND_SCHEMA or b.get('image_id') != iid
                    or not valid_image(iid) or not re.fullmatch(r'[0-9a-f]{32}', str(b.get('jti')))):
                raise ValueError('invalid staging evidence')
            care = rows.get('receipts/care/'+b['jti']+'.json')
            if (not isinstance(care, dict) or care.get('schema') != CARE_SCHEMA
                    or care.get('jti') != b['jti'] or care.get('kid') != b.get('kid')
                    or not CODE.fullmatch(str(care.get('code')))
                    or not ORG.fullmatch(str(care.get('org')))):
                raise ValueError('missing care evidence')
            if care_keys is None: raise ValueError('care verification key required')
            expected = care_keys.subject(b['kid'], care['code'], care['org'])
            if not hmac.compare_digest(expected, str(b.get('subject_binding', ''))):
                raise ValueError('care binding mismatch')
            # Historical expiration is not new consent. This establishes only
            # ownership, never authorization to upload/train or restore media.
            own(care['code'], care['org'], iid)
        except (KeyError, ValueError, TypeError) as exc:
            errors.append({'source_sha256': digest(key), 'reason': str(exc)})
    known = {o['image_id'] for o in owners.values()}
    for key in before['media_keys']:
        part = key.split('/')[1]
        match = re.match(r'^([0-9a-f]{16})(?:[._/]|$)', part)
        if not match or match[1] not in known:
            errors.append({'source_sha256': digest(key), 'reason': 'unresolved media owner'})
    if snapshot(order) != before: raise ValueError('migration inputs changed')
    verify_existing_indexes(order, states, list(owners.values()))
    body = {'schema': 'medical-consent-migration/1', 'snapshot': before,
            'states': states, 'owners': sorted(owners.values(), key=lambda x:(x['code'],x['org'],x['image_id'])),
            'blockers': errors}
    return {**body, 'sha256': digest(body), 'apply_allowed': not errors}


def apply_migration(order, plan, expected_sha256, *, writers_paused=False, care_keys=None):
    if not writers_paused: raise ValueError('all writers paused and drained required')
    if not isinstance(plan, dict) or plan.get('sha256') != expected_sha256:
        raise ValueError('migration plan digest mismatch')
    current = plan_migration(order, care_keys)
    if current != plan or not current['apply_allowed']:
        raise ValueError('migration plan stale, modified or blocked')
    key = MIGRATIONS+'/'+expected_sha256
    order.store.put_json_immutable(key+'.intent.json', plan)
    for state in plan['states']:
        order.index_images(state['code'], state['org'], state['image_ids'])
    for row in plan['owners']:
        record_media_owner(order.store, row['code'], row['org'], row['image_id'])
    if snapshot(order) != plan['snapshot']:
        raise ValueError('migration inputs changed before completion')
    for state in plan['states']:
        for iid in state['image_ids']:
            expected = dict(code=state['code'], org=state['org'], image_id=iid)
            if order.store.get_json(IMAGE_INDEX+'/'+iid+'/'+subject(state['code'])+'.json') != expected:
                raise ValueError('consent index readback mismatch')
    for row in plan['owners']:
        expected = dict(schema='medical-media-owner/1', **row)
        for path in (MEDIA_OWNERS+'/'+subject(row['code'])+'/'+row['image_id']+'.json',
                     MEDIA_IMAGE_OWNERS+'/'+row['image_id']+'/'+subject(row['code'])+'.json'):
            if order.store.get_json(path) != expected: raise ValueError('ownership readback mismatch')
    verify_existing_indexes(order, plan['states'], plan['owners'])
    result = {'schema':'medical-consent-migration-result/1', 'plan_sha256':expected_sha256,
              'subjects':len(plan['states']), 'owners':len(plan['owners']), 'status':'completed'}
    order.store.put_json_immutable(key+'.verified.json', result)
    order.store.put_json_immutable(INDEX_MARKER, {'schema': INDEX_SCHEMA})
    return result
