import math
import unittest
from dataclasses import replace
from lite_prelaunch_economics import Inputs, evaluate, conversion_threshold, burst, scenarios


class Economics(unittest.TestCase):
    def test_no_ad_provider_no_default_revenue(self):
        self.assertEqual(evaluate(Inputs())['ads'], 0)

    def test_paid_net_independent_arithmetic(self):
        self.assertAlmostEqual(evaluate(Inputs())['net_subscription_per_user'], 1.99/1.05*.85*.98)

    def test_delivered_not_opportunities_and_no_paid_ads(self):
        r = evaluate(Inputs(delivered_ads_per_free_user=3))
        self.assertAlmostEqual(r['ads'], 9000*3*.002*.95)
        self.assertEqual(evaluate(Inputs(paid_fraction=1, delivered_ads_per_free_user=3))['ads'], 0)

    def test_empty_and_error_compute_without_success_charge(self):
        a = evaluate(Inputs()); b = evaluate(Inputs(empty_ratio=1, failure_ratio=.2))
        self.assertEqual(a['successes'], b['successes'])
        self.assertEqual(a['revenue'], b['revenue'])
        self.assertGreater(b['compute_cost'], a['compute_cost'])

    def test_service_staging_includes_soft_deleted_bytes(self):
        x = Inputs(mau=1, paid_fraction=0, free_successes=20, consent_fraction=0)
        r = evaluate(x)
        self.assertAlmostEqual(r['transient_and_soft_delete_cost'], 25*8/1024*8/30*.02)
        self.assertEqual(r['new_research_gib'], 0)

    def test_raw_depth_storage_is_not_free(self):
        a = evaluate(Inputs()); b = evaluate(Inputs(storage_mib=80))
        self.assertAlmostEqual(b['current_storage'], a['current_storage']*10)

    def test_sampling_does_not_change_consent_service_or_revenue(self):
        a = evaluate(Inputs()); b = evaluate(Inputs(selection_fraction=.5))
        for k in ('successes', 'compute_cost', 'revenue'):
            self.assertEqual(a[k], b[k])
        self.assertEqual(b['research_bundles'], a['research_bundles']/2)
        self.assertGreater(b['transient_and_soft_delete_cost'], a['transient_and_soft_delete_cost'])

    def test_nonconsent_cannot_be_selected_into_storage(self):
        self.assertEqual(evaluate(Inputs(consent_fraction=0))['research_bundles'], 0)

    def test_future_storage_replaces_not_adds_current(self):
        r = evaluate(Inputs())
        self.assertAlmostEqual(r['funded_cost']-r['current_cost'], r['future_storage_provision']-r['current_storage'])

    def test_inventory_caps_at_full_retention_plus_soft_window(self):
        r = evaluate(Inputs(operating_month=1000))
        self.assertEqual(r['funded_cost'], r['current_cost'])

    def test_full_31_day_does_not_cap_at_300(self):
        r = evaluate(Inputs(days=31, paid_fraction=1, paid_successes=310))
        self.assertEqual(r['successes'], 3100000)

    def test_daily_success_and_attempt_limits(self):
        for x in (Inputs(free_successes=61), Inputs(paid_successes=301), Inputs(extra_empty_per_user=901)):
            with self.assertRaises(ValueError): evaluate(x)

    def test_bad_numbers_rejected(self):
        for x in (Inputs(ecpm=math.nan), Inputs(storage_rate=math.inf), Inputs(mau=True),
                  Inputs(consent_fraction=1.01), Inputs(days=0), Inputs(version_factor=.5)):
            with self.assertRaises(ValueError): evaluate(x)

    def test_breakeven_ad_formula_independent(self):
        x = Inputs(paid_fraction=0)
        r = evaluate(x)
        ads = r['required_delivered_ads_funded']
        self.assertAlmostEqual(evaluate(replace(x, delivered_ads_per_free_user=ads))['funded_profit'], 0, places=8)

    def test_zero_ecpm_unreachable_not_infinite(self):
        self.assertIsNone(evaluate(Inputs(paid_fraction=0, ecpm=0))['required_delivered_ads_funded'])

    def test_conversion_matches_linear_solution(self):
        x = Inputs()
        a = evaluate(replace(x, paid_fraction=0))['funded_profit']
        b = evaluate(replace(x, paid_fraction=1))['funded_profit']
        self.assertAlmostEqual(conversion_threshold(x), -a/(b-a))
        self.assertAlmostEqual(evaluate(replace(x, paid_fraction=conversion_threshold(x)))['funded_profit'], 0, places=8)

    def test_no_conversion_can_cover_full_entitlement(self):
        self.assertIsNone(conversion_threshold(Inputs(days=31, free_successes=62, paid_successes=310)))

    def test_burst_queue_bound(self):
        r = burst()
        self.assertEqual((r['admitted'], r['busy_keep_local']), (4, 96))
        self.assertEqual(r['last_admitted_finish'], 20)
        self.assertEqual(r['naive_unbounded_last_finish'], 500)

    def test_burst_workers_and_zero_wait(self):
        self.assertEqual(burst(workers=4)['admitted'], 16)
        self.assertEqual(burst(workers=4, max_wait=0)['admitted'], 4)
        self.assertEqual(burst(workers=200)['busy_keep_local'], 0)

    def test_invalid_burst(self):
        for kw in ({'workers': 0}, {'seconds': 0}, {'max_wait': math.nan}):
            with self.assertRaises(ValueError): burst(**kw)

    def test_all_scenarios_finite_and_not_launch_authorization(self):
        r = scenarios()
        self.assertFalse(r['launch_ready'])
        self.assertEqual(len(r['scenarios']), 23)
        for v in r['scenarios'].values():
            self.assertTrue(math.isfinite(v['funded_profit']))


if __name__ == '__main__':
    unittest.main()
