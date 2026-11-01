import unittest
from lite_subscription_migration_probe import probe


class MigrationProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.r=probe()

    def test_pending_work_and_existing_results_survive_migration(self):
        r=self.r;self.assertTrue(r['passed']);self.assertTrue(r['migration_preserved_original_ledger_and_operations'])
        self.assertEqual(r['pending_old_operation'],dict(http=200,admitted_limit=10))
        self.assertEqual(r['final'],dict(model_calls=3,used=3,pending=0))

    def test_lost_ack_and_retries_do_not_query_or_charge_twice(self):
        self.assertEqual(self.r['lost_completion'],dict(injected=True,same_receipt=True,apple_queries_on_recovery=0))
        self.assertEqual(self.r['new_policy_operation'],dict(http=200,admitted_limit=10,repeated_receipt_identical=True,apple_queries=1))

    def test_reader_rollback_blocks_new_admission_but_keeps_historical_receipts(self):
        self.assertEqual(self.r['rollback'],dict(old_receipt_http=200,old_receipt_identical=True,
            new_admission_http=503,apple_queries=0,ledger_results_unchanged=True))
        self.assertEqual(self.r['refund'],dict(new_http=429,used=3,pending=0))

    def test_research_and_old_evidence_unchanged_without_live_claim(self):
        for k in ('old_links_unchanged','old_audit_unchanged','registration_unchanged','research_unchanged'):
            self.assertTrue(self.r[k])
        for k in ('research_stored','real_apple_calls','real_cloud_calls','live_fence_verified','store_products_created'):
            self.assertFalse(self.r[k])


if __name__=='__main__':unittest.main()
