"""End-to-end selected-source retirement admission; synthetic Google only."""
import argparse
import json
import socket
import unittest
from pathlib import Path
from unittest.mock import patch
from lite_attest_state import StateUnavailable
from lite_shared_key_retirement import RetirementRejected
from lite_rotating_grant_keys_probe import raw_backup_open
from test_lite_shared_key_retirement import RetirementFixture
from test_lite_rotating_grant_keys import V1,V2
import test_lite_native_purchase as native


def run():
    native.legacy.LinksTests.setUpClass();host=unittest.TestCase()
    try:
        with patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden')):
            x=RetirementFixture(host,backup=False,third=True);f=x.f
            try:x.g.prepare()
            except RetirementRejected:pass
            else:raise AssertionError('old-only recovery copy was accepted')
            assert x.mutations()==[]
            x.refresh_backup();baseline=x.original_rows();prepared=x.g.prepare();review=prepared['review_sha256']
            assert prepared['accounts']==3 and prepared['active_accounts']==2 and prepared['closed_accounts']==1
            assert raw_backup_open(f.kms,x.original_a,*x.a_snapshot)['refresh_token']=='private-refresh-token'
            p=x.g.plan(review,'disable');c=x.command(p);scope=x.scope(p)
            x.wire.after=lambda a:(_ for _ in ()).throw(TimeoutError('synthetic lost acknowledgement')) if a=='disable' else None
            try:x.g.execute(p,c,scope=scope)
            except StateUnavailable:pass
            else:raise AssertionError('lost response not detected')
            x.wire.after=lambda a:None;x.g=x.restart()
            before=len(x.mutations())
            try:x.g.execute(p,c,scope=scope)
            except StateUnavailable:pass
            else:raise AssertionError('uncertain operation retried')
            assert len(x.mutations())==before==1
            observed=x.g.reconcile(p,x.drain(p,c));assert observed['provider_state']=='DISABLED'
            assert x.g.execute(p,c,scope=x.scope(p))==observed
            for pair in (x.b_snapshot,x.third_snapshot):assert f.cipher.open(*pair)['refresh_token']=='private-refresh-token'
            p,c,_=x.perform(review,'schedule')
            # A restore must remain possible if the recovery source is unreachable.
            x.x.soft_status=400
            _,_,restored=x.perform(review,'restore');assert restored['provider_state']=='DISABLED'
            x.x.soft_status=200
            assert raw_backup_open(f.kms,x.original_a,*x.a_snapshot)['refresh_token']=='private-refresh-token'
            p,c,_=x.perform(review,'schedule');x.wire.finish_destruction();x.now+=31*86400
            final=x.g.reconcile(p,x.drain(p,c));assert final['provider_state']=='DESTROYED'
            try:raw_backup_open(f.kms,x.original_a,*x.a_snapshot)
            except KeyError:pass  # Synthetic V1 material was removed; not real cloud proof.
            else:raise AssertionError('removed simulated old key still usable')
            for pair in (x.b_snapshot,x.third_snapshot):assert f.cipher.open(*pair)['refresh_token']=='private-refresh-token'
            try:f.cipher.open(*x.a_snapshot)
            except StateUnavailable:pass
            else:raise AssertionError('closed account revived')
            assert baseline==x.original_rows() and f.kms.states[V2]=='ENABLED'
            assert len(x.mutations())==4
            proof=x.g._review(review)
            return dict(schema='lite-shared-key-retirement-probe/1',synthetic_only=True,network_allowed=False,
                accounts=3,active_accounts=2,closed_accounts=1,selected_sources=2,
                physical_versions=sum(len(s['records']) for s in proof['binding']['inventory']['sources']),
                stale_backup_blocked_before_mutation=True,new_recovery_copies_verified=True,
                provider_mutation_attempts=4,extra_mutations_on_uncertain_replay=0,
                surviving_accounts_read_after_old_key_removed=True,closed_account_never_reopened=True,
                restore_possible_during_inventory_outage=True,restore_did_not_enable_key=True,
                original_versions_grants_audits_quota_unchanged=True,
                coverage_and_drain_are_signed_external_attestations=True,
                real_cloud_calls=0,real_keys_destroyed=0,complete_account_deletion=False,
                physical_backup_erasure_verified=False,production_startup_enabled=False,final=final)
    finally:host.doCleanups()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True,type=Path);a=p.parse_args()
    a.output.write_text(json.dumps(run(),indent=2)+'\n')
