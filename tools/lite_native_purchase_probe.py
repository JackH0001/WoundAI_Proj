"""Local signed-fixture rehearsal; no enrollment, purchase, cloud or patient data."""
import argparse,json,unittest
from pathlib import Path
from unittest.mock import patch
import test_lite_native_purchase as native
import test_lite_subscription_runtime as runtime
from lite_attest_state import StateUnavailable
from lite_usage_ledger import UsageLimit
from test_lite_subscription_grace import rows

def run():
    runtime.RuntimeTests.setUpClass();host=unittest.TestCase();h=native.Fixture(host)
    rtest=runtime.RuntimeTests();rtest.setUp()
    try:
        c=h.context();before=rows(h.f.ledger.store)
        h.confirm(c);after=rows(h.f.ledger.store)
        with patch.object(h.a.client,'get_all_subscription_statuses',wraps=h.a.fetch) as api:
            h.confirm(device='second-device');restore_queries=api.call_count
        for n in range(3,11):h.f.use(n,'first-device' if n%2 else 'second-device')
        limited=False
        try:h.f.use(11)
        except UsageLimit:limited=True
        d=[rtest.new(n) for n in range(100,305)]
        for i in range(0,len(d),100):e,a=rtest.batch(d[i:i+100],'probe-'+str(i));e.advance(a)
        ready=sum(rtest.grant(x).limit==2 for x in d)
        late=rtest.grant(rtest.new(999)).limit
        before_control=rows(rtest.f.control);rtest.f.a.now+=31;blocked=False
        try:rtest.grant('new-999')
        except StateUnavailable:blocked=True
        rtest.f.publish();recovered=rtest.grant('new-999').limit
        return dict(schema='woundailite-native-purchase-probe/1',synthetic_only=True,
            free_used_before=2,upgrade_preserved_usage=before==after,daily_total_after_upgrade=10,
            eleventh_denied=limited,restore_apple_queries=restore_queries,
            final_used=h.f.ledger.snapshot(h.f.subject,h.f.day,h.f.grant())['used'],
            batch_sizes=[100,100,5],accounts_available=ready,new_after_batches_limit=late,
            expired_observer_blocks=blocked,observer_recovers_free=recovered,
            observation_outage_keeps_control=before_control==rows(rtest.f.control),
            actual_apple_sandbox=False,actual_cloud=False,public_startup_enabled=False)
    finally:host.doCleanups();rtest.doCleanups()

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);a=p.parse_args()
    result=run();Path(a.output).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    assert result['upgrade_preserved_usage'] and result['eleventh_denied'] and result['final_used']==10
    assert result['restore_apple_queries']==0 and result['accounts_available']==205 and result['new_after_batches_limit']==2
    assert result['expired_observer_blocks'] and result['observer_recovers_free']==2 and result['observation_outage_keeps_control']
