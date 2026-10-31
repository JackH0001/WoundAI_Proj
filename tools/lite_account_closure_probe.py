"""Real Flask/App Attest synthetic closure replay; no live Apple/GCS/deletion."""
import argparse,json
from pathlib import Path
from test_lite_account_lifecycle import ClosureHTTPTests
from test_lite_subscription_grace import rows


def run():
    ClosureHTTPTests.setUpClass();f=ClosureHTTPTests();f.setUp()
    try:
        h=f.h.h;result=h.complete(77).json
        before=rows(h.ops.store),rows(h.ledger.store),rows(h.privacy.store)
        receipt=f.close();again=f.close()
        blocked=[h.json_request('status',77),h.run_request(77),h.json_request('prepare',78)]
        person=f.account['subject'][6:];page=f.lifecycle.page(person)
        grant=page['items'][0]
        apple=f.g.a.revoke(grant['installation'],grant['operation'])
        unchanged=before==(rows(h.ops.store),rows(h.ledger.store),rows(h.privacy.store))
        report=dict(schema='lite-account-closure-probe/1',synthetic_only=True,model_calls=h.model_calls,
            saved_result_succeeded=result['state']=='completed',closure_replay_identical=receipt==again,
            blocked_new_work_and_old_result=all(x.status_code==401 for x in blocked),
            original_service_quota_research_unchanged=unchanged,inventory_count=page['total'],
            captured_grant_revoked=apple['state']=='apple_token_revoked',account_deleted=receipt['account_deleted'],
            actual_apple=False,actual_gcs=False,data_erasure_executed=False,default_startup_enabled=False)
        assert report['model_calls']==1 and report['inventory_count']==1
        assert all(report[k] for k in ('saved_result_succeeded','closure_replay_identical',
            'blocked_new_work_and_old_result','original_service_quota_research_unchanged','captured_grant_revoked'))
        assert not report['account_deleted']
        return report
    finally:f.doCleanups()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    a.output.write_text(json.dumps(run(),indent=2)+'\n')
