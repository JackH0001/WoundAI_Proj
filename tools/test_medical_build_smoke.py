import json,tempfile,unittest
from pathlib import Path
from medical_build_smoke import prepare,exact_policy,project_roles,provision,MEMBER,EMAIL,BUCKET,PROJECT,NUMBER,REPO_NAME
from restrict_mmh_bucket_iam import DEFAULTS
class Tests(unittest.TestCase):
 def test_synthetic_context_and_explicit_identity(self):
  with tempfile.TemporaryDirectory() as td:
   r=prepare(Path(td)/'context','a'*40);c=r['config']
   self.assertEqual(c['serviceAccount'],f'projects/{PROJECT}/serviceAccounts/{EMAIL}')
   self.assertEqual(c['timeout'],'300s');self.assertEqual(c['options'],{'logging':'CLOUD_LOGGING_ONLY'})
   self.assertEqual(set(r['source_files']),{'proof.txt','Dockerfile','cloudbuild.json','.gcloudignore'})
   self.assertIn('/woundai-medical-build/smoke:',c['images'][0]);self.assertNotIn('availableSecrets',c)
   self.assertIn('cmp proof.txt readback.txt',c['steps'][0]['args'][1])
   with self.assertRaises(ValueError):prepare(Path(td)/'context','b'*40)
 def test_bad_commit_rejected(self):
  with tempfile.TemporaryDirectory() as td:
   with self.assertRaises(ValueError):prepare(Path(td)/'out','HEAD')
 def test_policy_rejects_extra_and_condition(self):
  expected={'role':{MEMBER}}
  exact_policy({'bindings':[{'role':'role','members':[MEMBER]}]},expected)
  for b in [{'role':'role','members':[MEMBER,'allUsers']},{'role':'role','members':[MEMBER],'condition':{}}]:
   with self.assertRaises(ValueError):exact_policy({'bindings':[b]},expected)
 def test_ambiguous_ancestor_rejected(self):
  for m in ('allUsers','group:test@example.com','domain:example.com','principalSet://anything'):
   with self.assertRaises(ValueError):project_roles({'etag':'e','bindings':[{'role':'roles/editor','members':[m]}]})
 def test_unknown_or_conditional_build_grant(self):
  self.assertEqual(project_roles({'etag':'e','bindings':[{'role':'roles/editor','members':[MEMBER]}]}),['roles/editor'])
  with self.assertRaises(ValueError):project_roles({'etag':'e','bindings':[{'role':'x','members':[MEMBER],'condition':{'expression':'true'}}]})
 def inventory(self,args):
  key=tuple(args[:3])
  if args[:2]==['projects','describe']:return {'projectId':PROJECT,'projectNumber':NUMBER,'lifecycleState':'ACTIVE'}
  if args[:2]==['auth','list']:return [{'account':'jack.hou@gmail.com'}]
  if args[:2]==['projects','get-iam-policy']:return {'etag':'e','bindings':[]}
  if args[-1]=='list' or 'list' in args:return []
  raise AssertionError('unexpected mutation')
 def test_dry_run_has_no_mutation(self):self.assertFalse(provision(self.inventory)['applied'])
 def test_existing_account_stops_before_mutation(self):
  def run(args):
   if args[:3]==['iam','service-accounts','list']:return [{'email':EMAIL}]
   return self.inventory(args)
  with self.assertRaises(ValueError):provision(run,True)
 def test_foreign_parent_refused(self):
  def run(args):
   r=self.inventory(args)
   if args[:2]==['projects','describe']:r['parent']={'type':'organization','id':'1'}
   return r
  with self.assertRaises(ValueError):provision(run,True)
 def test_apply_readback_and_no_runtime_changes(self):
  calls=[];state={'bucket':{'etag':'bucket-etag','bindings':[{'role':r,'members':sorted(m)} for r,m in DEFAULTS.items()]},'repo':{},'project':{'etag':'project-etag','bindings':[]}}
  def run(a):
   calls.append(a)
   if a[:2]==['projects','get-iam-policy']:return state['project']
   if a[:3]==['iam','service-accounts','describe']:return {'email':EMAIL,'projectId':PROJECT}
   if a[:3]==['storage','buckets','describe']:return {'name':BUCKET,'projectNumber':NUMBER,'location':'ASIA-EAST1','storageClass':'STANDARD','iamConfiguration':{'uniformBucketLevelAccess':{'enabled':True},'publicAccessPrevention':'enforced'},'softDeletePolicy':{'retentionDurationSeconds':'0'}}
   if a[:3]==['storage','buckets','get-iam-policy']:return state['bucket']
   if a[:3]==['storage','buckets','set-iam-policy']:
    self.assertIn('--etag=bucket-etag',a);state['bucket']=json.loads(Path(a[4]).read_text());return {}
   if a[:3]==['artifacts','repositories','describe']:return {'name':REPO_NAME,'format':'DOCKER'}
   if a[:3]==['artifacts','repositories','get-iam-policy']:return state['repo']
   if a[:3]==['artifacts','repositories','add-iam-policy-binding']:
    state['repo']={'bindings':[{'role':'roles/artifactregistry.writer','members':[MEMBER]}]};return {}
   if a[:2]==['projects','add-iam-policy-binding']:
    state['project']={'etag':'new','bindings':[{'role':'roles/logging.logWriter','members':[MEMBER]}]};return {}
   if 'create' in a:return {}
   return self.inventory(a)
  self.assertTrue(provision(run,True)['applied'])
  self.assertFalse(any(a[0] in ('run','secrets','builds') for a in calls))
  writes=[a for a in calls if 'add-iam-policy-binding' in a]
  self.assertEqual(len(writes),2)
  self.assertFalse(any('roles/editor' in x for a in writes for x in a))
 def test_unexpected_project_role_blocks_before_create(self):
  def run(a):
   if a[:2]==['projects','get-iam-policy']:return {'etag':'e','bindings':[{'role':'roles/editor','members':[MEMBER]}]}
   return self.inventory(a)
  with self.assertRaises(ValueError):provision(run,True)
if __name__=='__main__':unittest.main(verbosity=2)
