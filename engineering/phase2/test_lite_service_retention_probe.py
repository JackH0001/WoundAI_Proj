import unittest
from lite_service_retention_probe import probe


class RetentionProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.result=probe()

    def test_conditional_compaction_and_zero_write_replay(self):
        for name,case in self.result['cases'].items():
            with self.subTest(name=name):
                self.assertTrue(case['conditional_uploads'])
                self.assertEqual(case['replay']['write_attempts'],0)
                self.assertEqual(case['measured']['write_attempts'],4 if name=='unstarted' else 3)
                self.assertEqual(case['model_calls'],0 if name=='unstarted' else 1)

    def test_retention_is_not_erasure_or_guaranteed_total_savings(self):
        self.assertFalse(self.result['physical_generation_delete'])
        for case in self.result['cases'].values():
            self.assertTrue(case['prior_generation_preserved']);self.assertGreater(case['audit_current_bytes'],0)
        dense=self.result['cases']['dense_completed'];small=self.result['cases']['small_completed']
        self.assertLess(dense['after_current_bytes'],dense['before_current_bytes'])
        self.assertGreater(small['after_current_bytes']+small['audit_current_bytes'],small['before_current_bytes'])


if __name__=='__main__':unittest.main()
