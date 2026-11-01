"""Probe reliability tests. A detected gap is NOT a passing launch gate."""
import unittest
from lite_prelaunch_quota_probe import characterize


class Probe(unittest.TestCase):
    def test_sequential_two_and_ten_enforced_with_synthetic_overrides(self):
        for n in (2, 10):
            r = characterize(1, n)
            self.assertEqual([x['status'] for x in r['responses']], [200]*n+[429])
            self.assertEqual(r['model_calls'], n)
            self.assertTrue(r['quota_within_limit'])
            self.assertFalse(r['research_media_created'])

    def test_race_is_detected_not_reported_as_launch_pass(self):
        r = characterize(8, 2)
        self.assertEqual(r['model_calls'], 8)
        self.assertEqual(r['recorded_successes'], 8)
        self.assertFalse(r['quota_within_limit'])
        self.assertFalse(r['research_media_created'])
        self.assertTrue(all(x['status'] == 200 and x['polygons'] and not x['stored'] for x in r['responses']))

    def test_probe_is_bounded(self):
        with self.assertRaises(ValueError): characterize(33, 2)


if __name__ == '__main__':
    unittest.main()
