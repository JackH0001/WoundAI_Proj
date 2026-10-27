"""Synthetic Apple chains and durable CAS. No Apple purchase or cloud calls."""
import copy,json,unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from dataclasses import asdict
from flask import Flask
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from lite_attest_state import SQLiteStateStore,StateUnavailable
from lite_subscription_executor import SignedEvidence,canonical,key
from lite_subscription_runtime import SubscriptionRuntime
from lite_subscription_migration import StableSubscriptionLinks
from lite_service_operations import ServiceAccounts
from lite_native_purchase import NativePurchaseBridge,PREFIX,blueprint
from lite_subscription_authority import SubscriptionRejected
from lite_usage_ledger import digest,UsageConflict,UsageLimit
import test_lite_service_subscription_links as legacy
import test_lite_subscription_executor as control
from test_lite_subscription_grace import rows
import base64

class Fixture:
    def __init__(self,host,accounts=None):
        self.f=legacy.LinksTests();self.f.setUp();host.addCleanup(self.f.doCleanups)
        f=self.f;self.a=f.a;self.n=0
        self.links=StableSubscriptionLinks(store=f.store,audit_store=f.audit,authorities=(self.a.a,),
            active_policy=digest(asdict(self.a.a.policy)),legacy_policies=(),authorize=lambda *_:False,
            writer_fence=lambda _:False,clock=lambda:self.a.now)
        self.accounts=accounts or ServiceAccounts(f.accounts.store,subscription_links=self.links)
        self.accounts.subscription_links=self.links;self.accounts.authority=self.links.authority
        f.accounts=self.accounts
        self.signer=Ed25519PrivateKey.generate();self.observer_signer=Ed25519PrivateKey.generate()
        self.evidence=SignedEvidence({'operator':self.signer.public_key().public_bytes_raw()},
            {'observer':self.observer_signer.public_key().public_bytes_raw()},clock=lambda:self.a.now)
        def store(n):return SQLiteStateStore(f.root/(n+'.sqlite'))
        self.state,self.audit,self.observer=store('runtime'),store('runtime-audit'),store('observer')
        self.r=SubscriptionRuntime(links=self.links,registry=self.accounts,state_store=self.state,audit_store=self.audit,
            observation_store=self.observer,evidence=self.evidence,expected_source=control.SOURCE,expected_image=control.IMAGE)
        self.m=dict(v=1,namespace='fresh',source=control.SOURCE,image=control.IMAGE,inventory='b'*64,fence='f'*64,operation='c'*64,entries=[])
        self.publish()
        self.r.activate_fresh(self.m,self.sign(dict(kind='lite-migration-approval/1',manifest=digest(self.m),
            action='activate-fresh',expected_state=digest(None),issued_at=self.a.now,expires_at=self.a.now+900,nonce='a'*64)))
        if accounts is None:self.r.bootstrap('first-device')
        self.bridge=NativePurchaseBridge(runtime=self.r,store=store('purchase-context'),audit_store=store('purchase-audit'))
    def sign(self,p,role='operator'):
        return dict(key_id=role,payload=p,signature=base64.b64encode((self.signer if role=='operator' else self.observer_signer).sign(canonical(p))).decode())
    def publish(self):
        m=self.m;p=dict(kind='lite-migration-observation/1',namespace=m['namespace'],manifest=digest(m),source=m['source'],image=m['image'],
            inventory=m['inventory'],fence=m['fence'],legacy_disabled=True,legacy_inflight=0,migration_inflight=0,stable_reader=True,
            issued_at=self.a.now,expires_at=self.a.now+30)
        k=key('observation',m['namespace']);r=self.observer.load(k)
        self.observer.compare_exchange(k,r[0] if r else 0,self.sign(p,'observer'))
    def context(self,device='first-device',op=None):
        self.n+=1;return self.bridge.context(device,op or f'{self.n:032x}')
    def confirm(self,c=None,device='first-device',payload=None):
        c=c or self.context(device)
        return self.bridge.confirm(device,c['operation'],self.a.sign(payload or self.a.payload))

class NativePurchaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):legacy.LinksTests.setUpClass()
    def setUp(self):self.h=Fixture(self);self.b=self.h.bridge
    def test_context_uses_server_mapping_no_client_chosen_token(self):
        c=self.h.context();self.assertEqual(c['app_account_token'],self.h.f.token)
        self.assertEqual(c,self.b.context('first-device',c['operation']))
        self.assertEqual(self.h.f.grant().limit,2)
    def test_upgrade_and_restore_preserve_used_two_total_ten(self):
        h=self.h;before=rows(h.f.ledger.store);r=h.confirm()
        self.assertEqual(before,rows(h.f.ledger.store));self.assertNotIn('limit',r)
        for n in range(3,11):h.f.use(n,'first-device' if n%2 else 'second-device')
        with self.assertRaises(UsageLimit):h.f.use(11)
        before=rows(h.f.ledger.store)
        with patch.object(h.a.client,'get_all_subscription_statuses',side_effect=AssertionError('no repeat lookup')):
            self.assertEqual(h.confirm(device='second-device'),r)
        self.assertEqual(before,rows(h.f.ledger.store))
    def test_unknown_installation_never_creates_human_account(self):
        before=rows(self.h.accounts.store)
        with self.assertRaises(StateUnavailable):self.h.context('unknown')
        self.assertEqual(before,rows(self.h.accounts.store))
    def test_other_verified_account_cannot_claim_transaction(self):
        h=self.h;h.accounts.provision('other','other','30000000-0000-4000-8000-000000000001',None,'a'*64)
        with self.assertRaises(SubscriptionRejected):h.confirm(device='other')
        self.assertEqual(h.f.grant('other').limit,2);self.assertEqual(rows(self.b.audit),[])
    def test_matching_token_but_different_subject_cannot_steal_existing_purchase(self):
        h=self.h;h.confirm();h.accounts.provision('other','other',h.f.token,None,'a'*64)
        with self.assertRaises(UsageConflict):h.confirm(device='other')
        self.assertEqual(h.f.grant('other').limit,2)
    def test_unsigned_or_wrong_product_environment_bundle_or_token_rejected(self):
        for k,v in [('productId','bad'),('environment','Production'),('bundleId','bad'),('appAccountToken','bad'),
                    ('inAppOwnershipType','FAMILY_SHARED'),('isUpgraded',True),('transactionId',True)]:
            with self.subTest(k=k),self.assertRaises(SubscriptionRejected):self.h.confirm(payload=dict(self.h.a.payload,**{k:v}))
        c=self.h.context()
        with self.assertRaises(SubscriptionRejected):self.b.confirm('first-device',c['operation'],'bad')
        self.assertEqual(rows(self.b.audit),[])
    def test_client_signature_alone_does_not_enable_plus_if_apple_unavailable(self):
        with patch.object(self.h.a.client,'get_all_subscription_statuses',side_effect=TimeoutError()),self.assertRaises(StateUnavailable):self.h.confirm()
        self.assertEqual(self.h.f.grant().limit,2);self.assertEqual(rows(self.b.audit),[])
    def test_server_current_owner_mismatch_rejects_even_valid_client_jws(self):
        h=self.h;c=h.context();signed=h.a.sign(h.a.payload);h.a.payload['appAccountToken']='40000000-0000-4000-8000-000000000001'
        with self.assertRaises(SubscriptionRejected):self.b.confirm('first-device',c['operation'],signed)
        self.assertEqual(h.f.grant().limit,2)
    def test_confirm_requires_saved_context(self):
        with self.assertRaises(UsageConflict):self.b.confirm('first-device','f'*32,self.h.a.sign(self.h.a.payload))
    def test_context_cannot_move_to_different_installation(self):
        c=self.h.context()
        with self.assertRaises(UsageConflict):self.b.confirm('second-device',c['operation'],self.h.a.sign(self.h.a.payload))
    def test_expired_saved_context_can_finish_pending_apple_transaction(self):
        c=self.h.context();self.h.a.now+=901;self.h.publish()
        r=self.h.confirm(c);self.assertEqual(r['state'],'associated')
    def test_lost_confirmation_response_replays_same_receipt_without_apple(self):
        h=self.h;c=h.context();real=self.b.audit.compare_exchange
        def lost(*args):real(*args);raise StateUnavailable('lost acknowledgment')
        with patch.object(self.b.audit,'compare_exchange',side_effect=lost),self.assertRaises(StateUnavailable):h.confirm(c)
        before=rows(h.f.ledger.store)
        with patch.object(h.a.client,'get_all_subscription_statuses',side_effect=AssertionError('repeat')):r=h.confirm(c)
        self.assertEqual(r,h.confirm(c));self.assertEqual(before,rows(h.f.ledger.store))
    def test_lost_association_completion_recovers_same_intent(self):
        h=self.h;c=h.context();real=h.links.audit.compare_exchange
        def fail(k,rev,v):
            if v.get('kind')=='subscription-policy-transition-confirmed':raise StateUnavailable('completion')
            return real(k,rev,v)
        with patch.object(h.links.audit,'compare_exchange',side_effect=fail),self.assertRaises(StateUnavailable):h.confirm(c)
        before=rows(h.links.store)
        with patch.object(h.a.client,'get_all_subscription_statuses',side_effect=AssertionError('repeat')):h.confirm(c)
        self.assertEqual(before,rows(h.links.store))
    def test_two_devices_parallel_confirmation_does_not_reset_usage(self):
        h=self.h;contexts=[h.context(d) for d in ('first-device','second-device')];before=rows(h.f.ledger.store)
        with ThreadPoolExecutor(max_workers=2) as pool:
            rs=list(pool.map(lambda i:h.confirm(contexts[i],('first-device','second-device')[i]),range(2)))
        self.assertEqual(rs[0],rs[1]);self.assertEqual(before,rows(h.f.ledger.store))
    def test_refund_restore_retains_history_without_paid_flag(self):
        h=self.h;r=h.confirm();h.f.use(3);before=rows(h.f.ledger.store);h.a.status=5
        self.assertEqual(h.confirm(),r);self.assertEqual(h.f.grant().limit,2)
        self.assertEqual(before,rows(h.f.ledger.store))
    def test_context_and_receipt_corruption_fail_closed_without_overwrite(self):
        h=self.h;c=h.context();r=self.b.store.load(self.b._key('first-device',c['operation']))
        self.b.store.compare_exchange(self.b._key('first-device',c['operation']),r[0],dict(r[1],v=True));before=rows(self.b.store)
        with self.assertRaises(UsageConflict):h.confirm(c)
        self.assertEqual(before,rows(self.b.store))
    def test_observer_outage_blocks_new_confirmation_then_recovers(self):
        c=self.h.context();self.h.a.now+=31
        with self.assertRaises(StateUnavailable):self.h.confirm(c)
        self.h.publish();self.h.confirm(c)
    def test_restore_new_transaction_same_original_keeps_single_binding(self):
        h=self.h;h.confirm();before=rows(h.links.store)
        r=h.confirm(payload=dict(h.a.payload,transactionId='987654321'))
        self.assertEqual(before,rows(h.links.store));self.assertEqual(r['transaction_id'],'987654321')

class PurchaseHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):legacy.LinksTests.setUpClass()
    def setUp(self):
        import test_lite_service_http as http
        from lite_attest_http import install_lite_attest
        self.h=http.ServiceHTTPTests()
        def install(app,**kw):
            self.p=Fixture(self,kw['commerce'].accounts)
            return install_lite_attest(app,**kw,purchases=self.p.bridge)
        with patch.object(http,'install_lite_attest',side_effect=install):self.h.setUp()
        self.addCleanup(self.h.doCleanups)
    def post(self,kind,**extra):
        return self.h.post_signed(PREFIX+kind,json.dumps(dict(anon_id=self.h.owner,operation='1'*32,**extra)).encode())
    def test_signed_http_context_confirm_and_replay_do_not_write_usage_or_research(self):
        h=self.h;p=self.p;before=[rows(h.ledger.store),rows(h.privacy.store)]
        c=self.post('context');self.assertEqual(c.status_code,200,c.json)
        self.assertEqual(c.json['app_account_token'],h.token)
        signed=p.a.sign(p.a.payload);r=self.post('confirm',signed_transaction=signed)
        self.assertEqual(r.status_code,200,r.json);self.assertEqual(r.headers['Cache-Control'],'no-store')
        self.assertEqual(self.post('confirm',signed_transaction=signed).json,r.json)
        self.assertEqual(before,[rows(h.ledger.store),rows(h.privacy.store)])
        self.assertEqual(h.model_calls,0)
    def test_unsigned_and_owner_spoofing_rejected(self):
        self.assertEqual(self.h.client.post(PREFIX+'context',json={}).status_code,401)
        raw=json.dumps(dict(anon_id='different',operation='1'*32)).encode()
        self.assertEqual(self.h.post_signed(PREFIX+'context',raw).status_code,403)
    def test_client_tier_token_or_subject_fields_not_accepted(self):
        for k,v in [('paid',True),('app_account_token',self.h.token),('subject',self.h.subject)]:
            self.assertEqual(self.post('context',**{k:v}).status_code,409)
    def test_withdrawn_research_does_not_prevent_explicit_purchase_or_reconsent(self):
        h=self.h;path='/api/v1/lite/data/'+h.owner
        r=h.client.delete(path,headers=h.headers(path,b'','',method='DELETE',purpose='withdraw'))
        self.assertEqual(r.status_code,200,r.json);before=rows(h.privacy.store)
        self.assertEqual(self.post('context').status_code,200)
        self.assertEqual(self.post('confirm',signed_transaction=self.p.a.sign(self.p.a.payload)).status_code,200)
        self.assertEqual(before,rows(h.privacy.store))

if __name__=='__main__':unittest.main()
