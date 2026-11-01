import unittest
from lite_service_release_probe import run

class ReleaseProbeTests(unittest.TestCase):
    def test_real_coordinator_replays_once_and_keeps_receipt_during_pause(self):
        r=run()
        self.assertTrue(r['before_observer_31s_new_work_denied'])
        self.assertEqual(r['after_observer_48h_prepared'],'ready')
        self.assertEqual((r['model_calls'],r['used'],r['attempts']),(1,1,1))
        for name in ('same_result_on_retry','cross_instance_pause_blocks','paused_new_denied',
                     'paused_old_receipt_readable','original_result_preserved'):self.assertTrue(r[name],name)
        self.assertEqual(r['resume_epoch'],3)
        for name in ('research_stored','actual_cloud','actual_apple','startup_enabled'):self.assertFalse(r[name],name)

if __name__=='__main__':unittest.main()
