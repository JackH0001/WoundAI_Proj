"""Synthetic expired-reservation and observation/resume evidence; no live provider."""
from pathlib import Path
import argparse,base64,json,sys
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'Backend/Flask'),str(ROOT/'engineering/phase2')]
from test_lite_service_resubmit import ResubmitTests
from test_lite_rollout_review import RolloutReviewTests
from test_lite_attest_request import request
from lite_attest_request import client_data,client_data_hash
from lite_usage_rollout import ObservationUnavailable
from lite_usage_ledger import digest

def fixture():
    t=ResubmitTests();t.setUp()
    try:
        expired=t.expired();h=t.h
        with patch('lite_service_recovery._new_id',side_effect=['2'*32,'3'*32]):a=t.retry()
        assert a.status_code==200,a.json
        new=int(a.json['recovery']['successor_operation'],16)
        ready=h.json_request('prepare',new);done=h.run_request(new)
        assert ready.status_code==done.status_code==200
        assert h.run_request(new).json==done.json and h.model_calls==h.quota()['used']==1
        assert h.ops.load(h.subject,t.op)[1]==t.original
        value=dict(request(),path='/api/v1/lite/service/resubmit')
        binding={**{k:base64.b64encode(v).decode() if isinstance(v,bytes) else v for k,v in value.items()},
                 'expected_client_data':base64.b64encode(client_data(**value)).decode(),
                 'expected_sha256':client_data_hash(**value).hex()}
        return dict(schema='lite-native-service-resubmit/1',installation='synthetic-native-service',
            capture_id=t.op,jpeg=base64.b64encode(h.f.jpeg).decode(),image_sha256=h.image_hash,
            cancelled=expired,authorization=a.json,prepare=ready.json,completed=done.json,
            signature_binding=binding,verified_model_calls=h.model_calls,verified_success_charges=h.quota()['used'])
    finally:t.doCleanups()

def rollout():
    t=RolloutReviewTests();t.setUp()
    try:
        f=t.f;f.activate();before=f.executor._read()
        with patch.object(f.provider,'observe',side_effect=OSError('unavailable')):
            observation=f.executor.inspect()
            try:f.executor.execute()
            except ObservationUnavailable:pass
            else:raise AssertionError('unavailable not reported')
        assert before==f.executor._read() and f.provider.new_enabled
        f.executor.rollback();paused=f.executor._read()[1];usage=f.snap()
        f.executor.resume_paused(expected_pause_sha256=digest(paused),authorization_sha256='a'*64)
        f.executor.advance()
        assert f.snap()==usage and f.provider.legacy_disabled and f.provider.new_enabled
        return dict(observation=observation,active_preserved_after_observation_error=True,
            resumed_with_same_usage=True,legacy_disabled=True,cloud_calls=0)
    finally:t.doCleanups()

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--fixture',type=Path);p.add_argument('--report',type=Path);a=p.parse_args();f=fixture()
    if a.fixture:a.fixture.write_text(json.dumps(f,sort_keys=True,indent=2)+'\n')
    if a.report:a.report.write_text(json.dumps(dict(scope='SYNTHETIC_ONLY',deployment_ready=False,
        resubmit_model_calls=f['verified_model_calls'],success_charges=f['verified_success_charges'],
        originals_preserved=True,rollout=rollout()),sort_keys=True,indent=2)+'\n')
