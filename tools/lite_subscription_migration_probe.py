"""Signed synthetic HTTP + durable SQLite policy migration / recovery rehearsal."""
from pathlib import Path
import argparse
import copy
from dataclasses import asdict, replace
import json
import sys
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'Backend/Flask'),str(ROOT/'engineering/phase2')]
from test_lite_subscription_grace import GraceFlowTests, rows
from lite_subscription_authority import AppleSubscriptionAuthority
from lite_subscription_migration import StableSubscriptionLinks
from lite_service_operations import ServiceAccounts
from lite_attest_state import StateUnavailable
from lite_usage_ledger import digest


def probe():
    GraceFlowTests.setUpClass();f=GraceFlowTests();f.setUp()
    try:
        h,a=f.h,f.a;f.attach()
        legacy_store=dict((r[0],r) for r in rows(f.links.store))
        legacy_audit=dict((r[0],r) for r in rows(f.links.audit))
        registration=rows(h.accounts.store);privacy=copy.deepcopy(h.privacy._load(h.owner))
        first=h.complete(1).json
        assert h.json_request('prepare',2).status_code==200
        original_operation_bytes=rows(h.ops.store)
        original_ledger=rows(h.ledger.store)
        old=a.a;product='com.woundai.lite.synthetic.next'
        new=AppleSubscriptionAuthority(policy=replace(old.policy,product=product),verifier=a.verifier,
            client=a.client,proof_store=a.store,clock=lambda:a.now,allowed_products=(old.policy.product,product))
        old_id=digest(asdict(old.policy));new_id=digest(asdict(new.policy));approved=set()
        def reader(active,authorities=(old,new)):
            return StableSubscriptionLinks(store=f.links.store,audit_store=f.links.audit,authorities=authorities,
                active_policy=active,legacy_policies=(old_id,),clock=lambda:a.now,
                authorize=lambda p,proof:proof=='a'*64 and digest(p) in approved,
                writer_fence=lambda proof:proof=='f'*64)
        def install(r):
            h.accounts=ServiceAccounts(h.accounts.store,subscription_links=r);h.ops.accounts=h.accounts
        def plan(r,n):
            p=r.plan(h.accounts,h.owner,operation=f'{n:064x}',inventory_sha256='b'*64,writer_fence_sha256='f'*64)
            approved.add(digest(p));return p
        stable=reader(old_id);install(stable);p=plan(stable,1)
        stable.execute(p,h.accounts,h.owner,authorization_sha256='a'*64)
        stable=reader(new_id);install(stable);a.payload['productId']=product;q=plan(stable,2)
        real=f.links.audit.compare_exchange
        def lose(k,rev,value):
            out=real(k,rev,value)
            if value.get('kind')=='subscription-policy-transition-confirmed':raise StateUnavailable('lost completion response')
            return out
        lost=False
        with patch.object(f.links.audit,'compare_exchange',side_effect=lose):
            try:stable.execute(q,h.accounts,h.owner,authorization_sha256='a'*64)
            except StateUnavailable:lost=True
        with patch.object(a.client,'get_all_subscription_statuses',side_effect=TimeoutError()) as query:
            receipt=stable.execute(q,h.accounts,h.owner,authorization_sha256='a'*64)
            repeat=stable.execute(q,h.accounts,h.owner,authorization_sha256='a'*64)
            recovery_queries=query.call_count
        migration_preserved=(original_ledger==rows(h.ledger.store) and original_operation_bytes==rows(h.ops.store))
        second=h.run_request(2)
        with patch.object(a.client,'get_all_subscription_statuses',wraps=a.client.get_all_subscription_statuses) as query:
            third=h.complete(3)
            repeated=h.json_request('prepare',3);third_status=h.json_request('status',3)
            new_queries=query.call_count
        full_ledger=rows(h.ledger.store);full_results=rows(h.ops.store)
        install(reader(old_id,(old,)))
        with patch.object(a.client,'get_all_subscription_statuses',side_effect=TimeoutError()) as query:
            old_status=h.json_request('status',1);blocked=h.json_request('prepare',4)
            rollback_queries=query.call_count
        rollback_preserved=(full_ledger==rows(h.ledger.store) and full_results==rows(h.ops.store))
        install(stable);a.status=5;refund=h.json_request('prepare',4)
        final=h.quota()
        r=dict(schema='lite-subscription-policy-migration-probe/1',scope='SIGNED_SYNTHETIC_SQLITE_AUTHENTICATED_HTTP',
            before=dict(used=1,pending=1,model_calls=1),migration_preserved_original_ledger_and_operations=migration_preserved,
            lost_completion=dict(injected=lost,same_receipt=receipt==repeat,apple_queries_on_recovery=recovery_queries),
            pending_old_operation=dict(http=second.status_code,admitted_limit=second.json['receipt']['admitted_limit']),
            new_policy_operation=dict(http=third.status_code,admitted_limit=third.json['receipt']['admitted_limit'],
                repeated_receipt_identical=repeated.json==third_status.json==third.json,apple_queries=new_queries),
            rollback=dict(old_receipt_http=old_status.status_code,old_receipt_identical=old_status.json==first,
                new_admission_http=blocked.status_code,apple_queries=rollback_queries,ledger_results_unchanged=rollback_preserved),
            refund=dict(new_http=refund.status_code,used=final['used'],pending=final['pending']),
            final=dict(model_calls=h.model_calls,used=final['used'],pending=final['pending']),
            old_links_unchanged=legacy_store.items()<=dict((r[0],r) for r in rows(f.links.store)).items(),
            old_audit_unchanged=legacy_audit.items()<=dict((r[0],r) for r in rows(f.links.audit)).items(),
            registration_unchanged=registration==rows(h.accounts.store),research_unchanged=privacy==h.privacy._load(h.owner),
            research_stored=third.json['research_stored'] or second.json['research_stored'],
            real_apple_calls=False,real_cloud_calls=False,live_fence_verified=False,store_products_created=False)
        r['passed']=(migration_preserved and lost and receipt==repeat and recovery_queries==0
            and second.status_code==third.status_code==200 and third.json['receipt']['admitted_limit']==10
            and r['new_policy_operation']['repeated_receipt_identical'] and new_queries==1
            and old_status.status_code==200 and old_status.json==first and blocked.status_code==503
            and rollback_queries==0 and rollback_preserved and refund.status_code==429
            and r['final']==dict(model_calls=3,used=3,pending=0)
            and all(r[k] for k in ('old_links_unchanged','old_audit_unchanged','registration_unchanged','research_unchanged'))
            and not r['research_stored'])
        return r
    finally:f.doCleanups()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();result=probe()
    with a.output.open('x') as f:json.dump(result,f,indent=2,allow_nan=False);f.write('\n')
    if not result['passed']:raise SystemExit('migration probe failed')
