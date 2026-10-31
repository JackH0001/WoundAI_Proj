"""Real indexed operations/GCS state adapter, bounded version-aware bucket double."""
import base64
import copy
import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from google.auth.credentials import AnonymousCredentials
from google.cloud import storage
import requests
from google.api_core.exceptions import BadRequest, NotFound, PreconditionFailed
from lite_attest_state import GCSStateStore, StateUnavailable
from lite_service_history import ServiceResultHistory
from lite_subscription_executor import key
from lite_usage_ledger import digest
from lite_service_history_probe import HistoryBucket, run
import test_lite_service_disposition as disposition_fixture
from test_lite_subscription_grace import rows
import test_lite_native_purchase as native


class HistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):native.legacy.LinksTests.setUpClass()
    def setUp(self):
        self.f=disposition_fixture.ServiceDispositionTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.b=HistoryBucket();self.f.ops.store=GCSStateStore(self.b,prefix='synthetic/results')
        self.f.d=self.f.worker();self.audit=self.f.store('history-audit');self.jobs=self.f.store('history-jobs')
        self.policy=dict(v=1,id='synthetic-v1',bucket=self.b.name,prefix=self.f.ops.store.prefix)
        self.h=self.worker()
    def worker(self):
        return ServiceResultHistory(disposition=self.f.d,store=self.jobs,audit_store=self.audit,policy=self.policy)
    def setup_closed(self):
        self.f.complete();self.f.close();self.f.advance()
        self.name=self.f.ops.store._name(self.f.ops.key(self.f.subject,self.f.op));self.seal=self.b.live[self.name]
    def run_history(self,**kw):return self.h.advance(self.f.person,self.f.op,**kw)
    def test_dry_run_lists_history_without_changes(self):
        self.setup_closed();before=copy.deepcopy(self.b.versions);r=self.run_history()
        self.assertEqual(r['status'],'active_history_pending');self.assertGreater(r['versions_selected'],0)
        self.assertEqual(before,self.b.versions);self.assertEqual(rows(self.audit),[]);self.assertEqual(self.b.delete_calls,[])
    def test_all_active_history_removed_current_seal_preserved(self):
        self.setup_closed();original=self.f.unchanged();raw=self.f.raw;r=self.run_history(apply=True)
        self.assertEqual(r['status'],'listed_history_absent_at_check')
        self.assertEqual(self.b.live[self.name],self.seal);self.assertEqual(len(self.b.versions),1)
        self.assertEqual(original,self.f.unchanged());self.assertEqual(self.f.raw,raw);self.assertEqual(self.f.calls,1)
        self.assertFalse(r['account_deleted']);self.assertFalse(r['physical_backups_erased'])
    def test_exact_generation_conditions_and_no_sdk_automatic_retry(self):
        self.setup_closed();self.run_history(apply=True)
        for c in self.b.delete_calls:
            self.assertEqual(c['generation'],c['if_generation_match']);self.assertLess(c['generation'],self.seal[0])
            self.assertEqual(c['name'],self.name);self.assertIsNone(c['retry']);self.assertEqual(c['timeout'],10)
        for c in self.b.list_calls:self.assertEqual(c['prefix'],self.name);self.assertIsNone(c['retry'])
    def test_missing_unsealed_or_active_cannot_delete(self):
        self.f.complete()
        with self.assertRaises(Exception):self.run_history(apply=True)
        self.f.close()
        with self.assertRaises(StateUnavailable):self.run_history(apply=True)
        self.assertEqual(self.b.delete_calls,[])
    def test_other_operation_or_account_never_scanned(self):
        self.setup_closed()
        for person,op in [(self.f.person,'f'*32),('e'*64,self.f.op)]:
            with self.assertRaises(Exception):self.h.advance(person,op,apply=True)
        self.assertEqual(self.b.list_calls,[]);self.assertEqual(self.b.delete_calls,[])
    def test_history_from_other_namespace_or_operation_survives(self):
        self.setup_closed();foreign='synthetic/other/result.json'
        self.b.versions[(foreign,1)]=b'foreign';self.b.live[foreign]=(1,b'foreign')
        self.run_history(apply=True)
        self.assertEqual(self.b.versions[(foreign,1)],b'foreign');self.assertEqual(self.b.live[foreign],(1,b'foreign'))
    def test_namespace_mismatch_or_shared_audit_rejected(self):
        for p in (dict(self.policy,bucket='another-bucket'),dict(self.policy,prefix='other'),dict(self.policy,v=True)):
            with self.assertRaises(ValueError):ServiceResultHistory(disposition=self.f.d,store=self.jobs,audit_store=self.audit,policy=p)
        with self.assertRaises(ValueError):ServiceResultHistory(disposition=self.f.d,store=self.jobs,audit_store=self.f.ops.store,policy=self.policy)
    def test_seal_audit_missing_stops_before_list_or_delete(self):
        self.setup_closed();self.f.d.audit=self.f.store('missing-original')
        with self.assertRaises(StateUnavailable):self.run_history(apply=True)
        self.assertEqual(self.b.list_calls,[]);self.assertEqual(self.b.delete_calls,[])
    def test_incorrect_closure_seal_not_accepted(self):
        self.setup_closed();rev,v=self.f.row();v['disposition']['closure_operation']='a'*32
        self.f.ops.store.compare_exchange(self.f.ops.key(self.f.subject,self.f.op),rev,v)
        with self.assertRaises(StateUnavailable):self.run_history(apply=True)
        self.assertEqual(self.b.delete_calls,[])
    def test_lost_delete_reply_resolves_same_generation(self):
        self.setup_closed();self.b.lost_reply=True
        self.assertEqual(self.run_history(apply=True)['status'],'listed_history_absent_at_check')
        self.assertEqual(self.b.live[self.name],self.seal)
    def test_delete_not_committed_remains_pending_and_resumes(self):
        self.setup_closed();self.b.refuse_delete=True
        with self.assertRaises(StateUnavailable):self.run_history(apply=True)
        self.assertNotEqual(len(self.b.versions),1);self.b.refuse_delete=False
        self.assertEqual(self.run_history(apply=True)['status'],'listed_history_absent_at_check')
    def test_audit_unavailable_prevents_deletes(self):
        self.setup_closed()
        with patch.object(self.audit,'compare_exchange',side_effect=StateUnavailable('offline')):
            with self.assertRaises(StateUnavailable):self.run_history(apply=True)
        self.assertEqual(self.b.delete_calls,[])
    def test_lost_outcome_audit_can_resume_without_destroying_seal(self):
        self.setup_closed();real=self.audit.compare_exchange
        def fail(k,rev,value):
            if 'active_generation_absent' in value:raise StateUnavailable('outcome write failed')
            return real(k,rev,value)
        with patch.object(self.audit,'compare_exchange',side_effect=fail):
            with self.assertRaises(StateUnavailable):self.run_history(apply=True)
        bound=self.h._bound(self.f.person,self.f.op);batch=self.h._job(bound)[1]
        self.assertFalse(batch['complete']);self.assertTrue(batch['items'])
        self.h=self.worker()
        self.assertEqual(self.run_history(apply=True)['status'],'listed_history_absent_at_check')
        self.assertTrue(self.h._job(bound)[1]['complete'])
        for item in batch['items']:self.assertIsNotNone(self.h._outcome(bound,item))
        self.assertEqual(self.b.live[self.name],self.seal)
    def test_repeated_idle_observation_does_not_grow_audit(self):
        self.setup_closed();self.run_history(apply=True);self.run_history(apply=True);before=rows(self.audit)
        self.run_history(apply=True);self.assertEqual(before,rows(self.audit))
    def test_soft_deleted_generations_not_claimed_erased(self):
        self.setup_closed();self.b._properties['softDeletePolicy']={'retentionDurationSeconds':'604800'}
        r=self.run_history(apply=True);self.assertEqual(r['status'],'soft_history_pending')
        self.assertTrue(r['active_history_absent_at_check']);self.assertGreater(r['soft_versions_observed'],0)
        self.assertFalse(r['account_deleted']);self.assertEqual(self.b.live[self.name],self.seal)
        self.b.soft.clear() # synthetic expiry; worker never deletes soft versions
        self.assertEqual(self.run_history(apply=True)['status'],'listed_history_absent_at_check')
    def test_disabled_soft_policy_does_not_prove_old_soft_history_absent(self):
        self.setup_closed();self.b.soft_unavailable=True;r=self.run_history(apply=True)
        self.assertEqual(r['status'],'soft_history_unverified');self.assertFalse(r['soft_inventory_available'])
        self.assertFalse(r['account_deleted']);self.assertTrue(r['active_history_absent_at_check'])
    def test_retention_or_hold_stops_before_delete(self):
        self.setup_closed()
        for p in ({'retentionPolicy':{'isLocked':True}},{'defaultEventBasedHold':True}):
            self.b._properties=p
            with self.assertRaises(StateUnavailable):self.run_history(apply=True)
        self.assertEqual(self.b.delete_calls,[])
    def test_object_hold_stops_before_delete(self):
        self.setup_closed()
        def held(items):
            if items:items[0]._properties['temporaryHold']=True
            return items
        self.b.mutate=held
        with self.assertRaises(StateUnavailable):self.run_history(apply=True)
        self.assertEqual(self.b.delete_calls,[])
    def test_bad_metadata_or_extra_named_object_fail_closed(self):
        self.setup_closed()
        for attr,value in [('name','another/object'),('crc32c','invalid'),('generation',True),('size',20000)]:
            def bad(items):
                if items:setattr(items[0],attr,value)
                return items
            self.b.mutate=bad
            with self.assertRaises(StateUnavailable):self.run_history(apply=True)
        self.assertEqual(self.b.delete_calls,[])
    def test_short_page_with_continuation_not_complete(self):
        self.setup_closed();self.b.page_cap=1
        r=self.run_history(apply=True,limit=1);self.assertEqual(r['status'],'active_history_pending')
        self.assertLessEqual(len(self.b.delete_calls),1)
        for _ in range(10):r=self.run_history(apply=True,limit=1)
        self.assertEqual(r['status'],'listed_history_absent_at_check')
    def test_empty_page_with_continuation_cannot_claim_absence(self):
        self.setup_closed();self.b.force_more=True;self.b.mutate=lambda _:[]
        r=self.run_history(apply=True);self.assertEqual(r['status'],'active_history_pending');self.assertEqual(self.b.delete_calls,[])
    def test_complete_page_omitting_seal_is_invalid(self):
        self.setup_closed();self.b.mutate=lambda _:[]
        with self.assertRaises(StateUnavailable):self.run_history(apply=True)
        self.assertEqual(self.b.delete_calls,[])
    def test_later_out_of_contract_writer_prevents_success(self):
        self.setup_closed();old=self.f.row()[1]
        def write(_):
            self.b.delete_hook=lambda _:None
            self.f.ops.store.compare_exchange(self.f.ops.key(self.f.subject,self.f.op),self.seal[0],old)
        self.b.delete_hook=write
        with self.assertRaises(StateUnavailable):self.run_history(apply=True)
        self.assertEqual(len(self.b.delete_calls),1)
    def test_limit_and_apply_type_are_strict(self):
        self.setup_closed()
        for kw in ({'limit':0},{'limit':51},{'limit':True},{'apply':'yes'}):
            with self.assertRaises(ValueError):self.run_history(**kw)
        self.assertEqual(self.b.delete_calls,[])
    def test_frozen_catalog_page_bounds_and_pending_revisit(self):
        self.setup_closed();r=self.h.page(self.f.person,slots=1,versions=1,apply=True)
        self.assertIsNone(r['next_cursor']);self.assertEqual(r['pending_operations'],[self.f.op])
        self.assertFalse(r['account_deleted']);self.assertEqual(len(r['results']),1)
        for slots in (0,6,True):
            with self.assertRaises(ValueError):self.h.page(self.f.person,slots=slots,apply=True)
    def test_global_clock_or_prior_check_cannot_skip_fresh_listing(self):
        self.setup_closed();self.run_history(apply=True);count=len(self.b.list_calls)
        self.run_history(apply=True);self.assertGreater(len(self.b.list_calls),count)
    def test_permanent_seal_still_rejects_late_create_or_update(self):
        self.setup_closed();self.run_history(apply=True)
        k=self.f.ops.key(self.f.subject,self.f.op)
        self.assertFalse(self.f.ops.store.compare_exchange(k,0,{'late':'create'}))
        self.assertFalse(self.f.ops.store.compare_exchange(k,self.seal[0]-1,{'late':'update'}))
        self.assertEqual(self.b.live[self.name],self.seal)
    def test_checkpoint_unavailable_prevents_all_deletes(self):
        self.setup_closed()
        with patch.object(self.jobs,'compare_exchange',side_effect=StateUnavailable('offline')):
            with self.assertRaises(StateUnavailable):self.run_history(apply=True)
        self.assertEqual(self.b.delete_calls,[])
    def test_lost_batch_checkpoint_reply_resolves_exact_state(self):
        self.setup_closed();real=self.jobs.compare_exchange
        def lost(*args):real(*args);raise StateUnavailable('committed reply lost')
        with patch.object(self.jobs,'compare_exchange',side_effect=lost):
            self.assertEqual(self.run_history(apply=True)['status'],'listed_history_absent_at_check')
        self.assertEqual(len(self.b.versions),1)
    def test_changed_limit_resumes_entire_prior_batch_over_multiple_calls(self):
        self.setup_closed();self.b.refuse_delete=True
        with self.assertRaises(StateUnavailable):self.run_history(apply=True)
        bound=self.h._bound(self.f.person,self.f.op);batch=self.h._job(bound)[1];self.b.refuse_delete=False
        self.assertGreater(len(batch['items']),1)
        while not self.h._job(bound)[1]['complete']:
            before=len(self.b.delete_calls);self.run_history(apply=True,limit=1)
            self.assertLessEqual(len(self.b.delete_calls)-before,1)
        for item in batch['items']:self.assertIsNotNone(self.h._outcome(bound,item))
    def test_missing_batch_evidence_prevents_resume(self):
        self.setup_closed();self.b.refuse_delete=True
        with self.assertRaises(StateUnavailable):self.run_history(apply=True)
        self.b.refuse_delete=False;self.h.audit=self.f.store('missing-history-evidence');n=len(self.b.delete_calls)
        with self.assertRaises(StateUnavailable):self.run_history(apply=True)
        self.assertEqual(len(self.b.delete_calls),n)
    def test_corrupt_batch_is_not_reset(self):
        self.setup_closed();self.b.refuse_delete=True
        with self.assertRaises(StateUnavailable):self.run_history(apply=True)
        bound=self.h._bound(self.f.person,self.f.op);k=key('service-history-job',digest(bound));rev,j=self.jobs.load(k)
        self.jobs.compare_exchange(k,rev,dict(j,sequence=True));n=len(self.b.delete_calls)
        with self.assertRaises(StateUnavailable):self.run_history(apply=True)
        self.assertEqual(len(self.b.delete_calls),n)
    def test_two_workers_preserve_generation_and_all_outcomes(self):
        self.setup_closed()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:self.worker().advance(self.f.person,self.f.op,apply=True),range(2)))
        self.assertTrue(all(r['active_history_absent_at_check'] for r in results))
        self.assertEqual(self.b.live[self.name],self.seal);self.assertEqual(self.f.calls,1)
        bound=self.h._bound(self.f.person,self.f.op);self.assertTrue(self.h._job(bound)[1]['complete'])
    def test_more_than_fifty_versions_uses_bounded_resumable_batches(self):
        self.f.complete();k=self.f.ops.key(self.f.subject,self.f.op)
        for _ in range(65):
            rev,v=self.f.row();self.f.ops.store.compare_exchange(k,rev,v)
        self.f.close();self.f.advance();self.name=self.f.ops.store._name(k);self.seal=self.b.live[self.name]
        old_count=len(self.b.versions)-1;self.assertGreater(old_count,50)
        r=self.run_history(apply=True,limit=50);self.assertEqual(r['status'],'active_history_pending')
        self.assertEqual(len(self.b.delete_calls),50);self.h=self.worker()
        self.assertEqual(self.run_history(apply=True,limit=50)['status'],'listed_history_absent_at_check')
        self.assertEqual(len(self.b.delete_calls),old_count);self.assertEqual(len(self.b.versions),1)
    def test_completed_outcome_tamper_is_not_silently_accepted(self):
        self.setup_closed();self.run_history(apply=True);bound=self.h._bound(self.f.person,self.f.op)
        item=self.h._job(bound)[1]['items'][0];sid=digest(dict(v=1,bound=bound,item=item))
        k=key('service-history-outcome',sid);rev,v=self.audit.load(k)
        self.audit.compare_exchange(k,rev,dict(v,active_generation_absent=False));n=len(self.b.delete_calls)
        with self.assertRaises(StateUnavailable):self.run_history(apply=True)
        self.assertEqual(len(self.b.delete_calls),n)
    def test_other_account_live_history_is_preserved(self):
        self.f.complete();self.f.f.login('other','apple-human-2');other=self.f.f.person
        subject='apple-'+other;self.f.ledger.initialize_day(subject,self.f.f.f.now()//86400,
            baseline_successes=0,baseline_attempts=0,manifest_sha256='b'*64)
        self.f.complete('2'*32,'other');name=self.f.ops.store._name(self.f.ops.key(subject,'2'*32))
        original={k:v for k,v in self.b.versions.items() if k[0]==name}
        self.f.close();self.f.advance();self.name=self.f.ops.store._name(self.f.ops.key(self.f.subject,self.f.op))
        self.seal=self.b.live[self.name];self.run_history(apply=True)
        self.assertEqual(original,{k:v for k,v in self.b.versions.items() if k[0]==name})
    def test_unknown_bucket_policy_fails_closed(self):
        self.setup_closed()
        for policy in ({'versioning':{'enabled':'yes'}},{'softDeletePolicy':{'retentionDurationSeconds':True}}):
            self.b._properties=policy
            with self.assertRaises(StateUnavailable):self.run_history(apply=True)
        self.assertEqual(self.b.delete_calls,[])

    def test_probe_current_history_and_soft_history_are_separate(self):
        active=run();soft=run(soft=True)
        for r in (active,soft):
            self.assertGreater(r['old_mask_versions_before'],0);self.assertEqual(r['old_active_versions_after'],0)
            self.assertEqual(r['model_calls'],1);self.assertTrue(r['quota_identity_unchanged'])
            self.assertTrue(r['current_seal_preserved']);self.assertFalse(r['account_deleted'])
        self.assertEqual(active['result']['status'],'soft_history_unverified')
        self.assertEqual(soft['result']['status'],'soft_history_pending');self.assertGreater(soft['soft_versions_after'],0)


