#!/usr/bin/env python3
"""Prepare (default) or execute an explicitly approved isolated synthetic run.

No resource creation, IAM/policy change, audit deletion, application deployment
or automatic teardown. Do not run --execute without human cloud authorization.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'Backend/Flask'))
from medical_consent_smoke_scope import (PROJECT, EXECUTOR, MEDIA, AUDIT, PREFIX, CONTINUATION_PREFIX,
    BUCKETS, LIMITS, PROBE_PERMISSIONS, UBLA_UNSUPPORTED_PROBES,
    canonical, sha, manifest, validate_manifest,
    validate_environment, validate_bucket)
from store import GcsStore, _refuse_cloud_in_test_process
from medical_consent_order import ConsentOrder, ConsentOrderError
from medical_consent_migration import plan_migration, apply_migration
from medical_media_cleanup import cleanup_subject, inventory
from medical_consent_maintenance import cleanup_run

POLICY = dict(id='synthetic-smoke-zero-delay', retention_seconds=0)


def source_snapshot():
    from plan_medical_consent_operations import FILES
    def git(*args): return subprocess.check_output(['git', '-C', str(ROOT), *args], text=True).strip()
    if git('status', '--porcelain'): raise ValueError('commit a clean source before preparing/executing')
    return git('rev-parse', 'HEAD'), {f: hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in FILES}


class Budget:
    def __init__(self, clock=time.monotonic, expires_at=None):
        self.clock, self.start = clock, clock()
        self.expires_at = expires_at
        self.lock = threading.Lock()
        self.calls = self.writes = self.bytes = 0
        self.observed = set()
    def take(self, *, write=False, size=0):
        with self.lock:
            if (self.clock()-self.start >= LIMITS['max_seconds']
                    or (self.expires_at is not None and time.time() >= self.expires_at)
                    or self.calls >= LIMITS['max_sdk_calls']
                    or self.writes+int(write) > LIMITS['max_writes']
                    or self.bytes+size > LIMITS['max_upload_bytes']):
                raise RuntimeError('synthetic run budget exhausted; retain all objects')
            self.calls += 1; self.writes += int(write); self.bytes += size
    def summary(self):
        return dict(sdk_calls_or_list_pages=self.calls, attempted_writes=self.writes,
                    mutation_count_includes_deletes=True,
                    attempted_upload_bytes=self.bytes, elapsed_seconds=self.clock()-self.start,
                    http_request_count_verified=False)


def _options(): return dict(timeout=LIMITS['request_timeout'], retry=None)


class ScopedClient:
    """Capability boundary shared by two independent storage clients.

    Counts SDK calls/pages, disables SDK retries and pins deletions. Counts are
    NOT a billing/HTTP trace; credential refresh and transport can add requests.
    """
    def __init__(self, raw, budget, *, prefix=PREFIX):
        if prefix not in (PREFIX, CONTINUATION_PREFIX):
            raise PermissionError('unreviewed synthetic write prefix')
        self.raw, self.budget, self.prefix = raw, budget, prefix
    def bucket(self, name):
        if name not in BUCKETS: raise PermissionError('bucket outside synthetic scope')
        return ScopedBucket(self, self.raw.bucket(name))
    def key(self, bucket, name, listing=False):
        if bucket not in BUCKETS or not isinstance(name, str) or not name.startswith(self.prefix+'/'):
            raise PermissionError('object outside synthetic prefix')
        rel = name[len(self.prefix)+1:]
        parts = rel.rstrip('/').split('/') if listing else rel.split('/')
        allowed = {'receipts'} if bucket == AUDIT else {'images', 'depth_maps', 'wound_masks', 'tissue_masks', 'staging', 'quarantine', 'staging_meta'}
        if (not parts or parts[0] not in allowed or any(p in ('', '.', '..') for p in parts)
                or any(c in rel for c in ('\\', '%', '\x00'))):
            raise PermissionError('unapproved object namespace')
    def list_blobs(self, name, prefix='', **kw):
        self.key(name, prefix, listing=True)
        if set(kw)-{'versions', 'soft_deleted', 'max_results'}:
            raise PermissionError('unreviewed list option')
        requested=kw.get('max_results',LIMITS['max_listed_per_call']+1)
        if type(requested) is not int or not 1 <= requested <= LIMITS['max_listed_per_call']+1:
            raise PermissionError('unbounded list request')
        kw['max_results']=requested
        kw['page_size']=100
        self.budget.take()
        raw = self.raw.list_blobs(name, prefix=prefix, **kw, **_options())
        # Page iteration is lazy in the real SDK. Budget before fetching pages.
        # Google HTTPIterator.pages marks the iterator started on access.
        # hasattr(raw, 'pages') would acquire it once and the next access fails
        # before any page request. Read this stateful property exactly once.
        page_iterator = getattr(raw, 'pages', None)
        pages = iter(page_iterator) if page_iterator is not None else iter([raw])
        count = 0
        while True:
            self.budget.take()
            try: page = next(pages)
            except StopIteration: return
            for blob in page:
                count += 1
                if count > LIMITS['max_listed_per_call']: raise RuntimeError('list bound exceeded')
                self.key(name, blob.name)
                if kw.get('versions') and type(blob.generation) is int:
                    self.budget.observed.add((name, blob.name, blob.generation))
                yield ScopedBlob(self.bucket(name), blob)


class ScopedBucket:
    def __init__(self, client, raw): self.client, self.raw, self.name = client, raw, raw.name
    @property
    def _properties(self): return self.raw._properties
    def reload(self):
        self.client.budget.take(); self.raw.reload(**_options())
    def blob(self, name, generation=None):
        self.client.key(self.name, name)
        return ScopedBlob(self, self.raw.blob(name, generation=generation))
    def check(self):
        self.reload(); self.client.budget.take()
        policy = self.raw.get_iam_policy(requested_policy_version=3, **_options())
        self.client.budget.take()
        granted = self.raw.test_iam_permissions(sorted(PROBE_PERMISSIONS), **_options())
        validate_bucket(self.name, self._properties, list(policy.bindings), granted)
        return dict(metadata=dict(self._properties), bindings=[dict(b, members=sorted(b['members'])) for b in policy.bindings],
                    granted_permissions=sorted(granted),
                    permission_probe=dict(requested=sorted(PROBE_PERMISSIONS),
                        not_tested=sorted(UBLA_UNSUPPORTED_PROBES),
                        reason='Object ACL operations are disabled by required uniform bucket-level access; GCS rejects these probes.'))


class ScopedBlob:
    def __init__(self, bucket, raw): self.bucket, self.raw = bucket, raw
    @property
    def name(self): return self.raw.name
    @property
    def generation(self): return self.raw.generation
    @property
    def size(self): return self.raw.size
    def exists(self):
        self.bucket.client.budget.take(); return self.raw.exists(**_options())
    def download_as_bytes(self, **kw):
        if set(kw)-{'if_generation_match'}: raise PermissionError('unreviewed download option')
        self.bucket.client.budget.take(); data = self.raw.download_as_bytes(**kw, **_options())
        if len(data) > LIMITS['max_upload_bytes']: raise RuntimeError('unexpected object size')
        return data
    def upload_from_string(self, data, content_type=None, if_generation_match=None):
        if self.name.startswith(self.bucket.client.prefix+'/staging_meta/'):
            raise PermissionError('historical binding inventory is read-only')
        data = data.encode() if isinstance(data, str) else data
        if not isinstance(data, bytes): raise ValueError('generated bytes required')
        # All writes create-only, except an explicitly inventoried synthetic
        # media generation replacement used in the versioning scenario.
        key = (self.bucket.name, self.name, if_generation_match)
        if (type(if_generation_match) is not int or if_generation_match < 0
                or (if_generation_match and (self.bucket.name == AUDIT or key not in self.bucket.client.budget.observed))):
            raise PermissionError('write must be create-only or pinned synthetic media replacement')
        self.bucket.check()
        self.bucket.client.budget.take(write=True, size=len(data))
        self.raw.upload_from_string(data, content_type=content_type,
                                    if_generation_match=if_generation_match, **_options())
    def delete(self, if_generation_match=None):
        from medical_media_cleanup import media_key
        rel=self.name[len(self.bucket.client.prefix)+1:]
        image_id=rel.split('/')[1][:16] if '/' in rel else ''
        if (not media_key(rel,image_id) or self.bucket.name != MEDIA or type(if_generation_match) is not int
                or self.generation != if_generation_match
                or (MEDIA, self.name, self.generation) not in self.bucket.client.budget.observed):
            raise PermissionError('only inventoried pinned synthetic media can be deleted')
        self.bucket.check(); self.bucket.client.budget.take(write=True)
        self.raw.delete(if_generation_match=if_generation_match, **_options())


class SyntheticSmokeStore(GcsStore):
    """Explicit disposable-only adapter; never registered in get_store()."""
    audit_write_mode = 'isolated-synthetic-smoke-only'
    def __init__(self, client):
        # Intentionally accepts an already scoped client; no ambient ADC here.
        if not isinstance(client, ScopedClient): raise TypeError('scoped client required')
        self._client, self.prefix = client, client.prefix
        self._bucket_name, self._audit_bucket_name = MEDIA, AUDIT
        self._bucket, self._audit_bucket = client.bucket(MEDIA), client.bucket(AUDIT)
        self._security_bucket = None
    def require_audit_write_policy(self):
        self._audit_bucket.check()
    def append_chained(self, *args, **kw): raise PermissionError('smoke supports consent receipts only')
    def append_next_chained(self, *args, **kw): raise PermissionError('smoke supports consent receipts only')


def preflight(clients):
    """No object writes until both clients verify metadata, permissions, emptiness."""
    if any(c.prefix != PREFIX for c in clients):
        raise PermissionError('continuation requires its separate preservation preflight')
    evidence = []
    for client in clients:
        for name in BUCKETS:
            evidence.append(client.bucket(name).check())
            # Root listing is limited to proving these exact dedicated buckets
            # are empty, including noncurrent/soft versions. No body downloads.
            for options in ({'versions': True}, {'soft_deleted': True}):
                client.budget.take()
                rows = client.raw.list_blobs(name, max_results=1, **options, **_options())
                if next(iter(rows), None) is not None: raise PermissionError('disposable bucket is not empty; refuse reuse')
    return evidence


def scenarios(stores, run, record):
    """Same implementation runs against a real SDK or an offline SDK boundary."""
    a, b = stores
    if any(st.prefix != run['prefix'] for st in stores):
        raise PermissionError('scenario manifest/write scope mismatch')
    def order(st): return ConsentOrder(st, lambda: [])
    def iid(label): return hashlib.sha256((run['run_id']+label).encode()).hexdigest()[:16]
    def image(st, image_id, payload=b'synthetic RGB fixture'):
        st.put_blob_immutable('images/'+image_id+'.jpg', payload)
    def event(st, code, action, intent, revision=None, images=()):
        return order(st).apply(code, 'synthetic', 'synthetic:engineer', action, intent, revision, list(images))
    def check(name, condition, **details):
        record(dict(case=name, passed=bool(condition), **details))
        if not condition: raise AssertionError(name)
    # One constant create-only claim prevents different approved runs racing
    # between empty-bucket preflight and the first fixture write.
    claim='receipts/smoke_run/claim.json'
    a.put_json_immutable(claim, run)
    before = a.get_blob(claim)
    code='SYNTHETIC-RESTORE'; old=iid('restore'); image(a, old)
    withdrawn=event(a, code, 'withdraw', 'withdraw-restore-first-0001', images=[old])
    restored=event(b, code, 'restore', 'restore-before-cleanup-0001', withdrawn['revision'], [old])
    check('restore-before-cleanup', cleanup_subject(order(a), code, 'synthetic', POLICY, apply=True)['status']=='not_due' and a.exists('images/'+old+'.jpg'))
    try:
        event(a, code, 'restore', 'stale-restore-intent-000001', withdrawn['revision'], [old])
    except ConsentOrderError as exc:
        check('stale-restore-rejected', exc.status==409)
    else: check('stale-restore-rejected', False)
    # Equivalent retry must return the exact prior consent receipt.
    check('same-intent-replay', event(b, code, 'restore', 'restore-before-cleanup-0001', withdrawn['revision'], [old])==dict(restored,replayed=True))
    code='SYNTHETIC-RACE'; race_id=iid('restore-purge-race');image(a,race_id)
    withdrawn=event(a,code,'withdraw','withdraw-race-start-000001',images=[race_id])
    start=threading.Barrier(2)
    def contend(pair):
        st,action=pair;start.wait(timeout=10)
        try:return dict(action=action,receipt=event(st,code,action,'race-'+action+'-intent-00000001',withdrawn['revision'],[race_id]))
        except ConsentOrderError as exc:return dict(action=action,conflict=exc.status)
    with ThreadPoolExecutor(max_workers=2) as pool:
        contenders=list(pool.map(contend,[(a,'restore'),(b,'purge')]))
    state=order(a).state(code,'synthetic')
    winners=[r['action'] for r in contenders if 'receipt' in r]
    check('two-client-restore-purge-CAS',len(winners)==1 and state['sequence']==2
          and all(r.get('conflict',409)==409 for r in contenders),outcomes=contenders)
    cleanup_subject(order(b),code,'synthetic',POLICY,apply=True)
    check('race-winner-media-disposition',a.exists('images/'+race_id+'.jpg')==(winners[0]=='restore'))
    code='SYNTHETIC-PURGE'; old=iid('purge'); new=iid('renewed'); image(a, old)
    # Create an additional noncurrent version with an observed generation pin.
    items=inventory(a, old); first=items[0]
    a._bucket.blob(a._k(first['key'])).upload_from_string(b'synthetic replacement', if_generation_match=first['generation'])
    event(a, code, 'withdraw', 'withdraw-purge-first-000001', images=[old])
    barrier=threading.Barrier(2)
    def clean(st):
        barrier.wait(timeout=10)
        try: return dict(result=cleanup_subject(order(st), code, 'synthetic', POLICY, apply=True))
        except ConsentOrderError as exc: return dict(conflict=exc.code)
    with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(clean, stores))
    state=order(b).state(code, 'synthetic')
    check('overlap-single-purge-sequence', state['sequence']==2 and not inventory(a, old), outcomes=results)
    check('soft-versions-retained', len(inventory(a, old, soft_deleted=True))==2)
    event(b, code, 'restore', 'restore-after-purge-000001', state['revision'])
    image(b, new)
    event(b, code, 'restore', 'new-image-reconsent-000001', order(b).state(code,'synthetic')['revision'], [new])
    result=cleanup_subject(order(a),code,'synthetic',POLICY,apply=True)
    check('new-media-survives-old-purge', a.exists('images/'+new+'.jpg') and result['status']=='recoverable_media_pending')
    # Migration is additive; the already present marker is never removed to
    # manufacture an old state in live WORM/audit storage.
    plan=plan_migration(order(a)); check('migration-plan-resolved', plan['apply_allowed'])
    applied=apply_migration(order(a),plan,plan['sha256'],writers_paused=True)
    check('migration-readback', applied['status']=='completed' and a.get_blob(claim)==before)
    result=cleanup_run(order(a),POLICY,apply=True,run_id=run['run_id'],max_subjects=10)
    again=cleanup_run(order(b),POLICY,apply=True,run_id=run['run_id'],max_subjects=10)
    check('run-replay-same-result', again==dict(result,replayed=True))
    check('audit-claim-preserved', b.get_blob(claim)==before)
    record(dict(case='natural-soft-expiry', status='pending', reason='7-day retention exceeds this run; no clock substitution'))


def write_json(path, value):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as f: f.write(canonical(value)+b'\n')


def evidence_inventory(client):
    """Read back each synthetic generation; never restore soft-deleted bytes."""
    rows=[]
    for bucket_name in BUCKETS:
        namespaces = ('receipts',) if bucket_name == AUDIT else (
            'images', 'depth_maps', 'wound_masks', 'tissue_masks', 'staging', 'quarantine', 'staging_meta')
        bucket=client.bucket(bucket_name)
        bucket.check()
        for namespace in namespaces:
            for soft in (False,True):
                for blob in client.list_blobs(bucket_name, prefix=client.prefix+'/'+namespace+'/',
                        **({'soft_deleted':True} if soft else {'versions':True})):
                    row=dict(bucket=bucket_name,name=blob.name,generation=int(blob.generation),
                             bytes=blob.size,soft_deleted=soft)
                    if not soft:
                        data=bucket.blob(blob.name,generation=blob.generation).download_as_bytes(
                            if_generation_match=blob.generation)
                        row.update(sha256=hashlib.sha256(data).hexdigest(),bytes=len(data))
                    rows.append(row)
    return sorted(rows,key=lambda r:(r['bucket'],r['name'],r['generation']))


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--prepare',help='create a review manifest; no cloud access')
    p.add_argument('--execute',action='store_true'); p.add_argument('--manifest')
    p.add_argument('--expected-sha256'); p.add_argument('--evidence-dir')
    args=p.parse_args(argv)
    if args.prepare:
        if args.execute or args.manifest or args.expected_sha256 or args.evidence_dir: p.error('prepare cannot execute')
        commit,files=source_snapshot(); value=manifest(commit,files,'smoke-'+uuid.uuid4().hex[:24],int(time.time()))
        write_json(args.prepare,value); print(json.dumps(dict(manifest=args.prepare,sha256=sha(value),execution_authorized=False))); return 0
    if not (args.execute and args.manifest and args.expected_sha256 and args.evidence_dir): p.error('explicit manifest, digest, execute and new evidence directory required')
    value=validate_manifest(json.loads(Path(args.manifest).read_text()),args.expected_sha256)
    validate_environment(os.environ)
    commit,files=source_snapshot()
    if (commit,files)!=(value['source_commit'],value['source_files']): raise ValueError('reviewed source changed')
    _refuse_cloud_in_test_process()  # BEFORE credential discovery / SDK construction
    evidence=Path(args.evidence_dir); evidence.mkdir(mode=0o700,exist_ok=False)
    write_json(evidence/'manifest.json',value)
    (evidence/'progress.jsonl').touch(mode=0o600,exist_ok=False)
    budget=Budget(expires_at=value['expires_at']); outcomes=[]
    def record(row):
        outcomes.append(row)
        with open(evidence/'progress.jsonl','ab') as f:
            f.write(canonical(row)+b'\n'); f.flush(); os.fsync(f.fileno())
    status='blocked'; error=None
    try:
        import google.auth
        from google.cloud import storage
        credentials,_=google.auth.default(scopes=['https://www.googleapis.com/auth/devstorage.full_control'])
        if getattr(credentials,'service_account_email',None)!=EXECUTOR:
            raise PermissionError('exact dedicated executor credentials required; no user ADC')
        clients=[ScopedClient(storage.Client(project=PROJECT,credentials=credentials),budget) for _ in range(2)]
        write_json(evidence/'preflight.json',preflight(clients))
        scenarios([SyntheticSmokeStore(c) for c in clients],value,record)
        write_json(evidence/'object-readback.json',evidence_inventory(clients[1]))
        status='bounded-scenarios-passed-expiry-pending'
    except Exception as exc:
        error=dict(type=type(exc).__name__,http_status=getattr(exc,'code',None))
        # Preserve fixtures and evidence. Never auto-delete or reuse a failed run.
    write_json(evidence/'result.json',dict(status=status,error=error,outcomes=outcomes,
        counters=budget.summary(),live_gcs=True,audit_is_worm=False,teardown_performed=False))
    print(json.dumps(dict(status=status,evidence=str(evidence),error=error)))
    return 0 if status=='bounded-scenarios-passed-expiry-pending' else 2


if __name__=='__main__': raise SystemExit(main())
