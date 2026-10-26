"""Real signed synthetic Apple chains + SQLite CAS; no purchases or cloud."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import copy
import sqlite3
import threading
import unittest
from unittest.mock import patch
import test_lite_subscription_authority as apple
import test_lite_service_http as http
from lite_attest_state import SQLiteStateStore, StateUnavailable, GCSStateStore
from lite_service_operations import ServiceAccounts
from lite_service_subscription_links import ServiceSubscriptionLinks, SubscriptionLinkConflict
from lite_subscription_authority import SubscriptionRejected
from lite_usage_ledger import UsageLedger, UsageLimit, UsageConflict, digest, free_grant
from test_lite_attest_state import FakeBucket


class LinksTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):apple.AuthorityTests.setUpClass()

    def setUp(self):
        self.a=apple.AuthorityTests();self.a.setUp();self.addCleanup(self.a.doCleanups)
        self.root=Path(self.a.tmp.name);self.subject=apple.BINDING.subject;self.token=apple.TOKEN
        self.original=apple.BINDING.original_transaction
        self.store=SQLiteStateStore(self.root/'links.sqlite');self.audit=SQLiteStateStore(self.root/'link-audit.sqlite')
        self.links=ServiceSubscriptionLinks(store=self.store,audit_store=self.audit,authority=self.a.a,clock=lambda:self.a.now)
        self.accounts=ServiceAccounts(SQLiteStateStore(self.root/'accounts.sqlite'),subscription_links=self.links)
        for device in ('first-device','second-device'):
            self.accounts.provision(device,self.subject,self.token,None,'a'*64)
        self.ledger=UsageLedger(SQLiteStateStore(self.root/'usage.sqlite'),namespace='synthetic-links',clock=lambda:self.a.now)
        self.day=self.a.now//86400
        self.ledger.initialize_day(self.subject,self.day,baseline_successes=2,baseline_attempts=2,manifest_sha256='f'*64)

    def attach(self,device='first-device',original=None):
        return self.links.attach(self.accounts,device,original or self.original,authentication_proof_sha256='b'*64)
    def grant(self,device='first-device'):
        return self.accounts.grant(self.accounts.resolve(device),self.a.now)
    def rows(self,store):
        with sqlite3.connect(store.path) as c:return c.execute('select key_id,revision,data from attest_state order by key_id').fetchall()
    def use(self,n,device='first-device'):
        t=self.ledger.reserve(self.subject,self.day,f'{n:032x}','c'*64,self.grant(device))
        self.assertTrue(self.ledger.claim(t,f'{n:032x}'))
        return self.ledger.settle(t,f'{n:032x}','succeeded','d'*64)

    def test_upgrade_preserves_baseline_and_two_devices_share_total_ten(self):
        registration=self.rows(self.accounts.store);usage=self.rows(self.ledger.store)
        with self.assertRaises(UsageLimit):self.use(3)
        receipt=self.attach();self.assertEqual(receipt['kind'],'subscription-association-confirmed')
        self.assertEqual(self.rows(self.accounts.store),registration);self.assertEqual(self.rows(self.ledger.store),usage)
        self.assertEqual(self.grant('second-device').limit,10)
        for n in range(3,11):self.use(n,'first-device' if n%2 else 'second-device')
        with self.assertRaises(UsageLimit):self.use(11)
        self.assertEqual(self.ledger.snapshot(self.subject,self.day,self.grant())['used'],10)
        self.assertEqual(len(self.rows(self.store)),1);self.assertEqual(len(self.rows(self.audit)),2)

    def test_restore_is_read_only_after_completion_and_not_an_active_entitlement(self):
        first=self.attach();before=[self.rows(s) for s in (self.store,self.audit,self.accounts.store,self.ledger.store)]
        with patch.object(self.a.client,'get_all_subscription_statuses',side_effect=TimeoutError()):
            self.assertEqual(self.attach('second-device'),first)
            with self.assertRaises(StateUnavailable):self.grant()
        self.assertNotIn('limit',first)
        self.assertEqual(before,[self.rows(s) for s in (self.store,self.audit,self.accounts.store,self.ledger.store)])

    def test_refund_retains_usage_and_original_receipts(self):
        self.attach();receipt=self.use(3);before=self.rows(self.ledger.store);self.a.status=5
        self.assertEqual(self.grant().limit,2);self.attach('second-device')
        with self.assertRaises(UsageLimit):self.use(4)
        self.assertEqual(before,self.rows(self.ledger.store))
        self.assertEqual(self.ledger.snapshot(self.subject,self.day,self.grant())['used'],3)
        self.assertEqual(receipt['admitted_limit'],10)

    def test_expiry_and_billing_retry_do_not_clear_subscription_or_usage(self):
        self.attach();before=self.rows(self.store);self.a.payload['expiresDate']=(self.a.now-1)*1000
        for status in (2,3):
            self.a.status=status;self.assertEqual(self.grant().limit,2)
            with self.assertRaises(UsageLimit):self.use(3)
        self.assertEqual(before,self.rows(self.store))

    def test_inflight_pre_refund_reservation_is_honored_once(self):
        self.attach();ticket=self.ledger.reserve(self.subject,self.day,'1'*32,'c'*64,self.grant())
        self.a.status=5
        self.assertEqual(self.ledger.reserve(self.subject,self.day,'1'*32,'c'*64,self.grant()),ticket)
        self.assertTrue(self.ledger.claim(ticket,'2'*32));self.assertFalse(self.ledger.claim(ticket,'3'*32))
        self.ledger.settle(ticket,'2'*32,'succeeded','d'*64)
        with self.assertRaises(UsageLimit):self.use(3)

    def test_restore_does_not_initialize_a_new_day(self):
        self.attach();self.a.now+=86400;self.attach('second-device')
        with self.assertRaises(StateUnavailable):
            self.ledger.snapshot(self.subject,self.a.now//86400,free_grant(self.subject,self.a.now))

    def test_unregistered_device_is_not_enrolled_by_restore(self):
        before=self.rows(self.accounts.store)
        with self.assertRaises(StateUnavailable):self.attach('unknown-device')
        self.assertEqual(before,self.rows(self.accounts.store));self.assertEqual(self.rows(self.store),[])

    def test_other_account_cannot_restore_same_purchase(self):
        self.attach();self.accounts.provision('other','other-subject',self.token,None,'a'*64)
        with self.assertRaises(SubscriptionRejected):self.attach('other')
        self.assertEqual(len(self.rows(self.store)),1)

    def test_same_subject_with_different_token_blocks_instead_of_falling_back_free(self):
        self.attach();other='20000000-0000-4000-8000-000000000001'
        self.accounts.provision('bad-mapping',self.subject,other,None,'a'*64)
        with self.assertRaises(StateUnavailable):self.accounts.resolve('bad-mapping')

    def test_client_chosen_token_is_not_accepted(self):
        self.a.payload['appAccountToken']='20000000-0000-4000-8000-000000000001'
        with self.assertRaises(SubscriptionRejected):self.attach()
        self.assertEqual(self.rows(self.store),[]);self.assertEqual(self.rows(self.audit),[])

    def test_invalid_signature_cannot_write_link_or_intent(self):
        with patch.object(self.a.verifier,'verify_and_decode_signed_transaction',side_effect=ValueError('bad')):
            with self.assertRaises(SubscriptionRejected):self.attach()
        self.assertEqual(self.rows(self.store),[]);self.assertEqual(self.rows(self.audit),[])

    def test_unavailable_apple_prevents_first_link_without_changing_free_account(self):
        with patch.object(self.a.client,'get_all_subscription_statuses',side_effect=TimeoutError()):
            with self.assertRaises(StateUnavailable):self.attach()
        self.assertEqual(self.grant().limit,2);self.assertEqual(self.rows(self.store),[])

    def test_different_original_transaction_never_replaces_bound_purchase(self):
        self.attach();before=self.rows(self.store)
        with self.assertRaises(SubscriptionLinkConflict):self.attach(original='999')
        self.assertEqual(self.rows(self.store),before)

    def test_legacy_paid_record_is_retained_or_conflict_blocks(self):
        self.accounts.provision('legacy-paid',self.subject,self.token,self.original,'a'*64)
        self.attach('legacy-paid');self.assertEqual(self.accounts.resolve('legacy-paid')['original_transaction'],self.original)
        self.accounts.provision('legacy-other',self.subject,self.token,'999','a'*64)
        with self.assertRaises(UsageConflict):self.accounts.resolve('legacy-other')
        with self.assertRaises(SubscriptionLinkConflict):self.attach('legacy-other')

    def test_no_subscription_configuration_preserves_legacy_behavior(self):
        old=ServiceAccounts(self.accounts.store)
        self.attach();self.assertIsNone(old.resolve('first-device')['original_transaction'])
        self.assertEqual(old.grant(old.resolve('first-device'),self.a.now).limit,2)
        # Thus rollout MUST keep the reader enabled once links exist; not a safe rollback.

    def test_mismatched_authority_configuration_rejected(self):
        with self.assertRaises(ValueError):ServiceAccounts(self.accounts.store,authority=object(),subscription_links=self.links)
        with self.assertRaises(ValueError):self.links.attach(ServiceAccounts(self.accounts.store),'first-device',self.original,
                                                          authentication_proof_sha256='b'*64)

    def test_malformed_control_inputs_cannot_mutate_stores(self):
        for proof in ('',True,'x'*64):
            with self.assertRaises(UsageConflict):self.links.attach(self.accounts,'first-device',self.original,authentication_proof_sha256=proof)
        for original in (True,'../x',''):
            with self.assertRaises(SubscriptionRejected):self.links.attach(self.accounts,'first-device',original,authentication_proof_sha256='b'*64)
        self.assertEqual(self.rows(self.store),[]);self.assertEqual(self.rows(self.audit),[])

    def test_store_unavailable_blocks_instead_of_granting_free(self):
        with patch.object(self.store,'load',side_effect=StateUnavailable('offline')):
            with self.assertRaises(StateUnavailable):self.accounts.resolve('first-device')

    def test_intent_write_failure_does_not_link(self):
        with patch.object(self.audit,'compare_exchange',side_effect=StateUnavailable('offline')):
            with self.assertRaises(StateUnavailable):self.attach()
        self.assertEqual(self.rows(self.store),[])

    def test_grant_expiring_during_audit_write_does_not_link(self):
        real=self.audit.compare_exchange
        def slow(*args):
            result=real(*args);self.a.now+=31;return result
        with patch.object(self.audit,'compare_exchange',side_effect=slow):
            with self.assertRaises(UsageConflict):self.attach()
        self.assertEqual(self.rows(self.store),[]);self.assertEqual(len(self.rows(self.audit)),1)

    def test_faults_after_each_write_can_resume_without_quota_changes(self):
        for phase in ('intent','link','complete'):
            with self.subTest(phase=phase):
                # Independent case, inject a lost response after real durable write.
                self.setUp();before=self.rows(self.ledger.store)
                target=self.store if phase=='link' else self.audit;real=target.compare_exchange;seen=0
                def lose(key,revision,value):
                    nonlocal seen
                    result=real(key,revision,value)
                    match=phase=='link' or value.get('kind')==('verified-subscription-link' if phase=='intent' else 'subscription-association-confirmed')
                    if match and seen==0:seen+=1;raise StateUnavailable('lost ack')
                    return result
                with patch.object(target,'compare_exchange',side_effect=lose):
                    with self.assertRaises(StateUnavailable):self.attach()
                first=self.attach();self.assertEqual(first,self.attach());self.assertEqual(before,self.rows(self.ledger.store))

    def test_pending_completion_blocks_reads_until_explicit_recovery(self):
        real=self.audit.compare_exchange
        def stop(key,rev,value):
            if value['kind']=='subscription-association-confirmed':raise StateUnavailable('completion')
            return real(key,rev,value)
        with patch.object(self.audit,'compare_exchange',side_effect=stop):
            with self.assertRaises(StateUnavailable):self.attach()
        with self.assertRaises(StateUnavailable):self.accounts.resolve('second-device')
        with patch.object(self.a.client,'get_all_subscription_statuses',side_effect=TimeoutError()):
            self.attach('second-device')  # historical association recovery, not entitlement
        self.assertEqual(self.grant().limit,10)

    def test_corrupt_link_intent_or_completion_is_never_repaired_by_overwrite(self):
        for where in ('link','intent','complete'):
            with self.subTest(where=where):
                self.setUp();self.attach();key=self.links.key(self.subject);row=self.store.load(key);link=row[1]
                store=self.store if where=='link' else self.audit
                if where!='link':key=self.links.audit_key(where,link['intent']);row=store.load(key)
                bad=copy.deepcopy(row[1]);bad['v']=True;store.compare_exchange(key,row[0],bad)
                before=self.rows(store)
                with self.assertRaises(StateUnavailable):self.accounts.resolve('first-device')
                with self.assertRaises(StateUnavailable):self.attach()
                self.assertEqual(self.rows(store),before)

    def test_concurrent_same_subscription_yields_one_link_and_receipt(self):
        barrier=threading.Barrier(6)
        def run(n):barrier.wait(10);return self.attach('first-device' if n%2 else 'second-device')
        with ThreadPoolExecutor(max_workers=6) as pool:receipts=list(pool.map(run,range(6)))
        self.assertTrue(all(r==receipts[0] for r in receipts))
        self.assertEqual(len(self.rows(self.store)),1)
        self.assertEqual(self.ledger.snapshot(self.subject,self.day,self.grant())['used'],2)

    def test_twelve_parallel_requests_on_two_devices_reserve_only_eight_remaining(self):
        self.attach();barrier=threading.Barrier(12)
        def run(n):
            grant=self.grant('first-device' if n%2 else 'second-device');barrier.wait(10)
            try:
                self.ledger.reserve(self.subject,self.day,f'{n:032x}','c'*64,grant)
                return 'reserved'
            except UsageLimit:return 'quota'
        with ThreadPoolExecutor(max_workers=12) as pool:outcomes=list(pool.map(run,range(3,15)))
        self.assertEqual(outcomes.count('reserved'),8);self.assertEqual(outcomes.count('quota'),4)
        snapshot=self.ledger.snapshot(self.subject,self.day,self.grant())
        self.assertEqual(snapshot['used'],2);self.assertEqual(snapshot['pending'],8)

    def test_different_original_race_preserves_winner_and_both_verification_intents(self):
        barrier=threading.Barrier(2);realgrant=self.a.a.grant
        def grant(binding):
            # Both candidates genuinely signed with this account token.
            with patch.object(self.a.client,'get_all_subscription_statuses') as api:
                response=self.a.fetch(self.original);response.data[0].lastTransactions[0].originalTransactionId=binding.original_transaction
                payload=dict(self.a.payload,originalTransactionId=binding.original_transaction)
                response.data[0].lastTransactions[0].signedTransactionInfo=self.a.sign(payload);api.return_value=response
                return realgrant(binding)
        # Build verified grants sequentially; synchronize only real CAS link writers.
        grants={x:grant(replace(apple.BINDING,original_transaction=x)) for x in (self.original,'999')}
        def ready(binding):barrier.wait(10);return grants[binding.original_transaction]
        def run(original):
            try:return self.attach(original=original)
            except SubscriptionLinkConflict:return 'conflict'
        with patch.object(self.a.a,'grant',side_effect=ready),ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(run,(self.original,'999')))
        self.assertEqual(results.count('conflict'),1);self.assertEqual(len(self.rows(self.store)),1)
        self.assertEqual(len(self.rows(self.audit)),3)

    def test_actual_gcs_adapter_fake_transport_preserves_generations_and_cas(self):
        bucket=FakeBucket();self.links.store=GCSStateStore(bucket,prefix='synthetic_binding')
        self.links.audit=GCSStateStore(bucket,prefix='synthetic_binding_audit')
        self.attach();before=len(bucket.versions);start=len(bucket.calls);self.attach('second-device')
        self.assertEqual(len(bucket.versions),before);self.assertFalse(any(c[0]=='upload' for c in bucket.calls[start:]))
        self.assertTrue(all(c[2].get('if_generation_match')==0 for c in bucket.calls if c[0]=='upload'))


class SignedFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):apple.AuthorityTests.setUpClass()

    def setUp(self):
        self.h=http.ServiceHTTPTests();self.h.setUp();self.addCleanup(self.h.doCleanups)
        self.a=apple.AuthorityTests();self.a.setUp();self.addCleanup(self.a.doCleanups)
        # Initialize this synthetic day explicitly; never reset the fixture's old day.
        self.h.e.now=apple.NOW;self.h.initialize_day()
        self.links=ServiceSubscriptionLinks(store=SQLiteStateStore(self.h.root/'links.sqlite'),
            audit_store=SQLiteStateStore(self.h.root/'link-audit.sqlite'),authority=self.a.a,clock=lambda:self.a.now)
        self.h.accounts.subscription_links=self.links;self.h.accounts.authority=self.a.a

    def test_two_free_upgrade_to_total_ten_refund_keeps_prior_receipts(self):
        first=self.h.complete(1);self.h.complete(2);self.assertEqual(self.h.json_request('prepare',3).status_code,429)
        self.links.attach(self.h.accounts,self.h.owner,apple.BINDING.original_transaction,authentication_proof_sha256='b'*64)
        self.assertEqual(self.h.json_request('status',1).json,first.json)
        self.assertEqual(first.json['receipt']['admitted_limit'],2)
        third=self.h.complete(3);self.assertEqual(third.json['receipt']['admitted_limit'],10)
        self.a.status=5
        self.assertEqual(self.h.json_request('prepare',4).status_code,429)
        with patch.object(self.a.client,'get_all_subscription_statuses',side_effect=TimeoutError()):
            self.assertEqual(self.h.json_request('status',3).json,third.json)
        self.assertEqual(self.h.model_calls,3);self.assertEqual(self.h.quota()['used'],3)

    def test_research_withdrawal_before_upgrade_remains_withdrawn(self):
        path='/api/v1/lite/data/'+self.h.owner
        response=self.h.client.delete(path,headers=self.h.headers(path,b'','',method='DELETE',purpose='withdraw'))
        self.assertEqual(response.status_code,200,response.json);before=copy.deepcopy(self.h.privacy._load(self.h.owner))
        self.links.attach(self.h.accounts,self.h.owner,apple.BINDING.original_transaction,authentication_proof_sha256='b'*64)
        result=self.h.complete();self.assertFalse(result.json['research_stored'])
        self.assertEqual(self.h.privacy._load(self.h.owner),before)

    def test_paid_prepare_queries_apple_once_and_completed_retry_zero(self):
        self.links.attach(self.h.accounts,self.h.owner,apple.BINDING.original_transaction,authentication_proof_sha256='b'*64)
        with patch.object(self.a.client,'get_all_subscription_statuses',wraps=self.a.fetch) as query:
            result=self.h.complete()
            self.assertEqual(query.call_count,1)
            self.assertEqual(result.json['receipt']['admitted_limit'],10)
            self.assertEqual(self.h.json_request('prepare').json,result.json)
            self.assertEqual(self.h.json_request('status').json,result.json)
            self.assertEqual(query.call_count,1)
        self.assertEqual(self.h.model_calls,1)

    def test_expired_preflight_refresh_observes_refund_before_admission(self):
        self.h.complete(1);self.h.complete(2)
        self.links.attach(self.h.accounts,self.h.owner,apple.BINDING.original_transaction,authentication_proof_sha256='b'*64)
        original=self.h.ops.store.compare_exchange
        def delayed_refund(*args):
            result=original(*args)
            self.h.e.now+=31;self.a.now+=31;self.a.status=5
            return result
        with patch.object(self.h.ops.store,'compare_exchange',side_effect=delayed_refund), \
                patch.object(self.a.client,'get_all_subscription_statuses',wraps=self.a.fetch) as query:
            response=self.h.json_request('prepare',3)
        self.assertEqual(query.call_count,2)
        self.assertEqual(response.status_code,429,response.json)
        self.assertEqual(self.h.quota()['used'],2)
        self.assertEqual(self.h.quota()['pending'],0)
        self.assertEqual(self.h.model_calls,2)

    def test_signed_http_cannot_select_account_or_tier(self):
        for field,value in [('paid',True),('original_transaction',apple.BINDING.original_transaction),
                            ('appAccountToken',apple.TOKEN),('subject','other')]:
            response=self.h.json_request('prepare',**{field:value});self.assertEqual(response.status_code,409,response.json)
        self.assertIsNone(self.links.lookup(self.h.subject,self.h.token));self.assertEqual(self.h.model_calls,0)


if __name__=='__main__':unittest.main()
