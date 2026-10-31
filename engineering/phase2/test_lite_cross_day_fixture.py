import json
import unittest
from lite_cross_day_fixture import fixture, rollout, ROOT


class CrossDayFixtureTests(unittest.TestCase):
    def test_native_golden_matches_current_signed_backend(self):
        saved=json.loads((ROOT/'iOS/WoundLiteTests/Fixtures/lite_service_recovery.json').read_text())
        actual=fixture()
        self.assertEqual(saved,actual)
        self.assertEqual((actual['verified_model_calls'],actual['verified_success_charges']),(1,1))

    def test_pause_keeps_nonzero_baseline_and_legacy_closed(self):
        v=rollout()
        self.assertEqual(v['active_before_pause'],v['after_pause'])
        self.assertEqual(v['phase'],'paused')
        self.assertTrue(v['legacy_writers_remain_disabled'])
        self.assertEqual(v['cloud_calls'],0)


if __name__=='__main__':unittest.main()
