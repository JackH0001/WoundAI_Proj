"""Synthetic signed HTTP intake + durable revoke worker; no live deletion."""
import argparse,json
from pathlib import Path
from unittest.mock import patch
from lite_attest_state import StateUnavailable
from lite_apple_oauth import REVOKE_URL, TOKEN_URL
from test_lite_account_deletion import DeletionHTTPTests
from test_lite_subscription_grace import rows


def run():
    DeletionHTTPTests.setUpClass();f=DeletionHTTPTests();f.setUp()
    try:
        h=f.h.h
        account=h.ops.accounts.resolve(h.owner)
        h.ledger.initialize_day(account['subject'],h.e.now//86400,
            baseline_successes=0,baseline_attempts=0,manifest_sha256='a'*64)
        f.h.f.h.publish()
        result=h.complete(77)
        assert result.status_code==200 and result.json['state']=='completed'
        before=rows(h.ops.store),rows(h.ledger.store),rows(h.privacy.store),rows(f.d.bridge.registry.store)
        ack=f.post();assert ack.status_code==202
        assert f.post().json==ack.json
        real=f.d.audit.compare_exchange
        def fail(k,revision,value):
            if 'outcome' in value:raise StateUnavailable('synthetic audit outage')
            return real(k,revision,value)
        with patch.object(f.d.audit,'compare_exchange',side_effect=fail):
            try:f.d.advance(f.person)
            except StateUnavailable:pass
            else:raise AssertionError('outage must not claim completed')
        during=f.post('status');assert during.status_code==202
        assert during.json['apple_revocation']!='confirmed'
        f.h.f.h.a.now+=f.d.LEASE_SECONDS
        resumed=f.d.advance(f.person)
        assert resumed['apple_revocation']=='confirmed'
        assert f.post('status').json==resumed
        assert f.d.advance(f.person)==resumed
        blocked=[h.json_request('status',77),h.run_request(77),h.json_request('prepare',78)]
        after=rows(h.ops.store),rows(h.ledger.store),rows(h.privacy.store),rows(f.d.bridge.registry.store)
        report=dict(schema='lite-account-deletion-probe/1',synthetic_only=True,
            saved_result_succeeded=True,model_calls=h.model_calls,
            closure_replay_identical=True,audit_outage_reported_pending=True,
            worker_restart_completed_revocation=resumed['apple_revocation']=='confirmed',
            revoke_calls=f.g.calls.count(REVOKE_URL),exchange_calls=f.g.calls.count(TOKEN_URL),
            blocked_service=all(r.status_code==401 for r in blocked),
            service_quota_research_registry_unchanged=before==after,
            account_deleted=resumed['account_deleted'],data_disposition=resumed['data_disposition'],
            actual_apple=False,actual_gcs=False,data_erasure_executed=False,default_startup_enabled=False)
        assert report['model_calls']==report['revoke_calls']==report['exchange_calls']==1
        assert report['blocked_service'] and report['service_quota_research_registry_unchanged']
        assert not report['account_deleted'] and report['data_disposition']=='not_started'
        return report
    finally:f.doCleanups()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    a.output.write_text(json.dumps(run(),indent=2)+'\n')
