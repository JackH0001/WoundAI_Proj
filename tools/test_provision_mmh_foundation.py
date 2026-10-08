import copy
import unittest
from provision_mmh_foundation import provision, verify_bucket, verify_identity, PROJECT, NUMBER, REGION, SA, BUCKETS

class FoundationTests(unittest.TestCase):
    def bucket(self, name):
        return dict(name=name, projectNumber=NUMBER, location=REGION, storageClass='STANDARD',
                    iamConfiguration={'uniformBucketLevelAccess':{'enabled':True},'publicAccessPrevention':'enforced'},
                    softDeletePolicy={'retentionDurationSeconds':'0'})
    def run_fake(self, existing=False, mutate=None, apply=True):
        buckets=[self.bucket(n) for n in BUCKETS] if existing else []
        accounts=[dict(email=SA,projectId=PROJECT,disabled=False)] if existing else []
        calls=[]
        def run(args):
            calls.append(args)
            if args[:2]==['projects','describe']:return dict(projectId=PROJECT,projectNumber=NUMBER,lifecycleState='ACTIVE')
            if args==['storage','buckets','list']:return copy.deepcopy(buckets)
            if args==['iam','service-accounts','list']:return copy.deepcopy(accounts)
            if args[:3]==['storage','buckets','create']:
                row=self.bucket(args[3][5:]);buckets.append(row);return None
            if args[:3]==['storage','buckets','describe']:
                row=copy.deepcopy(next(r for r in buckets if r['name']==args[3][5:]))
                if mutate:mutate(row)
                return row
            if args[:3]==['iam','service-accounts','create']:
                accounts.append(dict(email=SA,projectId=PROJECT));return None
            if args[:3]==['iam','service-accounts','describe']:return accounts[0]
            raise AssertionError(args)
        return provision(run,apply),calls
    def test_dry_run_never_mutates(self):
        result,calls=self.run_fake(apply=False)
        self.assertEqual(len(calls),3);self.assertEqual(result['created'],[])
    def test_creates_only_exact_private_foundation(self):
        result,calls=self.run_fake()
        self.assertEqual(set(result['created']),set(BUCKETS)|{SA})
        for c in calls:
            self.assertFalse(any(x in c for x in ('delete','update','deploy','add-iam-policy-binding','secrets')))
            if c[:3]==['storage','buckets','create']:
                self.assertIn('--public-access-prevention',c);self.assertIn('--uniform-bucket-level-access',c)
                self.assertIn('--soft-delete-duration=0',c)
    def test_matching_existing_resources_are_not_mutated(self):
        result,calls=self.run_fake(existing=True)
        self.assertEqual(result['created'],[]);self.assertFalse(any('create' in c for c in calls))
    def test_each_bucket_guard_rejects(self):
        for field,value in [('name','other'),('projectNumber','1'),('location','US'),('storageClass','NEARLINE'),
                            ('iamConfiguration',{}),
                            ('retentionPolicy',{'isLocked':True}),('lifecycle',{'rule':[{}]}),
                            ('defaultEventBasedHold',True),('versioning',{'enabled':True}),
                            ('softDeletePolicy',{'retentionDurationSeconds':'604800'})]:
            with self.subTest(field=field):
                row=self.bucket(BUCKETS[0]);row[field]=value
                with self.assertRaises(ValueError):verify_bucket(row,BUCKETS[0])
    def test_readback_mismatch_stops(self):
        with self.assertRaises(ValueError):self.run_fake(mutate=lambda row:row.update(iamConfiguration={}))
    def test_identity_foreign_or_disabled_rejected(self):
        for field,value in [('email','other'),('projectId','other'),('disabled',True)]:
            row=dict(email=SA,projectId=PROJECT);row[field]=value
            with self.assertRaises(ValueError):verify_identity(row)
    def test_project_mismatch_stops_before_inventory(self):
        calls=[]
        def run(args):calls.append(args);return dict(projectId=PROJECT,projectNumber='1',lifecycleState='ACTIVE')
        with self.assertRaises(ValueError):provision(run,True)
        self.assertEqual(len(calls),1)
    def test_missing_inventory_fails_closed(self):
        def run(args):
            if args[:2]==['projects','describe']:return dict(projectId=PROJECT,projectNumber=NUMBER,lifecycleState='ACTIVE')
            return None
        with self.assertRaises(ValueError):provision(run,True)

if __name__=='__main__':unittest.main(verbosity=2)
