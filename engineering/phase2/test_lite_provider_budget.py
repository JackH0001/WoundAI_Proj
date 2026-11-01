import copy, tempfile, unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import patch
from lite_attest_state import SQLiteStateStore, StateUnavailable
from lite_provider_budget import ProviderBudget, BudgetUnavailable
from lite_usage_ledger import digest
from lite_subscription_executor import key


class Fixture:
    def __init__(self,host):
        t=tempfile.TemporaryDirectory();host.addCleanup(t.cleanup);self.root=Path(t.name)
        self.store=SQLiteStateStore(self.root/'budget.db');self.audit=SQLiteStateStore(self.root/'audit.db')
        self.now=1000;self.mono=50
        self.p=dict(v=1,namespace='synthetic-budget',manifest='a'*64,source='b'*40,image='c'*64,
            not_before=1000,expires_at=1100,max_allocations=3,requests=3,send_bytes=100,receive_bytes=300,seconds=30)
    def build(self,**kw):
        args=dict(policy=self.p,expected=digest(self.p),store=self.store,audit_store=self.audit,
                  clock=lambda:self.now,monotonic=lambda:self.mono);args.update(kw)
        return ProviderBudget(**args)


class BudgetTests(unittest.TestCase):
    def setUp(self):self.f=Fixture(self)
    def test_reserve_before_dispatch(self):
        b=self.f.build();a=b.allocate();t=a.begin(provider='gcs',method='GET',send_bytes=0,response_limit=100)
        self.assertEqual(b.status()['allocated_attempts'],1);self.assertEqual(a.used['receive_bytes'],100)
        t.received_bytes(10);t.finish();self.assertEqual(a.snapshot()['response_bytes_observed'],10)
        self.assertEqual(a.snapshot()['refunded'],0)
    def test_restart_keeps_spent_allocation(self):
        self.f.build().allocate();self.f.build().allocate()
        self.assertEqual(self.f.build().status()['remaining_attempts'],1)
    def test_exhausted_allocation_count_never_refills(self):
        for _ in range(3):self.f.build().allocate()
        self.f.now+=31
        with self.assertRaises(BudgetUnavailable):self.f.build().allocate()
        self.assertEqual(self.f.build().status()['allocated_attempts'],3)
    def test_lost_cas_ack_burns_allocation_without_authority(self):
        b=self.f.build();fn=self.f.store.compare_exchange
        def lost(k,rev,v):
            result=fn(k,rev,v)
            if k==b._key('state'):raise StateUnavailable('response lost')
            return result
        with patch.object(self.f.store,'compare_exchange',side_effect=lost):
            with self.assertRaises(BudgetUnavailable):b.allocate()
        self.assertEqual(self.f.build().status()['allocated_attempts'],1)
        self.assertFalse(hasattr(b,'recover'));self.assertEqual(self.f.build().allocate().used['requests'],0)
        self.assertEqual(b.status()['allocated_attempts'],2)
    def test_concurrent_allocation_cas_loser_cannot_dispatch(self):
        b=self.f.build();b.allocate();fn=self.f.store.compare_exchange;barrier=Barrier(2)
        def synchronized(k,rev,v):
            if k==b._key('state'):barrier.wait(timeout=5)
            return fn(k,rev,v)
        def allocate():
            try:return b.allocate()
            except BudgetUnavailable:return None
        with patch.object(self.f.store,'compare_exchange',side_effect=synchronized):
            with ThreadPoolExecutor(max_workers=2) as pool:values=list(pool.map(lambda _:allocate(),range(2)))
        self.assertEqual(sum(x is not None for x in values),1);self.assertEqual(b.status()['allocated_attempts'],2)
    def test_policy_change_cannot_reset_same_namespace(self):
        self.f.build().allocate();self.f.p['requests']=4
        with self.assertRaises(BudgetUnavailable):self.f.build()
    def test_source_and_image_remain_in_policy_pin(self):
        original=digest(self.f.p);self.f.p['source']='f'*40
        with self.assertRaises(ValueError):self.f.build(expected=original)
    def test_invalid_or_unbounded_policy(self):
        for name,value in [('max_allocations',0),('max_allocations',21),('requests',True),('requests',10001),
                           ('send_bytes',0),('receive_bytes',0),('seconds',301),('expires_at',5000)]:
            with self.subTest(name=name,value=value):
                p=copy.deepcopy(self.f.p);p[name]=value
                with self.assertRaises(ValueError):self.f.build(policy=p,expected=digest(p))
    def test_aggregate_bounds(self):
        for name,value in [('requests',10000),('send_bytes',32*1024**2),('receive_bytes',512*1024**2)]:
            p=copy.deepcopy(self.f.p);p.update(max_allocations=20);p[name]=value
            with self.subTest(name=name):
                with self.assertRaises(ValueError):self.f.build(policy=p,expected=digest(p))
    def test_same_state_audit_refused(self):
        with self.assertRaises(ValueError):self.f.build(audit_store=self.f.store)
    def test_missing_bound_state_never_reinitialized(self):
        b=self.f.build();b.allocate();load=self.f.store.load
        with patch.object(self.f.store,'load',side_effect=lambda k:None if k==b._key('state') else load(k)):
            with self.assertRaises(BudgetUnavailable):self.f.build()
    def test_missing_binding_refused(self):
        b=self.f.build();b.allocate();load=self.f.store.load
        with patch.object(self.f.store,'load',side_effect=lambda k:None if k==b._key('binding') else load(k)):
            with self.assertRaises(BudgetUnavailable):b.status()
    def test_missing_checkpoint_audit_refused(self):
        b=self.f.build();b.allocate();j=b._read()[1];target=key('provider-budget-checkpoint',digest(j));load=self.f.audit.load
        with patch.object(self.f.audit,'load',side_effect=lambda k:None if k==target else load(k)):
            with self.assertRaises(BudgetUnavailable):b.status()
    def test_audit_failure_no_new_allocation(self):
        b=self.f.build();b.allocate();before=b.status()
        with patch.object(self.f.audit,'compare_exchange',side_effect=StateUnavailable('audit unavailable')):
            with self.assertRaises(BudgetUnavailable):b.allocate()
        self.assertEqual(b.status(),before)
    def test_readback_failure_consumes_allocation(self):
        b=self.f.build();fn=b._read;calls=0
        def unavailable():
            nonlocal calls
            calls+=1
            if calls==2:raise StateUnavailable('readback unavailable')
            return fn()
        with patch.object(b,'_read',side_effect=unavailable):
            with self.assertRaises(StateUnavailable):b.allocate()
        self.assertEqual(b.status()['allocated_attempts'],1)
    def test_expired_and_future_policy_no_allocation(self):
        for now in (999,1100):
            self.f.now=now
            with self.assertRaises(BudgetUnavailable):self.f.build().allocate()
        self.assertEqual(self.f.build().status()['allocated_attempts'],0)
    def test_requests_exhaustion_does_not_allow_extra_transport(self):
        a=self.f.build().allocate();sent=[]
        for _ in range(4):
            try:a.begin(provider='kms',method='GET',send_bytes=0,response_limit=1);sent.append(1)
            except BudgetUnavailable:pass
        self.assertEqual(len(sent),3);self.assertEqual(a.snapshot()['closed'],'exhausted')
    def test_send_budget_reserved_on_failed_attempt(self):
        a=self.f.build().allocate();a.begin(provider='apple',method='POST',send_bytes=90,response_limit=1)
        with self.assertRaises(BudgetUnavailable):a.begin(provider='apple',method='POST',send_bytes=20,response_limit=1)
        self.assertEqual(a.used['send_bytes'],90)
    def test_receive_capacity_not_refunded_for_empty_response(self):
        a=self.f.build().allocate();t=a.begin(provider='gcs',method='DELETE',send_bytes=0,response_limit=300);t.finish()
        with self.assertRaises(BudgetUnavailable):a.begin(provider='gcs',method='GET',send_bytes=0,response_limit=1)
        self.assertEqual(a.snapshot()['response_bytes_observed'],0)
    def test_per_response_limit_stops_and_latches(self):
        a=self.f.build().allocate();t=a.begin(provider='gcs',method='GET',send_bytes=0,response_limit=10)
        self.assertEqual(t.read_size(50),10);t.received_bytes(10)
        with self.assertRaises(BudgetUnavailable):t.received_bytes(1)
        with self.assertRaises(BudgetUnavailable):a.begin(provider='gcs',method='GET',send_bytes=0,response_limit=1)
    def test_exact_response_limit_may_finish(self):
        a=self.f.build().allocate();t=a.begin(provider='gcs',method='GET',send_bytes=0,response_limit=10)
        t.received_bytes(10);t.finish();self.assertEqual(a.snapshot()['completed_responses'],1)
        with self.assertRaises(BudgetUnavailable):t.finish()
    def test_read_size_cannot_exceed_reserved_capacity(self):
        a=self.f.build().allocate();t=a.begin(provider='gcs',method='GET',send_bytes=0,response_limit=10)
        t.received_bytes(9);self.assertEqual(t.read_size(4096),1)
    def test_wall_deadline_stops_before_dispatch(self):
        a=self.f.build().allocate();self.f.now=1030
        with self.assertRaises(BudgetUnavailable):a.begin(provider='gcs',method='GET',send_bytes=0,response_limit=1)
        self.assertEqual(a.used['requests'],0)
    def test_monotonic_deadline_stops_with_frozen_wall_clock(self):
        a=self.f.build().allocate();self.f.mono+=30
        with self.assertRaises(BudgetUnavailable):a.begin(provider='gcs',method='GET',send_bytes=0,response_limit=1)
    def test_clock_regression_is_terminal(self):
        a=self.f.build().allocate();self.f.now-=1
        with self.assertRaises(BudgetUnavailable):a.begin(provider='gcs',method='GET',send_bytes=0,response_limit=1)
        self.f.now+=1
        with self.assertRaises(BudgetUnavailable):a.begin(provider='gcs',method='GET',send_bytes=0,response_limit=1)
    def test_invalid_clock_stops(self):
        a=self.f.build().allocate();self.f.mono=float('nan')
        with self.assertRaises(BudgetUnavailable):a.begin(provider='gcs',method='GET',send_bytes=0,response_limit=1)
    def test_timeout_is_clamped_to_remaining_allocation(self):
        a=self.f.build().allocate();t=a.begin(provider='gcs',method='GET',send_bytes=0,response_limit=1)
        self.f.now+=29;self.assertEqual(t.timeout(10),1)
    def test_response_after_deadline_is_not_a_success(self):
        a=self.f.build().allocate();t=a.begin(provider='gcs',method='GET',send_bytes=0,response_limit=1);self.f.now+=30
        with self.assertRaises(BudgetUnavailable):t.finish()
        self.assertEqual(a.snapshot()['completed_responses'],0)
    def test_concurrent_requests_share_one_meter(self):
        self.f.p['requests']=1;a=self.f.build().allocate()
        def begin():
            try:a.begin(provider='gcs',method='GET',send_bytes=0,response_limit=1);return True
            except BudgetUnavailable:return False
        with ThreadPoolExecutor(max_workers=8) as pool:out=list(pool.map(lambda _:begin(),range(8)))
        self.assertEqual(sum(out),1);self.assertEqual(a.used['requests'],1)
    def test_counters_contain_no_payload_or_account_identity(self):
        a=self.f.build().allocate();a.begin(provider='apple',method='POST',send_bytes=12,response_limit=16)
        self.assertEqual(set(a.snapshot()),{'used','limits','providers','response_bytes_observed','completed_responses','closed','refunded','payload_only'})
    def test_restarting_does_not_recover_old_lease_authority(self):
        b=self.f.build();old=b.allocate();new=self.f.build().allocate()
        self.assertNotEqual(old.name,new.name);self.assertEqual(b.status()['allocated_attempts'],2)
    def test_invalid_payload_bounds_do_not_send(self):
        a=self.f.build().allocate()
        for kw in (dict(provider='other'),dict(method='TRACE'),dict(send_bytes=-1),dict(response_limit=True)):
            args=dict(provider='gcs',method='GET',send_bytes=0,response_limit=1);args.update(kw)
            with self.assertRaises(ValueError):a.begin(**args)
        self.assertEqual(a.used['requests'],0)


if __name__=='__main__':unittest.main()
