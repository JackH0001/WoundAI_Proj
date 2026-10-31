"""Full candidate graph + signed HTTP, synthetic Apple/KMS/model, durable SQLite.

Provider signatures and App Attest cryptography run normally against test roots.
There are no cloud credentials, purchases, research media or live KMS actions.
"""
import hashlib
import io
import json
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from PIL import Image
from lite_native_backend import compose_native_backend, STORE_NAMES, location, overlaps
from lite_attest_state import SQLiteStateStore, GCSStateStore, StateUnavailable
from lite_attest_budget import RequestBudget
from lite_privacy_state import PrivacyState
from lite_grant_key_custody import GoogleGrantKeyProvider
from lite_service_operations import RasterInference
from lite_usage_ledger import free_grant
from lite_apple_oauth import TOKEN_URL, REVOKE_URL
from test_lite_service_release import Fixture as ReleaseFixture
from test_lite_account_identity import IdentityFixture
from test_lite_apple_grants import GrantFixture
from test_lite_grant_key_custody import KMSSession
from test_lite_subscription_grace import rows
import test_lite_native_purchase as native
import test_lite_attest_http as attest_http
import test_lite_attest_enrollment as enrollment
import test_lite_service_http as service_http


class Fixture:
    def __init__(self, host, day_offset=86400):
        self.release = r = ReleaseFixture(host); r.active()
        # General registration opens after the reviewed cutover UTC day. The
        # existing first-day allowlist is not silently given zero baselines.
        r.n.a.now += day_offset
        r.n.a.payload.update(purchaseDate=(r.n.a.now-100)*1000,
            signedDate=r.n.a.now*1000,expiresDate=(r.n.a.now+1000)*1000)
        self.identity = IdentityFixture(host, runtime_fixture=r.n)
        self.grant = GrantFixture(host, identity=self.identity)
        self.kms = KMSSession()
        root = r.n.f.root
        self.stores = {k:SQLiteStateStore(root/('native-'+k+'.sqlite')) for k in STORE_NAMES}
        self.clock = self.identity.now
        self.model_calls = 0
        self.model_hook = lambda:None
        def model(rgb):
            self.model_calls += 1; self.model_hook()
            mask = np.zeros(rgb.shape[:2],np.uint8); mask[8:56,8:56] = 1
            return mask, {'route':'student'}
        self.inference = RasterInference(model,lambda _:(False,None))
        self.e = enrollment.EnrollmentTests(); self.e.setUp(); host.addCleanup(self.e.doCleanups)
        self.e.now = self.clock(); self.e.challenge = self.e.service.issue_challenge(self.e.key_id)
        self.e.encoded = self.e.make_proof()
        self.budget = RequestBudget(SQLiteStateStore(root/'native-budget.sqlite'),
            minute_limit=5000,day_limit=10000,clock=self.clock)
        self.privacy = PrivacyState(SQLiteStateStore(root/'native-research-privacy.sqlite'))
        self.kw = dict(enabled=True,runtime=r.n.r,rollout=r.rollout,security=self.e.service,
            budget=self.budget,privacy=self.privacy,verifier=self.identity.verifier,oauth=self.grant.provider,
            key_provider=GoogleGrantKeyProvider(session=self.kms,
                keyring='projects/synthetic-only/locations/test/keyRings/lite'),
            stores=self.stores,namespace='native-sandbox-candidate',identity_key=b'n'*32,
            inference=self.inference,model_version='a'*64)

    def compose(self,**changes): return compose_native_backend(**dict(self.kw,**changes))
    def snapshot(self): return {k:rows(s) for k,s in self.stores.items()}


