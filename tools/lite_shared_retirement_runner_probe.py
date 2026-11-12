"""Replay the one-shot retirement runner on synthetic encrypted accounts only."""
import argparse
import copy
import json
from pathlib import Path
import socket
import unittest
from unittest.mock import patch
from lite_attest_state import StateUnavailable
from test_lite_shared_retirement_runner import RunnerFixture
import test_lite_native_purchase as native


def probe():
    native.legacy.LinksTests.setUpClass();host=unittest.TestCase()
    try:
        with patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden')):
            f=RunnerFixture(host);x=f.x;original=f.snapshot()
            review=f.call('prepare')['result']
            plan=f.call('plan',review=review['review_sha256'],action='disable')['result'];command=x.command(plan)
            x.wire.after=lambda action:(_ for _ in ()).throw(TimeoutError()) if action=='disable' else None
            try:f.call('execute',plan=plan,command=command,scope=x.scope(plan))
            except StateUnavailable:pass
            else:raise AssertionError('fault not injected')
            x.wire.after=lambda action:None
            try:f.call('execute',plan=plan,command=command,scope=x.scope(plan))
            except StateUnavailable:pass
            else:raise AssertionError('uncertain mutation was retried')
            reconciled=f.call('reconcile',plan=plan,drain=x.drain(plan,command))
            assert len(x.mutations())==1 and reconciled['result']['provider_state']=='DISABLED'
            states=[]
            for action in ('schedule','restore'):
                p=f.call('plan',review=review['review_sha256'],action=action)['result']
                c=x.command(p);r=f.call('execute',plan=p,command=c,scope=x.scope(p))
                again=f.call('execute',plan=p,command=c,scope=x.scope(p))
                assert r['result']==again['result'];states.append(r['result']['provider_state'])
            assert len(x.mutations())==3 and f.snapshot()==original
            assert x.f.cipher.open(*x.b_snapshot)['refresh_token']=='private-refresh-token'
            return dict(schema='lite-shared-retirement-runner-probe/1',passed=True,
                synthetic_accounts=2,active_recovery_preserved=True,original_records_preserved=True,
                lost_reply_mutations=1,total_lifecycle_mutations=3,
                states=['DISABLED']+states,completed_replay_mutations=0,
                invocations=len(f.providers),provider_cleanup_complete=all(p.closed==1 for p in f.providers),
                real_cloud_calls=0,account_deleted=False,physical_backups_erased=False)
    finally:host.doCleanups()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    result=probe();a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(result,sort_keys=True,indent=2)+'\n');print(json.dumps(result,sort_keys=True))
