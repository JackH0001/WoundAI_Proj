"""Same synthetic signed-HTTP late-write probe on parent and candidate source.

Run against the parent's modules as well. Only temporary generated fixtures are
changed. There is no network, user photo, cloud cleanup or billing operation.
"""
import argparse
import copy
import json
from pathlib import Path
from unittest.mock import patch
from lite_attest_state import SQLiteStateStore
from test_lite_account_lifecycle import ClosureHTTPTests
from test_lite_subscription_grace import rows


def run():
    ClosureHTTPTests.setUpClass();f=ClosureHTTPTests();f.service_catalog=True;f.setUp()
    try:
        h=f.h.h;subject=f.account['subject'];person=subject[6:]
        supported=getattr(f.lifecycle,'service_catalog',False)
        worker=None
        if supported:
            from lite_service_disposition import ServiceResultDisposition
            worker=ServiceResultDisposition(operations=h.ops,lifecycle=f.lifecycle,
                store=SQLiteStateStore(h.root/'disposition.sqlite'),audit_store=SQLiteStateStore(h.root/'disposition-audit.sqlite'))
        completed=h.complete(77).json
        original_capture=bytes(h.f.jpeg);original_result=copy.deepcopy(completed)
        assert h.json_request('prepare',78).status_code==200
        real=h.ops.store.compare_exchange;observed={}
        def late(k,rev,value):
            if value.get('operation')==f'{78:032x}' and value.get('result') is not None:
                observed['before']=[rows(s) for s in (h.ledger.store,h.privacy.store,h.accounts.store)]
                f.close()
                if worker is not None:observed['disposition']=worker.advance(person)
                observed['late_write_won']=real(k,rev,value)
                return observed['late_write_won']
            return real(k,rev,value)
        with patch.object(h.ops.store,'compare_exchange',side_effect=late):late_reply=h.run_request(78)
        assert late_reply.status_code!=200
        after=[rows(s) for s in (h.ledger.store,h.privacy.store,h.accounts.store)]
        earlier=h.ops.load(subject,f'{77:032x}')[1];late_row=h.ops.load(subject,f'{78:032x}')[1]
        retry=h.run_request(78);assert retry.status_code==401
        report=dict(schema='lite-service-disposition-probe/1',synthetic_only=True,
            service_catalog_enabled=supported,model_calls=h.model_calls,
            stale_result_write_won=observed['late_write_won'],
            earlier_current_mask_present=earlier.get('result') is not None,
            late_current_mask_present=late_row.get('result') is not None,
            quota_research_registry_unchanged=observed['before']==after,
            original_capture_and_saved_receipt_preserved=(h.f.jpeg==original_capture and completed==original_result),
            retry_http=retry.status_code,late_response_http=late_reply.status_code,
            disposition=observed.get('disposition'),account_deleted=False,
            real_apple_calls=0,real_gcs_calls=0,original_user_data_changed=False,
            startup_enabled=False,historical_erasure_verified=False)
        assert report['model_calls']==2 and report['quota_research_registry_unchanged']
        assert report['original_capture_and_saved_receipt_preserved']
        if supported:
            assert not report['stale_result_write_won'] and not report['earlier_current_mask_present'] and not report['late_current_mask_present']
            assert report['disposition']['sealed']==2
        else:
            assert report['stale_result_write_won'] and report['earlier_current_mask_present'] and report['late_current_mask_present']
        return report
    finally:f.doCleanups()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True,type=Path);args=parser.parse_args()
    args.output.write_text(json.dumps(run(),indent=2)+'\n')