class Wire:
    """Pinned Google SDK with an in-memory HTTP transport, no socket or ADC."""
    is_mtls=False
    def __init__(self,replies):self.replies=list(replies);self.calls=[]
    def request(self,method,url,**kwargs):
        self.calls.append(dict(method=method,url=url,kwargs=kwargs))
        if not self.replies:raise AssertionError('unexpected SDK request')
        code,body=self.replies.pop(0);r=requests.Response();r.status_code=code;r.url=url
        r._content=json.dumps(body).encode();r.headers={'content-type':'application/json'}
        r.request=requests.Request(method,url).prepare();return r


class SDKContractTests(unittest.TestCase):
    def setUp(self):
        # Exclude optional OpenTelemetry metadata prefetch from the wire test;
        # the production executor must pin its telemetry configuration too.
        p=patch.dict(os.environ,{'DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA':'true'})
        p.start();self.addCleanup(p.stop)
    def bucket(self,replies):
        self.wire=Wire(replies)
        return storage.Client(project='synthetic',credentials=AnonymousCredentials(),_http=self.wire).bucket('synthetic-lite-history')
    def history(self,replies):
        # Isolate provider wire contract; composition/authorization is exercised
        # above with real stores and lifecycle, not mocked by these wire tests.
        h=object.__new__(ServiceResultHistory);h.bucket=self.bucket(replies);return h
    def test_active_list_is_exact_prefix_versions_bounded_and_continuation_aware(self):
        h=self.history([(200,{'items':[dict(name='p/a.json',generation='1',size='2',crc32c='AAAAAA==')],
                             'nextPageToken':'next'})])
        r=h._list(dict(name='p/a.json',seal_generation=2),3)
        q=parse_qs(urlsplit(self.wire.calls[0]['url']).query)
        self.assertEqual(q['prefix'],['p/a.json']);self.assertEqual(q['versions'],['True']);self.assertEqual(q['maxResults'],['4'])
        self.assertTrue(r['more']);self.assertEqual(len(self.wire.calls),1)
    def test_soft_list_never_combines_versions(self):
        h=self.history([(200,{})]);h._list(dict(name='p/a.json',seal_generation=2),3,soft=True)
        q=parse_qs(urlsplit(self.wire.calls[0]['url']).query)
        self.assertEqual(q['softDeleted'],['True']);self.assertNotIn('versions',q)
    def test_soft_policy_400_is_unknown_not_absence(self):
        h=self.history([(400,{'error':{'message':'soft policy disabled'}})])
        self.assertTrue(h._list(dict(name='p/a.json',seal_generation=2),3,soft=True)['unavailable'])
    def test_list_503_is_single_attempt_not_hidden_retry(self):
        h=self.history([(503,{'error':{'message':'unavailable'}})])
        with self.assertRaises(StateUnavailable):h._list(dict(name='p/a.json',seal_generation=2),3)
        self.assertEqual(len(self.wire.calls),1)
    def test_exact_version_delete_and_readback_query(self):
        b=self.bucket([(204,{}),(404,{'error':{'message':'absent'}})]);blob=b.blob('p/a.json',generation=123)
        blob.delete(if_generation_match=123,timeout=10,retry=None)
        with self.assertRaises(NotFound):blob.reload(if_generation_match=123,timeout=10,retry=None)
        self.assertEqual([c['method'] for c in self.wire.calls],['DELETE','GET'])
        for c in self.wire.calls:
            q=parse_qs(urlsplit(c['url']).query)
            self.assertEqual(q['generation'],['123']);self.assertEqual(q['ifGenerationMatch'],['123'])
            self.assertEqual(c['kwargs']['timeout'],10)
    def test_bucket_policy_is_read_from_sdk_response(self):
        h=self.history([(200,{'versioning':{'enabled':True},'softDeletePolicy':{'retentionDurationSeconds':'604800'}})])
        self.assertEqual(h._bucket_policy(),dict(soft_delete_seconds=604800,versioning_enabled=True))


if __name__=='__main__':unittest.main()
