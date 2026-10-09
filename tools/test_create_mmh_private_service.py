import copy
import json
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

import create_mmh_private_service as m
from test_check_mmh_runtime_preflight import plan, FakeCloud, COMMIT, BUILD


class GrantsCloud(FakeCloud):
    def __init__(self):
        super().__init__()
        self.bad = lambda a, r: r

    def __call__(self, a):
        if a[:3] == ['iam', 'roles', 'describe']:
            g = next(x for x in plan()['spec']['planned_bucket_grants'] if x['role_id'] == a[3])
            r = {'name': 'projects/'+m.PROJECT+'/roles/'+a[3], 'stage': 'GA', 'includedPermissions':g['permissions']}
        elif a[:3] == ['storage', 'buckets', 'get-iam-policy']:
            g = next(x for x in plan()['spec']['planned_bucket_grants'] if 'gs://'+x['bucket'] == a[3])
            r = {'bindings':[{'role':k,'members':sorted(v)} for k,v in m.TARGET.items()] +
                 [{'role':'projects/'+m.PROJECT+'/roles/'+g['role_id'],'members':[m.MEMBER]}]}
        elif a[:2] == ['secrets', 'get-iam-policy']:
            r = {'bindings':[{'role':'roles/secretmanager.secretAccessor','members':[m.MEMBER]}]}
        elif a[:3] == ['storage', 'buckets', 'list']:
            r = [{'name':b} for b in (*m.BUCKETS, 'other-bucket')]
        elif a[:2] == ['secrets', 'list']:
            r = [{'name':'projects/'+m.NUMBER+'/secrets/'+s} for s in (*m.SECRET_NAMES.values(),'other-secret')]
        elif a[:3] == ['iam', 'service-accounts', 'list']:
            r = [{'email':m.SA},{'email':'other@other.iam.gserviceaccount.com'}]
        else:
            r = super().__call__(a)
        return self.bad(a, copy.deepcopy(r))


class CreatorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.journal = Path(self.tmp.name)/'attempt'
        self.cloud = GrantsCloud()
        self.posts = []

    def api(self, method, path, body=None):
        if method == 'POST' and body.get('name'):
            raise ValueError('service.name must be empty on CreateServiceRequest')
        self.posts.append((method,path,body))
        return {'name':m.PARENT+'/operations/one'}

    def execute(self, **kw):
        options = dict(run=self.cloud, main=lambda:COMMIT,
                       effective=lambda *args:True, api=self.api, empty=lambda:True)
        options.update(kw)
        return m.execute(plan(), BUILD, self.journal, **options)

    def test_payload_keeps_all_three_buckets_and_four_pinned_secrets(self):
        p=m.payload(plan()); env=p['template']['containers'][0]['env']
        self.assertNotIn('name',p)
        self.assertFalse(p['invokerIamDisabled'])
        self.assertEqual(p['template']['containers'][0]['ports'], [{'name':'http1','containerPort':8080}])
        self.assertEqual(p['template']['serviceAccount'],m.SA)
        self.assertEqual(len([e for e in env if 'valueSource' in e]),4)
        refs=[e['valueSource']['secretKeyRef'] for e in env if 'valueSource' in e]
        self.assertTrue(all(r['version']=='1' and '/'+m.NUMBER+'/' in r['secret'] for r in refs))
        values=[e.get('value') for e in env]
        for bucket in m.BUCKETS:self.assertIn(bucket,values)

    def test_tampered_rehashed_plan_rejected_before_journal_or_api(self):
        from plan_mmh_runtime import canonical
        import hashlib
        p=plan();p['spec']['service']='woundai-backend';p['spec_sha256']=hashlib.sha256(canonical(p['spec'])).hexdigest()
        with self.assertRaises(ValueError):m.execute(p,BUILD,self.journal,api=self.api)
        self.assertFalse(self.journal.exists());self.assertEqual(self.posts,[])

    def test_default_check_never_creates(self):
        r=self.execute();self.assertEqual(r['state'],'preflight_passed_no_create');self.assertFalse(r['deployed'])
        self.assertEqual(self.posts,[])

    def test_confirmed_create_posts_once_and_is_not_acceptance(self):
        r=self.execute(confirm_sha=plan()['spec_sha256'])
        self.assertEqual(len(self.posts),1);self.assertEqual(self.posts[0][:2],('POST',m.PARENT+'/services?serviceId='+m.SERVICE))
        self.assertNotIn('name',self.posts[0][2])
        self.assertFalse(r['deployed']);self.assertEqual(r['state'],'submitted_not_accepted')
        with self.assertRaises(FileExistsError):self.execute(confirm_sha=plan()['spec_sha256'])
        self.assertEqual(len(self.posts),1)

    def test_wrong_confirmation_no_cloud_or_journal(self):
        with self.assertRaises(ValueError):self.execute(confirm_sha='bad')
        self.assertFalse(self.journal.exists());self.assertEqual(self.cloud.calls,[])

    def test_missing_metadata_or_unmerged_source_blocks_before_iam_and_post(self):
        with self.assertRaises(ValueError):self.execute(main=lambda:'d'*40, effective=lambda *a:self.fail('should stop first'))
        self.assertEqual(self.posts,[])
        self.assertEqual(json.loads((self.journal/'result.json').read_text())['state'],'blocked')

    def test_each_exact_grant_gate_rejects(self):
        mutations=[
            lambda a,r:dict(r,parent={'id':'org'}) if a[:2]==['projects','describe'] else r,
            lambda a,r:dict(r,includedPermissions=['storage.objects.get']) if a[:3]==['iam','roles','describe'] else r,
            lambda a,r:dict(r,deleted=True) if a[:3]==['iam','roles','describe'] else r,
            lambda a,r:dict(r,stage='DISABLED') if a[:3]==['iam','roles','describe'] else r,
            lambda a,r:{'bindings':[]} if a[:3]==['storage','buckets','get-iam-policy'] else r,
            lambda a,r:{'bindings':[]} if a[:2]==['secrets','get-iam-policy'] else r,
        ]
        for change in mutations:
            c=GrantsCloud();c.bad=change
            with self.subTest(change=mutations.index(change)),self.assertRaises(ValueError):m.inventory_and_grants(plan(),c)

    def test_groups_domains_public_federated_unknown_and_case_variant_runtime_rejected(self):
        for member in ['group:staff@example.com','domain:example.com','allUsers','allAuthenticatedUsers',
                       'principal://iam.googleapis.com/a','principalSet://iam.googleapis.com/a','deleted:serviceAccount:a@b',m.MEMBER.upper(),'serviceAccount:'+m.SA.upper()]:
            p={'etag':'x','bindings':[{'role':'roles/viewer','members':[member]}]}
            with self.subTest(member=member),self.assertRaises(ValueError):m.check_project(p)
        for binding in [{'role':'roles/viewer','members':['user:a@b'],'condition':{'expression':'true'}},
                        {'role':'roles/viewer','members':[]}]:
            with self.assertRaises(ValueError):m.check_project({'etag':'x','bindings':[binding]})

    def test_conditional_extra_and_duplicate_resource_grants_rejected(self):
        wanted={'roles/secretmanager.secretAccessor':{m.MEMBER}}
        valid={'role':'roles/secretmanager.secretAccessor','members':[m.MEMBER]}
        for bs in [[dict(valid,condition={'expression':'true'})],[valid,valid],
                   [dict(valid,members=[m.MEMBER,'allUsers'])]]:
            with self.assertRaises(ValueError):m.exact_bindings({'bindings':bs},wanted)

    def test_inventory_missing_or_wrong_resource_names_rejected(self):
        for prefix,value in [(['secrets','list'],[{'name':'projects/'+m.PROJECT+'/secrets/x'}]),
                             (['storage','buckets','list'],[]),
                             (['iam','service-accounts','list'],[{'email':'bad'}])]:
            c=GrantsCloud();c.bad=lambda a,r: value if a[:len(prefix)]==prefix else r
            with self.assertRaises(ValueError):m.inventory_and_grants(plan(),c)

    def test_permissions_cover_cross_environment_read_write_and_credential_paths(self):
        rows=m.runtime_cases(plan(),m.inventory_and_grants(plan(),self.cloud))
        for row in rows:
            if row[1].endswith('other-bucket') or row[1].endswith('other-secret') or row[2] in m.IMPERSONATION:
                self.assertFalse(row[3])
        for permission in m.IMPERSONATION:
            self.assertTrue(any(r[2]==permission and 'other@other.iam.gserviceaccount.com' in r[1] for r in rows))
        for bucket in m.BUCKETS[1:]:
            self.assertIn((m.SA,'//storage.googleapis.com/projects/_/buckets/'+bucket,'storage.objects.delete',False),rows)
        self.assertTrue(any(r[2]=='secretmanager.versions.access' and r[3] for r in rows))

    def test_failed_effective_iam_or_empty_epoch_never_posts(self):
        for key in ['effective','empty']:
            self.journal=Path(self.tmp.name)/key
            with self.assertRaises(ValueError):self.execute(confirm_sha=plan()['spec_sha256'],**{key:lambda *a:False})
            self.assertEqual(self.posts,[])

    def test_metadata_change_during_effective_check_blocks(self):
        def effective(*args):
            self.cloud.edit=lambda a,r:dict(r,bindings=[{'role':'roles/editor','members':['serviceAccount:'+m.NUMBER+'-compute@developer.gserviceaccount.com']}]) if a[:2]==['projects','get-iam-policy'] else r
            return True
        with self.assertRaises(ValueError):self.execute(confirm_sha=plan()['spec_sha256'],effective=effective)
        self.assertEqual(self.posts,[])

    def test_epoch_change_before_post_blocks(self):
        values=iter([True,False])
        with self.assertRaises(ValueError):self.execute(confirm_sha=plan()['spec_sha256'],empty=lambda:next(values))
        self.assertEqual(self.posts,[])

    def test_unknown_post_result_never_retries_or_echoes_error(self):
        def fail(*args):self.posts.append(args);raise TimeoutError('sensitive content')
        with self.assertRaises(TimeoutError):self.execute(confirm_sha=plan()['spec_sha256'],api=fail)
        r=json.loads((self.journal/'result.json').read_text());self.assertEqual(r['state'],'create_outcome_unknown')
        self.assertNotIn('sensitive',json.dumps(r));self.assertEqual(len(self.posts),1)

    def ready(self):
        s=m.payload(plan());s.update(name=m.NAME,generation='1',observedGeneration='1',latestReadyRevision='r1',latestCreatedRevision='r1',terminalCondition={'state':'CONDITION_SUCCEEDED'},reconciling=False)
        # Real Cloud Run readback includes the default HTTP/1 protocol name.
        # Keep this independent of the create payload to detect API-shape regressions.
        s['template']['containers'][0]['ports']=[{'name':'http1','containerPort':8080}]
        return s

    def test_readback_rejects_wrong_or_missing_protocol_and_port(self):
        for ports in ([], [{'containerPort':8080}], [{'name':'h2c','containerPort':8080}],
                      [{'name':'http1','containerPort':8081}],
                      [{'name':'http1','containerPort':8080},{'name':'http1','containerPort':8081}]):
            service=self.ready();service['template']['containers'][0]['ports']=ports
            with self.subTest(ports=ports),self.assertRaises(ValueError):
                m.check_ready(plan(),service,{})

    def test_readback_still_requires_exact_server_assigned_name(self):
        for name in (None, '', m.PARENT+'/services/woundai-backend'):
            service=self.ready()
            if name is None:service.pop('name')
            else:service['name']=name
            with self.subTest(name=name),self.assertRaises(ValueError):
                m.check_ready(plan(),service,{})

    def test_ready_does_not_claim_gcs_or_mobile_acceptance(self):
        r=m.check_ready(plan(),self.ready(),{})
        self.assertTrue(r['private_service_ready']);self.assertFalse(r['gcs_e2e_passed']);self.assertFalse(r['mobile_released'])

    def test_readback_rejects_unready_public_wrong_image_env_scaling_or_runtime(self):
        for edit in [lambda s:s.update(invokerIamDisabled=True),lambda s:s.update(reconciling=True),
                     lambda s:s.update(observedGeneration='0'),lambda s:s.update(latestReadyRevision='old'),
                     lambda s:s['template'].update(serviceAccount='wrong'),
                     lambda s:s['template']['scaling'].update(maxInstanceCount=2),
                     lambda s:s['scaling'].update(scalingMode='MANUAL',manualInstanceCount=3),
                     lambda s:s['template']['containers'][0].update(image='wrong'),
                     lambda s:s['template']['containers'][0]['env'].append({'name':'DEBUG','value':'1'}),
                     lambda s:s.update(traffic=[])]:
            row=self.ready();edit(row)
            with self.assertRaises(ValueError):m.check_ready(plan(),row,{})
        with self.assertRaises(ValueError):m.check_ready(plan(),self.ready(),{'bindings':[{'role':'roles/run.invoker','members':['allUsers']}]})

    def test_status_invalidates_previous_green_before_failed_readback(self):
        self.journal.mkdir()
        (self.journal/'result.json').write_text(json.dumps({'spec_sha256':plan()['spec_sha256'],'creation_attempted':True,'private_service_ready':True,'deployed':True}))
        def fail(*args):raise RuntimeError('unavailable')
        with self.assertRaises(RuntimeError):m.status(plan(),self.journal,api=fail,run=self.cloud)
        r=json.loads((self.journal/'result.json').read_text());self.assertFalse(r['private_service_ready']);self.assertFalse(r['deployed'])

    def test_effective_iam_requires_echo_and_known_decisions(self):
        case=(m.SA,'//storage.googleapis.com/projects/_/buckets/other','storage.objects.get',False)
        def response():
            return {'accessTuple':{'principal':case[0],'fullResourceName':case[1],'permission':case[2]},
                    'overallAccessState':'CANNOT_ACCESS',
                    'allowPolicyExplanation':{'allowAccessState':'ALLOW_ACCESS_STATE_NOT_GRANTED'},
                    'denyPolicyExplanation':{'denyAccessState':'DENY_ACCESS_STATE_NOT_DENIED'}}
        for edit in [lambda r:None,lambda r:r.update(overallAccessState='UNKNOWN_INFO_DENIED'),
                     lambda r:r['accessTuple'].update(principal='other@example.com'),
                     lambda r:r['allowPolicyExplanation'].update(allowAccessState='UNKNOWN')]:
            row=response();edit(row)
            report=Path(self.tmp.name)/'effective.json'
            with patch.object(m,'legacy_cases',return_value=[]),patch.object(m,'runtime_cases',return_value=[case]),patch.object(m,'access_token',return_value='not-a-real-token'),patch.object(m,'request_v3',return_value=row):
                if row==response():self.assertTrue(m.live_effective(plan(),(),report))
                else:
                    with self.assertRaises(ValueError):m.live_effective(plan(),(),report)
                    self.assertFalse(json.loads(report.read_text())['complete'])

    def refresh_case(self):
        case=(m.SA,'//storage.googleapis.com/projects/_/buckets/other','storage.objects.get',False)
        response={'accessTuple':{'principal':case[0],'fullResourceName':case[1],'permission':case[2]},
                  'overallAccessState':'CANNOT_ACCESS',
                  'allowPolicyExplanation':{'allowAccessState':'ALLOW_ACCESS_STATE_NOT_GRANTED'},
                  'denyPolicyExplanation':{'denyAccessState':'DENY_ACCESS_STATE_NOT_DENIED'}}
        return case,response

    def test_expired_token_refreshes_once_for_identical_read_only_query(self):
        case,response=self.refresh_case()
        expired=urllib.error.HTTPError('https://example.invalid',401,'unauthorized',None,None)
        with patch.object(m,'request_v3',side_effect=[expired,response]) as request,patch.object(m,'access_token',return_value='fresh-token') as token:
            got,new_token=m.effective_response(case,'expired-token')
        self.assertEqual(new_token,'fresh-token');self.assertEqual(got,response)
        self.assertEqual(request.call_args_list[0].args,(*case[:3],'expired-token'))
        self.assertEqual(request.call_args_list[1].args,(*case[:3],'fresh-token'))
        token.assert_called_once_with()

    def test_repeated_401_is_not_an_unbounded_retry(self):
        case,_=self.refresh_case()
        with patch.object(m,'request_v3',side_effect=urllib.error.HTTPError('https://example.invalid',401,'unauthorized',None,None)) as request,patch.object(m,'access_token',return_value='fresh-token') as token:
            with self.assertRaises(urllib.error.HTTPError):m.effective_response(case,'expired-token')
        self.assertEqual(request.call_count,2);token.assert_called_once_with()

    def test_non_401_errors_do_not_refresh_or_retry(self):
        case,_=self.refresh_case()
        for code in (403,429,500):
            with self.subTest(code=code),patch.object(m,'request_v3',side_effect=urllib.error.HTTPError('https://example.invalid',code,'failed',None,None)) as request,patch.object(m,'access_token') as token:
                with self.assertRaises(urllib.error.HTTPError):m.effective_response(case,'old-token')
                request.assert_called_once();token.assert_not_called()

    def test_failed_credential_refresh_stops_before_second_query(self):
        case,_=self.refresh_case()
        with patch.object(m,'request_v3',side_effect=urllib.error.HTTPError('https://example.invalid',401,'unauthorized',None,None)) as request,patch.object(m,'access_token',side_effect=ValueError('credential unavailable')):
            with self.assertRaises(ValueError):m.effective_response(case,'old-token')
            request.assert_called_once()

    def test_unknown_after_refresh_does_not_complete_the_gate(self):
        case,response=self.refresh_case();response['overallAccessState']='UNKNOWN_INFO_DENIED'
        report=Path(self.tmp.name)/'refresh-unknown.json'
        with patch.object(m,'legacy_cases',return_value=[]),patch.object(m,'runtime_cases',return_value=[case]),patch.object(m,'access_token',side_effect=['old-token','fresh-token']),patch.object(m,'request_v3',side_effect=[urllib.error.HTTPError('https://example.invalid',401,'unauthorized',None,None),response]):
            with self.assertRaises(ValueError):m.live_effective(plan(),(),report)
        self.assertFalse(json.loads(report.read_text())['complete'])

    def test_full_gate_uses_refreshed_token_on_following_case(self):
        case,response=self.refresh_case();second=(*case[:2],'storage.objects.list',False)
        second_response=copy.deepcopy(response);second_response['accessTuple']['permission']=second[2]
        report=Path(self.tmp.name)/'refresh-success.json'
        with patch.object(m,'legacy_cases',return_value=[]),patch.object(m,'runtime_cases',return_value=[case,second]),patch.object(m,'access_token',side_effect=['old-token','fresh-token']) as token,patch.object(m,'request_v3',side_effect=[urllib.error.HTTPError('https://example.invalid',401,'unauthorized',None,None),response,second_response]) as request,patch.object(m.time,'sleep'):
            self.assertTrue(m.live_effective(plan(),(),report))
        self.assertEqual(request.call_args_list[-1].args,(*second[:3],'fresh-token'))
        self.assertEqual(token.call_count,2)
        self.assertTrue(json.loads(report.read_text())['complete'])

    def test_existing_service_or_disabled_secret_prevents_create(self):
        for prefix,edit in [(['run','services','list'],lambda r:[{'metadata':{'name':m.SERVICE}}]),
                            (['secrets','versions','describe'],lambda r:dict(r,state='DISABLED'))]:
            self.journal=Path(self.tmp.name)/prefix[0]
            self.cloud.edit=lambda a,r:edit(r) if a[:len(prefix)]==prefix else r
            with self.assertRaises(ValueError):self.execute(confirm_sha=plan()['spec_sha256'])
            self.assertEqual(self.posts,[])

    def test_cloud_and_rest_reject_mutations_outside_single_create(self):
        with patch.object(m.subprocess,'run') as run:
            for args in [['run','deploy','woundai-backend'],['secrets','versions','access','1'],['projects','set-iam-policy',m.PROJECT,'p']]:
                with self.assertRaises(ValueError):m.cloud(args)
            run.assert_not_called()
        with patch.object(m,'access_token') as token:
            for method,path in [('PATCH',m.NAME),('DELETE',m.NAME),('POST',m.NAME+':setIamPolicy'),('GET',m.PARENT+'/services/other')]:
                with self.assertRaises(ValueError):m.rest(method,path)
            token.assert_not_called()


if __name__=='__main__':unittest.main()
