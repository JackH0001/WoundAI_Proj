import json
import unittest
import lite_resubmit_review_fixture as f


class ResubmitFixtureTests(unittest.TestCase):
    def test_native_golden_matches_signed_backend(self):
        saved=json.loads((f.ROOT/'iOS/WoundLiteTests/Fixtures/lite_service_resubmit.json').read_text())
        actual=f.fixture()
        self.assertEqual(actual,saved)
        self.assertEqual((actual['verified_model_calls'],actual['verified_success_charges']),(1,1))

    def test_observation_and_explicit_resume_do_not_reset_usage(self):
        result=f.rollout()
        self.assertEqual(result['observation'],dict(phase='active',observation='unavailable'))
        self.assertTrue(result['active_preserved_after_observation_error'])
        self.assertTrue(result['resumed_with_same_usage'])
        self.assertTrue(result['legacy_disabled'])
        self.assertEqual(result['cloud_calls'],0)


if __name__=='__main__':unittest.main()
