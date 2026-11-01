"""Reproduce review gaps using synthetic identities, SQLite and signed fake Apple.

This characterizes still-blocked rollout conditions; a passing probe is NOT
launch acceptance. No deployment, patient media, credentials or external calls.
"""
from pathlib import Path
import argparse
import json
import sys
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'Backend/Flask'),str(ROOT/'engineering/phase2')]
import test_lite_service_http as http
import test_lite_service_subscription_links as links
from lite_attest_state import StateUnavailable


def scenario(fn):
    h=http.ServiceHTTPTests();h.setUp()
    try:return fn(h)
    finally:h.doCleanups()


def probe():
    def uninitialized(h):
        h.e.now=(h.e.now//86400+1)*86400+1
        response=h.json_request('prepare')
        return dict(http=response.status_code,error=response.json['error'],model_calls=h.model_calls)
    def abandoned(h):
        prepared=[h.json_request('prepare',n).status_code for n in (1,2)]
        response=h.json_request('prepare',3)
        return dict(prepared=prepared,third_http=response.status_code,quota=h.quota(),model_calls=h.model_calls)
    def midnight(h):
        h.e.now=(h.e.now//86400+1)*86400-5
        with patch.object(h.ledger,'reserve',side_effect=StateUnavailable('SYNTHETIC pre-write outage')):
            initial=h.json_request('prepare')
        h.e.now+=6;h.initialize_day()
        prepared=h.json_request('prepare');status=h.json_request('status');run=h.run_request()
        return dict(initial_http=initial.status_code,retry_http=prepared.status_code,
                    status=status.json['state'],retry_action=status.json['retry_action'],
                    run_http=run.status_code,model_calls=h.model_calls)
    links.SignedFlowTests.setUpClass();p=links.SignedFlowTests();p.setUp()
    try:
        p.links.attach(p.h.accounts,p.h.owner,links.apple.BINDING.original_transaction,
                       authentication_proof_sha256='b'*64)
        with patch.object(p.a.client,'get_all_subscription_statuses',wraps=p.a.fetch) as query:
            p.h.complete();first=query.call_count
            p.h.json_request('prepare');p.h.json_request('status')
            paid=dict(new_operation_queries=first,receipt_replay_queries=query.call_count-first,
                      model_calls=p.h.model_calls)
    finally:p.doCleanups()
    result=dict(schema='lite-commerce-review-probe/1',scope='SYNTHETIC_LOCAL_ONLY',
                G1=scenario(uninitialized),G2=scenario(abandoned),G3=scenario(midnight),G4=paid,
                release_ready=False,real_apple_calls=False,real_cloud_calls=False)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=probe()
    with args.output.open('x') as f:
        json.dump(result,f,indent=2,allow_nan=False);f.write('\n')
