"""Pure policy-delta rehearsal with synthetic public inputs; no cloud access."""
import argparse
import copy
import json
from pathlib import Path
from lite_subscription_executor import canonical
from lite_usage_ledger import digest
from lite_bootstrap_iam import propose, add_delta, remove_delta, check_roles
import test_lite_fresh_bootstrap_runner as fixture


def run():
    h=fixture.RunnerTests();h.setUpClass();h.setUp()
    try:
        before=h.snapshot();r=h.runner
        p=propose(canonical(r),canonical(h.config),expected_config=digest(r),expected_native=digest(h.config),
            expected_source=r['deployment']['source'],expected_image=r['deployment']['image'],
            allowed_buckets=sorted(r['buckets']),clock=h.f.h.clock)
        role_readback={v['name']:dict(status=404,body=None) for v in p['roles'].values()}
        roles=check_roles(role_readback,p,expected_proposal=digest(p))
        counts=[]
        for bucket in p['bucket_bindings']:
            original=dict(version=3,etag='before',bindings=[dict(role='roles/storage.objectViewer',members=['user:existing@example.test'])])
            pending,receipt=add_delta(original,p,bucket=bucket,expected_proposal=digest(p),expected_etag='before')
            # Simulated provider committed but its response was lost. A fresh
            # read also contains someone else's later unrelated update.
            observed=copy.deepcopy(pending);observed['etag']='fresh-after-other-update'
            other=dict(role='roles/storage.legacyBucketReader',members=['user:other@example.test'])
            observed['bindings'].append(other)
            recovered,status=remove_delta(observed,receipt,bucket=bucket,expected_receipt=digest(receipt),expected_etag=observed['etag'])
            assert recovered['bindings']==original['bindings']+[other]
            again,repeat=remove_delta(recovered,receipt,bucket=bucket,expected_receipt=digest(receipt),expected_etag=recovered['etag'])
            assert again==recovered and repeat['removed']==0
            counts.append(dict(bucket=bucket,added=len(receipt['added']),removed=status['removed'],retry_removed=0))
        assert before==h.snapshot()
        return dict(passed=True,synthetic_only=True,cloud_calls=0,proposal=p,
            role_preflight=roles,recovery_cases=counts,unrelated_policy_retained=True,
            repeated_removal_noop=True,original_state_unchanged=True,effective_iam_verified=False,
            cel_provider_validation=False)
    finally:h.doCleanups()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();result=run()
    with args.output.open('x') as f:json.dump(result,f,indent=2);f.write('\n')
    print(json.dumps({k:v for k,v in result.items() if k!='proposal'}))
