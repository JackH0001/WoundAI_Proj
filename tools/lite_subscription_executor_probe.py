"""Local signed-HTTP cutover rehearsal. No cloud, Apple account or migration CLI."""
import argparse
import base64
import copy
from dataclasses import asdict
import json
from pathlib import Path
import sys
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'Backend/Flask'),str(ROOT/'engineering/phase2')]
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_lite_subscription_grace import GraceFlowTests, rows
from lite_subscription_migration import StableSubscriptionLinks
from lite_subscription_executor import (SignedEvidence, StoredObservation, SubscriptionMigrationExecutor,
    make_manifest, canonical, key)
from lite_service_operations import ServiceAccounts
from lite_attest_state import SQLiteStateStore, StateUnavailable
from lite_usage_ledger import digest


def probe():
    GraceFlowTests.setUpClass();f=GraceFlowTests();f.setUp()
    try:
        h,a=f.h,f.a;f.attach()
        first=h.complete(1);assert first.status_code==200
        assert h.json_request('prepare',2).status_code==200
        old_ledger=rows(h.ledger.store);old_work=rows(h.ops.store)
        old_registration=rows(h.accounts.store);old_research=copy.deepcopy(h.privacy._load(h.owner))
        old_links=dict((r[0],r) for r in rows(f.links.store));old_audit=dict((r[0],r) for r in rows(f.links.audit))
        policy=digest(asdict(a.a.policy))
        stable=StableSubscriptionLinks(store=f.links.store,audit_store=f.links.audit,authorities=(a.a,),
            active_policy=policy,legacy_policies=(policy,),clock=lambda:a.now,authorize=lambda *args:False,
            writer_fence=lambda proof:False)
        accounts=ServiceAccounts(h.accounts.store,subscription_links=stable)
        approver=Ed25519PrivateKey.generate();observer=Ed25519PrivateKey.generate()
        evidence=SignedEvidence({'operator':approver.public_key().public_bytes_raw()},
            {'observer':observer.public_key().public_bytes_raw()},clock=lambda:a.now)
        root=Path(a.tmp.name)
        control=SQLiteStateStore(str(root/'control.sqlite'));audit=SQLiteStateStore(str(root/'executor-audit.sqlite'))
        observations=SQLiteStateStore(str(root/'observations.sqlite'))
        m=make_manifest(stable,accounts,[h.owner],namespace='http-synthetic',source='1'*40,image='2'*64,
            inventory='3'*64,fence='4'*64,operation='5'*64)
        def signed(p,role):
            k,name=(approver,'operator') if role=='approval' else (observer,'observer')
            return dict(key_id=name,payload=p,signature=base64.b64encode(k.sign(canonical(p))).decode())
        p=dict(kind='lite-migration-observation/1',namespace=m['namespace'],manifest=digest(m),source=m['source'],
            image=m['image'],inventory=m['inventory'],fence=m['fence'],legacy_disabled=True,legacy_inflight=0,
            migration_inflight=0,stable_reader=True,issued_at=a.now,expires_at=a.now+30)
        observations.compare_exchange(key('observation',m['namespace']),0,signed(p,'observation'))
        e=SubscriptionMigrationExecutor(manifest=m,links=stable,registry=accounts,state_store=control,audit_store=audit,
            observation=StoredObservation(observations,evidence,m['namespace']),evidence=evidence,
            expected_source=m['source'],expected_image=m['image'])
        def approval(action):
            return signed(dict(kind='lite-migration-approval/1',manifest=digest(m),action=action,
                expected_state=e.state_digest(),issued_at=a.now,expires_at=a.now+900,nonce=digest([action,e.state_digest()])),'approval')
        h.accounts=accounts;h.ops.accounts=accounts
        calls_before=h.model_calls;real=e.links.execute
        def lost(*args,**kwargs):
            real(*args,**kwargs);raise StateUnavailable('synthetic completion response lost')
        interrupted=False
        with patch.object(e.links,'execute',side_effect=lost):
            try:e.advance(approval('start'))
            except StateUnavailable:interrupted=True
        assert e.inspect()['phase']=='paused'
        with patch.object(a.client,'get_all_subscription_statuses',side_effect=TimeoutError()) as q:
            retained=h.json_request('status',1);denied=h.json_request('prepare',3)
            paused_apple=q.call_count
        during=dict(receipt_http=retained.status_code,receipt_unchanged=retained.json==first.json,
            new_http=denied.status_code,apple_queries=paused_apple,model_calls=h.model_calls-calls_before)
        with patch.object(a.client,'get_all_subscription_statuses',side_effect=TimeoutError()) as q:
            e.advance(approval('resume'),resume=True);resume_apple=q.call_count
        unchanged=(old_ledger==rows(h.ledger.store) and old_work==rows(h.ops.store))
        pending=h.run_request(2);pending_repeat=h.run_request(2)
        third=h.complete(3);third_repeat=h.json_request('prepare',3)
        settled=rows(h.ledger.store);results=rows(h.ops.store);e.pause(approval('pause'))
        old=h.json_request('status',3);blocked=h.json_request('prepare',4)
        rollback=dict(old_http=old.status_code,old_receipt_unchanged=old.json==third.json,new_http=blocked.status_code,
            ledger_unchanged=settled==rows(h.ledger.store),results_unchanged=results==rows(h.ops.store))
        e.advance(approval('resume'),resume=True)
        final=h.quota()
        r=dict(schema='lite-subscription-executor-probe/1',scope='SYNTHETIC_SIGNED_HTTP_SQLITE',
            interrupted=interrupted,during_pause=during,resume_apple_queries=resume_apple,
            migration_preserved_ledger_and_work=unchanged,
            pending=dict(http=pending.status_code,repeated_identical=pending.json==pending_repeat.json),
            new_work=dict(http=third.status_code,repeated_identical=third.json==third_repeat.json),rollback=rollback,
            final=dict(model_calls=h.model_calls,used=final['used'],pending=final['pending'],phase=e.inspect()['phase']),
            registration_unchanged=old_registration==rows(h.accounts.store),
            research_unchanged=old_research==h.privacy._load(h.owner),
            old_links_preserved=old_links.items()<=dict((r[0],r) for r in rows(f.links.store)).items(),
            old_audit_preserved=old_audit.items()<=dict((r[0],r) for r in rows(f.links.audit)).items(),
            research_stored=third.json.get('research_stored'),real_apple=False,real_gcs=False,live_fence=False)
        r['passed']=(interrupted and during==dict(receipt_http=200,receipt_unchanged=True,new_http=503,apple_queries=0,model_calls=0)
            and resume_apple==0 and unchanged and r['pending']==dict(http=200,repeated_identical=True)
            and r['new_work']==dict(http=200,repeated_identical=True)
            and rollback==dict(old_http=200,old_receipt_unchanged=True,new_http=503,ledger_unchanged=True,results_unchanged=True)
            and r['final']==dict(model_calls=3,used=3,pending=0,phase='active')
            and all(r[k] for k in ('registration_unchanged','research_unchanged','old_links_preserved','old_audit_preserved'))
            and r['research_stored'] is False)
        return r
    finally:f.doCleanups()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();result=probe()
    with args.output.open('x') as out:json.dump(result,out,indent=2,allow_nan=False);out.write('\n')
    if not result['passed']:raise SystemExit('executor rehearsal failed')
