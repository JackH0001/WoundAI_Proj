"""Synthetic App Attest HTTP/service replay. No Apple, GCS or real media."""
import argparse,json
from pathlib import Path
from lite_account_identity import PREFIX
from lite_apple_oauth import TOKEN_URL
from test_lite_account_sessions import RenewalHTTPTests
from test_lite_subscription_grace import rows


def run():
    RenewalHTTPTests.setUpClass();fixture=RenewalHTTPTests();fixture.setUp()
    try:
        h=fixture.h;g=fixture.g
        privacy=rows(h.h.privacy.store)
        first=h.post(PREFIX+'renew',fixture.body);assert first.status_code==200,first.json
        repeated=h.post(PREFIX+'renew',fixture.body);assert repeated.json==first.json
        account=h.h.ops.accounts.resolve(h.h.owner)
        h.h.ledger.initialize_day(account['subject'],h.h.e.now//86400,baseline_successes=0,baseline_attempts=0,manifest_sha256='a'*64)
        h.f.h.publish()  # Refresh this fixture's legacy migration observation, not a live provider.
        before_result=h.h.complete(77).json
        used_before=rows(h.h.ledger.store)
        h.f.h.a.now+=901
        renewal=h.post(PREFIX+'renew',dict(operation='1'*32,renewal_operation='b'*32,expected_generation=1))
        assert renewal.status_code==200,renewal.json
        after=h.h.json_request('status',77);assert after.status_code==200,after.json
        retry=h.h.run_request(77);assert retry.status_code==200,retry.json
        logout=h.post(PREFIX+'logout',dict(operation='1'*32));assert logout.status_code==200
        blocked=h.post(PREFIX+'renew',dict(operation='1'*32,renewal_operation='c'*32,expected_generation=2))
        result=dict(schema='lite-session-renewal-probe/1',synthetic_only=True,
            first_generation=first.json['renewal_generation'],second_generation=renewal.json['renewal_generation'],
            renewal_replay_identical=repeated.json==first.json,
            saved_result_unchanged=after.json==before_result==retry.json,
            model_calls=h.h.model_calls,quota_rows_unchanged=used_before==rows(h.h.ledger.store),
            research_rows_unchanged=privacy==rows(h.h.privacy.store),
            apple_exchange_calls=g.calls.count(TOKEN_URL),logout_blocks_renewal=blocked.status_code==401,
            actual_apple=False,actual_gcs=False,default_startup_enabled=False)
        assert result['model_calls']==1 and result['apple_exchange_calls']==1
        assert all(result[k] for k in ('renewal_replay_identical','saved_result_unchanged','quota_rows_unchanged','research_rows_unchanged','logout_blocks_renewal'))
        return result
    finally:fixture.doCleanups()

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);a=parser.parse_args()
    a.output.write_text(json.dumps(run(),indent=2)+'\n')
