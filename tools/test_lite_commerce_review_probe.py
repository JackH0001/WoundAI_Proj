"""Characterization assertions intentionally expose remaining rollout blockers."""
import unittest
from lite_commerce_review_probe import probe


class ReviewProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.result=probe()

    def test_uninitialized_new_day_fails_closed_without_inference(self):
        self.assertEqual(self.result['G1'],dict(http=503,error='lite_operation_unconfirmed',model_calls=0))

    def test_abandoned_prepares_currently_block_capacity_without_successes(self):
        r=self.result['G2']
        self.assertEqual(r['prepared'],[200,200]);self.assertEqual(r['third_http'],429)
        self.assertEqual((r['quota']['used'],r['quota']['pending'],r['model_calls']),(0,2,0))

    def test_midnight_provisional_gap_is_reproduced_not_declared_fixed(self):
        r=self.result['G3']
        self.assertEqual((r['initial_http'],r['retry_http'],r['run_http']),(503,409,409))
        self.assertEqual((r['status'],r['retry_action'],r['model_calls']),('awaiting_reservation','prepare',0))
        self.assertFalse(self.result['release_ready'])

    def test_paid_new_operation_queries_once_and_receipt_replay_never_queries(self):
        self.assertEqual(self.result['G4'],dict(new_operation_queries=1,receipt_replay_queries=0,model_calls=1))
        self.assertFalse(self.result['real_apple_calls']);self.assertFalse(self.result['real_cloud_calls'])


if __name__=='__main__':unittest.main()
