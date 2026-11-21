"""Loopback-only actual HTTP retry comparison and dispatch recovery evidence."""
import argparse
import importlib.metadata
import json
import unittest
from pathlib import Path
from google.auth.transport.requests import AuthorizedSession
from lite_attest_state import StateUnavailable
from lite_key_lifecycle_dispatch import PinnedKeyLifecycle
from test_lite_key_lifecycle_dispatch import DispatchFixture
from test_lite_key_lifecycle_transport import CredentialsDouble,LocalKMS,TOKEN
from test_lite_rotating_grant_keys import V1,V2
from test_lite_subscription_grace import rows
from test_lite_shared_key_retirement import RetirementFixture
import test_lite_native_purchase as native


def guarded_wire_trial(host):
    x=RetirementFixture(host,backup=False);wire=LocalKMS(x.f.kms);host.addCleanup(wire.close)
    x.provider=PinnedKeyLifecycle.with_access_token(version=V1,protected_version=V2,
        access_token=TOKEN,expires_at=1500,clock=lambda:x.now);host.addCleanup(x.provider.close);x.repin()
    with wire.route():
        try:x.g.prepare()
        except StateUnavailable:pass
        else:raise AssertionError('old backup was accepted')
        assert not any(v['method']!='GET' for v in wire.calls)
        x.refresh_backup();original=x.original_rows();review=x.g.prepare()['review_sha256']
        p=x.g.plan(review,'disable');c=x.command(p);wire.fault='drop'
        try:x.g.execute(p,c,scope=x.scope(p))
        except StateUnavailable:pass
        else:raise AssertionError('response loss not exercised')
        count=sum(v['method']!='GET' for v in wire.calls);x.g=x.restart()
        try:x.g.execute(p,c,scope=x.scope(p))
        except StateUnavailable:pass
        else:raise AssertionError('uncertain guard was replayed')
        assert sum(v['method']!='GET' for v in wire.calls)==count==1
        x.g.reconcile(p,x.drain(p,c));x.perform(review,'schedule');_,_,r=x.perform(review,'restore')
        assert r['provider_state']=='DISABLED' and x.original_rows()==original
        assert x.f.cipher.open(*x.b_snapshot)['refresh_token']=='private-refresh-token'
        assert [v['action'] for v in wire.calls if v['method']!='GET']==['disable','destroy','restore']
        return dict(selected_inventory_and_recovery_checked=True,old_backup_blocks_before_mutation=True,
            signed_retirement_composition_used=True,mutation_attempts=3,extra_attempts_on_replay=0,
            original_versions_audits_grants_quota_preserved=True,real_provider_calls=0)


def run():
    native.legacy.LinksTests.setUpClass();host=unittest.TestCase()
    try:
        x=DispatchFixture(host);f=x.f;wire=LocalKMS(f.kms);host.addCleanup(wire.close)
        with wire.route():
            credentials=CredentialsDouble();old=AuthorizedSession(credentials);old.trust_env=False;host.addCleanup(old.close)
            baseline=PinnedKeyLifecycle(session=old,version=V1,protected_version=V2)
            wire.fault='401';baseline.dispatch('disable')
            baseline_attempts=sum(v['method']!='GET' for v in wire.calls)
            assert baseline_attempts==2 and credentials.refreshes==1
            wire.calls=[];wire.state='ENABLED';f.kms.states[V1]='ENABLED'
            x.provider=PinnedKeyLifecycle.with_access_token(version=V1,protected_version=V2,
                access_token=TOKEN,expires_at=1500,clock=lambda:x.now);host.addCleanup(x.provider.close)
            x.plan('disable');original=rows(f.g.store),rows(f.key_store),rows(f.key_audit),rows(f.f.h.f.ledger.store)
            wire.fault='401';c=x.command()
            try:x.e.execute(c)
            except StateUnavailable:pass
            else:raise AssertionError('401 was hidden')
            assert x.e.inspect()['phase']=='uncertain'
            assert sum(v['method']!='GET' for v in wire.calls)==1
            first=x.e.reconcile(x.drain(c));assert first['provider_state']=='ENABLED'
            x.plan('disable');c=x.command();wire.fault='drop'
            try:x.e.execute(c)
            except StateUnavailable:pass
            else:raise AssertionError('socket response loss not observed')
            assert wire.state=='DISABLED';count=len(wire.calls);x.e=x.restart()
            try:x.e.execute(c)
            except StateUnavailable:pass
            else:raise AssertionError('uncertain operation resent')
            assert len(wire.calls)==count
            resolved=x.e.reconcile(x.drain(c));assert resolved['provider_state']=='DISABLED'
            assert x.e.execute(c)==resolved
            _,scheduled=x.perform('schedule');_,restored=x.perform('restore')
            assert scheduled['provider_state']=='DESTROY_SCHEDULED' and restored['provider_state']=='DISABLED'
            mutations=[v['action'] for v in wire.calls if v['method']!='GET']
            assert mutations==['disable','disable','destroy','restore']
            assert all(v['authorization_valid'] for v in wire.calls)
            assert original==(rows(f.g.store),rows(f.key_store),rows(f.key_audit),rows(f.f.h.f.ledger.store))
            assert f.cipher.open(*x.b_snapshot)['refresh_token']=='private-refresh-token'
            result=dict(schema='key-lifecycle-http-probe/1',synthetic_only=True,
                transport='requests/urllib3 through loopback HTTP; HTTPS adapter route replaced only by test',
                dependencies={n:importlib.metadata.version(n) for n in ('requests','urllib3','google-auth')},
                baseline_401_mutation_attempts=baseline_attempts,baseline_credential_refreshes=credentials.refreshes,
                fixed_401_mutation_attempts=1,fixed_automatic_refreshes=0,extra_attempts_on_uncertain_replay=0,
                explicit_mutation_attempts=4,mutation_sequence=mutations,
                uncertain_socket_outcome_reconciled_readonly=True,restore_returns_disabled=True,
                original_grants_keys_audits_quota_preserved=True,surviving_account_readable=True,
                external_network_calls=0,real_cloud_mutations=0,real_credentials_used=False,
                real_tls_or_iam_verified=False,complete_account_deletion=False,production_wired=False)
        result['retirement_composition']=guarded_wire_trial(host)
        return result
    finally:host.doCleanups()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True,type=Path);a=p.parse_args()
    a.output.write_text(json.dumps(run(),indent=2)+'\n')
