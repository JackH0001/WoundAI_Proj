"""Offline signed-batch experiment; real AES/SQLite, simulated KMS/Apple only."""
import argparse
import json
import socket
import unittest
from pathlib import Path
from unittest.mock import patch
from lite_attest_state import StateUnavailable
from lite_subscription_executor import key
from lite_usage_ledger import digest
from test_lite_shared_kek_preparation import PreparationFixture
from test_lite_rotating_grant_keys import V1, V2
from test_lite_subscription_grace import rows
from lite_rotating_grant_keys_probe import raw_backup_open
import test_lite_native_purchase as native


def run():
    native.legacy.LinksTests.setUpClass(); host=unittest.TestCase()
    try:
        with patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden')):
            x=PreparationFixture(host,accounts=3,steps=1); f=x.f; e=x.executor
            a=f.who('apple-human-1'); f.close_revoke()
            closure=f.cipher.destroy_after_revocation(f.d,a)
            baseline=(rows(f.b.registry.store),rows(f.f.h.f.ledger.store),rows(f.g.store))
            before=sum(action=='encrypt' for action,_ in f.kms.calls)
            # Lose a receipt after the rewrapped key was committed. The new
            # process reads the same proposal and does not generate another DEK.
            cas=f.key_audit.compare_exchange; injected=[]
            def fail(k,g,v):
                if set(v)=={'v','operation','source_sha256','target_sha256'} and not injected:
                    injected.append(True); raise StateUnavailable('synthetic acknowledgement lost')
                return cas(k,g,v)
            with patch.object(f.key_audit,'compare_exchange',side_effect=fail):
                while e.inspect()['phase']!='uncertain':
                    try: e.execute(x.command())
                    except StateUnavailable:
                        assert injected and e.inspect()['phase']=='uncertain'
                        break
            pending=e.inspect()
            x.executor=e=x.restart(); command=x.command('resume')
            wraps_before_resume=sum(action=='encrypt' for action,_ in f.kms.calls)
            recovered=e.execute(command,drain=x.drain(command))
            assert sum(action=='encrypt' for action,_ in f.kms.calls)==wraps_before_resume
            final=x.complete()
            # Independent crypto replay intentionally succeeds for the closed
            # account's old backup. This workflow MUST NOT claim its destruction.
            ea,ca=x.snapshots[a]
            assert raw_backup_open(f.kms,x.sources[a],ea,ca)['refresh_token']=='private-refresh-token'
            f.kms.states[V1]='DISABLED'  # synthetic provider observation, not executor action
            for p,(envelope,ctx) in x.snapshots.items():
                if p!=a: assert f.cipher.open(envelope,ctx)['refresh_token']=='private-refresh-token'
            f.kms.states[V1]='ENABLED'
            assert raw_backup_open(f.kms,x.sources[a],ea,ca)['refresh_token']=='private-refresh-token'
            try: f.cipher.open(ea,ca)
            except StateUnavailable: pass
            else: raise AssertionError('closed account revived')
            assert baseline==(rows(f.b.registry.store),rows(f.f.h.f.ledger.store),rows(f.g.store))
            assert not any(action=='destroy' for action,_ in f.kms.calls)
            assert f.key_audit.load(key('shared-kek-write-fence',digest([f.cipher.scope,V1])))
            wraps=sum(action=='encrypt' for action,_ in f.kms.calls)-before
            assert wraps==2 and final['cursor']==3 and final['selected_batch_complete']
            assert not any(final[k] for k in ('complete_inventory_verified','all_writers_drained',
                                              'kms_destruction_authorized','account_deleted'))
            return dict(schema='lite-shared-kek-preparation-probe/1',synthetic_only=True,
                network_allowed=False,selected_accounts=3,live_accounts_rewrapped=2,
                closed_accounts_preserved=1,kms_wrap_calls=wraps,extra_wraps_on_recovery=0,
                interrupted_phase=pending['phase'],recovered_cursor=recovered['cursor'],
                final=final,original_grants_and_quota_unchanged=True,
                surviving_accounts_read_with_old_kek_disabled=True,
                closed_account_app_denied=True,closed_backup_still_decryptable=True,
                closure_receipt_sha256=digest(closure),real_kms_calls=0,kms_destroy_calls=0,
                real_apple_calls=0,model_calls=0,production_startup_enabled=False)
    finally: host.doCleanups()


if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--output',required=True,type=Path); args=p.parse_args()
    args.output.write_text(json.dumps(run(),indent=2,ensure_ascii=False)+'\n')
