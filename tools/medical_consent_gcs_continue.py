#!/usr/bin/env python3
"""Review-only preparation or explicitly authorized synthetic continuation.

The original first-run tool still refuses nonempty buckets. This separate entry
point reads two exact preserved objects and writes only a new fixed namespace.
It neither provisions resources nor changes IAM/policies, restores soft bytes,
deletes audit, retries a failed run, or discovers patient data.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time
import traceback
import uuid

from medical_consent_smoke_scope import (PROJECT, EXECUTOR, MEDIA, AUDIT, BUCKETS,
    PREFIX, CONTINUATION_PREFIX, LIMITS, canonical, sha, manifest,
    validate_manifest, validate_environment)
from medical_consent_gcs_smoke import (ROOT, Budget, ScopedClient, SyntheticSmokeStore,
    source_snapshot, scenarios, evidence_inventory, write_json, _options,
    _refuse_cloud_in_test_process)

# Expected bytes are derived from the stopped 7df6037 run, not a claim that
# post-failure inventory has already been performed. Generations MUST be read.
PRESERVED = (
    (AUDIT, PREFIX+'/receipts/smoke_run/claim.json', 2308,
     'f762933d2bf169b63ef773d27645fe86dd717d04c8ff46ade89872b4ea36d18b'),
    (MEDIA, PREFIX+'/images/caa426f79aac9259.jpg', 21,
     'f7d958ee0fd3857df9bb5b6fabc25107ad545354251d40f65b1e26fcc6180a95'),
)


def continuation_manifest(commit, files, run_id, created_at):
    value=manifest(commit,files,run_id,created_at)
    value.update(schema='medical-consent-gcs-continuation/1',prefix=CONTINUATION_PREFIX,
        preserved_objects=[dict(bucket=b,name=n,bytes=z,sha256=h) for b,n,z,h in PRESERVED],
        preserved_generation_policy='read-and-pin-before-writes; exact-match-after',
        previous_source='7df6037487a251a30a4737a310ee040747fbe1bf',
        previous_run_id='smoke-8714863703524a81bbedfd90')
    return value


def validate_continuation(value, expected_sha256, now=None):
    if not isinstance(value,dict) or sha(value)!=expected_sha256:
        raise ValueError('reviewed continuation digest mismatch')
    common={k:v for k,v in value.items() if k not in
            {'preserved_objects','preserved_generation_policy','previous_source','previous_run_id'}}
    common.update(schema='medical-consent-gcs-smoke/1',prefix=PREFIX)
    validate_manifest(common,sha(common),now)
    expected=continuation_manifest(common['source_commit'],common['source_files'],
                                   common['run_id'],common['created_at'])
    if value!=expected or value['run_id']==value['previous_run_id']:
        raise ValueError('continuation scope differs or old run ID reused')
    return value


def root_versions(client, bucket, *, soft=False):
    """Bounded READ-ONLY exception: inventory the two dedicated buckets.

    Never return an object capability to callers. The writable ScopedClient
    cannot address preserved paths. Unknown objects are inspected as metadata
    only and cause a stop; their contents are never downloaded.
    """
    client.budget.take()
    rows=client.raw.list_blobs(bucket,max_results=LIMITS['max_listed_per_call']+1,
        page_size=100,**({'soft_deleted':True} if soft else {'versions':True}),**_options())
    pages=getattr(rows,'pages',None)
    pages=iter(pages) if pages is not None else iter([rows])
    count=0
    while True:
        client.budget.take()
        try:page=next(pages)
        except StopIteration:return
        for blob in page:
            count+=1
            if count>LIMITS['max_listed_per_call']:
                raise RuntimeError('continuation inventory limit exceeded')
            yield dict(name=blob.name,generation=blob.generation,bytes=blob.size)


def preserved_readback(client, *, baseline=None, allow_new=False):
    if client.prefix!=CONTINUATION_PREFIX:
        raise PermissionError('continuation client required')
    expected={(b,n):(z,h) for b,n,z,h in PRESERVED}
    found={};policies=[]
    for bucket in BUCKETS:
        policies.append(client.bucket(bucket).check())
        for soft in (False,True):
            for row in root_versions(client,bucket,soft=soft):
                key=(bucket,row['name'])
                if key not in expected:
                    if not allow_new:
                        raise PermissionError('unexpected object or occupied new namespace')
                    client.key(bucket,row['name']) # rejects unknown old/foreign paths
                    continue
                if soft or key in found:
                    raise PermissionError('preserved object has extra/non-live versions')
                size,digest=expected[key];generation=row['generation']
                if type(generation) is not int or generation<=0 or row['bytes']!=size:
                    raise PermissionError('preserved metadata mismatch')
                # Require the listed generation to still be the LIVE object;
                # then pin the content read to that exact version.
                client.budget.take()
                live=client.raw.bucket(bucket).blob(row['name'])
                live.reload(if_generation_match=generation,**_options())
                if live.generation!=generation or live.size!=size:
                    raise PermissionError('preserved live generation changed')
                client.budget.take()
                raw=client.raw.bucket(bucket).blob(row['name'],generation=generation)
                data=raw.download_as_bytes(if_generation_match=generation,**_options())
                if len(data)!=size or hashlib.sha256(data).hexdigest()!=digest:
                    raise PermissionError('preserved content mismatch')
                found[key]=dict(bucket=bucket,**row,sha256=digest)
    if set(found)!=set(expected):
        raise PermissionError('preserved object missing')
    objects=sorted(found.values(),key=lambda r:(r['bucket'],r['name']))
    if baseline is not None and objects!=baseline:
        raise PermissionError('preserved generations/content differ from baseline')
    return dict(objects=objects,bucket_checks=policies,read_only=True)


def preservation_preflight(clients):
    first=preserved_readback(clients[0])
    second=preserved_readback(clients[1],baseline=first['objects'])
    return dict(clients=[first,second],baseline=first['objects'],new_namespace_empty=True)


def safe_error(exc, phase):
    # No exception messages, request bodies, credentials or local variables.
    frames=[]
    for frame in traceback.extract_tb(exc.__traceback__):
        path=Path(frame.filename).resolve()
        if path.is_relative_to(ROOT):
            frames.append(dict(file=str(path.relative_to(ROOT)),line=frame.lineno,function=frame.name))
    code=getattr(exc,'code',None)
    return dict(type=type(exc).__name__,http_status=code if isinstance(code,int) else None,
                phase=phase,source_frames=frames)


def run_verified(clients, value, evidence, budget):
    phase='preservation_preflight';status='blocked';error=None;baseline=None
    preserved_after=None;post_error=None;outcomes=[]
    def record(row):
        outcomes.append(row)
        with open(evidence/'progress.jsonl','ab') as f:
            f.write(canonical(row)+b'\n');f.flush();os.fsync(f.fileno())
    try:
        checks=preservation_preflight(clients);baseline=checks['baseline']
        write_json(evidence/'preflight.json',checks)
        phase='scenarios'
        scenarios([SyntheticSmokeStore(c) for c in clients],value,record)
        phase='new_object_readback'
        write_json(evidence/'new-object-readback.json',evidence_inventory(clients[1]))
        status='bounded-scenarios-passed-expiry-pending'
    except Exception as exc:error=safe_error(exc,phase)
    finally:
        # Readback is also budgeted. If interruption/exhaustion prevents it, do
        # not claim preservation was confirmed; never exceed scope to recover.
        if baseline is not None:
            try:
                preserved_after=[preserved_readback(c,baseline=baseline,allow_new=True) for c in clients]
                write_json(evidence/'preserved-after.json',preserved_after)
            except Exception as exc:
                post_error=safe_error(exc,'preserved_after');status='blocked'
    result=dict(status=status,error=error,preservation_error=post_error,
        preserved_after_verified=preserved_after is not None and post_error is None,
        outcomes=outcomes,counters=budget.summary(),live_gcs=True,audit_is_worm=False,
        teardown_performed=False,new_prefix=CONTINUATION_PREFIX)
    write_json(evidence/'result.json',result)
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--prepare');p.add_argument('--execute',action='store_true')
    p.add_argument('--manifest');p.add_argument('--expected-sha256');p.add_argument('--evidence-dir')
    args=p.parse_args(argv)
    if args.prepare:
        if any((args.execute,args.manifest,args.expected_sha256,args.evidence_dir)):p.error('prepare cannot execute')
        commit,files=source_snapshot()
        value=continuation_manifest(commit,files,'smoke-'+uuid.uuid4().hex[:24],int(time.time()))
        write_json(args.prepare,value)
        print(json.dumps(dict(path=args.prepare,sha256=sha(value),execution_authorized=False)));return 0
    if not all((args.execute,args.manifest,args.expected_sha256,args.evidence_dir)):
        p.error('explicit execute, reviewed manifest/digest and new evidence directory required')
    value=validate_continuation(json.loads(Path(args.manifest).read_text()),args.expected_sha256)
    validate_environment(os.environ)
    if source_snapshot()!=(value['source_commit'],value['source_files']):raise ValueError('source changed')
    _refuse_cloud_in_test_process() # before credentials and every cloud client
    evidence=Path(args.evidence_dir);evidence.mkdir(mode=0o700,exist_ok=False)
    write_json(evidence/'manifest.json',value)
    (evidence/'progress.jsonl').touch(mode=0o600,exist_ok=False)
    import google.auth
    from google.cloud import storage
    credentials,_=google.auth.default(scopes=['https://www.googleapis.com/auth/devstorage.full_control'])
    if getattr(credentials,'service_account_email',None)!=EXECUTOR:
        raise PermissionError('exact dedicated executor required')
    budget=Budget(expires_at=value['expires_at'])
    clients=[ScopedClient(storage.Client(project=PROJECT,credentials=credentials),budget,
                          prefix=CONTINUATION_PREFIX) for _ in range(2)]
    result=run_verified(clients,value,evidence,budget)
    print(json.dumps(dict(status=result['status'],evidence=str(evidence))))
    return 0 if result['status']=='bounded-scenarios-passed-expiry-pending' else 2


if __name__=='__main__':raise SystemExit(main())
