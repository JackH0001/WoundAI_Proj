import unittest
from lite_subscription_executor_probe import probe


class ExecutorProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.r=probe()

    def test_resume_and_repeated_work_do_not_run_or_charge_twice(self):
        r=self.r;self.assertTrue(r['passed']);self.assertTrue(r['interrupted'])
        self.assertEqual(r['resume_apple_queries'],0)
        self.assertEqual(r['final'],dict(model_calls=3,used=3,pending=0,phase='active'))
        self.assertEqual(r['pending'],dict(http=200,repeated_identical=True))
        self.assertEqual(r['new_work'],dict(http=200,repeated_identical=True))

    def test_pause_keeps_readback_and_stops_new_admission(self):
        self.assertEqual(self.r['during_pause'],dict(receipt_http=200,receipt_unchanged=True,new_http=503,apple_queries=0,model_calls=0))
        self.assertEqual(self.r['rollback'],dict(old_http=200,old_receipt_unchanged=True,new_http=503,ledger_unchanged=True,results_unchanged=True))

    def test_no_original_or_research_state_rewritten(self):
        for name in ('migration_preserved_ledger_and_work','registration_unchanged','research_unchanged','old_links_preserved','old_audit_preserved'):
            self.assertTrue(self.r[name],name)

    def test_synthetic_evidence_not_claimed_as_cloud_or_apple_acceptance(self):
        for name in ('real_apple','real_gcs','live_fence','research_stored'):self.assertIs(self.r[name],False)


if __name__=='__main__':unittest.main()
