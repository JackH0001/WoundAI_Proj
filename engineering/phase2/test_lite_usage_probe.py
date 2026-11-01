import unittest
from lite_usage_probe import probe


class ProbeTests(unittest.TestCase):
    def test_core_claims_retry_and_real_adapter_counts(self):
        r=probe()
        self.assertTrue(r['core_gate']);self.assertFalse(r['http_cutover'])
        self.assertEqual(r['distinct']['outcomes'],{'executor_claimed':2,'quota_wait':6})
        self.assertEqual(r['duplicate']['outcomes'],{'executor_claimed':1,'existing_operation':7})
        self.assertEqual(r['transport']['new_completed_operation'],
            {'write_attempts':3,'metadata_reads':4,'content_reads':4,'total':11})
        self.assertEqual(r['transport']['completed_operation_retry']['write_attempts'],0)
        self.assertEqual(r['cost_subset']['quota_write_attempts'],1312500)
        self.assertAlmostEqual(r['cost_subset']['quota_operation_cost'],8.0445)
        self.assertAlmostEqual(r['cost_subset']['avoided_duplicate_inference_compute_if_each_retry_would_rerun'],19.575)


if __name__=='__main__':unittest.main()
