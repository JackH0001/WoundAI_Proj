"""Actual signed synthetic App Attest requests through the opt-in coordinator.

No successor endpoint/native rollover is enabled. All original capture/results
remain bound to the same operation; fenced recovery is a separate future gate.
"""
import unittest
from unittest.mock import patch
from lite_attest_state import SQLiteStateStore, StateUnavailable
from lite_usage_cutover import CutoverPolicy, seal_cutover
from lite_usage_ledger import UsageLedger, UsageConflict, free_grant
from test_lite_usage_recovery import manifest
import test_lite_service_http as service_fixture


class RecoveryHTTPTests(unittest.TestCase):
    def setUp(self):
        self.h=service_fixture.ServiceHTTPTests();self.h.setUp();self.addCleanup(self.h.doCleanups)
        h=self.h;self.frozen=True
        control=SQLiteStateStore(h.root/'cutover.sqlite');value=manifest('service-tests',h.e.now)
        proof=seal_cutover(control,value,verify_frozen_inputs=lambda m:True)
        policy=CutoverPolicy(control,namespace='service-tests',expected_sha256=proof,
            verify_writer_fence=lambda m:self.frozen)
        h.ledger=UsageLedger(h.ledger.store,namespace='service-tests',clock=lambda:h.e.now,cutover=policy)
        h.ops.ledger=h.ledger
        h.e.now=(h.e.now//86400+1)*86400+100

    def test_next_day_prepares_and_completes_without_manual_daily_initialization(self):
        h=self.h;self.assertEqual(h.json_request('prepare').status_code,200)
        result=h.run_request();self.assertEqual(result.status_code,200)
        self.assertEqual(result.json['state'],'completed');self.assertEqual(h.model_calls,1)
        self.assertFalse(result.json['research_stored']);self.assertEqual(h.quota()['used'],1)
        self.assertEqual(h.run_request().json,result.json);self.assertEqual(h.model_calls,1)

    def test_verified_account_required_even_with_cutover(self):
        h=self.h
        with patch.object(h.accounts,'resolve',side_effect=StateUnavailable('account missing')):
            self.assertEqual(h.json_request('prepare').status_code,503)
        self.assertIsNone(h.ledger.store.load(h.ledger.key(h.subject,h.e.now//86400)))
        self.assertEqual(h.model_calls,0)

    def test_fence_uncertain_does_not_start_or_create_daily_row(self):
        self.frozen=False;h=self.h
        self.assertEqual(h.json_request('prepare').status_code,503)
        self.assertIsNone(h.ledger.store.load(h.ledger.key(h.subject,h.e.now//86400)))
        self.assertEqual(h.model_calls,0)

    def test_abandoned_two_prepares_reclaimed_on_next_admission(self):
        h=self.h
        for n in (1,2):self.assertEqual(h.json_request('prepare',n).status_code,200)
        self.assertEqual(h.json_request('prepare',3).status_code,429)
        h.e.now+=900
        self.assertEqual(h.json_request('prepare',3).status_code,200)
        self.assertEqual(h.quota()['attempts'],3)
        self.assertEqual(h.run_request(1).json['state'],'cancelled')
        self.assertEqual(h.run_request(2).json['state'],'cancelled')
        self.assertEqual(h.model_calls,0)
        self.assertEqual(h.run_request(3).json['state'],'completed');self.assertEqual(h.model_calls,1)

    def test_expired_run_cancels_without_an_admission_or_sweeper(self):
        h=self.h;h.json_request('prepare');h.e.now+=900
        reply=h.run_request();self.assertEqual(reply.status_code,200)
        self.assertEqual(reply.json['state'],'cancelled');self.assertEqual(h.model_calls,0)

    def test_unknown_model_execution_stays_pending_without_second_call(self):
        h=self.h;h.json_request('prepare')
        def fail(rgb):h.model_calls+=1;raise StateUnavailable('synthetic unknown execution')
        with patch.object(h.inference,'infer',side_effect=fail):self.assertEqual(h.run_request().status_code,503)
        h.e.now+=901;self.assertEqual(h.run_request().status_code,202)
        self.assertEqual(h.quota()['pending'],1);self.assertEqual(h.model_calls,1)

    def test_saved_result_lost_settlement_reply_reuses_same_result(self):
        h=self.h;h.json_request('prepare');settle=h.ledger.settle
        def lost(*args):settle(*args);raise StateUnavailable('lost settlement response')
        with patch.object(h.ledger,'settle',side_effect=lost):self.assertEqual(h.run_request().status_code,503)
        h.e.now+=901
        self.assertEqual(h.json_request('status').json['state'],'completed')
        self.assertEqual(h.run_request().json['state'],'completed')
        self.assertEqual((h.quota()['used'],h.model_calls),(1,1))

    def test_control_fields_cannot_be_sent_by_client(self):
        h=self.h
        for extra in ({'cutover':True},{'day':h.e.now//86400},{'reservation_seconds':1}):
            self.assertEqual(h.json_request('prepare',**extra).status_code,409)
        self.assertEqual(h.model_calls,0)

    def test_g3_primitive_is_not_a_silent_native_successor(self):
        h=self.h;h.e.now=(h.e.now//86400+1)*86400-5
        with patch.object(h.ledger,'reserve',side_effect=StateUnavailable('synthetic write unavailable')):
            self.assertEqual(h.json_request('prepare').status_code,503)
        _,original=h.ops.load(h.subject,f'{1:032x}');ticket=h.ops.ticket(h.subject,original)
        h.e.now+=6;proof=h.ledger.fence_missing(ticket)
        self.assertEqual(h.ledger.fence_missing(ticket),proof)
        self.assertEqual(h.json_request('prepare').status_code,409)
        self.assertEqual(h.run_request().status_code,409)
        self.assertIsNone(h.ops.load(h.subject,f'{2:032x}'))
        self.assertEqual(h.model_calls,0)
        self.assertEqual(h.ops.load(h.subject,f'{1:032x}')[1],original)

    def test_cutover_day_v1_history_and_result_remain_readable(self):
        h=self.h;h.e.now-=86400;h.json_request('prepare');old=h.run_request().json
        h.e.now+=86400-1;self.frozen=False
        self.assertEqual(h.json_request('status').json,old);self.assertEqual(h.model_calls,1)


if __name__=='__main__':unittest.main()
