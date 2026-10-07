import copy
import json
from pathlib import Path
import unittest
import protect_mmh_buckets as m
import test_provision_mmh_foundation as f
from test_restrict_mmh_bucket_iam import policy as allow_policy

class Fake:
    def __init__(self):
        self.calls=[];self.key=None;self.value=None;self.attached=set();self.deny=None
        self.project_tag=False;self.readback_failure=False
    def __call__(self,a):
        self.calls.append(a)
        if a[:2]==['projects','describe']:return dict(projectId=m.PROJECT,projectNumber=m.NUMBER,lifecycleState='ACTIVE')
        if a[:2]==['auth','list']:return [{'account':m.ADMIN}]
        if a[:2]==['projects','get-iam-policy']:return {'bindings':[{'role':'roles/owner','members':[m.OWNER_MEMBER]}]}
        if a[:3]==['storage','buckets','list']:return [dict(name=b,location='ASIA-EAST1') for b in (*m.BUCKETS,'existing-control')]
        if a[:3]==['storage','buckets','describe']:return f.FoundationTests().bucket(a[3][5:])
        if a[:3]==['storage','buckets','get-iam-policy']:return allow_policy(m.TARGET)
        if a[:3]==['resource-manager','tags','keys']:
            if a[3]=='create':self.key='tagKeys/123'
            row={'name':self.key,'shortName':m.TAG_SHORT_NAME,'parent':'projects/'+m.NUMBER}
            return row if a[3]=='create' else ([row] if self.key else [])
        if a[:3]==['resource-manager','tags','values']:
            if a[3]=='create':self.value='tagValues/456'
            row={'name':self.value,'shortName':m.TAG_VALUE,'parent':self.key}
            return row if a[3]=='create' else ([row] if self.value else [])
        if a[:3]==['resource-manager','tags','bindings']:
            parent=next(x[9:] for x in a if x.startswith('--parent='))
            if a[3]=='create':self.attached.add(parent);return {}
            yes=parent in self.attached or (self.project_tag and '/projects/'+m.NUMBER==parent[-len('/projects/'+m.NUMBER):])
            return [{'tagValue':self.value}] if yes else []
        if a[:3]==['iam','policies','list']:return {'policies':[{'name':'policies/foo/denypolicies/'+m.POLICY_ID}]} if self.deny else {}
        if a[:3]==['iam','policies','create']:
            self.deny=json.loads(Path(next(x[14:] for x in a if x.startswith('--policy-file='))).read_text());return {}
        if a[:3]==['iam','policies','get']:return {} if self.readback_failure else copy.deepcopy(self.deny)
        raise AssertionError(a)

class ProtectionTests(unittest.TestCase):
    def test_policy_has_only_exact_bucket_control_permissions_and_owner_exception(self):
        r=m.policy('tagKeys/123','tagValues/456')['rules'][0]['denyRule']
        self.assertEqual(r['deniedPrincipals'],['principalSet://goog/public:all'])
        self.assertEqual(r['exceptionPrincipals'],['principal://goog/subject/jack.hou@gmail.com'])
        self.assertEqual(set(r['deniedPermissions']),{'storage.googleapis.com/buckets.'+x for x in ['delete','setIamPolicy','update','createTagBinding','deleteTagBinding']})
        self.assertEqual(r['denialCondition']['expression'],"resource.matchTagId('tagKeys/123', 'tagValues/456')")
    def test_rejects_noncanonical_or_injected_tag_ids(self):
        for x in ('tagKeys/name','tagKeys/123\n',"tagKeys/123') || true",'','projects/123'):
            with self.assertRaises(ValueError):m.policy(x,'tagValues/456')
        with self.assertRaises(ValueError):m.policy('tagKeys/123','tagValues/abc')
    def test_dry_run_no_mutation(self):
        f=Fake();m.protect(f)
        self.assertFalse(any('create' in c for c in f.calls))
    def test_apply_only_expected_resources_and_idempotence(self):
        f=Fake();m.protect(f,True,lambda:True)
        self.assertEqual(f.attached,{m.resource(b) for b in m.BUCKETS})
        f.calls=[];m.protect(f,True,lambda:True)
        self.assertFalse(any('create' in c for c in f.calls))
    def test_foreign_project_stops_before_mutation(self):
        f=Fake()
        def run(a):
            r=f(a)
            if a[:2]==['projects','describe']:r['projectNumber']='999'
            return r
        with self.assertRaises(ValueError):m.protect(run,True,lambda:True)
        self.assertEqual(len(f.calls),1)
    def test_owner_and_operator_must_match(self):
        for which in ('auth','owner'):
            f=Fake()
            def run(a):
                r=f(a)
                if which=='auth' and a[:2]==['auth','list']:return [{'account':'other@example.com'}]
                if which=='owner' and a[:2]==['projects','get-iam-policy']:r['bindings'][0]['condition']={'expression':'true'}
                return r
            with self.assertRaises(ValueError):m.protect(run,True,lambda:True)
            self.assertFalse(any('create' in c for c in f.calls))
    def test_missing_or_unknown_effective_authority_stops_before_tags(self):
        for answer in (None,False,'UNKNOWN',1):
            f=Fake()
            with self.assertRaises(PermissionError):m.protect(f,True,lambda:answer)
            self.assertFalse(any('create' in c for c in f.calls))

    def test_tag_outside_mmh_refused(self):
        f=Fake();f.key='tagKeys/123';f.value='tagValues/456';f.attached.add(m.resource('existing-control'))
        with self.assertRaises(ValueError):m.protect(f,True,lambda:True)
        self.assertFalse(any('create' in c for c in f.calls))
    def test_project_tag_refused(self):
        f=Fake();f.key='tagKeys/123';f.value='tagValues/456';f.project_tag=True
        with self.assertRaises(ValueError):m.protect(f,True,lambda:True)
        self.assertFalse(any('create' in c for c in f.calls))
    def test_changed_existing_policy_is_not_overwritten(self):
        f=Fake();m.protect(f,True,lambda:True);f.calls=[];f.deny['rules'][0]['denyRule']['denialCondition']['expression']='true'
        with self.assertRaises(ValueError):m.protect(f,True,lambda:True)
        self.assertFalse(any('create' in c for c in f.calls))
    def test_policy_readback_failure_stops(self):
        f=Fake();f.readback_failure=True
        with self.assertRaises(ValueError):m.protect(f,True,lambda:True)
    def test_tag_metadata_missing_parent_or_duplicate_refused(self):
        row={'name':'tagKeys/1','shortName':m.TAG_SHORT_NAME,'parent':'projects/'+m.NUMBER}
        for rows in ([row,row],[dict(row,parent='projects/2')],[dict(row,name='key/1')],None):
            with self.assertRaises(ValueError):m.exact_tag(rows,m.TAG_SHORT_NAME,'projects/'+m.NUMBER,'tagKeys')
    def test_policy_inventory_pagination_refused(self):
        f=Fake();m.protect(f,True,lambda:True);f.calls=[]
        def run(a):
            r=f(a)
            if a[:3]==['iam','policies','list']:r['nextPageToken']='pending'
            return r
        with self.assertRaises(ValueError):m.protect(run,True,lambda:True)
        self.assertFalse(any('create' in c for c in f.calls))

if __name__=='__main__':unittest.main(verbosity=2)