class NativeBackendTests(unittest.TestCase):
    register = attest_http.HTTPTests.register
    headers = attest_http.HTTPTests.headers
    post_signed = attest_http.HTTPTests.post_signed
    json_request = service_http.ServiceHTTPTests.json_request
    run_request = service_http.ServiceHTTPTests.run_request
    complete = service_http.ServiceHTTPTests.complete

    @classmethod
    def setUpClass(cls): native.NativePurchaseTests.setUpClass()

    def setUp(self):
        self.initialize()

    def initialize(self,day_offset=86400):
        self.h=Fixture(self,day_offset); self.e=self.h.e; self.backend=self.h.compose()
        self.app=self.backend.create_app(); self.client=self.app.test_client()
        self.counter=0; self.key=attest_http.b64(self.e.key_id); self.register()
        image=Image.new('RGB',(64,64),(200,100,120)); buf=io.BytesIO(); image.save(buf,format='JPEG')
        self.f=SimpleNamespace(jpeg=buf.getvalue()); self.image_hash=hashlib.sha256(self.f.jpeg).hexdigest()
        self.login_serial=100

    def post(self,path,**fields):
        raw=json.dumps({'anon_id':self.owner,**fields}).encode(); target='/api/v1/lite/'+path
        if path.startswith('account/deletion/'):
            headers=self.headers(target,raw,'application/json',purpose='withdraw')
            return self.client.post(target,data=raw,content_type='application/json',headers=headers)
        return self.post_signed(target,raw)

    def login(self,subject='apple-human-1'):
        self.login_serial+=1; op=f'{self.login_serial:032x}'
        response=self.post('account/challenge',operation=op)
        self.assertEqual(response.status_code,200,response.json); c=response.json
        b=self.backend.identity
        person=b.events.identity(subject); version=b.grants.cipher.provider.version(b.lifecycle.scope,person)
        if version not in self.h.kms.states: self.h.kms.provision(version)
        signed=self.h.identity.token(c,sub=subject)
        self.h.grant.token=signed
        self.confirm_fields=dict(operation=op,identity_token=signed,state=c['state'],authorization_code='synthetic-code-'+op)
        response=self.post('account/confirm',**self.confirm_fields)
        self.assertEqual(response.status_code,200,response.json)
        self.login_result=response.json; self.login_operation=op; self.person=person
        return response

    def quota(self):
        h=self.h; subject=h.release.n.accounts.resolve(self.owner)['subject']
        return h.release.ledger.snapshot(subject,h.clock()//86400,free_grant(subject,h.clock()))

    def test_end_to_end_two_free_then_block_without_research_or_original_mutation(self):
        before=rows(self.h.privacy.store); image=self.f.jpeg
        self.login(); self.complete(1); self.complete(2)
        denied=self.json_request('prepare',3); self.assertEqual(denied.status_code,429,denied.json)
        self.assertEqual((self.h.model_calls,self.quota()['used']),(2,2))
        self.assertEqual(rows(self.h.privacy.store),before); self.assertEqual(self.f.jpeg,image)
        for s in self.h.stores.values(): self.assertNotIn(image,repr(rows(s)).encode())

    def test_signed_login_and_service_receipt_replay_after_restart_never_reexecutes(self):
        self.login(); first=self.complete(); old=self.h.snapshot()
        self.backend=self.h.compose(); self.client=self.backend.create_app().test_client()
        again=self.post('account/confirm',**self.confirm_fields)
        self.assertEqual(again.json,self.login_result)
        self.assertEqual(self.json_request('status').json,first.json)
        self.assertEqual(self.run_request().json,first.json)
        self.assertEqual((self.h.model_calls,self.quota()['used']),(1,1))
        self.assertEqual(self.h.grant.calls,[TOKEN_URL]); self.assertEqual(old,self.h.snapshot())

    def test_no_session_rejects_model_before_upload(self):
        self.assertEqual(self.json_request('prepare').status_code,401)
        self.assertEqual(self.h.model_calls,0)

    def test_research_withdrawal_does_not_disable_independent_service(self):
        self.h.privacy.withdraw(self.owner); before=rows(self.h.privacy.store)
        self.login(); self.complete()
        self.assertEqual(rows(self.h.privacy.store),before)

    def test_purchase_and_restore_total_ten_preserves_two_used(self):
        self.login(); self.complete(1); self.complete(2)
        context=self.post('purchase/context',operation='c'*32)
        self.assertEqual(context.status_code,200,context.json)
        a=self.h.release.n.a
        a.payload['appAccountToken']=context.json['app_account_token']
        signed=a.sign(a.payload)
        reply=self.post('purchase/confirm',operation='c'*32,signed_transaction=signed)
        self.assertEqual(reply.status_code,200,reply.json)
        with patch.object(a.client,'get_all_subscription_statuses',side_effect=AssertionError('no duplicate Apple status')):
            self.assertEqual(self.post('purchase/confirm',operation='c'*32,signed_transaction=signed).json,reply.json)
        self.assertEqual(self.quota()['used'],2)
        acct=self.h.release.n.accounts.resolve(self.owner)
        self.assertEqual(self.h.release.n.accounts.grant(acct,self.h.clock()).limit,10)
        self.complete(3); self.assertEqual(self.quota()['used'],3)

    def test_wrong_purchase_account_token_cannot_upgrade_or_change_usage(self):
        self.login(); self.complete(); self.post('purchase/context',operation='c'*32)
        a=self.h.release.n.a; signed=a.sign(a.payload) # original fixture's OTHER account
        response=self.post('purchase/confirm',operation='c'*32,signed_transaction=signed)
        self.assertEqual(response.status_code,409,response.json); self.assertEqual(self.quota()['used'],1)

    def test_logout_blocks_new_work_and_late_confirmation_but_keeps_result(self):
        self.login(); self.complete(); old=rows(self.h.stores['operations'])
        self.assertEqual(self.post('account/logout',operation=self.login_operation).status_code,200)
        self.assertEqual(self.json_request('prepare',2).status_code,401)
        self.assertEqual(self.post('account/confirm',**self.confirm_fields).status_code,401)
        self.assertEqual(old,rows(self.h.stores['operations']))
        self.login(); self.assertEqual(self.json_request('status').status_code,200)
        self.assertEqual((self.h.model_calls,self.quota()['used']),(1,1))

    def test_closure_intake_and_readback_never_dispatch_cleanup_or_erase_original(self):
        self.login(); self.complete(); before=rows(self.h.stores['operations']); calls=list(self.h.kms.calls)
        result=self.post('account/deletion/request',operation='d'*32,confirmation='delete_account')
        self.assertEqual(result.status_code,202,result.json); self.assertFalse(result.json['account_deleted'])
        status=self.post('account/deletion/status',operation='d'*32)
        self.assertEqual(status.json,result.json)
        self.assertTrue(all(action in ('get','decrypt') for action,_ in self.h.kms.calls[len(calls):]))
        self.assertNotIn(REVOKE_URL,self.h.grant.calls)
        self.assertEqual(before,rows(self.h.stores['operations']))
        self.assertEqual(self.json_request('prepare',2).status_code,401)

    def test_late_model_after_closure_cannot_write_result_or_report_success(self):
        self.login(); self.assertEqual(self.json_request('prepare').status_code,200)
        self.h.model_hook=lambda:self.backend.deletion.begin(self.owner,'d'*32)
        response=self.run_request(); self.assertNotEqual(response.status_code,200,response.json)
        self.assertEqual(self.h.model_calls,1)
        subject=self.h.release.n.accounts.registration(self.owner)['subject']
        self.assertIsNone(self.backend.operations.load(subject,'0'*31+'1')[1]['result'])
        status=self.post('account/deletion/status',operation='d'*32)
        self.assertEqual(status.status_code,202,status.json); self.assertFalse(status.json['account_deleted'])

    def test_pause_blocks_new_work_and_still_allows_deletion_intake(self):
        self.login(); r=self.h.release; r.controller.apply(r.approval('pause'))
        self.assertEqual(self.json_request('prepare').status_code,503)
        self.backend=self.h.compose(); self.client=self.backend.create_app().test_client()
        self.assertEqual(self.post('account/deletion/request',operation='d'*32,confirmation='delete_account').status_code,202)

    def test_candidate_rejects_research_and_worker_routes_before_any_state_touch(self):
        before=rows(self.h.privacy.store),rows(self.h.budget.store),self.h.snapshot()
        for path in ('segment','annotation','data/'+self.owner,'admin/activate','account/deletion/advance'):
            self.assertEqual(self.client.post('/api/v1/lite/'+path,json={'anon_id':self.owner}).status_code,404)
        self.assertEqual(before,(rows(self.h.privacy.store),rows(self.h.budget.store),self.h.snapshot()))

    def test_invalid_signature_is_not_replaced_by_session_identity(self):
        self.login(); raw=json.dumps(dict(anon_id=self.owner,operation='1'*32,image_sha256=self.image_hash)).encode()
        self.assertEqual(self.client.post('/api/v1/lite/service/prepare',data=raw,content_type='application/json').status_code,401)
        self.assertEqual(self.h.model_calls,0)

    def test_expired_session_renews_without_interactive_apple_login_or_second_charge(self):
        self.login(); first=self.complete(); self.h.release.n.a.now+=601; self.e.now=self.h.clock()
        self.assertEqual(self.json_request('status').status_code,401)
        reply=self.post('account/renew',operation=self.login_operation,renewal_operation='e'*32,expected_generation=0)
        self.assertEqual(reply.status_code,200,reply.json)
        self.assertEqual(self.json_request('status').json,first.json)
        self.assertEqual(self.h.grant.calls,[TOKEN_URL]); self.assertEqual(self.h.model_calls,1)

    def test_cross_midnight_recovery_preserves_original_and_executes_once(self):
        h=self.h; h.release.n.a.now=(h.clock()//86400+1)*86400-5; self.e.now=h.clock()
        h.identity.verifier.expires_at=h.clock()+3599; self.login()
        with patch.object(h.release.ledger,'reserve',side_effect=StateUnavailable('synthetic CAS offline')):
            self.assertEqual(self.json_request('prepare').status_code,503)
        subject=h.release.n.accounts.resolve(self.owner)['subject']
        old=self.backend.operations.load(subject,'0'*31+'1')
        h.release.n.a.now+=6; self.e.now=h.clock()
        self.assertEqual(self.json_request('status').json['state'],'recovery_available')
        recovery=self.json_request('recover',image_sha256=self.image_hash)
        self.assertEqual(recovery.status_code,200,recovery.json)
        new=int(recovery.json['recovery']['successor_operation'],16)
        self.assertEqual(self.json_request('recover',image_sha256=self.image_hash).json,recovery.json)
        result=self.complete(new); self.assertEqual(self.run_request(new).json,result.json)
        self.assertEqual(self.backend.operations.load(subject,'0'*31+'1'),old)
        self.assertEqual((h.model_calls,self.quota()['used'],self.quota()['attempts']),(1,1,1))

    def test_model_result_saved_before_settlement_outage_reads_back_without_rerun(self):
        self.login(); self.assertEqual(self.json_request('prepare').status_code,200)
        with patch.object(self.h.release.ledger,'settle',side_effect=StateUnavailable('synthetic ledger offline')):
            self.assertEqual(self.run_request().status_code,503)
        result=self.json_request('status'); self.assertEqual(result.status_code,200,result.json)
        self.assertEqual(self.run_request().json,result.json)
        self.assertEqual((self.h.model_calls,self.quota()['used']),(1,1))

    def test_cutover_day_new_account_requires_reviewed_baseline_not_assumed_zero(self):
        self.initialize(day_offset=0); h=self.h
        self.login()
        self.assertEqual(self.json_request('prepare').status_code,503)
        self.assertEqual(h.model_calls,0)

    def test_signed_body_cannot_name_another_installation(self):
        self.login()
        response=self.post('service/prepare',operation='1'*32,image_sha256=self.image_hash,anon_id='0'*32)
        self.assertEqual(response.status_code,403,response.json); self.assertEqual(self.h.model_calls,0)

    def test_named_gcs_adapter_composition_uses_conditional_writes_with_fake_transport(self):
        from test_lite_attest_state import FakeBucket
        bucket=FakeBucket(); bucket.name='synthetic-native-candidate'
        stores={n:GCSStateStore(bucket,prefix='native/'+n) for n in STORE_NAMES}
        self.backend=self.h.compose(stores=stores); self.client=self.backend.create_app().test_client()
        self.login(); result=self.complete()
        self.backend=self.h.compose(stores=stores); self.client=self.backend.create_app().test_client()
        self.assertEqual(self.json_request('status').json,result.json)
        self.assertEqual((self.h.model_calls,self.quota()['used']),(1,1))
        uploads=[c for c in bucket.calls if c[0]=='upload']
        self.assertTrue(uploads)
        self.assertTrue(all('if_generation_match' in c[2] for c in uploads))


class CompositionPreflightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): native.NativePurchaseTests.setUpClass()
    def setUp(self): self.h=Fixture(self)
    def refused(self,**changes):
        before=self.h.snapshot()
        with self.assertRaises((ValueError,StateUnavailable)): self.h.compose(**changes)
        self.assertEqual(self.h.snapshot(),before)
        self.assertEqual(self.h.kms.calls,[]); self.assertEqual(self.h.grant.calls,[])
    def test_default_off(self): self.refused(enabled=False)
    def test_missing_store(self): self.refused(stores={k:s for k,s in self.h.stores.items() if k!='recovery'})
    def test_shared_native_store_rejected(self): self.refused(stores=dict(self.h.stores,identity=self.h.stores['grants']))
    def test_alias_to_quota_file_rejected(self):
        self.refused(stores=dict(self.h.stores,operations=SQLiteStateStore(self.h.release.ledger.store.path)))
    def test_apple_proof_store_cannot_alias_quota(self):
        next(iter(self.h.release.n.links.catalog.values())).proofs=self.h.release.ledger.store
        self.refused()
    def test_apple_bundle_mismatch(self):
        self.h.identity.verifier.audience='com.other.app'; self.refused()
    def test_app_attest_bundle_mismatch(self):
        self.h.e.service.admission.policy=replace(self.h.e.service.admission.policy,app_id='ABCDEFGHIJ.com.other.app'); self.refused()
    def test_different_service_release(self):
        self.h.release.rollout.service_release=None; self.refused()
    def test_missing_quota_fence(self):
        self.h.release.ledger.cutover=None; self.refused()
    def test_substituted_quota_guard(self):
        self.h.release.ledger.cutover.verify_writer_fence=lambda _:True; self.refused()
    def test_bad_identity_key(self): self.refused(identity_key=b'x')
    def test_production_subscription_not_allowed(self):
        authority=next(iter(self.h.release.n.links.catalog.values()))
        authority.policy=replace(authority.policy,environment='Production'); self.refused()
    def test_unknown_store_interface_not_trusted(self): self.refused(stores=dict(self.h.stores,identity=object()))
    def test_startup_does_not_call_providers_or_change_quota_or_activate(self):
        h=self.h; before=rows(h.release.ledger.store),h.release.reader.snapshot()
        with patch.object(h.release.rollout,'execute',side_effect=AssertionError('no rollout')):
            h.compose()
        self.assertEqual(before,(rows(h.release.ledger.store),h.release.reader.snapshot()))
        self.assertEqual(h.kms.calls,[]); self.assertEqual(h.grant.calls,[])
    def test_storage_overlap_includes_nested_gcs_prefixes_and_physical_sqlite_paths(self):
        self.assertTrue(overlaps(('gcs','bucket','a'),('gcs','bucket','a/b')))
        self.assertFalse(overlaps(('gcs','bucket','a'),('gcs','bucket','ab')))
        path=self.h.release.n.f.root/'alias.sqlite'
        self.assertEqual(location(SQLiteStateStore(path)),location(SQLiteStateStore(path.parent/'.'/path.name)))
