"""Reproducible signed-login/private-provisioning evidence; local synthetic only."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'engineering/phase2'))
from test_lite_key_provisioning import ProvisionedLoginTests


def run():
    ProvisionedLoginTests.setUpClass();out={}
    for scenario in ('fresh','private_reply_lost','kms_reply_lost','closure_during_create'):
        t=ProvisionedLoginTests();t.setUp()
        try:
            t.begin();before=dict(t.fields)
            if scenario=='private_reply_lost':t.transport.lost=True
            if scenario=='kms_reply_lost':t.kms.lost=True
            if scenario=='closure_during_create':
                t.kms.after=lambda *a:t.w.life.close(t.person,t.owner,'d'*32)
            first=t.confirm();first_apple=len(t.h.grant.calls)
            again=t.confirm()
            row=dict(first_status=first.status_code,retry_status=again.status_code,
                apple_exchange_after_first=first_apple,apple_exchange_total=len(t.h.grant.calls),
                kms_create_posts=t.kms.creates(),kms_versions=len(t.kms.states),
                saved_request_unchanged=before==t.fields,model_calls=0,used=0)
            if scenario!='closure_during_create':
                t.assertEqual(again.status_code,200)
                one=t.complete(1);replay=t.run_request(1)
                t.assertEqual(replay.json,one.json)
                row.update(model_calls=t.h.model_calls,used=t.quota()['used'],same_service_receipt=replay.json==one.json)
                t.assertEqual((row['apple_exchange_total'],row['kms_versions'],row['model_calls'],row['used']),(1,1,1,1))
            else:
                t.assertEqual((first.status_code,again.status_code,len(t.h.grant.calls)),(503,401,0))
                row.update(key_destroyed=False,orphan_cleanup_required=True)
            out[scenario]=row
        finally:t.doCleanups()
    return dict(schema='lite-key-provisioning-probe/1',passed=True,scope='local synthetic REST/SQLite/signed HTTP; not live KMS/Apple',scenarios=out)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    result=run();a.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
