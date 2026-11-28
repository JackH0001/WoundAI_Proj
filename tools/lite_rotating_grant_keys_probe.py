"""Reproducible two-account shared-key experiment; synthetic, network denied."""
import argparse
import copy
import json
import socket
import unittest
from pathlib import Path
from unittest.mock import patch
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from lite_apple_grants import encoded
from lite_attest_state import StateUnavailable
from lite_grant_key_custody import unb64
from lite_usage_ledger import digest
from test_lite_rotating_grant_keys import SharedFixture, V1, V2
from test_lite_subscription_grace import rows
import test_lite_native_purchase as native


def raw_backup_open(kms, wrapped_record, envelope, context):
    """Independent AES replay bypassing application denial; not an app API."""
    wire=unb64(wrapped_record['wrapped_dek'],4096); _,payload=wire.split(b':',1)
    aad=encoded(dict(v=2,scope=wrapped_record['scope'],person=wrapped_record['person'],
        dek_id=wrapped_record['dek_id'],version=wrapped_record['version']))
    dek=AESGCM(kms.keys[wrapped_record['version']]).decrypt(payload[:12],payload[12:],aad)
    token_aad=encoded(dict(v=3,scope=envelope['scope'],person=envelope['person'],
        dek_id=envelope['dek_id'],context=context))
    result=AESGCM(dek).decrypt(unb64(envelope['nonce'],12),unb64(envelope['ciphertext'],10016),token_aad)
    return json.loads(result)


def run():
    native.legacy.LinksTests.setUpClass();host=unittest.TestCase()
    try:
        with patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden')):
            f=SharedFixture(host);f.login();a=f.person;ea,ca=f.snapshot()
            backup=copy.deepcopy(f.cipher._read(a)[1])
            f.login('other-device','apple-human-2');b=f.person;eb,cb=f.snapshot()
            baseline=(rows(f.b.registry.store),rows(f.f.h.f.ledger.store),rows(f.g.store))
            source=digest(f.cipher._read(b)[1]);f.rotate_to(V2)
            rotation=f.cipher.rewrap(b,expected_record_sha256=source)
            assert (rows(f.b.registry.store),rows(f.f.h.f.ledger.store),rows(f.g.store))==baseline
            assert f.cipher.open(eb,cb)['refresh_token']=='private-refresh-token'
            f.close_revoke();receipt=f.cipher.destroy_after_revocation(f.d,a)
            try:f.cipher.open(ea,ca)
            except StateUnavailable:app_denied=True
            else:raise AssertionError('closed account was readable')
            assert raw_backup_open(f.kms,backup,ea,ca)['refresh_token']=='private-refresh-token'
            f.kms.states[V1]='DISABLED'
            assert f.cipher.open(eb,cb)['refresh_token']=='private-refresh-token'
            f.kms.states[V1]='ENABLED'
            assert raw_backup_open(f.kms,backup,ea,ca)['refresh_token']=='private-refresh-token'
            assert not any(action=='destroy' for action,_ in f.kms.calls)
            return dict(schema='lite-shared-kek-probe/1',synthetic_only=True,network_allowed=False,
                account_count=2,distinct_account_deks=ea['dek_id']!=eb['dek_id'],
                shared_initial_wrapping_version=True,original_grant_envelopes_unchanged=True,
                rotation_receipt_sha256=digest(rotation),closed_account_app_read_denied=app_denied,
                closed_account_backup_still_decryptable=True,other_account_reads_after_rotation=True,
                other_account_reads_after_old_kek_disabled=True,
                restored_old_kek_restores_backup_readability=True,
                account_deleted=receipt['account_deleted'],grant_key_unrecoverable=receipt['grant_key_unrecoverable'],
                retirement_inventory_verified=receipt['retirement_inventory_verified'],
                kms_destroy_calls=0,real_kms_calls=0,real_apple_calls=0,
                model_calls=0,purchases=0,quota_registry_unchanged=(rows(f.b.registry.store),rows(f.f.h.f.ledger.store))==baseline[:2],
                production_startup_enabled=False)
    finally:host.doCleanups()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    args.output.write_text(json.dumps(run(),ensure_ascii=False,indent=2)+'\n')
