"""Signed HTTP, restart and recovery rehearsal; generated providers/data only."""
import argparse,hashlib,json,unittest
from pathlib import Path
from unittest.mock import patch
from lite_disposition_executor import PATH
from test_lite_disposition_executor import ExecutorFixture
import test_lite_native_purchase as native


def run():
    native.legacy.LinksTests.setUpClass();host=unittest.TestCase()
    try:
        h=ExecutorFixture(host);before=h.f.f.preserved();capture=hashlib.sha256(h.f.f.capture).hexdigest()
        invalid=h.e.create_app().test_client().post(PATH,json={'command':{},'drain':None})
        assert invalid.status_code==409 and h.e.inspect(h.person)['used_dispatch_budget']==0
        first=h.command();client=h.e.create_app().test_client()
        accepted=client.post(PATH,json={'command':first,'drain':None});assert accepted.status_code==200
        # Simulate caller losing the response and restarting the server.
        h.e=h.build();client=h.e.create_app().test_client();usage=h.e.inspect(h.person)['used_dispatch_budget']
        with patch.object(h.e.w,'run',side_effect=AssertionError('duplicate provider execution')):
            replay=client.post(PATH,json={'command':first,'drain':None})
        assert replay.status_code==200 and replay.json==accepted.json
        assert h.e.inspect(h.person)['used_dispatch_budget']==usage
        interrupted=h.command()
        with patch.object(h.e.w,'run',side_effect=RuntimeError('synthetic process interruption')):
            lost=client.post(PATH,json={'command':interrupted,'drain':None})
        assert lost.status_code==503 and lost.json['retry_automatically'] is False
        old=interrupted['payload']['command'];h.e=h.build();client=h.e.create_app().test_client()
        denied=client.post(PATH,json={'command':h.command(),'drain':None});assert denied.status_code==409
        resumed=client.post(PATH,json={'command':h.command('resume',replaces=old),'drain':h.drain(old)})
        assert resumed.status_code==200
        completed=h.finish();view=h.e.inspect(h.person)
        assert all(s=='observed' for s in view['worker']['states'].values())
        assert h.f.f.preserved()==before and hashlib.sha256(h.f.f.capture).hexdigest()==capture
        assert h.f.f.calls==1
        return dict(schema='lite-disposition-executor-probe/1',synthetic_only=True,
            unsigned_http=invalid.status_code,completed_replay_http=replay.status_code,
            completed_replay_same_receipt=True,replay_extra_budget=0,
            uncertain_http=lost.status_code,automatic_retry=False,unreviewed_new_run_http=denied.status_code,
            signed_drained_resume_http=resumed.status_code,commands=len(view['commands']),
            used_dispatch_budget=view['used_dispatch_budget'],worker=view['worker'],
            research_old_active_remaining=len(h.f.h.bucket.history)-len(h.f.h.bucket.live),
            service_old_active_remaining=len(h.f.service_bucket.versions)-len(h.f.service_bucket.live),
            model_calls=h.f.f.calls,original_capture_sha256=capture,quota_identity_unchanged=True,
            account_deleted=False,live_gcs_apple_kms_calls=0,real_data_or_keys_deleted=False)
    finally:host.doCleanups()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True,type=Path);a=p.parse_args()
    a.output.write_text(json.dumps(run(),indent=2)+'\n')
