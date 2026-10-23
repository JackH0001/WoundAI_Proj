"""Review plan cannot silently authorize execution or omit source pins."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'tools'))
from plan_medical_consent_operations import FILES, plan

class Plan(unittest.TestCase):
    def make(self, clean=True): return plan('a'*40, {f:'b'*64 for f in FILES}, clean)
    def test_review_not_execution(self):
        p = self.make(); self.assertFalse(p['execution_authorized']); self.assertFalse(p['scheduler_candidate']['enabled'])
        self.assertIsNone(p['scheduler_candidate']['production_policy']); self.assertIsNone(p['scheduler_candidate']['image_digest'])
    def test_no_production_targets_or_irreversible_lock(self):
        p = self.make()['synthetic_gcs']
        self.assertIn('consent-smoke', p['media_bucket']); self.assertIn('consent-smoke', p['audit_bucket'])
        self.assertNotEqual(p['media_bucket'], p['audit_bucket']); self.assertTrue(p['require_new_empty_dedicated_buckets'])
        self.assertIn('no irreversible lock', p['audit_retention'])
    def test_fixed_bounded_scope(self):
        p = self.make(); self.assertEqual(p['project'], 'woundai-jackh001'); self.assertEqual(p['region'], 'asia-east1')
        self.assertEqual(p['synthetic_gcs']['max_minutes'], 20); self.assertEqual(p['scheduler_candidate']['max_retries'], 0)
        self.assertIn('OAuth', p['scheduler_candidate']['scheduler_auth'])
    def test_dirty_source_not_concealed(self): self.assertFalse(self.make(False)['source_clean'])
    def test_bad_digest_or_incomplete_manifest_rejected(self):
        for commit, files in [('HEAD', {f:'b'*64 for f in FILES}), ('a'*40, {}), ('a'*40, {f:'x'*64 for f in FILES})]:
            with self.subTest(commit=commit, files=len(files)), self.assertRaises(ValueError): plan(commit, files, True)

if __name__ == '__main__': unittest.main(verbosity=2)
