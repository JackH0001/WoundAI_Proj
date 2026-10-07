"""Synthetic signed HTTP: research withdrawal must not share inference capacity."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_lite_attest_http as fixture
from lite_attest_http import CHALLENGE_PATH
from lite_attest_budget import RequestBudget, BudgetExceeded
from lite_attest_state import MAX_CHALLENGES, MAX_WITHDRAWAL_CHALLENGES, ChallengeLimit, StateUnavailable

class WithdrawalBudgetTests(unittest.TestCase):
    def setUp(self):
        self.h=fixture.HTTPTests();self.h.setUp();self.addCleanup(self.h.doCleanups)
        self.h.register()
        self.path='/api/v1/lite/data/'+self.h.owner
    def exhaust_general(self):
        self.h.budget.minute_limit=1;self.h.budget.day_limit=1
        with self.assertRaises(BudgetExceeded):self.h.budget.reserve()
    def test_signed_delete_still_works_after_general_capacity_exhausted(self):
        h=self.h
        headers=h.headers(self.path,b'','',method='DELETE')
        self.exhaust_general()
        result=h.client.delete(self.path,headers=headers)
        self.assertEqual(result.status_code,200,result.json)
    def test_challenge_and_delete_use_reserved_capacity_even_when_general_slots_full(self):
        h=self.h
        for _ in range(MAX_CHALLENGES):h.e.service.admission.issue_challenge(h.e.key_id)
        # Retain the configured limit for privacy; fill general via trusted state.
        rev,state=h.budget.store.load(h.budget.key)
        state['minute_used']=h.budget.minute_limit;state['day_used']=h.budget.day_limit
        self.assertTrue(h.budget.store.compare_exchange(h.budget.key,rev,state))
        denied=h.client.post(CHALLENGE_PATH,json=dict(key_id=h.key,purpose='assert'))
        self.assertEqual(denied.status_code,429)
        headers=h.headers(self.path,b'','',method='DELETE',purpose='withdraw')
        result=h.client.delete(self.path,headers=headers)
        self.assertEqual(result.status_code,200,result.json)
        self.assertEqual(h.client.delete(self.path,headers=headers).status_code,401)
    def test_withdrawal_challenge_cannot_authorize_upload_even_with_valid_signature(self):
        h=self.h;path='/api/v1/lite/segment';raw,ctype=h.segment_bytes()
        headers=h.headers(path,raw,ctype,purpose='withdraw')
        result=h.client.post(path,data=raw,content_type=ctype,headers=headers)
        self.assertEqual(result.status_code,401)
        self.assertEqual(list(Path(h.f.tmp.name).rglob('*.jpg')),[])
    def test_withdrawal_capacity_is_bounded_persistent_and_independent(self):
        h=self.h
        h.budget.minute_limit=2;h.budget.day_limit=2
        for _ in range(2):h.budget.reserve(scope='withdraw')
        fresh=RequestBudget(h.budget.store,minute_limit=2,day_limit=2,clock=lambda:h.e.now)
        with self.assertRaises(BudgetExceeded):fresh.reserve(scope='withdraw')
        # Registration used exactly 2 general requests; privacy added none.
        self.assertEqual(h.budget.store.load(h.budget.key)[1]['day_used'],2)
        with self.assertRaises(ValueError):fresh.reserve(scope='typo')
    def test_withdrawal_slots_are_bounded_and_do_not_consume_normal_slots(self):
        h=self.h
        for _ in range(MAX_WITHDRAWAL_CHALLENGES):h.e.service.admission.issue_challenge(h.e.key_id,purpose='withdraw')
        with self.assertRaises(ChallengeLimit):h.e.service.admission.issue_challenge(h.e.key_id,purpose='withdraw')
        for _ in range(MAX_CHALLENGES):h.e.service.admission.issue_challenge(h.e.key_id)
        with self.assertRaises(ChallengeLimit):h.e.service.admission.issue_challenge(h.e.key_id)
    def test_invalid_keys_unknown_purpose_and_unsigned_deletion_are_not_exempt(self):
        h=self.h
        for purpose in ['withdraw','unexpected']:
            result=h.client.post(CHALLENGE_PATH,json=dict(key_id='invalid',purpose=purpose))
            self.assertEqual(result.status_code,401)
        self.assertEqual(h.client.delete(self.path).status_code,401)
        with patch.object(h.budget.store,'load',side_effect=StateUnavailable('private')):
            result=h.client.post(CHALLENGE_PATH,json=dict(key_id=h.key,purpose='withdraw'))
            self.assertEqual(result.status_code,503);self.assertNotIn(b'private',result.data)
    def test_malformed_control_and_nonempty_delete_fail_closed(self):
        h=self.h;before=h.budget.store.load(h.budget.key)[1]['day_used']
        self.assertEqual(h.client.post(CHALLENGE_PATH,data=b'{',content_type='application/json').status_code,401)
        self.assertEqual(h.budget.store.load(h.budget.key)[1]['day_used'],before+1)
        self.assertEqual(h.client.delete(self.path,data=b'x').status_code,413)
    def test_invalid_persisted_purpose_does_not_become_general(self):
        h=self.h;c=h.e.service.admission.issue_challenge(h.e.key_id,purpose='withdraw')
        rev,state=h.e.store.load(h.e.key_id)
        state['challenges'][c.identifier]['purpose']='unknown'
        self.assertTrue(h.e.store.compare_exchange(h.e.key_id,rev,state))
        with self.assertRaises(StateUnavailable):h.e.service.admission.issue_challenge(h.e.key_id,purpose='withdraw')
    def test_reserved_challenge_cannot_delete_another_owner(self):
        h=self.h;path='/api/v1/lite/data/victim'
        # client_data enforces path ownership even before signature generation.
        with self.assertRaises(ValueError):h.headers(path,b'','',method='DELETE',purpose='withdraw')

if __name__=='__main__':unittest.main()
