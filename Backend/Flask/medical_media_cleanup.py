"""Policy-driven medical media cleanup, separate from logical withdrawal.

No scheduler is enabled by this module. Dry-run by default. Immutable purge
claims arbitrate against restores; a claimed image is never re-admitted even
if the patient later consents again. Raw bytes are touched only by explicit
apply after an approved policy and worker deployment. Audit/annotations remain.
"""
import hashlib
import json
import re
import time
from medical_consent_order import ConsentOrder, ConsentOrderError, digest, subject, valid_image
from store import LocalStore, GcsStore

MEDIA = ('images', 'quarantine', 'staging', 'tissue_masks', 'wound_masks', 'depth_maps')
RECEIPTS = 'receipts/medical_media_cleanup'


def media_key(key, iid):
    if not valid_image(iid) or not isinstance(key,str): return False
    parts=key.split('/')
    if len(parts)<2 or parts[0] not in MEDIA or any(p in ('','..','.') for p in parts): return False
    return parts[1]==iid or parts[1].startswith(iid+'.') or parts[1].startswith(iid+'_')


def inventory(store, iid, *, soft_deleted=False):
    """All known RGB/mask/depth layouts, including GCS noncurrent generations."""
    if isinstance(store,LocalStore):
        if soft_deleted: return []
        from pathlib import Path
        out=[]
        for folder in MEDIA:
            base=Path(store._p(folder))
            if base.exists():
                for p in base.rglob('*'):
                    key=str(p.relative_to(store.root))
                    if p.is_file() and media_key(key,iid):
                        if p.is_symlink(): raise ValueError('media symlink is not an approved purge target')
                        raw=p.read_bytes()
                        out.append(dict(key=key, sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw)))
        return sorted(out,key=lambda r:r['key'])
    if isinstance(store,GcsStore):
        out=[]
        for folder in MEDIA:
            _,bucket=store._target(folder)
            prefix=store._k(folder)+'/'+iid
            options={'soft_deleted':True} if soft_deleted else {'versions':True}
            for blob in store._client.list_blobs(bucket,prefix=prefix,**options):
                key=blob.name[len(store.prefix)+1:] if store.prefix else blob.name
                if media_key(key,iid):
                    if type(blob.generation) is not int or blob.generation<=0:
                        raise ValueError('missing object generation')
                    out.append(dict(key=key,generation=blob.generation,bytes=blob.size))
        return sorted(out,key=lambda r:(r['key'],r['generation']))
    raise TypeError('unsupported medical media store')


def _delete(store, item):
    key=item['key']
    if store._is_audit(key): raise PermissionError('audit is not media')
    if isinstance(store,LocalStore):
        # Immutable media writers never replace differing bytes. Do not delete
        # a file replaced outside that contract after the audited inventory.
        data=store.get_blob(key)
        if data is not None and hashlib.sha256(data).hexdigest()!=item['sha256']:
            raise IOError('media changed after purge inventory')
        store.delete(key)
    elif isinstance(store,GcsStore):
        from google.api_core.exceptions import NotFound
        bucket,_=store._target(key)
        try:
            bucket.blob(store._k(key),generation=item['generation']).delete(if_generation_match=item['generation'])
        except NotFound: pass
    else: raise TypeError('unsupported medical media store')


def require_delete_policy(store):
    if isinstance(store,GcsStore):
        store._bucket.reload()
        metadata=store._bucket._properties
        if metadata.get('retentionPolicy') or metadata.get('defaultEventBasedHold'):
            raise PermissionError('media retention/hold requires separate disposition review')
        return int((metadata.get('softDeletePolicy') or {}).get('retentionDurationSeconds',0))
    return 0


def require_soft_inventory(store):
    """Prove recoverable-version listing is usable BEFORE claiming disposal.

    GCS currently returns 400 when the bucket's soft-delete policy is zero.
    Disabling that policy does not establish absence of older retained versions.
    Do not change the policy, skip verification, or delete first and fail later.
    """
    if isinstance(store, GcsStore):
        from google.api_core.exceptions import BadRequest
        _, bucket = store._target('images')
        try:
            probe = store._client.list_blobs(bucket, prefix=store._k('images')+'/',
                                           soft_deleted=True, max_results=1)
            next(iter(probe), None)  # listing is lazy in the real SDK
        except BadRequest as exc:
            raise PermissionError('soft-deleted inventory unavailable; disposition policy review required') from exc


def validate_policy(policy):
    if (not isinstance(policy,dict) or set(policy)!={'id','retention_seconds'}
            or not isinstance(policy['id'],str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',policy['id'])
            or type(policy['retention_seconds']) is not int or policy['retention_seconds']<0):
        raise ValueError('explicit approved retention policy required')


def cleanup_subject(order, code, org, policy, *, apply=False, now=None):
    """Repeatable worker unit; errors never produce a completion receipt.

    Policy must be supplied by an operator, not a request/body or nominal app
    default. This function does not change any bucket policy or job schedule.
    """
    now=int(time.time() if now is None else now)
    validate_policy(policy)
    _,chain=order.history(code,org)
    state=order._state(chain)
    due=(state['status']=='withdrawn' and chain[-1]['action']=='withdraw'
         and type(state['committed_at']) is int and now>=state['committed_at']+policy['retention_seconds'])
    owned,_=order.owned_images(code,org)
    images=set(state['purged_image_ids'])
    if due: images.update(set(state['image_ids'])|owned)
    result=dict(code=code,status='dry_run',due=due,image_count=len(images),policy=policy,
                media=[dict(image_id=i,objects=inventory(order.store,i)) for i in sorted(images)])
    if chain[-1]['action']=='withdraw' and type(state['committed_at']) is not int:
        return dict(result,status='blocked_missing_timestamp')
    if not apply: return result
    soft_seconds=require_delete_policy(order.store)
    if due or images: require_soft_inventory(order.store)
    if due:
        # CAS makes a restore winning first prevent deletion. A purge winning
        # first remains a permanent image disposition, even after later consent.
        receipt=order.apply(code,org,org+':retention-worker','purge',
            'purge-'+digest([state['revision'],policy])[:40],state['revision'],state['image_ids'])
        images.update(receipt['purged_image_ids'])
    if not images: return dict(result,status='not_due')
    # Resume old claims too, including after restores/crashes. Re-scan forever:
    # an upload already in flight at withdrawal can leave late orphan media.
    jobs=[]
    recoverable=False
    for iid in sorted(images):
        items=inventory(order.store,iid)
        job=dict(schema='medical-media-disposition/1',code=code,org=org,image_id=iid,
                 policy=policy,objects=items,checked_at=now)
        key=RECEIPTS+'/'+subject(code)+'/'+digest(job)
        order.store.put_json_immutable(key+'.intent.json',job)
        for item in items: _delete(order.store,item)
        if inventory(order.store,iid): raise IOError('media remains; keep cleanup pending')
        # Current policy does not describe older retained versions. Disabling
        # soft delete never shortens an existing object's original expiry.
        soft=inventory(order.store,iid,soft_deleted=True)
        recoverable=bool(soft) or recoverable
        complete=dict(job,status='recoverable_media_pending' if soft else 'media_versions_absent_at_check',
                      soft_delete_seconds=soft_seconds,soft_deleted_objects=soft,
                      retained='consent/audit/annotation metadata; backups/exports/models outside scope')
        outcome_key=key+'.'+digest(complete)+'.outcome.json'
        order.store.put_json_immutable(outcome_key,complete)
        jobs.append(outcome_key)
    return dict(result,status='recoverable_media_pending' if recoverable else 'media_versions_absent_at_check',receipts=jobs)
