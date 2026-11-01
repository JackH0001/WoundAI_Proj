import unittest
from lite_service_binding_probe import probe


class ProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.result=probe()

    def test_baseline_shared_upgrade_refund_and_no_restore_writes(self):
        r=self.result;self.assertTrue(r['passed'])
        self.assertEqual(r['before']['remaining'],0)
        self.assertEqual(r['after_upgrade']['remaining'],8)
        self.assertEqual(r['after_refund']['limit'],2)
        self.assertEqual(r['after_refund']['used'],10)
        self.assertEqual(r['operations']['completed_restore']['apple_status_calls'],0)
        self.assertFalse(r['real_apple_calls']);self.assertFalse(r['real_cloud_calls'])

    def test_new_association_and_fresh_grants_have_measured_cost(self):
        r=self.result['operations']
        self.assertEqual(r['first_association']['apple_status_calls'],1)
        self.assertGreaterEqual(r['first_association']['write_attempts'],5)
        self.assertEqual(r['fresh_paid_grant_with_account_lookup']['apple_status_calls'],1)
        self.assertGreater(r['fresh_paid_grant_with_account_lookup']['content_reads'],
                           r['free_grant_with_empty_link_lookup']['content_reads'])


if __name__=='__main__':unittest.main()
