"""Optional immutable-evidence cache; mutable controls always remain live."""
import unittest
from unittest.mock import patch
from lite_attest_state import SQLiteStateStore, StateUnavailable
from lite_service_release import ServiceReleaseReader
from lite_subscription_executor import key
from lite_usage_ledger import digest
from test_lite_service_release import Fixture
import test_lite_native_purchase as native

class ProofCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):native.NativePurchaseTests.setUpClass()
    def setUp(self):
        self.f=Fixture(self);self.f.active();self.r=self.reader(2)
    def reader(self,size):
        f=self.f
        return ServiceReleaseReader(store=f.store,audit_store=f.audit,manifest=f.manifest,immutable_proof_cache_size=size)
    def test_default_is_off_and_still_detects_audit_unavailability(self):
        r=self.reader(0);r.require_active()
        with patch.object(self.f.audit,'load',side_effect=StateUnavailable()),self.assertRaises(StateUnavailable):r.require_active()
    def test_warm_proof_skips_only_two_audit_reads(self):
        f=self.f;r=self.r;live=f.store.load;audit=f.audit.load
        with patch.object(f.store,'load',wraps=live) as l,patch.object(f.audit,'load',wraps=audit) as a:
            before=r.require_active();self.assertEqual((l.call_count,a.call_count),(1,2))
            self.assertEqual(before,r.require_active());self.assertEqual((l.call_count,a.call_count),(2,2))
    def test_pause_seen_immediately_across_reader_instances(self):
        f=self.f;r=self.r;r.require_active();f.controller.apply(f.approval('pause'))
        with self.assertRaises(StateUnavailable):r.require_active()
    def test_control_outage_never_uses_cached_allow(self):
        r=self.r;r.require_active()
        with patch.object(self.f.store,'load',side_effect=StateUnavailable()),self.assertRaises(StateUnavailable):r.require_active()
    def test_new_generation_requires_new_proof(self):
        f=self.f;r=self.r;r.require_active();f.controller.apply(f.approval('pause'));f.fresh()
        f.controller.apply(f.approval('resume'))
        with patch.object(f.audit,'load',return_value=None),self.assertRaises(StateUnavailable):r.require_active()
        self.assertEqual(r.require_active()['epoch'],3)
    def test_corrupted_cold_proof_never_cached(self):
        f=self.f;s=f.reader.snapshot()[1];k=key('service-release-confirmed',digest(s));rev,v=f.audit.load(k)
        f.audit.compare_exchange(k,rev,dict(v,epoch=123))
        for _ in range(2):
            with self.assertRaises(StateUnavailable):self.r.require_active()
        self.assertEqual(len(self.r._proofs),0)
    def test_cache_bound_and_eviction(self):
        f=self.f;r=self.r
        for _ in range(4):
            r.require_active();f.controller.apply(f.approval('pause'));f.fresh();f.controller.apply(f.approval('resume'))
        r.require_active();self.assertEqual(len(r._proofs),2)
    def test_restart_has_no_cross_process_or_persistent_allow(self):
        self.r.require_active();r=self.reader(2)
        with patch.object(self.f.audit,'load',return_value=None),self.assertRaises(StateUnavailable):r.require_active()
    def test_controller_confirmation_never_uses_reader_cache(self):
        state=self.r.require_active()
        with patch.object(self.f.audit,'load',return_value=None),self.assertRaises(StateUnavailable):self.r.confirmed(state)
    def test_return_value_mutation_cannot_modify_proof_or_store(self):
        s=self.r.require_active();s['phase']='paused';s['epoch']=999
        self.assertEqual(self.r.require_active()['epoch'],1)
    def test_invalid_bounds_fail_at_startup(self):
        for n in (-1,65,True,1.5,None):
            with self.subTest(n=n),self.assertRaises(ValueError):self.reader(n)
    def test_scope_and_store_cache_isolation(self):
        self.r.require_active();other=self.reader(2)
        with patch.object(other.audit,'load',return_value=None),self.assertRaises(StateUnavailable):other.require_active()
    def test_full_grant_read_count_is_reduced_without_caching_registry(self):
        f=self.f;real=SQLiteStateStore.load;reads=[]
        def count(s,k):reads.append((s.path,k));return real(s,k)
        with patch.object(SQLiteStateStore,'load',count):f.grant()
        baseline=len(reads);reads.clear();f.n.r.service_release=self.r;self.r.require_active();reads.clear()
        with patch.object(SQLiteStateStore,'load',count):self.assertEqual(f.grant().limit,2)
        self.assertEqual((baseline,len(reads)),(19,17))
        self.assertTrue(any(path==f.store.path and k==self.r.key for path,k in reads))

if __name__=='__main__':unittest.main()
