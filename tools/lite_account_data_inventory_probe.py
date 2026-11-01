"""Replay one generated multi-device account; no Apple/GCS network or deletion."""
import argparse
import json
import unittest
from pathlib import Path
from lite_disposition_progress_probe import ProgressFixture
from lite_attest_state import StateUnavailable
from lite_usage_ledger import digest
from test_lite_subscription_grace import rows
import test_lite_native_purchase as native


def run():
    native.legacy.LinksTests.setUpClass();host=unittest.TestCase()
    try:
        f=ProgressFixture(host);f.f.login('device-two');f.f.login('device-one')
        f.complete();f.close();before=f.preserved();original=f.capture
        calls=f.f.g.calls.copy(),f.f.kms.calls.copy(),f.calls
        try:from lite_account_data_inventory import AccountDataInventory
        except ModuleNotFoundError as e:
            if e.name!='lite_account_data_inventory':raise
            return dict(schema='account-data-inventory-probe/1',available=False,account_deleted=False)
        audit=f.store('inventory-audit');i=AccountDataInventory(deletion=f.d,audit_store=audit)
        sources={n:rows(s) for n,s in i.stores.items()}
        first=i.inspect(f.person,limit=2);second=i.inspect(f.person,cursor=first['next_cursor'],limit=2)
        found=first['grants']+second['grants']
        assert [x['position'] for x in found]==[3,2,1]
        assert len({x['installation_sha256'] for x in found})==2
        assert len({x['grant_id'] for x in found})==3
        i.verify_unchanged(first['report_sha256']);i.verify_unchanged(second['report_sha256'])
        assert before==f.preserved() and original==f.capture
        assert sources=={n:rows(s) for n,s in i.stores.items()}
        assert calls==(f.f.g.calls,f.f.kms.calls,f.calls)
        account=f.f.b._read('device-one')[1]['account']
        for secret in (account['token'],account['subject'],'private-refresh-token','private-access-token'):
            assert secret not in repr(first)+repr(second)+repr(rows(audit))
        # Independent saved-state drift: same values, different generation.
        k=f.f.b._key('device-one');v,j=f.f.b.store.load(k);f.f.b.store.compare_exchange(k,v,j)
        try:i.verify_unchanged(second['report_sha256'])
        except StateUnavailable:drift_rejected=True
        else:drift_rejected=False
        assert drift_rejected
        return dict(schema='account-data-inventory-probe/1',available=True,synthetic_only=True,
            observed_grants=len(found),observed_installations=2,pages=2,
            original_data_unchanged_during_inventory=True,new_model_calls=0,new_Apple_KMS_calls=0,
            generation_drift_rejected=drift_rejected,report_contains_token=False,
            whole_account_inventory_complete=False,account_deleted=False,
            research_changed=False,cloud_access=False,cleanup_authorized=False)
    finally:host.doCleanups()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True,type=Path);a=p.parse_args()
    a.output.write_text(json.dumps(run(),indent=2)+'\n')
