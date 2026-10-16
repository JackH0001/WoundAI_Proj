import copy,unittest
from plan_lite_candidate_resume import validate_state,snapshot_hash
import plan_lite_candidate_deploy as c
import plan_lite_cloud_build as b

def fixture():
 p,n=c.PROJECT,c.NUMBER;r=c.RUNTIME
 s={'cloud-inventory':{'lite-service':{'present':False}},
 'runtime':{'email':r,'projectId':p},'build-account':{'email':b.EMAIL,'projectId':p,'disabled':True},
 'ancestors':[{'type':'project','id':n,'policy':{'bindings':[{'role':'roles/owner','members':['user:operator@example.org']}]}}],
 'build-role':{'name':'projects/'+p+'/roles/'+b.ROLE,'stage':'GA','includedPermissions':b.PERMISSIONS},
 'build-bucket-iam':{},'repository-iam':{},
 'salt-metadata':{'name':'projects/'+n+'/secrets/'+c.SECRET,'replication':{'userManaged':{'replicas':[{'location':c.REGION}]}}},
 'salt-iam':{'bindings':[{'role':'roles/secretmanager.secretAccessor','members':['serviceAccount:'+r]}]}}
 for kind,listing in [('media',True),('security',False)]:
  rid='woundaiLite'+kind.capitalize()+'Objects';rn='projects/'+p+'/roles/'+rid
  s[kind+'-bucket']={'name':'woundai-lite-'+kind+'-'+n,'projectNumber':n,'location':'ASIA-EAST1','storageClass':'STANDARD','softDeletePolicy':{'retentionDurationSeconds':'0'},'iamConfiguration':{'publicAccessPrevention':'enforced','uniformBucketLevelAccess':{'enabled':True}}}
  s[kind+'-role']={**c.role(rid,listing),'name':rn}
  s[kind+'-iam']={'bindings':[{'role':rn,'members':['serviceAccount:'+r]}]}
 return s
class Tests(unittest.TestCase):
 def test_existing_resources_valid_but_not_authorized(self):
  result=validate_state(fixture());self.assertTrue(result['existing_resources_validated']);self.assertFalse(result['authorized_to_execute']);self.assertFalse(result['effective_isolation_proven']);self.assertFalse(result['create_resources'])
 def test_resource_drift_rejected(self):
  mutations=[lambda s:s['cloud-inventory']['lite-service'].update(present=True),lambda s:s['build-account'].update(disabled=False),lambda s:s['runtime'].update(disabled=True),lambda s:s['media-bucket'].update(retentionPolicy={'retentionPeriod':'10'}),lambda s:s['security-bucket'].update(lifecycle={'rule':[{'action':{'type':'Delete'}}]}),lambda s:s['media-bucket']['iamConfiguration'].update(publicAccessPrevention='inherited'),lambda s:s['security-role']['includedPermissions'].append('storage.objects.list'),lambda s:s['media-iam']['bindings'][0]['members'].append('allUsers'),lambda s:s['salt-iam']['bindings'][0]['members'].append('serviceAccount:'+b.EMAIL),lambda s:s['ancestors'][0]['policy']['bindings'][0]['members'].append('serviceAccount:'+c.RUNTIME),lambda s:s['repository-iam'].update(bindings=[{'role':'roles/artifactregistry.writer','members':['serviceAccount:'+b.EMAIL]}])]
  for i,mutate in enumerate(mutations):
   with self.subTest(case=i):
    state=fixture();mutate(state)
    with self.assertRaises(ValueError):validate_state(state)
 def test_baseline_hash_detects_etag_drift(self):
  s=fixture();old=snapshot_hash(s);s['media-iam']['etag']='changed';self.assertNotEqual(old,snapshot_hash(s))
 def test_existing_admin_binding_retained(self):
  s=fixture();s['media-iam']['bindings'].append({'role':'roles/storage.legacyBucketOwner','members':['projectOwner:'+c.PROJECT,'projectEditor:'+c.PROJECT]})
  before=copy.deepcopy(s);validate_state(s);self.assertEqual(s,before)
if __name__=='__main__':unittest.main()
