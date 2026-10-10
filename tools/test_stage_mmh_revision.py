import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import stage_mmh_revision as m
from plan_mmh_runtime import generate, canonical
from medical_image_build import IMAGE_ROOT


def plans():
    return tuple(generate(IMAGE_ROOT+'@sha256:'+ch*64, ch*40, ch*64,
        {key:'1' for key in m.base.SECRET_NAMES}, 'mmh-unlocked-validation') for ch in ('a','b'))


def baseline(old):
    s=m.base.payload(old)
    s.update(name=m.base.NAME,uid='fixed-uid',etag='etag-one',generation='1',observedGeneration='1',
             latestReadyRevision=m.base.NAME+'/revisions/'+m.base.SERVICE+'-00001-old',
             latestCreatedRevision=m.base.NAME+'/revisions/'+m.base.SERVICE+'-00001-old',
             terminalCondition={'state':'CONDITION_SUCCEEDED'},reconciling=False,
             trafficStatuses=[{'type':'TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST',
                 'revision':m.base.SERVICE+'-00001-old','percent':100}])
    return s


def rehash(p):
    p['sha256']=hashlib.sha256(canonical(p['spec'])).hexdigest()


class StageTests(unittest.TestCase):
    def setUp(self):
        self.old,self.new=plans();self.service=baseline(self.old);self.policy={'bindings':[]}
        self.p=m.proposal(self.old,self.new,self.service,self.policy)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.journal=Path(self.tmp.name)/'attempt';self.calls=[]

    def api(self,method,path,body=None):
        self.calls.append((method,path,copy.deepcopy(body)))
        if method=='PATCH':return {'name':m.base.PARENT+'/operations/test'}
        if path.endswith(':getIamPolicy'):return copy.deepcopy(self.policy)
        if '/operations/' in path:return {'done':True}
        return copy.deepcopy(self.service)

    def execute(self,**kw):
        options=dict(api=self.api,run=lambda args:{'bindings':[],'etag':'policy'},main=lambda:'b'*40,
                     effective=lambda *args:True)
        options.update(kw)
        with patch.object(m,'metadata',return_value={'complete':True}),\
             patch.object(m.base,'inventory_and_grants',return_value=(['bucket'],['secret'],['sa'])):
            return m.stage(self.p,'synthetic-build',self.journal,**options)

    def ready(self):
        s=m.base.payload(self.new);spec=self.p['spec']
        s.update(name=m.base.NAME,uid='fixed-uid',etag='etag-two',generation='2',observedGeneration='2',
                 latestReadyRevision=m.base.NAME+'/revisions/'+spec['candidate_revision'],
                 latestCreatedRevision=m.base.NAME+'/revisions/'+spec['candidate_revision'],
                 terminalCondition={'state':'CONDITION_SUCCEEDED'},reconciling=False,
                 traffic=copy.deepcopy(spec['request']['traffic']),
                 trafficStatuses=copy.deepcopy(spec['request']['traffic']))
        return s

    def test_stage_pins_old_revision_at_100_and_new_at_zero(self):
        req=self.p['spec']['request']
        self.assertEqual(req['traffic'][0],{'type':m.TYPE,'revision':m.base.SERVICE+'-00001-old','percent':100})
        self.assertEqual(req['traffic'][1]['percent'],0)
        self.assertEqual(req['traffic'][1]['tag'],'candidate')
        self.assertEqual(set(req),{'name','etag','template','traffic'})
        self.assertEqual(set(req['template']),{'containers','revision'})
        self.assertEqual(self.p['spec']['path'],m.base.NAME+'?updateMask=template.containers,template.revision,traffic')

    def test_same_image_or_source_refused(self):
        for key in ('image','source_commit'):
            n=copy.deepcopy(self.new);n['spec'][key]=self.old['spec'][key]
            if key=='source_commit':n['spec']['environment']['GIT_COMMIT']=self.old['spec'][key]
            n['spec_sha256']=hashlib.sha256(canonical(n['spec'])).hexdigest()
            with self.subTest(key=key),self.assertRaises(ValueError):m.proposal(self.old,n,self.service,self.policy)

    def test_secret_version_change_refused_even_valid_plan(self):
        n=generate(IMAGE_ROOT+'@sha256:'+'b'*64,'b'*40,'b'*64,
                   {k:'2' for k in m.base.SECRET_NAMES},'mmh-unlocked-validation')
        with self.assertRaises(ValueError):m.proposal(self.old,n,self.service,self.policy)

    def test_baseline_control_mutations_refused(self):
        mutations=[lambda s:s.pop('etag'),lambda s:s.update(uid=''),
          lambda s:s.update(invokerIamDisabled=True),lambda s:s.update(iapEnabled=True),
          lambda s:s.update(reconciling=True),lambda s:s.update(trafficStatuses=[]),
          lambda s:s['trafficStatuses'][0].update(percent=99),
          lambda s:s['trafficStatuses'][0].update(tag='unexpected'),
          lambda s:s.update(latestReadyRevision='projects/other/revisions/no'),
          lambda s:s['template'].update(serviceAccount='other'),
          lambda s:s['template']['containers'][0]['env'].append({'name':'EXTRA','value':'1'})]
        for edit in mutations:
            s=copy.deepcopy(self.service);edit(s)
            with self.subTest(edit=edit),self.assertRaises(ValueError):m.proposal(self.old,self.new,s,self.policy)

    def test_public_binding_refused(self):
        with self.assertRaises(ValueError):m.proposal(self.old,self.new,self.service,
            {'bindings':[{'role':'roles/run.invoker','members':['allUsers']}]})

    def test_preserves_default_startup_probe_and_rejects_unknown_container_fields(self):
        s=copy.deepcopy(self.service)
        s['template']['containers'][0]['startupProbe']=copy.deepcopy(m.DEFAULT_PROBE)
        p=m.proposal(self.old,self.new,s,self.policy);m.verify_proposal(p)
        self.assertEqual(p['spec']['request']['template']['containers'][0]['startupProbe'],m.DEFAULT_PROBE)
        s['template']['containers'][0]['startupProbe']['timeoutSeconds']=239
        with self.assertRaises(ValueError):m.proposal(self.old,self.new,s,self.policy)
        s=copy.deepcopy(self.service);s['template']['containers'][0]['livenessProbe']={'httpGet':{'path':'/'}}
        with self.assertRaises(ValueError):m.proposal(self.old,self.new,s,self.policy)

    def test_rehashed_request_tampering_refused(self):
        mutations=[lambda s:s.update(path=m.base.NAME+'?updateMask=*'),
            lambda s:s['request'].update(invokerIamDisabled=True),
            lambda s:s['request'].pop('etag'),
            lambda s:s['request']['traffic'][1].update(percent=100),
            lambda s:s['request']['traffic'][0].update(type='TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST'),
            lambda s:s['request']['template'].update(serviceAccount='other'),
            lambda s:s.update(candidate_revision='foreign'),
            lambda s:s['request'].update(name='other')]
        for edit in mutations:
            p=copy.deepcopy(self.p);edit(p['spec']);rehash(p)
            with self.subTest(edit=edit),self.assertRaises(ValueError):m.verify_proposal(p)

    def test_check_is_read_only(self):
        self.assertEqual(self.execute()['state'],'checked_no_update')
        self.assertFalse(any(c[0]=='PATCH' for c in self.calls))

    def test_exact_confirmation_and_journal_single_attempt(self):
        with self.assertRaises(ValueError):self.execute(confirm='wrong')
        self.assertFalse(self.journal.exists());self.assertEqual(self.calls,[])
        r=self.execute(confirm=self.p['sha256'])
        self.assertEqual(r['state'],'submitted_not_accepted');self.assertFalse(r['accepted'])
        self.assertEqual(len([c for c in self.calls if c[0]=='PATCH']),1)
        with self.assertRaises(FileExistsError):self.execute(confirm=self.p['sha256'])

    def test_effective_unknown_blocks(self):
        for result in (False,None,'UNKNOWN_INFO_DENIED'):
            with tempfile.TemporaryDirectory() as tmp:
                self.journal=Path(tmp)/'run'
                with self.subTest(result=result),self.assertRaises(ValueError):
                    self.execute(confirm=self.p['sha256'],effective=lambda *args:result)
        self.assertFalse(any(c[0]=='PATCH' for c in self.calls))

    def test_etag_or_uid_changed_after_preflight_blocks(self):
        for key in ('etag','uid','generation'):
            with tempfile.TemporaryDirectory() as tmp:
                self.journal=Path(tmp)/'run';self.service=baseline(self.old);self.service[key]='changed'
                with self.subTest(key=key),self.assertRaises(ValueError):self.execute(confirm=self.p['sha256'])
        self.assertFalse(any(c[0]=='PATCH' for c in self.calls))

    def test_network_error_after_patch_stays_unknown_no_retry(self):
        def api(method,path,body=None):
            if method=='PATCH':self.calls.append((method,path,body));raise TimeoutError()
            return self.api(method,path,body)
        with self.assertRaises(TimeoutError):self.execute(confirm=self.p['sha256'],api=api)
        r=json.loads((self.journal/'result.json').read_text())
        self.assertTrue(r['attempted']);self.assertEqual(r['state'],'outcome_unknown')
        self.assertEqual(len([c for c in self.calls if c[0]=='PATCH']),1)

    def test_changed_inventory_blocks(self):
        with patch.object(m,'metadata',return_value={}),patch.object(m.base,'inventory_and_grants',side_effect=[1,2]):
            with self.assertRaises(ValueError):m.stage(self.p,'b',self.journal,self.p['sha256'],api=self.api,effective=lambda *a:True)
        self.assertFalse(any(c[0]=='PATCH' for c in self.calls))

    def test_metadata_removes_only_creation_absence(self):
        report={'complete':True,'checks':[{'check':'mmh_service_absent','passed':False}]+
                [{'check':'guard'+str(i),'passed':True} for i in range(12)]}
        with patch.object(m.base,'inspect',return_value=report):
            self.assertIs(m.metadata(self.new,'b'),report)
            for i in range(1,13):
                report['checks'][i]['passed']=False
                with self.subTest(i=i),self.assertRaises(ValueError):m.metadata(self.new,'b')
                report['checks'][i]['passed']=True
            report['complete']=False
            with self.assertRaises(ValueError):m.metadata(self.new,'b')

    def test_ready_requires_real_observed_zero_traffic(self):
        s=self.ready();s['trafficStatuses'][1].pop('percent')
        r=m.check_candidate(self.p,s,self.policy)
        self.assertFalse(r['promoted']);self.assertEqual(r['candidate_default_traffic'],0)
        mutations=[lambda s:s['trafficStatuses'][1].update(percent=1),
          lambda s:s['trafficStatuses'][0].update(revision='other'),
          lambda s:s['traffic'][1].update(percent=100),lambda s:s.update(uid='other'),
          lambda s:s.update(latestReadyRevision=m.base.NAME+'/revisions/'+m.base.SERVICE+'-wrong'),
          lambda s:s.update(reconciling=True),lambda s:s.update(observedGeneration='1'),
          lambda s:s['template']['containers'][0].update(image=self.old['spec']['image'])]
        for edit in mutations:
            s=self.ready();edit(s)
            with self.subTest(edit=edit),self.assertRaises(ValueError):m.check_candidate(self.p,s,self.policy)

    def test_recovery_request_only_removes_tag_and_uses_fresh_etag(self):
        s=self.ready();s['etag']='newest'
        req=m.rollback_request(self.p,s,self.policy)
        self.assertEqual(set(req),{'name','etag','traffic'});self.assertEqual(req['etag'],'newest')
        self.assertEqual(req['traffic'],[{'type':m.TYPE,'revision':m.base.SERVICE+'-00001-old','percent':100}])
        s['traffic']=req['traffic'];s['trafficStatuses']=copy.deepcopy(req['traffic'])
        self.assertTrue(m.check_candidate(self.p,s,self.policy,rolled_back=True)['candidate_tag_removed'])

    def test_status_pending_never_accepts_previous_green(self):
        self.execute(confirm=self.p['sha256'])
        def api(method,path,body=None):return {'done':False}
        r=m.status(self.p,self.journal,api=api)
        self.assertFalse(r['accepted']);self.assertEqual(r['state'],'verifying')

    def test_status_checks_operation_and_private_policy(self):
        self.execute(confirm=self.p['sha256']);self.service=self.ready()
        r=m.status(self.p,self.journal,api=self.api,run=lambda a:{'bindings':[],'etag':'p'})
        self.assertTrue(r['accepted']);self.assertFalse(r['gcs_e2e_passed'])
        self.policy={'bindings':[{'role':'roles/run.invoker','members':['allUsers']}]}
        with self.assertRaises(ValueError):m.status(self.p,self.journal,api=self.api,run=lambda a:{'bindings':[],'etag':'p'})
        self.assertFalse(json.loads((self.journal/'result.json').read_text())['accepted'])


if __name__=='__main__':unittest.main(verbosity=2)
