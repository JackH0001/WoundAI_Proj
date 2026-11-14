"""Offline recovery trial: fixed KMS requests, lost replies and retained backups.

The provider lifecycle and material removal are simulated. No real key or account
is destroyed; this cannot certify Google backup removal or complete retirement.
"""
import argparse
import json
import socket
import unittest
from pathlib import Path
from unittest.mock import patch
from lite_attest_state import StateUnavailable
from test_lite_key_lifecycle_dispatch import DispatchFixture
from test_lite_subscription_grace import rows
from test_lite_rotating_grant_keys import V1, V2
from lite_rotating_grant_keys_probe import raw_backup_open
import test_lite_native_purchase as native


def run():
    native.legacy.LinksTests.setUpClass();host=unittest.TestCase()
    try:
        with patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden')):
            x=DispatchFixture(host);f=x.f
            original=(rows(f.g.store),rows(f.key_store),rows(f.key_audit),rows(f.f.h.f.ledger.store))
            def backup():return raw_backup_open(f.kms,x.old,*x.a_snapshot)
            assert backup()['refresh_token']=='private-refresh-token'
            command=x.command()
            x.wire.after=lambda action:(_ for _ in ()).throw(TimeoutError('synthetic response lost')) if action=='disable' else None
            try:x.e.execute(command)
            except StateUnavailable:pass
            else:raise AssertionError('lost response not exercised')
            assert x.e.inspect()['phase']=='uncertain' and f.kms.states[V1]=='DISABLED'
            calls=len(x.wire.calls)
            try:x.e.execute(command)
            except StateUnavailable:pass
            else:raise AssertionError('uncertain mutation was retried')
            assert len(x.wire.calls)==calls
            x.wire.after=lambda action:None
            x.e=x.restart();reconciled=x.e.reconcile(x.drain(command))
            assert reconciled['provider_state']=='DISABLED' and len(x.mutations())==1
            assert f.cipher.open(*x.b_snapshot)['refresh_token']=='private-refresh-token'
            _,scheduled=x.perform('schedule')
            assert scheduled['provider_state']=='DESTROY_SCHEDULED' and backup()['refresh_token']=='private-refresh-token'
            _,restored=x.perform('restore')
            assert restored['provider_state']=='DISABLED' and f.kms.states[V1]=='DISABLED'
            assert backup()['refresh_token']=='private-refresh-token'
            try:f.cipher.open(*x.a_snapshot)
            except StateUnavailable:pass
            else:raise AssertionError('closed account reopened')
            final_command,_=x.perform('schedule');x.wire.finish_destruction();x.now+=86400*31
            destroyed=x.e.reconcile(x.drain(final_command))
            assert destroyed['provider_state']=='DESTROYED' and V1 not in f.kms.keys
            try:backup()
            except KeyError:pass  # Independent AES cannot obtain removed synthetic KEK.
            else:raise AssertionError('synthetic key material remained')
            assert f.cipher.open(*x.b_snapshot)['refresh_token']=='private-refresh-token' and f.kms.states[V2]=='ENABLED'
            assert original==(rows(f.g.store),rows(f.key_store),rows(f.key_audit),rows(f.f.h.f.ledger.store))
            assert [action for action,_,_,_ in x.mutations()]==['disable','destroy','restore','destroy']
            for r in (reconciled,scheduled,restored,destroyed):
                for k in ('complete_inventory_verified','account_deleted','physical_backups_erased'):assert r[k] is False
            return dict(schema='lite-key-lifecycle-dispatch-probe/1',synthetic_only=True,network_allowed=False,
                accounts=2,provider_mutation_attempts=4,extra_attempts_on_uncertain_replay=0,
                mutation_sequence=[a for a,_,_,_ in x.mutations()],recovery_used_independent_signature=True,
                provider_states=[r['provider_state'] for r in (reconciled,scheduled,restored,destroyed)],
                original_custody_grants_quota_unchanged=True,surviving_account_reads_after_old_key_removed=True,
                closed_account_never_reopened=True,backup_readable_while_scheduled_or_restored=True,
                synthetic_key_material_removed=True,real_keys_destroyed=0,real_provider_calls=0,
                real_cloud_acceptance=False,complete_retirement=False,complete_account_deletion=False,
                production_startup_enabled=False,final=destroyed)
    finally:host.doCleanups()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True,type=Path);a=p.parse_args()
    a.output.write_text(json.dumps(run(),indent=2,ensure_ascii=False)+'\n')
