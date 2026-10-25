"""Actual Flask guard + synthetic App Attest crypto + durable usage/results.

No network/Apple/GCS/model accuracy claim. RGB is synthetic, the injected model
returns a known mask, and service/usage/result stores are temporary SQLite files.
"""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import hashlib,io,json,sqlite3,threading,unittest
from unittest.mock import patch
from flask import Flask
from werkzeug.test import EnvironBuilder
from lite_attest_http import install_lite_attest
from lite_attest_budget import RequestBudget,BudgetExceeded
from lite_privacy_state import PrivacyState
from lite_attest_state import SQLiteStateStore,StateUnavailable
from lite_usage_ledger import UsageLedger,free_grant,digest,UsageConflict,UsageLimit
from lite_service_operations import ServiceOperations,ServiceAccounts,RasterInference,RESULT_TTL
from lite_service_http import PREFIX
import test_lite_attest_http as http_fixture
import test_lite_attest_enrollment as enrollment_fixture
import test_lite_quota as media_fixture


class ServiceHTTPTests(unittest.TestCase):
    register=http_fixture.HTTPTests.register
    headers=http_fixture.HTTPTests.headers
    post_signed=http_fixture.HTTPTests.post_signed

    def setUp(self):
        self.e=enrollment_fixture.EnrollmentTests();self.e.setUp();self.addCleanup(self.e.doCleanups)
        self.f=media_fixture.LiteQuotaTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.app=self.f.client.application;self.client=self.f.client;self.counter=0;self.key=http_fixture.b64(self.e.key_id)
        self.root=Path(self.e.temp.name);self.model_calls=0
        def model(rgb):self.model_calls+=1;return self.f.segment(rgb)
        self.inference=RasterInference(model,lambda _: (False,None))
        self.accounts=ServiceAccounts(SQLiteStateStore(self.root/'accounts.sqlite'))
        self.ledger=UsageLedger(SQLiteStateStore(self.root/'usage.sqlite'),namespace='service-tests',clock=lambda:self.e.now)
        self.ops=ServiceOperations(ledger=self.ledger,accounts=self.accounts,store=SQLiteStateStore(self.root/'results.sqlite'),
            inference=self.inference,model_version='a'*64,clock=lambda:self.e.now)
        self.budget=RequestBudget(SQLiteStateStore(self.root/'budget.sqlite'),minute_limit=5000,day_limit=10000,clock=lambda:self.e.now)
        self.privacy=PrivacyState(SQLiteStateStore(self.root/'privacy.sqlite'))
        install_lite_attest(self.app,service=self.e.service,budget=self.budget,privacy=self.privacy,commerce=self.ops)
        self.register();self.subject='synthetic-service-account';self.token='10000000-0000-4000-8000-000000000001'
        self.accounts.provision(self.owner,self.subject,self.token,None,'f'*64)
        self.initialize_day();self.image_hash=hashlib.sha256(self.f.jpeg).hexdigest()

    def initialize_day(self):
        self.ledger.initialize_day(self.subject,self.e.now//86400,baseline_successes=0,baseline_attempts=0,manifest_sha256='d'*64)

    def json_request(self,kind,n=1,**extra):
        body=dict(anon_id=self.owner,operation=f'{n:032x}')
        if kind=='prepare':body['image_sha256']=self.image_hash
        body.update(extra)
        return self.post_signed(PREFIX+kind,json.dumps(body).encode())

    def run_request(self,n=1,raw=None,**fields):
        builder=EnvironBuilder(method='POST',data=dict(anon_id=self.owner,operation=f'{n:032x}',
                    image=(io.BytesIO(self.f.jpeg if raw is None else raw),'synthetic.jpg'),**fields))
        try:
            env=builder.get_environ();data=env['wsgi.input'].read();ctype=env['CONTENT_TYPE']
        finally:builder.close()
        return self.post_signed(PREFIX+'run',data,ctype)

    def complete(self,n=1):
        self.assertEqual(self.json_request('prepare',n).status_code,200)
        response=self.run_request(n);self.assertEqual(response.status_code,200,response.json)
        return response

    def quota(self):
        return self.ledger.snapshot(self.subject,self.e.now//86400,free_grant(self.subject,self.e.now))

    def test_signed_prepare_execute_and_receipt_without_research_media(self):
        prepared=self.json_request('prepare');self.assertEqual(prepared.json['state'],'ready')
        result=self.run_request();self.assertEqual(result.json['state'],'completed');self.assertEqual(self.model_calls,1)
        self.assertEqual(result.json['receipt']['outcome'],'succeeded')
        self.assertFalse(result.json['research_stored']);self.assertEqual(result.headers['Cache-Control'],'no-store')
        self.assertEqual(list(Path(self.f.tmp.name).rglob('*.jpg')),[])
        with sqlite3.connect(self.root/'results.sqlite') as db:payload=db.execute('select data from attest_state').fetchone()[0]
        self.assertNotIn(self.f.jpeg,payload);self.assertNotIn(b'raw_depth',payload)
        self.assertEqual(self.quota()['used'],1)

    def test_completed_readback_needs_no_image_or_entitlement_query(self):
        first=self.complete()
        with patch.object(self.accounts,'grant',side_effect=AssertionError('do not reauthorize a receipt')):
            repeat=self.json_request('status');self.assertEqual(repeat.json,first.json)
            prepared=self.json_request('prepare');self.assertEqual(prepared.json,first.json)
        self.assertEqual(self.model_calls,1);self.assertEqual(self.quota()['attempts'],1)

    def test_repeated_run_does_not_execute_or_change_receipt(self):
        first=self.complete();second=self.run_request()
        self.assertEqual(first.json,second.json);self.assertEqual(self.model_calls,1)

    def test_completed_operation_cannot_be_rebound_to_different_image(self):
        self.complete()
        self.assertEqual(self.run_request(raw=b'changed image').status_code,409)
        self.assertEqual(self.model_calls,1);self.assertEqual(self.quota()['used'],1)

    def test_two_free_then_third_blocked_before_image_upload(self):
        self.complete(1);self.complete(2)
        r=self.json_request('prepare',3)
        self.assertEqual(r.status_code,429);self.assertEqual(r.json['reason'],'success_quota')
        self.assertEqual(self.model_calls,2);self.assertIsNone(self.ops.load(self.subject,f'{3:032x}'))

    def test_concurrent_prepare_preserves_two_slot_bound(self):
        start=threading.Barrier(8)
        def prepare(n):
            start.wait(5)
            try:return self.ops.prepare(self.owner,f'{n:032x}',self.image_hash)['state']
            except UsageLimit:return 'quota'
        with ThreadPoolExecutor(max_workers=8) as pool:states=list(pool.map(prepare,range(1,9)))
        self.assertEqual(states.count('ready'),2);self.assertEqual(states.count('quota'),6)
        self.assertEqual(self.quota()['pending'],2);self.assertEqual(self.model_calls,0)

    def test_concurrent_same_prepare_preserves_one_ticket(self):
        start=threading.Barrier(8)
        def prepare(_):start.wait(5);return self.ops.prepare(self.owner,f'{1:032x}',self.image_hash)
        with ThreadPoolExecutor(max_workers=8) as pool:responses=list(pool.map(prepare,range(8)))
        self.assertTrue(all(r==responses[0] for r in responses));self.assertEqual(self.quota()['attempts'],1)

    def test_research_withdrawal_does_not_reset_or_block_service(self):
        self.complete();path='/api/v1/lite/data/'+self.owner
        response=self.client.delete(path,headers=self.headers(path,b'','',method='DELETE',purpose='withdraw'))
        self.assertEqual(response.status_code,200,response.json)
        self.complete(2);self.assertEqual(self.quota()['used'],2)
        self.assertEqual(self.json_request('prepare',3).status_code,429)
        raw,ctype=http_fixture.HTTPTests.segment_bytes(self)
        blocked=self.post_signed('/api/v1/lite/segment',raw,ctype)
        self.assertEqual(blocked.status_code,410)
        self.assertTrue(self.privacy._load(self.owner)[1]['withdrawn'])
        self.assertEqual(self.privacy._load(self.owner)[1]['writers'],[])

    def test_unprovisioned_authenticated_device_cannot_choose_account(self):
        self.ops.accounts=ServiceAccounts(SQLiteStateStore(self.root/'empty-accounts.sqlite'))
        r=self.json_request('prepare');self.assertEqual(r.status_code,503);self.assertEqual(self.model_calls,0)

    def test_client_cannot_supply_paid_research_depth_or_other_owner(self):
        for field,value in [('paid',True),('service_subject','victim'),('research_consent',True),('consent_epoch','a'*32)]:
            r=self.json_request('prepare',**{field:value});self.assertEqual(r.status_code,409,r.json)
        self.assertEqual(self.json_request('prepare',anon_id='victim').status_code,403)
        self.json_request('prepare')
        self.assertEqual(self.run_request(raw_depth_metadata='{}').status_code,409)
        self.assertEqual(self.model_calls,0)

    def test_ambiguous_json_duplicate_multipart_and_large_control_rejected(self):
        raw=('\u007b"anon_id":"'+self.owner+'","operation":"'+'1'*32+'","operation":"'+'2'*32+'","image_sha256":"'+self.image_hash+'"}').encode()
        self.assertEqual(self.post_signed(PREFIX+'prepare',raw).status_code,401)
        self.assertEqual(self.client.post(PREFIX+'prepare',data=b'x'*4097,content_type='application/json').status_code,413)
        self.json_request('prepare')
        from werkzeug.datastructures import MultiDict
        builder=EnvironBuilder(method='POST',data=MultiDict([
            ('anon_id',self.owner),('operation',f'{1:032x}'),('operation',f'{2:032x}'),
            ('image',(io.BytesIO(self.f.jpeg),'synthetic.jpg'))]))
        try:env=builder.get_environ();data=env['wsgi.input'].read();ctype=env['CONTENT_TYPE']
        finally:builder.close()
        self.assertEqual(self.post_signed(PREFIX+'run',data,ctype).status_code,409)
        self.assertEqual(self.model_calls,0)

    def test_invalid_or_rotated_raster_cancels_before_model(self):
        from PIL import Image
        out=io.BytesIO();im=Image.new('RGB',(20,20));exif=im.getexif();exif[274]=6
        im.save(out,format='JPEG',exif=exif)
        for n,raw in enumerate([b'not a JPEG',out.getvalue()],1):
            self.assertEqual(self.json_request('prepare',n,image_sha256=hashlib.sha256(raw).hexdigest()).status_code,200)
            self.assertEqual(self.run_request(n,raw=raw).status_code,422)
            self.assertEqual(self.json_request('status',n).json['state'],'cancelled')
        self.assertEqual(self.model_calls,0);self.assertEqual(self.quota()['attempts'],2)
        self.assertEqual(self.quota()['remaining'],2)

    def test_invalid_model_mask_stays_pending_and_is_not_published(self):
        import numpy as np
        self.inference.model=lambda rgb:np.full(rgb.shape[:2],float('nan'))
        self.json_request('prepare')
        self.assertEqual(self.run_request().status_code,503)
        status=self.json_request('status')
        self.assertEqual(status.status_code,202);self.assertIsNone(status.json['result'])
        self.assertEqual(self.quota()['used'],0);self.assertEqual(self.quota()['pending'],1)

    def test_unsigned_service_request_cannot_reach_model(self):
        r=self.client.post(PREFIX+'prepare',json=dict(anon_id=self.owner,operation='1'*32,image_sha256=self.image_hash))
        self.assertEqual(r.status_code,401);self.assertEqual(self.model_calls,0)

    def test_tampered_body_and_replayed_security_assertion_rejected(self):
        path=PREFIX+'prepare';body=json.dumps(dict(anon_id=self.owner,operation='1'*32,image_sha256=self.image_hash)).encode()
        headers=self.headers(path,body,'application/json')
        self.assertEqual(self.client.post(path,data=body+b' ',content_type='application/json',headers=headers).status_code,401)
        self.assertEqual(self.client.post(path,data=body,content_type='application/json',headers=headers).status_code,200)
        self.assertEqual(self.client.post(path,data=body,content_type='application/json',headers=headers).status_code,401)

    def test_default_install_does_not_expose_new_service_routes(self):
        app=Flask('default');install_lite_attest(app,service=self.e.service,budget=self.budget,privacy=self.privacy)
        for path in ('prepare','run','status'):
            self.assertEqual(app.test_client().post(PREFIX+path,json={}).status_code,404)

    def test_same_operation_new_image_is_conflict(self):
        self.json_request('prepare')
        self.assertEqual(self.json_request('prepare',image_sha256='c'*64).status_code,409)
        self.assertEqual(self.run_request(raw=b'other').status_code,409);self.assertEqual(self.model_calls,0)

    def test_unknown_operation_and_missing_baseline_never_create_zero_balance(self):
        self.assertEqual(self.json_request('status').status_code,404)
        self.e.now=(self.e.now//86400+1)*86400+1
        self.assertEqual(self.json_request('prepare').status_code,503)
        self.assertIsNone(self.ops.load(self.subject,f'{1:032x}'))

    def test_cross_day_retry_keeps_original_ticket_and_counts(self):
        self.e.now=(self.e.now//86400+1)*86400-10
        self.json_request('prepare');old=self.e.now//86400
        self.e.now=(old+1)*86400+1;self.initialize_day()
        r=self.run_request();self.assertEqual(r.status_code,200,r.json);self.assertEqual(r.json['day'],old)
        self.assertEqual(self.json_request('prepare').json['day'],old)
        self.assertEqual(self.quota()['used'],0)
        self.assertEqual(self.ledger.snapshot(self.subject,old,free_grant(self.subject,self.e.now))['used'],1)

    def test_model_version_change_does_not_silently_recompute(self):
        self.json_request('prepare');self.ops.model_version='b'*64
        self.assertEqual(self.run_request().status_code,409);self.assertEqual(self.model_calls,0)

    def test_result_window_expiry_never_reruns_or_erases_audit(self):
        self.complete();self.e.now+=RESULT_TTL
        self.assertEqual(self.json_request('status').status_code,410)
        self.assertEqual(self.run_request().status_code,410);self.assertEqual(self.model_calls,1)
        self.assertIsNotNone(self.ops.load(self.subject,f'{1:032x}')[1]['result'])

    def test_face_rejection_releases_unstarted_slot_without_model(self):
        self.json_request('prepare');self.inference.face_check=lambda _:(True,'synthetic')
        self.assertEqual(self.run_request().status_code,422)
        self.assertEqual(self.json_request('status').json['state'],'cancelled')
        self.assertEqual(self.model_calls,0);self.assertEqual(self.quota()['remaining'],2);self.assertEqual(self.quota()['attempts'],1)

    def test_unavailable_privacy_check_does_not_claim_or_leak_exception(self):
        self.json_request('prepare');self.inference.face_check=lambda _:(None,'private internal detail')
        r=self.run_request();self.assertEqual(r.status_code,503)
        self.assertNotIn('private',r.get_data(as_text=True));self.assertEqual(self.json_request('status').json['state'],'ready')
        self.assertEqual(self.model_calls,0)

    def test_empty_mask_consumes_attempt_not_success(self):
        self.inference.model=lambda rgb:None
        response=self.complete();self.assertEqual(response.json['receipt']['outcome'],'empty')
        self.assertEqual(self.quota()['used'],0);self.assertEqual(self.quota()['attempts'],1)

    def test_model_exception_stays_pending_without_automatic_reexecution(self):
        def failed(_):self.model_calls+=1;raise RuntimeError('private-secret-detail')
        self.inference.model=failed;self.json_request('prepare')
        r=self.run_request();self.assertEqual(r.status_code,503);self.assertNotIn('private-secret',r.get_data(as_text=True))
        self.assertEqual(self.json_request('status').status_code,202)
        self.assertEqual(self.run_request().status_code,202);self.assertEqual(self.model_calls,1)

    def test_lost_claim_response_never_executes_again(self):
        self.json_request('prepare');real=self.ledger.claim
        def lost(*a):real(*a);raise StateUnavailable('claim response lost')
        with patch.object(self.ledger,'claim',side_effect=lost):self.assertEqual(self.run_request().status_code,503)
        self.assertEqual(self.json_request('status').status_code,202)
        self.assertEqual(self.run_request().status_code,202);self.assertEqual(self.model_calls,0)

    def test_lost_prepare_mapping_response_can_resume_before_reservation(self):
        real=self.ops.store.compare_exchange
        def lost(*a):real(*a);raise StateUnavailable('mapping response lost')
        with patch.object(self.ops.store,'compare_exchange',side_effect=lost):self.assertEqual(self.json_request('prepare').status_code,503)
        self.assertEqual(self.json_request('status').json['state'],'awaiting_reservation')
        self.assertEqual(self.quota()['attempts'],0)
        self.complete();self.assertEqual(self.quota()['attempts'],1);self.assertEqual(self.model_calls,1)

    def test_lost_reserve_response_is_read_back_without_second_charge(self):
        real=self.ledger.reserve
        def lost(*a):real(*a);raise StateUnavailable('reserved response lost')
        with patch.object(self.ledger,'reserve',side_effect=lost):self.assertEqual(self.json_request('prepare').status_code,503)
        self.assertEqual(self.json_request('status').json['state'],'ready')
        self.complete();self.assertEqual(self.quota()['attempts'],1);self.assertEqual(self.model_calls,1)

    def test_lost_result_write_response_reconciles_without_image(self):
        self.json_request('prepare');real=self.ops.store.compare_exchange
        def lost(key,rev,value):
            result=real(key,rev,value)
            if value.get('result') is not None:raise StateUnavailable('persisted response lost')
            return result
        with patch.object(self.ops.store,'compare_exchange',side_effect=lost):self.assertEqual(self.run_request().status_code,503)
        r=self.json_request('status');self.assertEqual(r.status_code,200,r.json)
        self.assertEqual(r.json['state'],'completed');self.assertEqual(self.model_calls,1);self.assertEqual(self.quota()['used'],1)

    def test_process_restart_reopens_durable_result_and_settles_without_image(self):
        self.json_request('prepare')
        with patch.object(self.ledger,'settle',side_effect=StateUnavailable('simulated process exit')):
            self.assertEqual(self.run_request().status_code,503)
        accounts=ServiceAccounts(SQLiteStateStore(self.root/'accounts.sqlite'))
        ledger=UsageLedger(SQLiteStateStore(self.root/'usage.sqlite'),namespace='service-tests',clock=lambda:self.e.now)
        operations=ServiceOperations(ledger=ledger,accounts=accounts,store=SQLiteStateStore(self.root/'results.sqlite'),
            inference=self.inference,model_version='a'*64,clock=lambda:self.e.now)
        app=Flask('reopened');install_lite_attest(app,service=self.e.service,budget=self.budget,privacy=self.privacy,commerce=operations)
        self.app=app;self.client=app.test_client()
        r=self.json_request('status');self.assertEqual(r.status_code,200,r.json)
        self.assertEqual(r.json['state'],'completed');self.assertEqual(self.model_calls,1)
        self.assertEqual(self.quota()['used'],1)

    def test_lost_settlement_response_returns_same_receipt_without_model(self):
        self.json_request('prepare');real=self.ledger.settle
        def lost(*a):real(*a);raise StateUnavailable('settled response lost')
        with patch.object(self.ledger,'settle',side_effect=lost):self.assertEqual(self.run_request().status_code,503)
        r=self.json_request('status');self.assertEqual(r.status_code,200,r.json)
        self.assertEqual(self.quota()['used'],1);self.assertEqual(self.model_calls,1)
        self.assertEqual(r.json,self.json_request('status').json)

    def test_account_remapping_and_corruption_fail_closed(self):
        with self.assertRaises(UsageConflict):self.accounts.provision(self.owner,'other',self.token,None,'f'*64)
        self.json_request('prepare');key=self.ops.key(self.subject,f'{1:032x}');rev,row=self.ops.store.load(key)
        row['image_sha256']='b'*64;self.ops.store.compare_exchange(key,rev,row)
        self.assertEqual(self.run_request().status_code,503);self.assertEqual(self.model_calls,0)

    def test_missing_result_after_settlement_cannot_silently_rerun(self):
        self.complete();key=self.ops.key(self.subject,f'{1:032x}');rev,row=self.ops.store.load(key)
        row['result']=None;row['result_sha256']=None;self.ops.store.compare_exchange(key,rev,row)
        self.assertEqual(self.json_request('status').status_code,503)
        self.assertEqual(self.run_request().status_code,503);self.assertEqual(self.model_calls,1)

    def test_service_inference_does_not_mutate_legacy_research_or_quota(self):
        self.complete()
        self.assertEqual(list(Path(self.f.tmp.name).rglob('*')),[])
        self.assertEqual(media_fixture.lite.quota_snapshot(self.owner,'127.0.0.1')[0]['used'],0)

    def test_two_trusted_installations_share_allowance_not_two_accounts(self):
        self.accounts.provision('second-verified-installation',self.subject,self.token,None,'f'*64)
        self.complete()
        self.ops.prepare('second-verified-installation',f'{2:032x}',self.image_hash)
        self.ops.run('second-verified-installation',f'{2:032x}',self.f.jpeg)
        self.assertEqual(self.json_request('prepare',3).status_code,429)
        self.assertEqual(self.quota()['used'],2)

    def test_global_capacity_rejects_before_upload_parsing(self):
        with patch.object(self.budget,'reserve',side_effect=BudgetExceeded(12)):
            r=self.client.post(PREFIX+'run',data=b'invalid')
        self.assertEqual(r.status_code,429);self.assertEqual(r.headers['Retry-After'],'12');self.assertEqual(self.model_calls,0)

    def test_one_executor_during_inference_status_and_duplicate_run_are_pending(self):
        self.json_request('prepare');entered=threading.Event();resume=threading.Event()
        real=self.inference.model
        def slow(rgb):entered.set();self.assertTrue(resume.wait(5));return real(rgb)
        self.inference.model=slow
        # Actual coordinator contention is separate from the App Attest counter
        # order; HTTP crypto/replay is tested above, not bypassed in production.
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(self.ops.run,self.owner,f'{1:032x}',self.f.jpeg)
            try:
                self.assertTrue(entered.wait(5));self.assertEqual(self.json_request('status').status_code,202)
                self.assertEqual(self.run_request().status_code,202)
            finally:resume.set()
            self.assertEqual(future.result(timeout=5)['state'],'completed')
        self.assertEqual(self.model_calls,1)

    def test_official_apple_authority_grants_total_ten_and_refund_retains_usage(self):
        from test_lite_subscription_authority import AuthorityTests,BINDING,NOW
        AuthorityTests.setUpClass();a=AuthorityTests();a.setUp();self.addCleanup(a.doCleanups)
        self.e.now=NOW;self.subject=BINDING.subject
        self.ops.accounts=self.accounts=ServiceAccounts(SQLiteStateStore(self.root/'paid-accounts.sqlite'),a.a)
        self.accounts.provision(self.owner,self.subject,BINDING.app_account_token,BINDING.original_transaction,'f'*64)
        self.initialize_day()
        for n in range(1,11):self.complete(n)
        self.assertEqual(self.json_request('prepare',11).status_code,429)
        a.status=5
        self.assertEqual(self.json_request('prepare',11).status_code,429)
        self.assertEqual(self.json_request('status',1).status_code,200)
        self.assertEqual(self.model_calls,10);self.assertEqual(self.quota()['used'],10)


if __name__=='__main__':unittest.main()
