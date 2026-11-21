"""Provider-shaped control-plane metadata; no credentials or network."""
import copy
import unittest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from lite_bootstrap_control_plane import ControlPlaneInventory, ObservationUnavailable, EXECUTION_FIELDS
from lite_subscription_executor import SignedEvidence, EvidenceUnavailable
from lite_usage_ledger import digest


class ControlPlaneTests(unittest.TestCase):
    def setUp(self):
        self.plan=dict(schema='lite-control-plane-coverage/1',project='synthetic-project',region='asia-east1',
                       services=['woundai-lite-native-sandbox'],jobs=['woundai-lite-bootstrap-owner'])
        self.base='projects/synthetic-project/locations/asia-east1/'
        self.service=self.base+'services/'+self.plan['services'][0];self.job=self.base+'jobs/'+self.plan['jobs'][0]
        self.calls=[];self.responses={}
        for name in (self.service,self.job):
            self.responses['/v2/'+name]=(200,dict(name=name,uid='uid-'+name.rsplit('/',1)[-1],
                generation='1',observedGeneration='1',reconciling=False,etag='first'))
        self.responses['/v2/'+self.job+'/executions']=(200,dict(executions=[self.complete()]))
    def complete(self,ident='one',**updates):
        return dict(dict(name=self.job+'/executions/'+ident,uid='uid-'+ident,job=self.job,
            generation='1',observedGeneration='1',taskCount=1,succeededCount=1,
            completionTime='2026-11-10T01:00:00Z'),**updates)
    def get(self,path,params):
        self.calls.append((path,copy.deepcopy(params)))
        value=self.responses[path]
        if callable(value):return value(path,params)
        return copy.deepcopy(value)
    def inspect(self,**kw):
        return ControlPlaneInventory(get=self.get,**kw).inspect(self.plan,expected_coverage=digest(self.plan))
    def job_report(self,**kw):return self.inspect(**kw)['resources'][1]
    def test_complete_list_is_observation_not_automatic_fence(self):
        r=self.inspect();j=r['resources'][1]
        self.assertTrue(r['snapshot_complete']);self.assertEqual((j['visible_executions'],j['terminal_confirmed']),(1,1))
        self.assertEqual(r['requests'],6);self.assertFalse(r['writer_fence_verified']);self.assertFalse(r['authorizes_bootstrap'])
        self.assertFalse(r['effective_iam_verified']);self.assertEqual(r['signatures_created'],0)
        self.assertTrue(all(x['inflight'] is None and x['route_closed'] is None for x in r['resources']))
    def test_queued_zero_running_is_not_terminal(self):
        self.responses['/v2/'+self.job+'/executions']=(200,{'executions':[self.complete(succeededCount=0,completionTime=None)]})
        r=self.job_report();self.assertEqual(r['not_confirmed_terminal'],1)
    def test_running_task_not_terminal(self):
        self.responses['/v2/'+self.job+'/executions']=(200,{'executions':[self.complete(succeededCount=0,runningCount=1,completionTime=None)]})
        self.assertEqual(self.job_report()['terminal_confirmed'],0)
    def test_completed_counts_without_completion_or_observed_generation_not_terminal(self):
        for update in ({'completionTime':None},{'observedGeneration':'2'},{'reconciling':True}):
            with self.subTest(update=update):
                self.responses['/v2/'+self.job+'/executions']=(200,{'executions':[self.complete(**update)]})
                self.assertEqual(self.job_report()['not_confirmed_terminal'],1)
    def test_failed_and_cancelled_terminal_tasks_are_not_success(self):
        for field in ('failedCount','cancelledCount'):
            with self.subTest(field=field):
                self.responses['/v2/'+self.job+'/executions']=(200,{'executions':[self.complete(succeededCount=0,**{field:1})]})
                r=self.job_report();self.assertEqual(r['terminal_confirmed'],1)
                self.assertIsNone(r['inflight'])
    def test_absent_is_unknown_not_zero_or_never_used(self):
        for name in (self.service,self.job):self.responses['/v2/'+name]=(404,{'message':'unretained private error'})
        r=self.inspect();self.assertFalse(r['snapshot_complete'])
        self.assertTrue(all(x['status']=='absent' and x['inflight'] is None and not x['history_never_used'] for x in r['resources']))
        self.assertNotIn('private error',str(r));self.assertEqual(len(self.calls),2)
    def test_403_and_rate_limits_do_not_become_absence(self):
        for status in (401,403,429,500):
            with self.subTest(status=status):
                self.responses['/v2/'+self.job]=(status,{'message':'private error'})
                self.assertEqual(self.job_report()['status'],'unavailable')
    def test_execution_list_404_or_403_not_empty(self):
        for status in (404,403,429):
            with self.subTest(status=status):
                self.responses['/v2/'+self.job+'/executions']=(status,{})
                r=self.job_report();self.assertFalse(r['snapshot_complete']);self.assertNotIn('visible_executions',r)
    def test_page_two_running_execution_not_omitted(self):
        def pages(path,p):
            if 'pageToken' not in p:return 200,dict(executions=[self.complete()],nextPageToken='second')
            self.assertEqual(p['pageToken'],'second')
            return 200,dict(executions=[self.complete('two',succeededCount=0,runningCount=1,completionTime=None)])
        self.responses['/v2/'+self.job+'/executions']=pages
        r=self.job_report();self.assertEqual((r['visible_executions'],r['not_confirmed_terminal']),(2,1))
        q=[p for n,p in self.calls if n.endswith('/executions')]
        self.assertTrue(all(p['showDeleted']=='true' and p['pageSize']=='100' and p['fields']=='executions('+EXECUTION_FIELDS+'),nextPageToken' for p in q))
    def test_new_execution_between_passes_rejects_snapshot(self):
        seen=[]
        def changes(path,p):
            seen.append(path);return 200,dict(executions=[self.complete()]+([self.complete('new')] if len(seen)>1 else []))
        self.responses['/v2/'+self.job+'/executions']=changes
        r=self.job_report();self.assertFalse(r['snapshot_complete']);self.assertIn('changed',r['reason'])
    def test_resource_replaced_or_changed_between_reads(self):
        first=copy.deepcopy(self.responses['/v2/'+self.job][1]);seen=[]
        def changes(path,p):
            seen.append(path);return 200,dict(first,etag='second' if len(seen)>1 else 'first')
        self.responses['/v2/'+self.job]=changes
        self.assertFalse(self.job_report()['snapshot_complete'])
    def test_second_read_failure_does_not_confirm_first(self):
        first=self.responses['/v2/'+self.service];seen=[]
        def changes(path,p):seen.append(path);return first if len(seen)==1 else (403,{})
        self.responses['/v2/'+self.service]=changes
        self.assertFalse(self.inspect()['resources'][0]['snapshot_complete'])
    def test_page_repeats_and_duplicate_execution_rejected(self):
        for response in (dict(executions=[self.complete()],nextPageToken='again'),dict(executions=[self.complete(),self.complete()])):
            with self.subTest(response=response):
                self.responses['/v2/'+self.job+'/executions']=(200,response)
                self.assertFalse(self.job_report()['snapshot_complete'])
    def test_parent_scope_and_execution_identity_rejected(self):
        for update in ({'job':self.job+'-other'},{'name':self.job+'-other/executions/one'},{'uid':''}):
            with self.subTest(update=update):
                self.responses['/v2/'+self.job+'/executions']=(200,{'executions':[self.complete(**update)]})
                self.assertFalse(self.job_report()['snapshot_complete'])
    def test_malformed_and_boolean_counters_rejected(self):
        for update in ({'taskCount':True},{'runningCount':True},{'succeededCount':-1},{'failedCount':1},
                       {'generation':1},{'reconciling':'false'},{'completionTime':'not-a-time'},
                       {'completionTime':'2026-11-10T01:00:00'}):
            with self.subTest(update=update):
                self.responses['/v2/'+self.job+'/executions']=(200,{'executions':[self.complete(**update)]})
                self.assertFalse(self.job_report()['snapshot_complete'])
    def test_deleted_terminal_execution_included(self):
        self.responses['/v2/'+self.job+'/executions']=(200,{'executions':[self.complete(deleteTime='2026-11-10T02:00:00Z')]})
        self.assertEqual(self.job_report()['visible_executions'],1)
    def test_empty_visible_history_does_not_prove_never_used(self):
        self.responses['/v2/'+self.job+'/executions']=(200,{})
        r=self.job_report();self.assertEqual(r['visible_executions'],0);self.assertFalse(r['history_never_used']);self.assertIsNone(r['inflight'])
    def test_payloads_and_environment_values_not_returned(self):
        self.responses['/v2/'+self.service][1]['template']={'containers':[{'env':[{'value':'synthetic-private-value'}]}]}
        self.assertNotIn('synthetic-private-value',str(self.inspect()))
        self.assertTrue(all('template' not in p['fields'] and 'env' not in p['fields'] for _,p in self.calls))
    def test_wrong_pin_and_broadened_coverage_rejected_before_calls(self):
        with self.assertRaises(ValueError):ControlPlaneInventory(get=self.get).inspect(self.plan,expected_coverage='a'*64)
        for change in ({'services':['mmh-production']},{'jobs':[]},{'services':self.plan['services']*2},{'region':'../other'},{'project':'x/../../y'}):
            c=dict(self.plan,**change)
            with self.subTest(change=change),self.assertRaises(ValueError):ControlPlaneInventory(get=self.get).inspect(c,expected_coverage=digest(c))
        self.assertEqual(self.calls,[])
    def test_budget_and_page_limits_never_return_partial_as_complete(self):
        self.assertFalse(self.inspect(max_requests=2)['snapshot_complete']);self.assertEqual(len(self.calls),2)
        self.responses['/v2/'+self.job+'/executions']=(200,{'executions':[self.complete()], 'nextPageToken':'more'})
        self.assertFalse(self.job_report(max_pages=1)['snapshot_complete'])
    def test_time_limit_exception_and_oversize_are_unknown(self):
        def slow(path,p):raise TimeoutError('private token must not appear')
        self.responses['/v2/'+self.job]=slow
        r=self.job_report();self.assertFalse(r['snapshot_complete']);self.assertNotIn('private token',str(r))
        self.responses['/v2/'+self.job]=(200,{'oversize':'x'*(1024*1024+1)})
        self.assertFalse(self.job_report()['snapshot_complete'])
        ticks=iter((0,0,301,301,301))
        self.assertFalse(self.inspect(clock=lambda:next(ticks))['snapshot_complete'])
    def test_reader_cannot_be_reused(self):
        reader=ControlPlaneInventory(get=self.get);reader.inspect(self.plan,expected_coverage=digest(self.plan))
        before=len(self.calls)
        with self.assertRaises(ValueError):reader.inspect(self.plan,expected_coverage=digest(self.plan))
        self.assertEqual(before,len(self.calls))
    def test_report_is_rejected_by_actual_bootstrap_signature_verifier(self):
        approval=Ed25519PrivateKey.generate().public_key().public_bytes_raw()
        observer=Ed25519PrivateKey.generate().public_key().public_bytes_raw()
        evidence=SignedEvidence({'operator':approval},{'observer':observer})
        with self.assertRaises(EvidenceUnavailable):evidence.verify(self.inspect(),'observation')
    def test_invalid_service_flags_and_nonfinite_json_stay_unknown(self):
        original=copy.deepcopy(self.responses['/v2/'+self.service][1])
        for update in ({'invokerIamDisabled':'false'},{'defaultUriDisabled':0},{'extra':float('nan')}):
            with self.subTest(update=update):
                self.responses['/v2/'+self.service]=(200,dict(original,**update))
                self.assertFalse(self.inspect()['resources'][0]['snapshot_complete'])


if __name__=='__main__':unittest.main()
