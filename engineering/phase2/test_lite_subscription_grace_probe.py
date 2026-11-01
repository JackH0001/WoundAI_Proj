import unittest
from lite_subscription_grace_probe import probe


class GraceProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.result=probe()

    def test_grace_duplicate_requests_charge_and_infer_once(self):
        r=self.result;self.assertTrue(r['passed'])
        self.assertEqual(r['grace']['used'],1)
        self.assertEqual(r['grace']['apple_status_queries_for_new_and_retries'],1)
        self.assertTrue(r['grace']['repeated_prepare_and_status_identical'])

    def test_expiry_retry_and_apple_outage_keep_usage_and_old_receipts(self):
        r=self.result
        self.assertEqual(r['expired_proof'],dict(http=503,ledger_unchanged=True))
        self.assertEqual(r['billing_retry'],dict(second_http=200,third_http=429,limit=2,used=2))
        self.assertEqual(r['apple_unavailable'],dict(new_http=503,old_receipt_unchanged=True,queries_for_existing_receipt=0))
        self.assertEqual(r['final'],dict(model_calls=2,used=2,pending=0))

    def test_no_research_or_policy_migration_implied(self):
        r=self.result
        self.assertTrue(r['research_unchanged']);self.assertFalse(r['research_stored'])
        self.assertTrue(r['association_registration_audit_unchanged'])
        for key in ('real_apple_calls','real_cloud_calls','store_or_grace_setting_changed'):self.assertFalse(r[key])


if __name__=='__main__':unittest.main()
