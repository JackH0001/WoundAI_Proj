"""Reproducible signed synthetic G3 responses and operational failure evidence."""
from pathlib import Path
import argparse,base64,json,sys
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'Backend/Flask'),str(ROOT/'engineering/phase2')]
from test_lite_service_recovery import CrossDayTests
from test_lite_usage_rollout import RolloutTests
from test_lite_attest_request import request
from lite_attest_request import client_data,client_data_hash

def fixture():
    t=CrossDayTests();t.setUp()
    try:
        t.stranded();h=t.h
        available=h.json_request('status')
        with patch('lite_service_recovery._new_id',side_effect=['2'*32,'3'*32]):
            authorized=t.recover()
        new=t.new_id(authorized);ready=h.json_request('prepare',new);done=h.run_request(new)
        assert all(r.status_code==200 for r in (available,authorized,ready,done))
        assert h.run_request(new).json==done.json and h.model_calls==1 and h.quota()['used']==1
        value=dict(request(),path='/api/v1/lite/service/recover')
        binding={**{k:base64.b64encode(v).decode() if isinstance(v,bytes) else v for k,v in value.items()},
            'expected_client_data':base64.b64encode(client_data(**value)).decode(),
            'expected_sha256':client_data_hash(**value).hex()}
        return dict(schema='lite-native-service-recovery/1',installation='synthetic-native-service',
            capture_id=t.op,jpeg=base64.b64encode(h.f.jpeg).decode(),image_sha256=h.image_hash,
            available=available.json,authorization=authorized.json,prepare=ready.json,completed=done.json,
            signature_binding=binding,verified_model_calls=h.model_calls,verified_success_charges=h.quota()['used'])
    finally:t.doCleanups()

def rollout():
    t=RolloutTests();t.setUp()
    try:
        t.activate();before=t.snap();state=t.executor.rollback();after=t.snap()
        assert before==after and state['phase']=='paused' and t.provider.legacy_disabled and not t.provider.new_enabled
        return dict(scope='SYNTHETIC_ONLY',active_before_pause=before,after_pause=after,
            legacy_writers_remain_disabled=True,phase=state['phase'],cloud_calls=0)
    finally:t.doCleanups()

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--fixture',type=Path);p.add_argument('--report',type=Path)
    a=p.parse_args();f=fixture()
    if a.fixture:a.fixture.write_text(json.dumps(f,sort_keys=True,indent=2)+'\n')
    if a.report:a.report.write_text(json.dumps(dict(g3_model_calls=f['verified_model_calls'],
        g3_success_charges=f['verified_success_charges'],originals_preserved=True,rollout=rollout(),
        deployment_ready=False,live_provider_implemented=False),sort_keys=True,indent=2)+'\n')

