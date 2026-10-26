"""Read-only observations and explicit paused recovery; no production provider."""
import copy
import unittest
from unittest.mock import patch
from lite_attest_state import StateUnavailable,GCSStateStore
from lite_usage_ledger import UsageConflict,digest,free_grant
from lite_usage_rollout import ObservationUnavailable,ObservationContradiction,validate_baselines
import test_lite_usage_rollout as fixture

class RolloutReviewTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.RolloutTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
    def test_active_unavailable_observation_never_rolls_back(self):
        f=self.f;f.activate();before=f.executor._read();usage=f.snap();f.provider.calls=[]
        with patch.object(f.provider,'observe',side_effect=OSError('network')):
            self.assertEqual(f.executor.inspect(),dict(phase='active',observation='unavailable'))
            with self.assertRaises(ObservationUnavailable):f.executor.execute()
            self.assertFalse(f.executor.admission_allowed(f.m))
        self.assertEqual(f.executor._read(),before);self.assertEqual(f.snap(),usage)
        self.assertTrue(f.provider.new_enabled);self.assertNotIn('disable',f.provider.calls)
    def test_active_contradiction_is_distinct_and_does_not_mutate(self):
        f=self.f;f.activate();before=f.executor._read();f.provider.wrong='baseline_sha256';f.provider.calls=[]
        self.assertEqual(f.executor.inspect()['observation'],'contradictory')
        with self.assertRaises(ObservationContradiction):f.executor.advance()
        self.assertFalse(f.executor.admission_allowed(f.m))
        self.assertEqual(f.executor._read(),before);self.assertNotIn('disable',f.provider.calls)
    def test_incomplete_observation_is_not_declared_drift(self):
        f=self.f;f.activate()
        with patch.object(f.provider,'observe',return_value={'legacy_disabled':True}):
            self.assertEqual(f.executor.inspect()['observation'],'unavailable')
            with self.assertRaises(ObservationUnavailable):f.executor.advance()
        self.assertTrue(f.provider.new_enabled)
    def test_inspect_absent_and_paused_is_read_only(self):
        f=self.f
        self.assertEqual(f.executor.inspect(),dict(phase='absent',observation='not_requested'))
        self.assertIsNone(f.executor._read());self.assertEqual(f.provider.calls,[])
        f.activate();f.executor.rollback();before=f.executor._read();f.provider.calls=[]
        self.assertEqual(f.executor.inspect(),dict(phase='paused',observation='confirmed'))
        self.assertEqual(f.executor._read(),before);self.assertEqual(f.provider.calls,['observe'])
        with self.assertRaises(UsageConflict):f.executor.execute()
        self.assertEqual(f.provider.calls,['observe'])
    def test_advance_failure_still_closes_provider(self):
        f=self.f
        with patch.object(f.provider,'observe',side_effect=OSError('network')):
            with self.assertRaises(StateUnavailable):f.executor.advance()
        self.assertFalse(f.provider.new_enabled)
        self.assertEqual(f.executor._read()[1]['phase'],'paused')
    def resume(self):
        f=self.f
        return f.executor.resume_paused(expected_pause_sha256=digest(f.executor._read()[1]),authorization_sha256='a'*64)
    def test_explicit_same_day_resume_preserves_counts_and_history(self):
        f=self.f;f.activate();before=f.ledger._load('synthetic-a',f.m['day']);f.executor.rollback()
        paused=copy.deepcopy(f.executor._read()[1]);r=self.resume()
        self.assertEqual(r['phase'],'prepared');self.assertFalse(f.provider.new_enabled)
        self.assertEqual(r['history'][:len(paused['history'])],paused['history'])
        self.assertEqual(f.executor.advance()['phase'],'active')
        self.assertEqual(f.ledger._load('synthetic-a',f.m['day']),before)
        self.assertEqual((f.snap()['used'],f.snap()['attempts']),(1,3))
        self.assertTrue(f.provider.legacy_disabled)
    def test_partial_baseline_resume_initializes_only_missing_without_reset(self):
        f=self.f;original=f.ledger.initialize_day
        def fail(s,*a,**k):
            if s=='synthetic-b':raise StateUnavailable('second')
            return original(s,*a,**k)
        with patch.object(f.ledger,'initialize_day',side_effect=fail),self.assertRaises(StateUnavailable):f.executor.advance()
        before=f.ledger._load('synthetic-a',f.m['day'])
        self.resume();f.executor.advance()
        self.assertEqual(f.ledger._load('synthetic-a',f.m['day']),before)
        self.assertEqual(f.snap('synthetic-b')['attempts'],2)
    def test_stale_or_missing_authorization_cannot_resume(self):
        f=self.f;f.activate();f.executor.rollback();before=f.executor._read()
        for expected,proof in [('0'*64,'a'*64),(digest(before[1]),'')]:
            with self.assertRaises(UsageConflict):f.executor.resume_paused(expected_pause_sha256=expected,authorization_sha256=proof)
        self.assertEqual(f.executor._read(),before)
    def test_resume_observation_failure_has_no_write_or_enable(self):
        f=self.f;f.activate();f.executor.rollback();before=f.executor._read()
        with patch.object(f.provider,'observe',side_effect=OSError('unavailable')),self.assertRaises(ObservationUnavailable):self.resume()
        self.assertEqual(f.executor._read(),before);self.assertFalse(f.provider.new_enabled)
    def test_resume_audit_failure_has_no_phase_change(self):
        f=self.f;f.activate();f.executor.rollback();before=f.executor._read()
        with patch.object(f.stores['audit'],'compare_exchange',side_effect=StateUnavailable('audit')),self.assertRaises(StateUnavailable):self.resume()
        self.assertEqual(f.executor._read(),before);self.assertFalse(f.provider.new_enabled)
    def test_resume_lost_ack_does_not_double_reset(self):
        f=self.f;f.activate();f.executor.rollback();before=f.snap()
        cas=f.stores['rollout'].compare_exchange
        def lost(*a):cas(*a);raise StateUnavailable('lost resume ack')
        with patch.object(f.stores['rollout'],'compare_exchange',side_effect=lost),self.assertRaises(StateUnavailable):self.resume()
        self.assertEqual(f.executor.inspect()['phase'],'prepared')
        with self.assertRaises(UsageConflict):self.resume()
        f.executor.advance();self.assertEqual(f.snap(),before)
    def test_resume_next_day_requires_new_review_not_guessed_baseline(self):
        f=self.f;f.activate();f.executor.rollback();before=f.executor._read();f.now+=86400
        with self.assertRaises(UsageConflict):self.resume()
        self.assertEqual(f.executor._read(),before);self.assertFalse(f.provider.new_enabled)
    def test_midnight_after_enable_is_contained_before_active(self):
        f=self.f;enable=f.provider.enable_new
        def midnight(m):enable(m);f.now+=86400
        with patch.object(f.provider,'enable_new',side_effect=midnight),self.assertRaises(UsageConflict):f.executor.advance()
        self.assertEqual(f.executor._read()[1]['phase'],'paused');self.assertFalse(f.provider.new_enabled)
    def test_resume_bound_keeps_room_for_failure_containment(self):
        f=self.f;f.activate();f.executor.rollback()
        for _ in range(5):self.resume();f.executor.advance();f.executor.rollback()
        before=f.executor._read()
        with self.assertRaises(UsageConflict):self.resume()
        self.assertEqual(before,f.executor._read());self.assertFalse(f.provider.new_enabled)
    def test_cutover_day_unknown_account_and_v1_limits_explicit(self):
        f=self.f;f.activate()
        with self.assertRaises(StateUnavailable):f.snap('not-in-frozen-list')
        t=f.ledger.reserve('synthetic-b',f.m['day'],'c'*32,'d'*64,free_grant('synthetic-b',f.now))
        f.now+=901
        self.assertEqual(f.ledger.active_receipt(t)['outcome'],'reserved')
        self.assertIsNone(f.ledger.expired_reservation_proof(t))
        f.now=(f.m['day']+1)*86400
        with self.assertRaises(StateUnavailable):f.ledger.fence_missing(type(t)('synthetic-a',t.day,'e'*32,'f'*64))
        with self.assertRaises(UsageConflict):validate_baselines([dict(subject=str(i),successes=0,attempts=0) for i in range(1001)],f.m)

class RolloutReviewGCSTests(RolloutReviewTests):
    def setUp(self):
        self.f=fixture.RolloutGCSTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
    def test_prior_generations_survive_resume(self):
        f=self.f;f.activate();f.executor.rollback();before=dict(f.bucket.versions)
        self.resume();f.executor.advance()
        for k,v in before.items():self.assertEqual(f.bucket.versions[k],v)
        self.assertTrue(all(c[0] in ('reload','download','upload') for c in f.bucket.calls))

if __name__=='__main__':unittest.main()
