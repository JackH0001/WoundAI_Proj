"""Replay expired-login deletion with generated Apple signatures and real App Attest.

Supports the parent source for before/after comparison. No network, model run,
real Apple account, research cleanup, or production registration is performed.
"""
import argparse
import json
from pathlib import Path
from unittest.mock import patch
from lite_attest_state import StateUnavailable
from lite_usage_ledger import digest
from test_lite_account_deletion import DeletionHTTPTests
from test_lite_subscription_grace import rows


def run():
    DeletionHTTPTests.setUpClass(); f=DeletionHTTPTests(); f.setUp()
    try:
        h=f.h.h; f.h.f.h.a.now+=1201
        stores=(h.ops.store,h.ledger.store,h.privacy.store,f.d.bridge.registry.store,f.d.bridge.store,f.g.store)
        before=[rows(s) for s in stores]; exchange_before=len(f.g.calls)
        failed=f.post(); assert failed.status_code==401
        report=dict(schema='lite-deletion-identity/1',synthetic_only=True,first_request_status=401,
            deletion_id='d'*32,recovery_available=hasattr(f.d,'reauthentication'),
            accepted_after_recovery=False,same_receipt_after_response_loss=False,
            data_erasure_executed=False,account_deleted=False,actual_apple=False,actual_gcs=False)
        if report['recovery_available']:
            c=f.post('reauth/challenge',reauth_operation='e'*32,
                account_sha256=digest(f.d.bridge.registry.registration(f.owner)['subject']),confirmation='continue_deletion')
            assert c.status_code==200,c.json
            q=c.json
            # Persist the verified proof, then lose progress before closure.
            with patch.object(f.d.lifecycle,'close',side_effect=StateUnavailable('synthetic outage')):
                r=f.post('reauth/confirm',reauth_operation=q['reauth_operation'],account_sha256=q['account_sha256'],
                         state=q['state'],identity_token=f.h.f.token(q))
            assert r.status_code==503 and not r.json.get('account_deleted',False)
            f.h.f.h.a.now+=700 # JWT/challenge expired; durable proof still owns only this deletion.
            resumed=f.post(); again=f.post()
            assert resumed.status_code==202,resumed.json
            assert again.json==resumed.json and resumed.json['operation']=='d'*32
            report.update(accepted_after_recovery=True,same_receipt_after_response_loss=True,
                proof_interruption_status=r.status_code,accepted_status=resumed.status_code,
                account_deleted=resumed.json['account_deleted'],data_disposition=resumed.json['data_disposition'])
        report.update(additional_exchange_or_refresh_calls=len(f.g.calls)-exchange_before,
            model_calls=h.model_calls,original_service_quota_research_identity_vault_unchanged=before==[rows(s) for s in stores])
        assert report['additional_exchange_or_refresh_calls']==report['model_calls']==0
        assert report['original_service_quota_research_identity_vault_unchanged'] and not report['account_deleted']
        return report
    finally:f.doCleanups()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    a.output.write_text(json.dumps(run(),indent=2)+'\n')
