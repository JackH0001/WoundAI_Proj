"""Offline CAS core evidence, not an HTTP/cloud load or Apple purchase test.

Real SQLite handles thread contention. Real GCSStateStore uses a synthetic
transport solely to count API calls and conditional writes; no cloud client is
created. Result bytes below are synthetic and never contain medical media.
"""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import argparse
import json
import sys
import tempfile
import threading

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'Backend/Flask'),str(ROOT/'engineering/phase2')]
from lite_usage_ledger import UsageLedger, UsageLimit, free_grant, digest
from lite_attest_state import SQLiteStateStore, GCSStateStore
from test_lite_attest_state import FakeBucket

NOW=1792886500
SUBJECT='synthetic-probe-only'
DAY=NOW//86400


def initialize(l):
    l.initialize_day(SUBJECT,DAY,baseline_successes=0,baseline_attempts=0,
                     manifest_sha256=digest({'synthetic_verified_baseline':0}))


def concurrent_case(duplicate=False):
    with tempfile.TemporaryDirectory() as tmp:
        l=UsageLedger(SQLiteStateStore(Path(tmp)/'usage.sqlite'),namespace='offline-probe',clock=lambda:NOW)
        initialize(l);barrier=threading.Barrier(8)
        def run(n):
            barrier.wait(10)
            try:
                t=l.reserve(SUBJECT,DAY,f'{1 if duplicate else n:032x}',digest(['synthetic-input',1 if duplicate else n]),free_grant(SUBJECT,NOW))
                return 'executor_claimed' if l.claim(t,f'{n:032x}') else 'existing_operation'
            except UsageLimit:
                return 'quota_wait'
        with ThreadPoolExecutor(max_workers=8) as pool:
            outcomes=Counter(pool.map(run,range(1,9)))
        return dict(requests=8,duplicate_operation=duplicate,outcomes=dict(outcomes),
                    snapshot=l.snapshot(SUBJECT,DAY,free_grant(SUBJECT,NOW)))


def calls(bucket,start):
    counts=Counter(c[0] for c in bucket.calls[start:])
    return dict(write_attempts=counts['upload'],metadata_reads=counts['reload'],
                content_reads=counts['download'],total=sum(counts.values()))


def transport_case():
    bucket=FakeBucket()
    l=UsageLedger(GCSStateStore(bucket,prefix='synthetic_usage_probe/v1'),namespace='offline-probe',clock=lambda:NOW)
    initialize(l);init=calls(bucket,0);start=len(bucket.calls)
    t=l.reserve(SUBJECT,DAY,'1'*32,'b'*64,free_grant(SUBJECT,NOW))
    assert l.claim(t,'2'*32)
    receipt=l.settle(t,'2'*32,'succeeded',digest({'synthetic_result':'only'}))
    fresh=calls(bucket,start);start=len(bucket.calls)
    t2=l.reserve(SUBJECT,DAY,'1'*32,'b'*64,free_grant(SUBJECT,NOW))
    reran=l.claim(t2,'3'*32)
    repeated=l.receipt(t2)
    retry=calls(bucket,start)
    return dict(scope='ACTUAL_ADAPTER_FAKE_TRANSPORT_NO_LATENCY_OR_CLOUD',initialize=init,
                new_completed_operation=fresh,completed_operation_retry=retry,
                same_receipt=receipt==repeated,reexecuted=reran,
                conditional_writes=all('if_generation_match' in c[2] for c in bucket.calls if c[0]=='upload'),
                retained_fake_generations=len(bucket.versions),
                result_sha256=receipt['result_sha256'])


def planning(transport):
    """Subset of prior cost allowance, NOT an additive whole-platform estimate."""
    attempts=337500;retries=.20*attempts;days=300000
    a=transport['new_completed_operation'];r=transport['completed_operation_retry'];d=transport['initialize']
    writes=attempts*a['write_attempts']+retries*r['write_attempts']+days*d['write_attempts']
    reads=attempts*(a['metadata_reads']+a['content_reads'])+retries*(r['metadata_reads']+r['content_reads'])+days*(d['metadata_reads']+d['content_reads'])
    # Deliberately inherited sensitivity inputs, not a fetched account/SKU tariff.
    return dict(currency='USD',scope='QUOTA_SUBSET_ONLY_NOT_ADDITIONAL_TO_PRIOR_40A_100B_ALLOWANCE',
        assumptions=dict(mau=10000,paid_fraction=.1,free_successes=20,paid_successes=90,
            attempts_per_success=1.25,retry_fraction=.2,active_day_initializations=days,
            class_a_rate=.000005,class_b_rate=.0000004,inference_seconds=5,vcpu=2,memory_gib=4,
            cpu_rate=.000024,memory_rate=.0000025),
        attempts=attempts,repeated_operations=retries,quota_write_attempts=writes,quota_reads=reads,
        quota_operation_cost=writes*.000005+reads*.0000004,
        avoided_duplicate_inference_compute_if_each_retry_would_rerun=retries*5*(2*.000024+4*.0000025),
        excluded=['App Attest','Apple status calls and OCSP','subscription proofs','CAS contention and 429 retries',
                  'audit/version storage','model/results/media storage','egress','ads','tax','billing','support'],
        revenue_or_savings_measured=False)


def probe():
    distinct=concurrent_case();duplicate=concurrent_case(True);transport=transport_case()
    passed=(distinct['outcomes'].get('executor_claimed')==2
        and duplicate['outcomes'].get('executor_claimed')==1
        and transport['same_receipt'] and not transport['reexecuted'] and transport['conditional_writes'])
    return dict(schema='lite-atomic-usage-probe/1',scope='ISOLATED_CORE_OFFLINE',core_gate=passed,
        http_cutover=False,live_cloud_test=False,apple_sandbox_test=False,
        distinct=distinct,duplicate=duplicate,transport=transport,cost_subset=planning(transport))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();result=probe()
    with args.output.open('x') as f:json.dump(result,f,indent=2,allow_nan=False);f.write('\n')
    if not result['core_gate']:raise SystemExit('isolated core probe failed')
