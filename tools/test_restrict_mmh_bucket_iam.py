import copy
import json
from pathlib import Path
import unittest
from restrict_mmh_bucket_iam import restrict, bindings, DEFAULTS, TARGET
import test_provision_mmh_foundation as foundation_tests


def policy(rows):
    return {'etag': 'test-etag', 'bindings': [{'role': r, 'members': sorted(m)} for r,m in rows.items()]}

class IAMTests(unittest.TestCase):
    def fake(self, rows=DEFAULTS, mismatch=False):
        calls=[]; policies={}
        def run(args):
            calls.append(args)
            if args[2]=='describe': return foundation_tests.FoundationTests().bucket(args[3][5:])
            if args[2]=='get-iam-policy': return copy.deepcopy(policies.get(args[3],policy(rows)))
            if args[2]=='set-iam-policy':
                self.assertEqual(args[-1],'--etag=test-etag')
                if not mismatch:policies[args[3]]=json.loads(Path(args[4]).read_text())
                return None
            self.fail(str(args))
        return run,calls
    def test_exact_defaults_restrict_with_etag_and_readback(self):
        run,calls=self.fake(); result=restrict(run,True)
        self.assertEqual(len(result['changed']),3)
        self.assertTrue(all(c[2] in ('describe','get-iam-policy') for c in calls[:6]))
    def test_dry_run_and_idempotence(self):
        for rows,apply in ((DEFAULTS,False),(TARGET,True)):
            run,calls=self.fake(rows);self.assertEqual(restrict(run,apply)['changed'],[])
            self.assertFalse(any(c[2]=='set-iam-policy' for c in calls))
    def test_unknown_conditional_and_missing_owner_rejected(self):
        for mutate in (lambda p:p['bindings'][0]['members'].append('allUsers'),
                       lambda p:p['bindings'][0].update(condition={'expression':'true'}),
                       lambda p:p.update(etag=''),
                       lambda p:p.update(bindings=p['bindings'][1:])):
            p=policy(DEFAULTS);mutate(p)
            with self.assertRaises(ValueError):bindings(p)
    def test_readback_mismatch_stops_after_first_write(self):
        run,calls=self.fake(mismatch=True)
        with self.assertRaises(ValueError):restrict(run,True)
        self.assertEqual(sum(c[2]=='set-iam-policy' for c in calls),1)

if __name__=='__main__':unittest.main(verbosity=2)
