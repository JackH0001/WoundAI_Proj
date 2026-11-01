"""Official Apple 3.1.2 verifier, real synthetic EC certificate chains/JWS.

Server API responses and certificate roots are SYNTHETIC. No Apple sandbox,
OCSP, production purchase, credentials or live account registry is exercised.
"""
from dataclasses import replace
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace as Obj
import base64
import sys
import tempfile
import unittest
from unittest.mock import patch

import jwt
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID, ObjectIdentifier
from appstoreserverlibrary.signed_data_verifier import SignedDataVerifier
from appstoreserverlibrary.models.Environment import Environment

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'Backend/Flask'))
from lite_subscription_authority import *
from lite_usage_ledger import UsageLedger, UsageLimit, free_grant
from lite_attest_state import SQLiteStateStore

NOW=1792886500
TOKEN='10000000-0000-4000-8000-000000000001'
POLICY=ApplePolicy('com.woundai.lite',6825208065,'com.woundai.lite.plus.monthly','Sandbox')
BINDING=ServiceBinding('synthetic-account',TOKEN,'1000000000001')


def synthetic_chain():
    keys=[ec.generate_private_key(ec.SECP256R1()) for _ in range(3)]
    certs=[]
    for i,key in enumerate(keys):
        issuer=keys[max(0,i-1)]
        subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,f'SYNTHETIC TEST ONLY {i}')])
        issuer_name=subject if i==0 else certs[-1].subject
        now=datetime.fromtimestamp(NOW,timezone.utc)
        b=(x509.CertificateBuilder().subject_name(subject).issuer_name(issuer_name)
           .public_key(key.public_key()).serial_number(x509.random_serial_number())
           .not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=2))
           .add_extension(x509.BasicConstraints(ca=i<2,path_length=(1-i) if i<2 else None),True)
           .add_extension(x509.KeyUsage(digital_signature=True,content_commitment=False,key_encipherment=False,
                data_encipherment=False,key_agreement=False,key_cert_sign=i<2,crl_sign=i<2,
                encipher_only=False,decipher_only=False),True)
           .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()),False)
           .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer.public_key()),False))
        if i in (1,2):
            oid='1.2.840.113635.100.6.2.1' if i==1 else '1.2.840.113635.100.6.11.1'
            b=b.add_extension(x509.UnrecognizedExtension(ObjectIdentifier(oid),b'\x05\x00'),False)
        certs.append(b.sign(issuer,hashes.SHA256()))
    return keys,certs


class AuthorityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.keys,cls.certs=synthetic_chain()

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=SQLiteStateStore(Path(self.tmp.name)/'proof.sqlite');self.now=NOW
        self.verifier=SignedDataVerifier([self.certs[0].public_bytes(serialization.Encoding.DER)],False,
                                        Environment.SANDBOX,POLICY.bundle,POLICY.app_id)
        self.payload=dict(bundleId=POLICY.bundle,environment='Sandbox',productId=POLICY.product,
            originalTransactionId=BINDING.original_transaction,transactionId='100000000002',
            appAccountToken=TOKEN,type='Auto-Renewable Subscription',inAppOwnershipType='PURCHASED',
            isUpgraded=False,purchaseDate=(NOW-100)*1000,expiresDate=(NOW+1000)*1000,signedDate=NOW*1000)
        self.status=1;self.client=Obj(get_all_subscription_statuses=self.fetch)
        self.a=AppleSubscriptionAuthority(policy=POLICY,verifier=self.verifier,client=self.client,
                                          proof_store=self.store,clock=lambda:self.now)

    def sign(self,payload=None):
        chain=[base64.b64encode(c.public_bytes(serialization.Encoding.DER)).decode() for c in reversed(self.certs)]
        return jwt.encode(payload or self.payload,self.keys[2],algorithm='ES256',headers={'x5c':chain})

    def fetch(self,original):
        self.assertEqual(original,BINDING.original_transaction)
        return Obj(bundleId=POLICY.bundle,appAppleId=POLICY.app_id,environment=Environment.SANDBOX,
            data=[Obj(lastTransactions=[Obj(originalTransactionId=original,status=self.status,signedTransactionInfo=self.sign())])])

    def grant(self):return self.a.grant(BINDING)

    def test_verified_chain_current_active_returns_ten_and_durable_proof(self):
        g=self.grant();self.assertEqual(g.limit,10);g.validate(BINDING.subject,NOW)
        self.assertEqual(g.valid_until,NOW+30)
        key=hashlib.sha256(('woundlite/subscription-proof/1\0'+g.proof).encode()).digest()
        _,proof=self.store.load(key)
        self.assertEqual(proof['status'],1);self.assertEqual(digest(proof),g.proof)
        self.assertEqual(self.verifier.verify_and_decode_signed_transaction(proof['signed_transaction']).appAccountToken,TOKEN)

    def test_tampered_signature_rejected_by_official_library(self):
        response=self.fetch(BINDING.original_transaction)
        token=response.data[0].lastTransactions[0].signedTransactionInfo
        parts=token.split('.');parts[2]=('A' if parts[2][0]!='A' else 'B')+parts[2][1:]
        response.data[0].lastTransactions[0].signedTransactionInfo='.'.join(parts)
        with patch.object(self.client,'get_all_subscription_statuses',return_value=response):
            with self.assertRaises(SubscriptionRejected):self.grant()

    def test_untrusted_root_rejected_by_official_library(self):
        _,certs=synthetic_chain()
        self.a.verifier=SignedDataVerifier([certs[0].public_bytes(serialization.Encoding.DER)],False,
                                          Environment.SANDBOX,POLICY.bundle,POLICY.app_id)
        with self.assertRaises(SubscriptionRejected):self.grant()

    def test_claims_cannot_cross_owner_product_app_or_environment(self):
        for field,bad in [('bundleId','other.app'),('environment','Production'),('productId','other'),
                          ('originalTransactionId','999'),('appAccountToken','20000000-0000-4000-8000-000000000001'),
                          ('appAccountToken',None),('type','Consumable'),('inAppOwnershipType','FAMILY_SHARED'),('isUpgraded',True)]:
            original=dict(self.payload);self.payload[field]=bad
            with self.subTest(field=field,bad=bad),self.assertRaises(SubscriptionRejected):self.grant()
            self.payload=original

    def test_client_boolean_or_price_cannot_authorize_grant(self):
        with self.assertRaises(SubscriptionRejected):self.a.grant({'paid':True,'price':1.99})

    def test_verified_payload_requires_transaction_identity_and_chronology(self):
        for field,bad in [('transactionId',None),('transactionId','../x'),('transactionId',1),
                          ('signedDate',(NOW-101)*1000),('signedDate',(NOW+61)*1000),
                          ('purchaseDate',True),('expiresDate',(NOW-101)*1000),('isUpgraded',0)]:
            original=dict(self.payload);self.payload[field]=bad
            with self.subTest(field=field,bad=bad),self.assertRaises(SubscriptionRejected):self.grant()
            self.payload=original

    def test_later_refund_status_overrides_old_unexpired_signed_purchase(self):
        self.assertEqual(self.grant().limit,10)
        # API status is current; replaying the old valid JWS alone cannot restore Plus.
        self.status=5;self.assertEqual(self.grant().limit,2)
        self.assertEqual(self.grant().limit,2)

    def test_expired_and_billing_retry_without_grace_are_free(self):
        self.payload['expiresDate']=(NOW-1)*1000
        for status in (2,3):
            self.status=status;self.assertEqual(self.grant().limit,2)

    def test_grace_unknown_or_inconsistent_status_never_invents_entitlement(self):
        for status,error in [(4,StateUnavailable),(99,SubscriptionRejected),(2,StateUnavailable),(3,StateUnavailable)]:
            self.status=status
            with self.subTest(status=status),self.assertRaises(error):self.grant()
        self.status=1;self.payload['revocationDate']=NOW*1000
        with self.assertRaises(StateUnavailable):self.grant()

    def test_expiry_caps_short_grant(self):
        self.payload['expiresDate']=(NOW+4)*1000
        self.assertEqual(self.grant().valid_until,NOW+4)
        self.now+=5
        with self.assertRaises(StateUnavailable):self.grant()

    def test_status_service_unavailable_does_not_use_old_active_or_downgrade(self):
        self.grant()
        with patch.object(self.client,'get_all_subscription_statuses',side_effect=TimeoutError()):
            with self.assertRaises(StateUnavailable):self.grant()

    def test_unknown_or_duplicate_status_subscription_denied(self):
        for kind in ('missing','duplicate'):
            response=self.fetch(BINDING.original_transaction)
            r=response.data[0].lastTransactions
            response.data[0].lastTransactions=[] if kind=='missing' else r+r
            with patch.object(self.client,'get_all_subscription_statuses',return_value=response):
                with self.assertRaises(SubscriptionRejected):self.grant()

    def test_response_application_identity_checked_before_transaction(self):
        for field,bad in [('bundleId','other'),('environment','Production'),('appAppleId',1)]:
            response=self.fetch(BINDING.original_transaction);setattr(response,field,bad)
            with patch.object(self.client,'get_all_subscription_statuses',return_value=response):
                with self.assertRaises(SubscriptionRejected):self.grant()

    def test_network_or_verifier_taking_too_long_cannot_return_stale_grant(self):
        def slow(original):
            response=self.fetch(original);self.now+=31;return response
        with patch.object(self.client,'get_all_subscription_statuses',side_effect=slow):
            with self.assertRaises(StateUnavailable):self.grant()

    def test_proof_write_failure_stops_grant(self):
        with patch.object(self.store,'compare_exchange',side_effect=StateUnavailable('unconfirmed')):
            with self.assertRaises(StateUnavailable):self.grant()

    def test_same_purchase_cannot_fund_second_account(self):
        self.grant()
        with self.assertRaises(SubscriptionRejected):self.a.grant(replace(BINDING,subject='another-account'))

    def test_restore_same_service_account_does_not_create_new_daily_allowance(self):
        l=UsageLedger(SQLiteStateStore(Path(self.tmp.name)/'usage.sqlite'),namespace='synthetic',clock=lambda:self.now)
        l.initialize_day(BINDING.subject,NOW//86400,baseline_successes=2,baseline_attempts=2,manifest_sha256='f'*64)
        for n in range(3,11):
            g=self.grant()  # simulate another authenticated device restoring same account
            t=l.reserve(BINDING.subject,NOW//86400,f'{n:032x}','b'*64,g)
            self.assertTrue(l.claim(t,f'{n:032x}'));l.settle(t,f'{n:032x}','succeeded','c'*64)
        with self.assertRaises(UsageLimit):l.reserve(BINDING.subject,NOW//86400,'a'*32,'b'*64,self.grant())
        self.status=5
        with self.assertRaises(UsageLimit):l.reserve(BINDING.subject,NOW//86400,'b'*32,'b'*64,self.grant())
        self.assertEqual(l.snapshot(BINDING.subject,NOW//86400,self.grant())['used'],10)

    def test_malformed_server_binding_denied(self):
        for b in (replace(BINDING,app_account_token='bad'),replace(BINDING,original_transaction='../x')):
            with self.assertRaises(SubscriptionRejected):self.a.grant(b)

    def test_deployment_factory_enables_online_checks_and_rejects_xcode(self):
        root=self.certs[0].public_bytes(serialization.Encoding.DER)
        v=apple_verifier(POLICY,[root]);self.assertTrue(v._enable_online_checks)
        for policy in (replace(POLICY,environment='Xcode'),replace(POLICY,bundle='other'),replace(POLICY,app_id=0)):
            with self.assertRaises(ValueError):apple_verifier(policy,[root])
        with self.assertRaises(ValueError):apple_verifier(POLICY,[])


if __name__=='__main__':unittest.main()
