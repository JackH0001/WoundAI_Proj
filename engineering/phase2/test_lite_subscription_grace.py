"""Signed synthetic Apple renewal + transaction, real verifier and durable CAS.

No Apple account, sandbox purchase, online OCSP, cloud mutation or real billing.
"""
import base64
import copy
import hashlib
import json
import sqlite3
import unittest
from dataclasses import replace
from unittest.mock import patch

import jwt
import test_lite_subscription_authority as apple
import test_lite_service_subscription_links as links
from lite_subscription_authority import SubscriptionRejected
from lite_attest_state import StateUnavailable
from lite_usage_ledger import digest


def enable_grace(a, remaining=600):
    a.status=4
    a.payload['expiresDate']=(a.now-10)*1000
    renewal=dict(originalTransactionId=apple.BINDING.original_transaction,
        productId=apple.POLICY.product,environment=apple.POLICY.environment,
        appAccountToken=apple.TOKEN,gracePeriodExpiresDate=(a.now+remaining)*1000,
        signedDate=a.now*1000,isInBillingRetryPeriod=True,autoRenewStatus=1,
        autoRenewProductId=apple.POLICY.product)
    fetch=a.fetch
    def response(original):
        r=fetch(original)
        r.data[0].lastTransactions[0].signedRenewalInfo=a.sign(renewal)
        return r
    a.client.get_all_subscription_statuses=response
    return renewal


def rows(store):
    with sqlite3.connect(store.path) as c:
        return c.execute('select key_id,revision,data from attest_state order by key_id').fetchall()


class GraceAuthorityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):apple.AuthorityTests.setUpClass()

    def setUp(self):
        self.a=apple.AuthorityTests();self.a.setUp();self.addCleanup(self.a.doCleanups)
        self.renewal=enable_grace(self.a)

    def grant(self):return self.a.grant()

    def proof(self,g):
        key=hashlib.sha256(('woundlite/subscription-proof/1\0'+g.proof).encode()).digest()
        return self.a.store.load(key)[1]

    def test_valid_grace_admits_ten_with_both_signed_proofs_and_expiry(self):
        with patch.object(self.a.client,'get_all_subscription_statuses',wraps=self.a.client.get_all_subscription_statuses) as q:
            g=self.grant();self.assertEqual(q.call_count,1)
        g.validate(apple.BINDING.subject,self.a.now)
        self.assertEqual((g.limit,g.valid_until),(10,self.a.now+30))
        p=self.proof(g);self.assertEqual(p['v'],2);self.assertEqual(p['entitlement_basis'],'billing-grace')
        self.assertEqual(p['grace_expires_ms'],self.renewal['gracePeriodExpiresDate'])
        self.assertEqual(digest(p),g.proof)
        self.assertEqual(self.a.verifier.verify_and_decode_renewal_info(p['signed_renewal']).originalTransactionId,apple.BINDING.original_transaction)
        self.assertEqual(self.a.verifier.verify_and_decode_signed_transaction(p['signed_transaction']).appAccountToken,apple.TOKEN)

    def test_grace_expiry_caps_grant_without_hardcoded_days(self):
        for seconds in (1,4,29,30,600,28*86400):
            self.renewal['gracePeriodExpiresDate']=(self.a.now+seconds)*1000
            with self.subTest(seconds=seconds):
                self.assertEqual(self.grant().valid_until,self.a.now+min(seconds,30))

    def test_missing_renewal_temporarily_unavailable_not_free(self):
        self.a.client.get_all_subscription_statuses=self.a.fetch
        with self.assertRaises(StateUnavailable):self.grant()
        self.assertEqual(rows(self.a.store),[])

    def test_missing_required_signed_claims_never_grant(self):
        for k in ('originalTransactionId','productId','environment','gracePeriodExpiresDate','signedDate'):
            old=self.renewal.pop(k)
            with self.subTest(field=k),self.assertRaises(SubscriptionRejected):self.grant()
            self.renewal[k]=old
        self.assertEqual(rows(self.a.store),[])

    def test_optional_renewal_token_absence_still_requires_transaction_owner(self):
        del self.renewal['appAccountToken'];self.assertEqual(self.grant().limit,10)
        self.a.payload['appAccountToken']='20000000-0000-4000-8000-000000000001'
        with self.assertRaises(SubscriptionRejected):self.grant()

    def test_renewal_cannot_cross_original_product_environment_or_account(self):
        for k,bad in [('originalTransactionId','999'),('productId','other'),('environment','Production'),
                      ('appAccountToken','20000000-0000-4000-8000-000000000001')]:
            old=self.renewal[k];self.renewal[k]=bad
            with self.subTest(field=k),self.assertRaises(SubscriptionRejected):self.grant()
            self.renewal[k]=old
        self.assertEqual(rows(self.a.store),[])

    def test_coerced_renewal_claim_types_rejected(self):
        for k,bad in [('originalTransactionId',1000000000001),('productId',True),
                      ('gracePeriodExpiresDate',True),('gracePeriodExpiresDate',str((apple.NOW+20)*1000)),
                      ('gracePeriodExpiresDate',float((apple.NOW+20)*1000)),('signedDate',str(apple.NOW*1000)),
                      ('signedDate',False),('isInBillingRetryPeriod','true'),('isInBillingRetryPeriod',1),
                      ('autoRenewStatus',True),('autoRenewStatus',2),('appAccountToken',None),('autoRenewProductId',7)]:
            old=self.renewal[k];self.renewal[k]=bad
            with self.subTest(field=k,bad=bad),self.assertRaises(SubscriptionRejected):self.grant()
            self.renewal[k]=old
        self.assertEqual(rows(self.a.store),[])

    def test_duplicate_signed_json_key_rejected_after_signature_verification(self):
        response=self.a.client.get_all_subscription_statuses(apple.BINDING.original_transaction)
        header=jwt.get_unverified_header(self.a.sign(self.renewal))
        raw=json.dumps(self.renewal)[:-1]+',"gracePeriodExpiresDate":'+str((apple.NOW+60)*1000)+'}'
        signed=jwt.api_jws.encode(raw.encode(),self.a.keys[2],algorithm='ES256',headers=header)
        response.data[0].lastTransactions[0].signedRenewalInfo=signed
        with patch.object(self.a.client,'get_all_subscription_statuses',return_value=response):
            with self.assertRaises(SubscriptionRejected):self.grant()
        self.assertEqual(rows(self.a.store),[])

    def test_tampered_renewal_signature_rejected(self):
        response=self.a.client.get_all_subscription_statuses(apple.BINDING.original_transaction)
        row=response.data[0].lastTransactions[0];p=row.signedRenewalInfo.split('.')
        p[2]=('A' if p[2][0]!='A' else 'B')+p[2][1:];row.signedRenewalInfo='.'.join(p)
        with patch.object(self.a.client,'get_all_subscription_statuses',return_value=response):
            with self.assertRaises(SubscriptionRejected):self.grant()
        self.assertEqual(rows(self.a.store),[])

    def test_renewal_from_untrusted_chain_rejected_independently(self):
        keys,certs=apple.synthetic_chain()
        chain=[base64.b64encode(c.public_bytes(apple.serialization.Encoding.DER)).decode() for c in reversed(certs)]
        response=self.a.client.get_all_subscription_statuses(apple.BINDING.original_transaction)
        response.data[0].lastTransactions[0].signedRenewalInfo=jwt.encode(self.renewal,keys[2],algorithm='ES256',headers={'x5c':chain})
        with patch.object(self.a.client,'get_all_subscription_statuses',return_value=response):
            with self.assertRaises(SubscriptionRejected):self.grant()

    def test_bounded_renewal_input(self):
        for bad in ('',True,'x'*12001):
            r=self.a.client.get_all_subscription_statuses(apple.BINDING.original_transaction)
            r.data[0].lastTransactions[0].signedRenewalInfo=bad
            with patch.object(self.a.client,'get_all_subscription_statuses',return_value=r):
                with self.subTest(value_type=type(bad).__name__),self.assertRaises(SubscriptionRejected):self.grant()

    def test_stale_status_four_expired_grace_never_silently_downgrades(self):
        for grace in ((apple.NOW-1)*1000,apple.NOW*1000,apple.NOW*1000+500):
            self.renewal['gracePeriodExpiresDate']=grace
            with self.subTest(grace=grace),self.assertRaises(StateUnavailable):self.grant()
        self.assertEqual(rows(self.a.store),[])

    def test_renewal_chronology_invalid_or_future_rejected(self):
        for k,bad in [('signedDate',(apple.NOW-11)*1000),('signedDate',(apple.NOW+61)*1000),
                      ('signedDate',-1),('gracePeriodExpiresDate',(apple.NOW-10)*1000),
                      ('gracePeriodExpiresDate',2**53)]:
            old=self.renewal[k];self.renewal[k]=bad
            with self.subTest(field=k,bad=bad),self.assertRaises(SubscriptionRejected):self.grant()
            self.renewal[k]=old

    def test_unexpired_transaction_with_grace_status_is_contradictory(self):
        self.a.payload['expiresDate']=(apple.NOW+1)*1000
        self.renewal['signedDate']=(apple.NOW+1)*1000
        with self.assertRaises(StateUnavailable):self.grant()

    def test_revocation_with_grace_status_cannot_authorize(self):
        self.a.payload['revocationDate']=apple.NOW*1000
        with self.assertRaises(StateUnavailable):self.grant()
        self.assertEqual(rows(self.a.store),[])

    def test_explicit_not_retrying_grace_status_is_contradictory(self):
        self.renewal['isInBillingRetryPeriod']=False
        with self.assertRaises(StateUnavailable):self.grant()

    def test_optional_retry_flag_absent_does_not_override_current_status(self):
        del self.renewal['isInBillingRetryPeriod'];self.assertEqual(self.grant().limit,10)

    def test_future_renewal_change_or_off_does_not_terminate_current_grace(self):
        self.renewal['autoRenewStatus']=0;self.renewal['autoRenewProductId']='future.product'
        self.assertEqual(self.grant().limit,10)

    def test_renewal_verification_deadline_and_expiry_crossing(self):
        verify=self.a.verifier.verify_and_decode_renewal_info
        def slow(signed):
            r=verify(signed);self.a.now+=31;return r
        with patch.object(self.a.verifier,'verify_and_decode_renewal_info',side_effect=slow):
            with self.assertRaises(StateUnavailable):self.grant()
        self.assertEqual(rows(self.a.store),[])

    def test_grace_can_expire_during_verification_within_query_deadline(self):
        self.renewal['gracePeriodExpiresDate']=(apple.NOW+1)*1000
        verify=self.a.verifier.verify_and_decode_renewal_info
        def slow(signed):
            r=verify(signed);self.a.now+=2;return r
        with patch.object(self.a.verifier,'verify_and_decode_renewal_info',side_effect=slow):
            with self.assertRaises(StateUnavailable):self.grant()

    def test_apple_unavailable_never_reuses_old_grace_proof(self):
        self.grant();before=rows(self.a.store)
        with patch.object(self.a.client,'get_all_subscription_statuses',side_effect=TimeoutError()):
            with self.assertRaises(StateUnavailable):self.grant()
        self.assertEqual(rows(self.a.store),before)

    def test_grace_proof_write_lost_reply_retry_retains_identical_evidence(self):
        # Pin the SAME signed response. A new Apple query with a different JWS
        # is a new audit observation and must not overwrite this proof.
        response=self.a.client.get_all_subscription_statuses(apple.BINDING.original_transaction)
        self.a.client.get_all_subscription_statuses=lambda _:response
        real=self.a.store.compare_exchange
        def lose(key,revision,value):
            result=real(key,revision,value)
            if value.get('entitlement_basis')=='billing-grace':raise StateUnavailable('lost proof ack')
            return result
        with patch.object(self.a.store,'compare_exchange',side_effect=lose):
            with self.assertRaises(StateUnavailable):self.grant()
        before=rows(self.a.store);self.assertEqual(self.grant().limit,10)
        self.assertEqual(rows(self.a.store),before)

    def test_refund_and_billing_retry_override_retained_grace(self):
        self.grant()
        for status in (5,3,2):
            self.a.status=status
            with patch.object(self.a.verifier,'verify_and_decode_renewal_info',side_effect=AssertionError('not needed')):
                self.assertEqual(self.grant().limit,2)

    def test_grace_ownership_cannot_fund_second_account(self):
        self.grant()
        with self.assertRaises(SubscriptionRejected):self.a.a.grant(replace(apple.BINDING,subject='another-account'))


class GraceFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):links.SignedFlowTests.setUpClass()

    def setUp(self):
        self.f=links.SignedFlowTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.h,self.a,self.links=self.f.h,self.f.a,self.f.links

    def attach(self):
        return self.links.attach(self.h.accounts,self.h.owner,apple.BINDING.original_transaction,authentication_proof_sha256='b'*64)

    def test_grace_retries_infer_and_charge_once_without_research_upload(self):
        self.attach();enable_grace(self.a)
        with patch.object(self.a.client,'get_all_subscription_statuses',wraps=self.a.client.get_all_subscription_statuses) as q:
            result=self.h.complete(1);self.assertEqual(result.status_code,200,result.json)
            self.assertEqual(self.h.json_request('prepare',1).json,result.json)
            self.assertEqual(self.h.json_request('status',1).json,result.json)
            self.assertEqual(q.call_count,1)
        self.assertEqual(self.h.model_calls,1);self.assertEqual(self.h.quota()['used'],1)
        self.assertEqual(self.h.quota()['pending'],0);self.assertFalse(result.json['research_stored'])

    def test_enter_and_exit_grace_preserve_used_pending_originals_and_audit(self):
        self.attach();first=self.h.complete(1);second=self.h.complete(2)
        previous=[rows(s) for s in (self.links.store,self.links.audit,self.h.accounts.store)]
        enable_grace(self.a);third=self.h.complete(3)
        self.assertEqual(third.json['receipt']['admitted_limit'],10)
        self.a.status=3
        self.assertEqual(self.h.json_request('prepare',4).status_code,429)
        self.assertEqual(self.h.model_calls,3);self.assertEqual(self.h.quota()['used'],3)
        for n,result in ((1,first),(2,second),(3,third)):
            self.assertEqual(self.h.json_request('status',n).json,result.json)
        self.assertEqual(previous,[rows(s) for s in (self.links.store,self.links.audit,self.h.accounts.store)])

    def test_expired_grace_response_503_does_not_consume_attempt_or_change_records(self):
        self.attach();renewal=enable_grace(self.a);renewal['gracePeriodExpiresDate']=apple.NOW*1000
        before=[rows(s) for s in (self.h.ledger.store,self.h.ops.store,self.links.store,self.links.audit)]
        response=self.h.json_request('prepare',1);self.assertEqual(response.status_code,503,response.json)
        self.assertEqual(self.h.model_calls,0)
        self.assertEqual(before,[rows(s) for s in (self.h.ledger.store,self.h.ops.store,self.links.store,self.links.audit)])

    def test_grace_recovery_to_active_does_not_create_extra_allowance(self):
        self.attach();enable_grace(self.a)
        for n in range(1,11):self.assertEqual(self.h.complete(n).status_code,200)
        self.a.status=1;self.a.payload['expiresDate']=(apple.NOW+1000)*1000
        self.assertEqual(self.h.json_request('prepare',11).status_code,429)
        self.assertEqual(self.h.model_calls,10);self.assertEqual(self.h.quota()['used'],10)

    def test_inflight_grace_reservation_executes_once_after_refund(self):
        self.attach();enable_grace(self.a)
        prepare=self.h.json_request('prepare',1);self.assertEqual(prepare.status_code,200,prepare.json)
        self.a.status=5
        result=self.h.complete(1)
        self.assertEqual(self.h.json_request('status',1).json,result.json)
        self.assertEqual(self.h.model_calls,1);self.assertEqual(self.h.quota()['used'],1)
        self.assertEqual(result.json['receipt']['admitted_limit'],10)

    def test_old_link_policy_hash_and_ack_stay_identical_after_grace_upgrade(self):
        # Freeze the PRE-change schema instead of deriving it from ApplePolicy;
        # adding a grace/version field to that dataclass would orphan old keys.
        legacy_policy=digest(dict(bundle='com.woundai.lite',app_id=6825208065,
            product='com.woundai.lite.plus.monthly',environment='Sandbox'))
        key=hashlib.sha256(('lite/service-subscription/1\0'+legacy_policy+'\0'+apple.BINDING.subject).encode()).digest()
        old=self.attach()
        before=[rows(s) for s in (self.links.store,self.links.audit,self.h.accounts.store)]
        enable_grace(self.a)
        self.assertEqual(self.h.accounts.grant(self.h.accounts.resolve(self.h.owner),apple.NOW).limit,10)
        with patch.object(self.a.client,'get_all_subscription_statuses',side_effect=TimeoutError()):
            self.assertEqual(self.attach(),old)
        self.assertEqual(self.links.policy,legacy_policy)
        self.assertEqual(key,self.links.key(apple.BINDING.subject))
        self.assertEqual(before,[rows(s) for s in (self.links.store,self.links.audit,self.h.accounts.store)])

    def test_research_withdrawal_not_reversed_by_grace_entitlement(self):
        self.attach()
        path='/api/v1/lite/data/'+self.h.owner
        r=self.h.client.delete(path,headers=self.h.headers(path,b'','',method='DELETE',purpose='withdraw'))
        self.assertEqual(r.status_code,200,r.json)
        before=copy.deepcopy(self.h.privacy._load(self.h.owner))
        enable_grace(self.a);r=self.h.complete(1)
        self.assertFalse(r.json['research_stored']);self.assertEqual(self.h.privacy._load(self.h.owner),before)


if __name__=='__main__':unittest.main()
