"""Offline inventory replay: physical copies outlive closure/current-row rewrap.

Actual requests preparation, AES and custody validation; synthetic storage only.
Does not access, restore, delete or decrypt real provider data.
"""
import argparse
import copy
import json
import socket
import unittest
from pathlib import Path
from unittest.mock import patch
import requests
from lite_attest_state import StateUnavailable
from lite_shared_key_inventory import SharedKeyInventory, GCSInventoryReader
from lite_usage_ledger import digest
from lite_rotating_grant_keys_probe import raw_backup_open
from test_lite_shared_key_inventory import InventoryFixture, InventoryRESTAdapter
from test_lite_rotating_grant_keys import V1, V2, V3
from test_lite_subscription_grace import rows
import test_lite_native_purchase as native


def run():
    native.legacy.LinksTests.setUpClass();host=unittest.TestCase()
    try:
        with patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden')):
            x=InventoryFixture(host);f=x.f;x.add_mirror();x.close_and_rotate()
            f.login('device-three','apple-human-3');third=f.person
            source=digest(f.cipher._read(third)[1]);f.rotate_to(V3);cas=x.store.compare_exchange
            def interrupt(k,g,v):
                if k==f.cipher._key(third):raise StateUnavailable('synthetic CAS interruption')
                return cas(k,g,v)
            with patch.object(x.store,'compare_exchange',side_effect=interrupt):
                try:f.cipher.rewrap(third,expected_record_sha256=source)
                except StateUnavailable:pass
                else:raise AssertionError('interruption not exercised')
            assert f.cipher._read(third)[1]['version']==V2
            copies=lambda:copy.deepcopy([(b.name,b.live,b.versions) for b in x.buckets.values()])
            before=(copies(),rows(f.key_audit),rows(f.g.store),rows(f.f.h.f.ledger.store),f.kms.calls.copy())
            with requests.Session() as session:
                session.trust_env=False;adapter=InventoryRESTAdapter(x);session.mount('https://',adapter)
                def inspect():
                    return SharedKeyInventory(cipher=f.cipher,sources=x.sources,expected_sources=digest(x.sources),
                        get=GCSInventoryReader(session)).inspect()
                report=inspect();expected=x.independent_counts()
                assert report['all_selected_material_observed'] and report['observed_wrapped_dependencies']==expected
                # Primary current rows no longer depend on V1. An old mirrored
                # row, historical primary generations and an initial proposal do.
                primary=report['sources'][0]['records']
                assert not any(r['kind']=='current_key' and not r['noncurrent'] and r['wrapped_version']==V1 for r in primary)
                assert expected[V1]['current']==1 and expected[V1]['historical']>0 and expected[V1]['proposals']>0
                assert expected[V3]==dict(current=0,historical=0,proposals=1)
                # Soft-deleted listing is separate. It cannot grant media access.
                (name,g),raw=next(iter(x.bucket.versions.items()));x.soft[(name,g+1000)]=raw
                soft=inspect();assert not soft['all_selected_material_observed']
                x.soft.clear();x.soft_status=400
                unavailable=inspect();assert not unavailable['all_selected_material_observed']
                assert all(s['soft_count'] is None for s in unavailable['sources'])
                http_calls=len(adapter.calls)
            after=(copies(),rows(f.key_audit),rows(f.g.store),rows(f.f.h.f.ledger.store),f.kms.calls.copy())
            assert before==after
            for r in (report,soft,unavailable):
                for k in ('complete_global_inventory_verified','external_backups_verified','all_writers_drained','kms_destruction_authorized'):
                    assert r[k] is False
            assert raw_backup_open(f.kms,x.before_key,x.envelope,x.context)['refresh_token']=='private-refresh-token'
            return dict(schema='lite-shared-key-inventory-probe/1',synthetic_only=True,network_allowed=False,
                accounts=3,selected_sources=2,uncommitted_proposals=1,physical_dependencies=expected,
                original_versions_audit_grants_quota_unchanged=True,independent_copy_count_matches=True,
                independent_closed_backup_still_decryptable=True,inventory=report,
                soft_deleted_incomplete=not soft['all_selected_material_observed'],
                soft_policy_400_stays_unknown=True,http_get_calls=http_calls,provider_mutations=0,
                real_kms_calls=0,inventory_kms_calls=0,restore_calls=0,delete_calls=0,
                actual_cloud_verified=False,production_startup_enabled=False)
    finally:host.doCleanups()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True,type=Path);a=p.parse_args()
    a.output.write_text(json.dumps(run(),indent=2,ensure_ascii=False)+'\n')
