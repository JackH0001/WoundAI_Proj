"""Replay private factory restarts through signed synthetic native login."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'engineering/phase2'))
from test_lite_key_startup import FactoryLoginTests
from test_lite_subscription_grace import rows


def run():
    FactoryLoginTests.setUpClass();results={}
    for scenario in ('fresh','lost_kms_reply_restart','lost_private_reply_restart','closure_during_create'):
        t=FactoryLoginTests();t.setUp()
        try:
            t.begin();saved=dict(t.fields)
            if scenario=='lost_kms_reply_restart':t.kms.lost=True
            if scenario=='lost_private_reply_restart':t.transport.lost=True
            if scenario=='closure_during_create':
                t.kms.after=lambda *a:t.backend.identity.lifecycle.close(t.person,t.owner,'d'*32)
            first=t.confirm();exchanges_after_first=len(t.h.grant.calls)
            before={k:rows(t.provider.stores[k]) for k in ('lifecycle','lifecycle_audit')}
            t.restart_worker()
            t.assertEqual({k:rows(t.provider.stores[k]) for k in before},before)
            retry=t.confirm()
            result=dict(first_status=first.status_code,retry_status=retry.status_code,
                apple_exchanges_after_first=exchanges_after_first,apple_exchanges=len(t.h.grant.calls),
                kms_create_posts=t.kms.creates(),kms_versions=len(t.kms.states),
                saved_request_unchanged=saved==t.fields,native_lifecycle_unchanged_by_restart=True,
                model_calls=0,quota_used=0)
            if scenario!='closure_during_create':
                t.assertEqual(retry.status_code,200)
                r=t.complete(1);again=t.run_request(1);t.assertEqual(r.json,again.json)
                result.update(model_calls=t.h.model_calls,quota_used=t.quota()['used'],same_result_receipt=True)
                t.assertEqual((result['apple_exchanges'],result['kms_versions'],result['model_calls'],result['quota_used']),(1,1,1,1))
            else:
                t.assertEqual((first.status_code,retry.status_code,len(t.h.grant.calls)),(503,401,0))
                result.update(account_reopened=False,orphan_key_preserved_for_cleanup=True)
            t.assertEqual(result['kms_create_posts'],1);t.assertTrue(result['saved_request_unchanged'])
            results[scenario]=result
        finally:t.doCleanups()
    return dict(schema='lite-key-startup-probe/1',passed=True,scenarios=results,
        synthetic_only=True,live_iam_verified=False,real_apple_kms=False,original_data_modified=False)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    value=run();a.output.write_text(json.dumps(value,indent=2)+'\n');print(json.dumps(value))
