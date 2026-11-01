"""Signed synthetic Flask flow with real CAS adapter/fake GCS transport.

No live GCS, Apple, research media, deployment or model accuracy measurement.
Counters exclude the App Attest/budget stores (temporary SQLite in the fixture).
"""
from pathlib import Path
import argparse,json,sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'Backend/Flask'),str(ROOT/'engineering/phase2'),str(ROOT/'tools')]
from test_lite_service_http import ServiceHTTPTests
from test_lite_attest_state import FakeBucket
from lite_attest_state import GCSStateStore
from lite_usage_ledger import UsageLedger
from lite_service_operations import ServiceAccounts
from lite_usage_probe import calls


def probe():
    h=ServiceHTTPTests();h.setUp()
    buckets={name:FakeBucket() for name in ('account','usage','result')}
    def measure(action):
        starts={name:len(bucket.calls) for name,bucket in buckets.items()}
        response=action()
        counts={name:calls(bucket,starts[name]) for name,bucket in buckets.items()}
        return response,dict(stores=counts,write_attempts=sum(v['write_attempts'] for v in counts.values()),
                             reads=sum(v['metadata_reads']+v['content_reads'] for v in counts.values()))
    try:
        h.accounts=ServiceAccounts(GCSStateStore(buckets['account'],prefix='synthetic-service/accounts'))
        h.ledger=UsageLedger(GCSStateStore(buckets['usage'],prefix='synthetic-service/usage'),
                            namespace='flow-probe',clock=lambda:h.e.now)
        h.ops.accounts=h.accounts;h.ops.ledger=h.ledger
        h.ops.store=GCSStateStore(buckets['result'],prefix='synthetic-service/results')
        _,provision=measure(lambda:h.accounts.provision(h.owner,h.subject,h.token,None,'f'*64))
        _,day=measure(h.initialize_day)
        ready,prepare=measure(lambda:h.json_request('prepare'))
        assert ready.status_code==200 and ready.json['state']=='ready'
        saved,run=measure(h.run_request)
        assert saved.status_code==200 and saved.json['state']=='completed'
        receipt,status=measure(lambda:h.json_request('status'))
        assert receipt.json==saved.json and h.model_calls==1 and h.quota()['used']==1
        withdraw_path='/api/v1/lite/data/'+h.owner
        withdrawn=h.client.delete(withdraw_path,headers=h.headers(withdraw_path,b'','',method='DELETE',purpose='withdraw'))
        assert withdrawn.status_code==200
        h.complete(2)
        over=h.json_request('prepare',3)
        assert over.status_code==429 and h.model_calls==2
        assert h.privacy._load(h.owner)[1]['withdrawn']
        assert list(Path(h.f.tmp.name).rglob('*.jpg'))==[]
        body=json.dumps(dict(anon_id=h.owner,operation=f'{1:032x}')).encode()
        fresh=dict(write_attempts=prepare['write_attempts']+run['write_attempts'],reads=prepare['reads']+run['reads'])
        attempts=337500;retries=67500;days=300000
        writes=attempts*fresh['write_attempts']+retries*status['write_attempts']+days*day['write_attempts']
        reads=attempts*fresh['reads']+retries*status['reads']+days*day['reads']
        return dict(schema='lite-service-flow-probe/1',scope='SIGNED_FLASK_REAL_ADAPTER_FAKE_TRANSPORT',
            synthetic_gate=True,live_cloud=False,apple_sandbox=False,native_cutover=False,
            model_calls_after_first_and_readback=1,status_request_body_bytes=len(body),synthetic_image_bytes=len(h.f.jpeg),
            status_request_contains_image_or_depth=False,research_media_files_written=0,
            withdrawal_kept=True,service_after_withdrawal=True,used_after_withdrawal=2,third_prepare_http=over.status_code,
            actual_adapter_counts=dict(account_provision=provision,day_initialize=day,prepare=prepare,run=run,status=status,fresh_operation=fresh),
            conditional_writes=all('if_generation_match' in c[2] for b in buckets.values() for c in b.calls if c[0]=='upload'),
            cost_sensitivity=dict(currency='USD',scope='SERVICE_SUBSET_WITHIN_PRIOR_ALLOWANCE_NOT_ADDITIVE',
                attempts=attempts,status_retries=retries,day_initializations=days,
                class_a_assumption_per_operation=.000005,class_b_assumption_per_operation=.0000004,
                write_attempts=writes,reads=reads,operation_cost=writes*.000005+reads*.0000004,
                exclusions=['App Attest/challenges/budgets','Apple/OCSP/proofs','account provisioning or changes',
                    'CAS contention','errors/empty-result distribution','HTTP/compute/media egress',
                    'result retention/versioning/purge','research storage','ads/taxes/support'],
                measured_bill_or_profit=False,
                note='Prior sensitivity rates, not a current region/account tariff. Success-path counts applied to all attempts conservatively; distribution unmeasured.'))
    finally:h.doCleanups()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    result=probe();args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(output=str(args.output),synthetic_gate=result['synthetic_gate'],
                         counts=result['actual_adapter_counts']['fresh_operation'],
                         cost_subset=result['cost_sensitivity']['operation_cost'])))
