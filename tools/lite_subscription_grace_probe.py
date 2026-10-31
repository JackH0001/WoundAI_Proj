"""Synthetic signed Apple billing-grace lifecycle through authenticated HTTP.

Uses official JWS verification and SQLite CAS; no Apple/GCS/payment calls.
"""
from pathlib import Path
import argparse
import copy
import json
import sys
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'Backend/Flask'),str(ROOT/'engineering/phase2')]
from test_lite_subscription_grace import GraceFlowTests, enable_grace, rows


def probe():
    GraceFlowTests.setUpClass();f=GraceFlowTests();f.setUp()
    try:
        h,a=f.h,f.a
        f.attach()
        protected=[rows(s) for s in (f.links.store,f.links.audit,h.accounts.store)]
        privacy=copy.deepcopy(h.privacy._load(h.owner))
        renewal=enable_grace(a)
        with patch.object(a.client,'get_all_subscription_statuses',wraps=a.client.get_all_subscription_statuses) as query:
            result=h.complete(1)
            prepare=h.json_request('prepare',1);status=h.json_request('status',1)
            status_queries=query.call_count
        original=copy.deepcopy(result.json)
        grace_snapshot=h.quota()
        # A stale status=4 with expired renewal is not proof of free entitlement.
        renewal['gracePeriodExpiresDate']=a.now*1000
        before=rows(h.ledger.store)
        expired=h.json_request('prepare',2)
        expiry_preserved=before==rows(h.ledger.store)
        # Current Apple billing-retry (outside grace) permits only the free floor.
        a.status=3
        second=h.complete(2);denied=h.json_request('prepare',3)
        retry_snapshot=h.quota()
        a.status=5
        with patch.object(a.client,'get_all_subscription_statuses',side_effect=TimeoutError()) as query:
            old=h.json_request('status',1)
            existing_queries=query.call_count
            unavailable=h.json_request('prepare',3)
        final=h.quota()
        r=dict(schema='lite-subscription-grace-probe/1',
            scope='SIGNED_SYNTHETIC_APPLE_OFFICIAL_VERIFIER_SQLITE_HTTP',
            grace=dict(http=result.status_code,admitted_limit=result.json['receipt']['admitted_limit'],
                       used=grace_snapshot['used'],pending=grace_snapshot['pending'],
                       apple_status_queries_for_new_and_retries=status_queries,
                       repeated_prepare_and_status_identical=prepare.json==status.json==original),
            expired_proof=dict(http=expired.status_code,ledger_unchanged=expiry_preserved),
            billing_retry=dict(second_http=second.status_code,third_http=denied.status_code,
                               limit=retry_snapshot['limit'],used=retry_snapshot['used']),
            apple_unavailable=dict(new_http=unavailable.status_code,old_receipt_unchanged=old.json==original,
                                   queries_for_existing_receipt=existing_queries),
            final=dict(model_calls=h.model_calls,used=final['used'],pending=final['pending']),
            association_registration_audit_unchanged=protected==[rows(s) for s in (f.links.store,f.links.audit,h.accounts.store)],
            research_unchanged=privacy==h.privacy._load(h.owner),
            research_stored=result.json['research_stored'] or second.json['research_stored'],
            real_apple_calls=False,real_cloud_calls=False,store_or_grace_setting_changed=False,
            exclusions=['Real Apple Sandbox and online OCSP','App Store grace configuration',
                        'Policy/product-link migration','Native purchase/account recovery','Deployment or real research data'])
        r['passed']=(result.status_code==200 and result.json['receipt']['admitted_limit']==10
            and status_queries==1 and r['grace']['repeated_prepare_and_status_identical']
            and expired.status_code==503 and expiry_preserved
            and second.status_code==200 and denied.status_code==429
            and retry_snapshot['limit']==2 and retry_snapshot['used']==2
            and unavailable.status_code==503 and old.json==original and existing_queries==0
            and r['final']==dict(model_calls=2,used=2,pending=0)
            and r['association_registration_audit_unchanged'] and r['research_unchanged'] and not r['research_stored'])
        return r
    finally:f.doCleanups()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();r=probe()
    with args.output.open('x') as f:json.dump(r,f,indent=2,allow_nan=False);f.write('\n')
    if not r['passed']:raise SystemExit('grace probe failed')
