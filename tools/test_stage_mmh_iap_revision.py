import copy
import json
from pathlib import Path
import tempfile
import unittest
import stage_mmh_iap_revision as m
from test_stage_mmh_revision import plans, baseline


def boundary(plan):
    s=baseline(plan);s['uid']=m.UID;s['iapEnabled']=True
    s['traffic']=m.old.traffic(m.base.SERVICE+'-00001-original',m.base.SERVICE+'-00001-old')
    s['trafficStatuses']=copy.deepcopy(s['traffic'])
    r={'service':s,'run_policy':{'etag':'run','bindings':[{'role':'roles/run.invoker','members':[m.AGENT]}]},
       'project_policy':{'etag':'project','bindings':[]},'project_iap_settings':{'name':'projects/421209514056'},'iap':{}}
    for n in m.ANCESTORS:r['iap'][n]={'settings':{'name':n},'policy':{'etag':'ancestor'}}
    r['iap'][m.IAP]={'settings':{'name':m.IAP,'accessSettings':{'oauthSettings':{'clientId':m.WEB,'clientSecretSha256':'a'*64}}},
        'policy':{'etag':'iap','bindings':[{'role':'roles/iap.httpsResourceAccessor','members':[m.USER]}]}}
    return r


class IapStageTest(unittest.TestCase):
    def setUp(self):
        self.old,self.new=plans();self.b=boundary(self.old);self.p=m.propose(self.old,self.new,self.b)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name)/'run';self.calls=[]
    def ready(self):
        b=copy.deepcopy(self.b);s=b['service'];req=self.p['spec']['request']
        s['template'].update(copy.deepcopy(req['template']));s['traffic']=copy.deepcopy(req['traffic']);s['trafficStatuses']=copy.deepcopy(req['traffic'])
        s.update(etag='new',generation='2',observedGeneration='2',latestReadyRevision=m.base.NAME+'/revisions/'+req['template']['revision'],latestCreatedRevision=m.base.NAME+'/revisions/'+req['template']['revision'])
        return b
    def api(self,method,path,body=None):
        self.calls.append((method,path,body));return {'name':m.base.PARENT+'/operations/test','done':True}
    def execute(self,**kw):
        options=dict(read=lambda:copy.deepcopy(self.b),api=self.api,metadata=lambda *a:{},inventory=lambda *a:[1],effective=lambda *a:True)
        options.update(kw)
        return m.stage(self.p,'build',self.path,**options)
    def test_exact_image_only_mask_preserves_default_and_replaces_tag(self):
        m.verify(self.p);r=self.p['spec']['request']
        self.assertEqual(set(r),{'name','etag','template','traffic'});self.assertEqual(set(r['template']),{'containers','revision'})
        self.assertEqual(r['traffic'][0],self.b['service']['traffic'][0]);self.assertEqual(r['traffic'][1]['percent'],0)
    def test_rejects_iap_disabled_bypass_wrong_uid_or_unknown_controls(self):
        for k,v in [('iapEnabled',False),('invokerIamDisabled',True),('uid','other'),('sshEnabled',True)]:
            b=copy.deepcopy(self.b);b['service'][k]=v
            with self.subTest(k=k),self.assertRaises(ValueError):m.propose(self.old,self.new,b)
    def test_rejects_added_run_principal_condition_or_role(self):
        for binding in [{'role':'roles/run.invoker','members':[m.AGENT,'allUsers']},{'role':'roles/run.invoker','members':[m.AGENT],'condition':{}},{'role':'roles/owner','members':[m.AGENT]}]:
            b=copy.deepcopy(self.b);b['run_policy']['bindings']=[binding]
            with self.subTest(binding=binding),self.assertRaises(ValueError):m.propose(self.old,self.new,b)
    def test_rejects_iap_group_or_other_user(self):
        for member in ['allUsers','group:staff@example.com','user:other@example.com']:
            b=copy.deepcopy(self.b);b['iap'][m.IAP]['policy']['bindings'][0]['members']=[member]
            with self.subTest(member=member),self.assertRaises(ValueError):m.propose(self.old,self.new,b)
    def test_rejects_ancestor_grants_settings_or_missing_layer(self):
        for layer in m.ANCESTORS:
            for what in ['policy','settings','missing']:
                b=copy.deepcopy(self.b)
                if what=='missing': del b['iap'][layer]
                else:b['iap'][layer][what]['unexpected']=True
                with self.subTest(layer=layer,what=what),self.assertRaises(ValueError):m.propose(self.old,self.new,b)
    def test_rejects_oauth_audience_extra_client_or_unknown_setting(self):
        for k,v in [('clientId','foreign'),('programmaticClients',['extra']),('clientSecretSha256','')]:
            b=copy.deepcopy(self.b);b['iap'][m.IAP]['settings']['accessSettings']['oauthSettings'][k]=v
            with self.subTest(k=k),self.assertRaises(ValueError):m.propose(self.old,self.new,b)
    def test_rejects_parent_project_settings(self):
        b=copy.deepcopy(self.b);b['project_iap_settings']['accessSettings']={}
        with self.assertRaises(ValueError):m.propose(self.old,self.new,b)
    def test_rejects_wrong_or_unobserved_traffic(self):
        for field in ['traffic','trafficStatuses']:
            b=copy.deepcopy(self.b);b['service'][field][0]['percent']=99
            with self.subTest(field=field),self.assertRaises(ValueError):m.propose(self.old,self.new,b)
    def test_rehashed_request_cannot_change_iap_mask_or_environment(self):
        edits=[lambda s:s.update(path=m.base.NAME+'?updateMask=*'),lambda s:s['request'].update(iapEnabled=False),lambda s:s['request']['template']['containers'][0]['env'].append({'name':'EXTRA','value':'1'})]
        for edit in edits:
            p=copy.deepcopy(self.p);edit(p['spec']);p['sha256']=m.digest(p['spec'])
            with self.assertRaises(ValueError):m.verify(p)
    def test_read_only_check_has_no_patch(self):
        self.assertEqual(self.execute()['state'],'checked_no_update');self.assertFalse(self.calls)
    def test_confirm_and_journal_single_attempt(self):
        with self.assertRaises(ValueError):self.execute(confirm='other')
        self.assertFalse(self.path.exists())
        self.assertEqual(self.execute(confirm=self.p['sha256'])['state'],'submitted_not_accepted');self.assertEqual(len(self.calls),1)
        with self.assertRaises(FileExistsError):self.execute(confirm=self.p['sha256'])
    def test_stale_boundary_after_effective_check_blocks(self):
        b=copy.deepcopy(self.b);b['project_policy']['etag']='changed'
        with self.assertRaises(ValueError):self.execute(confirm=self.p['sha256'],read=lambda:b)
        self.assertFalse(self.calls)
    def test_effective_unknown_and_metadata_failure_block(self):
        for opts in [dict(effective=lambda *a:'UNKNOWN'),dict(metadata=lambda *a:(_ for _ in ()).throw(ValueError()))]:
            with tempfile.TemporaryDirectory() as tmp:
                self.path=Path(tmp)/'run'
                with self.assertRaises(ValueError):self.execute(confirm=self.p['sha256'],**opts)
        self.assertFalse(self.calls)
    def test_patch_timeout_stays_unknown_no_retry(self):
        def fail(*a):self.calls.append(a);raise TimeoutError()
        with self.assertRaises(TimeoutError):self.execute(confirm=self.p['sha256'],api=fail)
        self.assertEqual(len(self.calls),1);self.assertEqual(json.loads((self.path/'result.json').read_text())['state'],'outcome_unknown')
    def test_postcheck_keeps_all_boundaries_and_resources(self):
        self.assertTrue(m.check_after(self.p,self.ready())['iap_boundary_preserved'])
        edits=[lambda b:b['service'].update(iapEnabled=False),lambda b:b['run_policy'].update(etag='different'),lambda b:b['iap'][m.IAP]['settings']['accessSettings']['oauthSettings'].update(clientSecretSha256='b'*64),lambda b:b['service']['template']['scaling'].update(maxInstanceCount=2),lambda b:b['service'].update(customAudiences=['extra'])]
        for edit in edits:
            b=self.ready();edit(b)
            with self.assertRaises(ValueError):m.check_after(self.p,b)
    def test_rollback_restores_previous_candidate_not_removes_it(self):
        b=self.ready();req=m.rollback_request(self.p,b)
        self.assertEqual(req['traffic'],self.b['service']['traffic']);self.assertEqual(set(req),{'name','etag','traffic'})
        b['service']['traffic']=req['traffic'];b['service']['trafficStatuses']=copy.deepcopy(req['traffic'])
        self.assertTrue(m.check_after(self.p,b,rollback=True)['previous_tag_restored'])
    def test_status_pending_or_changed_boundary_never_stays_green(self):
        self.execute(confirm=self.p['sha256'])
        r=m.status(self.p,self.path,read=self.ready,api=self.api);self.assertTrue(r['accepted'])
        r=m.status(self.p,self.path,read=self.ready,api=lambda *a:{'done':False});self.assertFalse(r['accepted'])
        bad=self.ready();bad['service']['iapEnabled']=False
        with self.assertRaises(ValueError):m.status(self.p,self.path,read=lambda:bad,api=self.api)
        self.assertFalse(json.loads((self.path/'result.json').read_text())['accepted'])

if __name__=='__main__':unittest.main(verbosity=2)
