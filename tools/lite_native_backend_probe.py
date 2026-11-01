"""Replay the composed Sandbox candidate through signed HTTP, synthetic only.

Local SQLite + generated Apple/App Attest roots + fake KMS transport. Never
loads credentials or deploys. Output contains counters/digests, no tokens/RGB.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'Backend/Flask'),str(ROOT/'engineering/phase2'),str(ROOT/'tools')]
import test_lite_native_backend as fixture
from lite_apple_oauth import REVOKE_URL


def probe():
    fixture.NativeBackendTests.setUpClass()
    h=fixture.NativeBackendTests(); h.setUp()
    try:
        original=hashlib.sha256(h.f.jpeg).hexdigest()
        privacy=fixture.rows(h.h.privacy.store)
        h.test_purchase_and_restore_total_ten_preserves_two_used()
        counts=dict(model_calls=h.h.model_calls,used=h.quota()['used'],attempts=h.quota()['attempts'])
        subject=h.h.release.n.accounts.resolve(h.owner)
        h.assertEqual(h.h.release.n.accounts.grant(subject,h.h.clock()).limit,10)
        h.assertEqual(counts,dict(model_calls=3,used=3,attempts=3))
        saved=h.json_request('status',3).json
        with patch.object(h.h.release.n.a.client,'get_all_subscription_statuses',
                side_effect=AssertionError('readback must not query subscriptions')):
            h.backend=h.h.compose(); h.client=h.backend.create_app().test_client()
            h.assertEqual(h.json_request('status',3).json,saved)
        kms_start=len(h.h.kms.calls)
        intake=h.post('account/deletion/request',operation='d'*32,confirmation='delete_account')
        h.assertEqual(intake.status_code,202,intake.json)
        h.assertNotIn(REVOKE_URL,h.h.grant.calls)
        h.assertFalse(any(a=='destroy' for a,_ in h.h.kms.calls[kms_start:]))
        # Separate explicitly invoked worker, using only the fixture's KMS.
        progress=h.backend.progress.advance(h.person)
        h.assertEqual(progress['disposition']['service_sealed'],3)
        h.assertEqual(progress['apple_revocation'],'confirmed')
        h.assertEqual(progress['disposition']['grant_key_state'],'destroy_scheduled')
        h.assertFalse(progress['account_deleted'])
        h.assertEqual(h.h.grant.calls.count(REVOKE_URL),1)
        h.assertEqual(privacy,fixture.rows(h.h.privacy.store))
        h.assertEqual(original,hashlib.sha256(h.f.jpeg).hexdigest())
        outcome=dict(free_then_plus=dict(**counts,daily_total=10,restore_resets_usage=False),
            restart_readback_same=True,deletion_intake_http=202,intake_dispatches_cleanup=False,
            synthetic_worker=dict(service_sealed=3,apple_revokes=1,key_state='destroy_scheduled',
                account_deleted=False,pending_lanes=progress['disposition']['pending_lanes']),
            original_image_unchanged=True,research_privacy_unchanged=True,
            audit_rows=sum(len(fixture.rows(s)) for k,s in h.h.stores.items() if k.endswith('_audit')))
    finally: h.doCleanups()
    for name,method in (
        ('cross_midnight', 'test_cross_midnight_recovery_preserves_original_and_executes_once'),
        ('settlement_reply_loss','test_model_result_saved_before_settlement_outage_reads_back_without_rerun'),
        ('session_renewal','test_expired_session_renews_without_interactive_apple_login_or_second_charge')):
        h=fixture.NativeBackendTests();h.setUp()
        try:
            getattr(h,method)()
            outcome[name]=dict(model_calls=h.h.model_calls,used=h.quota()['used'],passed=True)
        finally:h.doCleanups()
    return dict(schema='lite-native-backend-probe/1',passed=True,
        scope='COMPOSED_SIGNED_HTTP_SYNTHETIC_PROVIDERS_SQLITE',live_cloud=False,
        apple_sandbox=False,device_capture=False,model_accuracy=False,**outcome)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True); args=parser.parse_args()
    result=probe();args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(passed=result['passed'],scope=result['scope'],output=str(args.output))))
