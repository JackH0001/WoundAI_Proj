"""Before/after synthetic HTTP evidence. No activation, account reset or cloud I/O."""
from pathlib import Path
import argparse
import json
import sys
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'Backend/Flask'),str(ROOT/'engineering/phase2')]
import test_lite_service_http as legacy
import test_lite_usage_recovery_http as recovery
from lite_attest_state import StateUnavailable


def scenario(modern, action):
    fixture=recovery.RecoveryHTTPTests() if modern else legacy.ServiceHTTPTests()
    fixture.setUp()
    try:return action(fixture.h if modern else fixture)
    finally:fixture.doCleanups()


def probe():
    def next_day(h):
        h.e.now=(h.e.now//86400+1)*86400+100
        result=h.json_request('prepare')
        return dict(http=result.status_code,model_calls=h.model_calls)
    def abandoned(h):
        prepared=[h.json_request('prepare',n).status_code for n in (1,2)]
        h.e.now+=900;result=h.json_request('prepare',3)
        return dict(prepared=prepared,third_http=result.status_code,quota=h.quota(),model_calls=h.model_calls)
    def fence(h):
        h.e.now=(h.e.now//86400+1)*86400-5
        with patch.object(h.ledger,'reserve',side_effect=StateUnavailable('synthetic pre-write outage')):
            initial=h.json_request('prepare')
        _,op=h.ops.load(h.subject,f'{1:032x}');h.e.now+=6
        ticket=h.ops.ticket(h.subject,op);proof=h.ledger.fence_missing(ticket)
        return dict(initial_http=initial.status_code,fence_replay_identical=proof==h.ledger.fence_missing(ticket),
            old_retry_http=h.json_request('prepare').status_code,model_calls=h.model_calls,
            new_operation_exists=h.ops.load(h.subject,f'{2:032x}') is not None,
            old_mapping_preserved=h.ops.load(h.subject,f'{1:032x}')[1]==op,
            successor_native_flow_complete=False)
    result=dict(schema='lite-usage-recovery-probe/1',scope='SYNTHETIC_LOCAL_ONLY',
        G1=dict(before=scenario(False,next_day),opt_in_after=scenario(True,next_day)),
        G2=dict(before=scenario(False,abandoned),opt_in_after=scenario(True,abandoned)),
        G3_fence_only=scenario(True,fence),release_ready=False,
        live_writer_fence_adapter=False,native_successor=False,cloud_writes=0)
    assert result['G1']['before']['http']==503 and result['G1']['opt_in_after']['http']==200
    assert result['G2']['before']['third_http']==429 and result['G2']['opt_in_after']['third_http']==200
    assert result['G2']['opt_in_after']['quota']['attempts']==3
    assert result['G3_fence_only']['old_retry_http']==409
    assert result['G3_fence_only']['fence_replay_identical'] and result['G3_fence_only']['old_mapping_preserved']
    assert not result['G3_fence_only']['new_operation_exists']
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    with args.output.open('x') as f:json.dump(probe(),f,indent=2,allow_nan=False);f.write('\n')
