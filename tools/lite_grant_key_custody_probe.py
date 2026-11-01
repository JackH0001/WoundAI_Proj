"""Before/after credential-backup replay; synthetic Apple/KMS, real AES and CAS.

Run this same file against the parent source as well. It never imports cloud
credentials, provisions resources or removes any user/research data.
"""
import argparse
import copy
import json
from pathlib import Path
import unittest
from lite_attest_state import StateUnavailable
from test_lite_account_lifecycle import Fixture
from test_lite_account_deletion import coordinator
from test_lite_subscription_grace import rows
import test_lite_native_purchase as native


def run():
    native.legacy.LinksTests.setUpClass();host=unittest.TestCase()
    try:
        try:from test_lite_grant_key_custody import KeyFixture
        except ModuleNotFoundError:KeyFixture=None
        f=KeyFixture(host) if KeyFixture else Fixture(host)
        f.login();person=f.person;cipher=f.g.cipher
        grant=copy.deepcopy(f.g.a._row(f.g.a._id(f.g.c['installation'],f.g.c['operation']))[1])
        original=copy.deepcopy(grant)
        f.login('other-device','apple-human-2')
        other=copy.deepcopy(f.g.a._row(f.g.a._id(f.g.c['installation'],f.g.c['operation']))[1])
        baseline=[rows(s) for s in (f.b.registry.store,f.f.h.f.ledger.store)]
        d=f.d if KeyFixture else coordinator(f)
        d.begin('device-one','d'*32);revocation=d.advance(person)
        assert revocation['apple_revocation']=='confirmed'
        after_revoke=cipher.open(grant['envelope'],grant['context'])
        report=dict(schema='lite-grant-key-custody-probe/1',synthetic_only=True,
            account_keys_supported=KeyFixture is not None,
            old_backup_decryptable_after_current_ciphertext_removed=bool(after_revoke),
            old_backup_decryptable_after_key_destruction=None,
            scheduled_claimed_unrecoverable=None,other_account_still_decrypts=False,
            live_kms_calls=0,live_apple_calls=0,cloud_resources_created=0,
            account_deleted=False,research_data_deleted=False)
        if KeyFixture:
            backup=copy.deepcopy(cipher._read(person)[1])
            scheduled=cipher.destroy_after_revocation(d,person)
            report['scheduled_claimed_unrecoverable']=scheduled['grant_key_unrecoverable']
            assert not scheduled['grant_key_unrecoverable']
            f.kms.destroy_now(backup['version'])
            final=cipher.destroy_after_revocation(d,person)
            assert final['grant_key_unrecoverable'] and not final['physical_backup_deletion_confirmed']
            # Bypass the app's own fence: try the backed-up wrapped key directly.
            try:f.provider.unwrap(backup['version'],backup['wrapped_dek'],cipher._aad(person))
            except StateUnavailable:report['old_backup_decryptable_after_key_destruction']=False
            else:raise AssertionError('destroyed wrapping key unexpectedly decrypted backup')
            report['kms_receipt']=final['kms_state']
            report['physical_backup_deletion_confirmed']=final['physical_backup_deletion_confirmed']
        report['other_account_still_decrypts']=bool(cipher.open(other['envelope'],other['context']))
        report['original_snapshot_unchanged']=grant==original
        report['registry_and_usage_unchanged']=baseline==[rows(s) for s in (f.b.registry.store,f.f.h.f.ledger.store)]
        assert report['other_account_still_decrypts'] and report['original_snapshot_unchanged']
        assert report['registry_and_usage_unchanged'] and not d.status('device-one','d'*32)['account_deleted']
        return report
    finally:host.doCleanups()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True,type=Path);a=p.parse_args()
    a.output.write_text(json.dumps(run(),indent=2)+'\n')
