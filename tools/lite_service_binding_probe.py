"""Synthetic before/after and storage-call evidence; never opens Apple or GCS."""
from pathlib import Path
import argparse
import json
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'Backend/Flask'),str(ROOT/'engineering/phase2')]
import test_lite_service_subscription_links as fixtures
from lite_attest_state import GCSStateStore
from test_lite_attest_state import FakeBucket
from lite_usage_probe import calls


def probe():
    fixtures.LinksTests.setUpClass();h=fixtures.LinksTests();h.setUp()
    try:
        bucket=FakeBucket();queries=0;real=h.a.fetch
        def query(original):
            nonlocal queries
            queries+=1;return real(original)
        h.a.client.get_all_subscription_statuses=query
        h.links.store=GCSStateStore(bucket,prefix='synthetic_link')
        h.links.audit=GCSStateStore(bucket,prefix='synthetic_audit')
        h.a.a.proofs=GCSStateStore(bucket,prefix='synthetic_apple_proof')
        h.accounts.store=GCSStateStore(bucket,prefix='synthetic_account')
        for device in ('first-device','second-device'):
            h.accounts.provision(device,h.subject,h.token,None,'a'*64)
        def measure(fn):
            start=len(bucket.calls);before=queries;value=fn()
            return value,dict(**calls(bucket,start),apple_status_calls=queries-before)
        g,free=measure(h.grant);before=h.ledger.snapshot(h.subject,h.day,g)
        receipt,attach=measure(h.attach)
        g,paid=measure(lambda:h.grant('second-device'));after=h.ledger.snapshot(h.subject,h.day,g)
        _,restore=measure(lambda:h.attach('second-device'))
        for n in range(3,11):h.use(n,'first-device' if n%2 else 'second-device')
        at_limit=h.ledger.snapshot(h.subject,h.day,h.grant())
        h.a.status=5;refunded=h.ledger.snapshot(h.subject,h.day,h.grant())
        start=len(bucket.calls);h.attach();idempotent=calls(bucket,start)
        result=dict(schema='lite-service-binding-probe/1',scope='REAL_ADAPTER_FAKE_TRANSPORT_SYNTHETIC_SIGNED_APPLE',
            before=before,after_upgrade=after,after_eight_additional_successes=at_limit,after_refund=refunded,
            association_receipt=receipt,
            operations=dict(free_grant_with_empty_link_lookup=free,first_association=attach,
                fresh_paid_grant_with_account_lookup=paid,completed_restore=restore,completed_restore_after_refund=idempotent),
            preserved_baseline=all(x['used']==2 for x in (before,after)),
            shared_limit=at_limit['used']==10 and at_limit['remaining']==0,
            refund_keeps_usage=refunded['used']==10 and refunded['remaining']==0,
            restore_writes=restore['write_attempts'],store_versions_preserved=len(bucket.versions),
            real_apple_calls=False,real_cloud_calls=False,revenue_or_profit_measured=False,
            exclusions=['Human login/device linking','Apple sandbox purchase and OCSP latency',
                        'Cloud IAM and latency','actual invoices and ad fill/eCPM','native UI wiring'])
        result['passed']=result['preserved_baseline'] and result['shared_limit'] and result['refund_keeps_usage'] and restore['write_attempts']==0
        return result
    finally:h.doCleanups()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();result=probe()
    with args.output.open('x') as f:json.dump(result,f,indent=2,allow_nan=False);f.write('\n')
    if not result['passed']:raise SystemExit('binding probe failed')
