import unittest
from lite_apple_provider_probe import run

class ProbeTests(unittest.TestCase):
    def test_identity_to_revocation_outage_recovery_keeps_usage(self):
        r=run()
        self.assertEqual(r['two_devices_blocked'],2)
        self.assertEqual(r['cached_fetches'],1)
        self.assertEqual(r['total_fetches_after_recovery'],3)
        for k in ('revocation_receipt_replay_identical','original_ledger_and_registry_preserved',
                  'explicit_relogin_same_account','stale_keys_refused','same_pending_signin_resumed','used_quota_preserved'):
            self.assertIs(r[k],True,k)
        self.assertEqual(r['cloud_calls'],0);self.assertFalse(r['real_apple_login']);self.assertFalse(r['real_sandbox'])

if __name__=='__main__':unittest.main()
