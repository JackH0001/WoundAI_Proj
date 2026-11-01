"""Same generated RGB-D epochs on parent/candidate; no network or real erasure."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import unittest
from test_lite_research_history import HistoryFixture
from test_lite_subscription_grace import rows
from lite_fenced_objects import ObjectRef, GenerationConflict
import test_lite_native_purchase as native


def run(*,soft=False):
    native.legacy.LinksTests.setUpClass();host=unittest.TestCase()
    try:
        f=HistoryFixture(host);m=f.m;current='legacy'
        m.seed(3,owner='c'*32)
        for i in range(3):
            for _ in range(3):m.seed(7,ep=current)
            if i<2:
                p=m.r.privacy.scoped(current);p.manifest.withdraw(m.owner);p.complete_withdrawal(m.owner)
                following=str(i+1)*32;m.rejoin(current,following);current=following
        ref=ObjectRef(m.owner,'image',f'{3:016x}')
        # Use an actually existing current slot, independent of kind ordering.
        names=sorted(n for n in f.bucket.live if '/'+m.owner+'/' in n and current in n)
        parts=names[0].split('/');ref=ObjectRef(m.owner,parts[-2],parts[-1])
        objects=m.r.privacy.scoped(current).objects;pin=objects.pin(ref)
        f.close();seals=copy.deepcopy(f.bucket.live)
        other={k:v for k,v in f.bucket.history.items() if '/'+('c'*32)+'/' in k[0]}
        preserved=(m.f.preserved(),rows(m.r.store),rows(m.audit),rows(m.closure.audit))
        original_capture=hashlib.sha256(m.f.capture).hexdigest()
        old=lambda:sum(1 for (n,g) in f.bucket.history if '/'+m.owner+'/' in n and g<f.bucket.live[n][0])
        before=old();r=None;calls=0;maximum=0
        if soft:f.bucket._properties['softDeletePolicy']={'retentionDurationSeconds':'604800'}
        if f.h is not None:
            for calls in range(1,300):
                n=len(f.bucket.deletes)
                r=f.h.advance_account(m.f.person,versions=2,apply=True);f.h=f.worker()
                maximum=max(maximum,len(f.bucket.deletes)-n)
                if r['scan_finished']:break
            assert r['scan_finished'] and old()==0 and r['objects_checked']==21
            assert maximum<=2 and not r['account_deleted']
        assert seals==f.bucket.live and all(f.bucket.history[k]==v for k,v in other.items())
        assert preserved==(m.f.preserved(),rows(m.r.store),rows(m.audit),rows(m.closure.audit))
        assert hashlib.sha256(m.f.capture).hexdigest()==original_capture
        try:objects.write(pin,b'late stale write')
        except GenerationConflict:pass
        else:raise AssertionError('old writer resurrected payload')
        return dict(schema='lite-research-history-probe/1',synthetic_only=True,implementation_present=f.h is not None,
                    original_epochs=3,original_slots=21,old_active_versions_before=before,old_active_versions_after=old(),
                    calls=calls,max_version_deletes_per_call=maximum,soft_versions_observed=len(f.bucket.soft),
                    current_seals_preserved=True,other_owner_preserved=True,consent_audit_identity_quota_preserved=True,
                    original_capture_sha256=original_capture,late_old_write_rejected=True,model_calls=m.f.calls,
                    live_gcs_apple_kms_calls=0,real_data_deleted=False,account_deleted=False,receipt=r)
    finally:host.doCleanups()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True,type=Path);a=p.parse_args()
    a.output.write_text(json.dumps(dict(active=run(),soft=run(soft=True)),indent=2)+'\n')
