"""Candidate plan safety checks; offline only, no account or GCP access."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tools'));sys.path.insert(0,str(ROOT/'Backend/Flask'))
import plan_lite_candidate_deploy as plan
from lite_service_profile import validate_lite_environment
from lite_bucket_policy import validate_bucket_policy


class CandidatePlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.context=self.base/'context';self.context.mkdir()
        raw=b'FROM reviewed-image\n';(self.context/'Dockerfile').write_bytes(raw)
        manifest={'schema':'lite.build-context/1','git_head':'a'*40,'working_tree_dirty':True,
                  'files':{'Dockerfile':{'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}}}
        (self.context/'build-context-manifest.json').write_text(json.dumps(manifest));self.sha=plan.build.sha(self.context/'build-context-manifest.json')
        self.probe=self.base/'probe.py';self.probe.write_text('print("synthetic")')
        self.fixture=self.base/'fixture.json';self.fixture.write_text('{}');self.output=self.base/'plan'
    def generate(self):return plan.generate(self.context,self.sha,self.probe,self.fixture,'35',self.output)

    def test_new_runtime_is_private_digest_pinned_zero_idle_and_isolated(self):
        argv=plan.deploy_argv('sha256:'+'a'*64,self.base/'runtime-env.json')
        self.assertEqual(argv[:4],['gcloud','run','deploy','woundai-lite-candidate'])
        self.assertIn('--no-allow-unauthenticated',argv);self.assertIn('--invoker-iam-check',argv)
        self.assertIn('--cpu-throttling',argv);self.assertIn('--no-cpu-boost',argv)
        for flag,value in [('--project',plan.PROJECT),('--region','asia-east1'),('--min','0'),('--min-instances','0'),
                           ('--max-instances','1'),('--concurrency','1'),('--service-account',plan.RUNTIME)]:
            self.assertEqual(argv[argv.index(flag)+1],value)
        self.assertEqual(argv[argv.index('--image')+1],plan.IMAGE+'@sha256:'+'a'*64)
        self.assertEqual(argv[argv.index('--set-secrets')+1],'LITE_IP_SALT=woundai-lite-ip-salt:1')
    def test_tags_wrong_repo_and_malformed_digest_cannot_be_bound(self):
        for digest in ('latest','a'*64,'sha256:'+'A'*64,plan.IMAGE+'@sha256:'+'a'*64,'sha256:'+'a'*64+'\n'):
            with self.subTest(digest=digest),self.assertRaises(ValueError):plan.deploy_argv(digest,'env.json')
    def test_explicit_version_no_secret_value_and_valid_runtime_profile(self):
        env=plan.runtime_environment('35',self.sha)
        self.assertEqual(env['WOUNDAI_LITE_ATTEST_VERSIONS'],'35')
        self.assertEqual(env['WOUNDAI_LITE_ATTEST_APP_ID'],'LY2F24ZM68.com.woundai.lite')
        self.assertNotIn('LITE_IP_SALT',env)
        self.assertNotIn('FLASK_SECRET_KEY',env);self.assertNotIn('JWT_SECRET_KEY',env)
        validate_lite_environment(dict(env,LITE_IP_SALT='synthetic-isolated-test-only-salt-12345'))
        for bad in ('35,36','35\n','0','035',35,True):
            with self.subTest(version=bad),self.assertRaises(ValueError):plan.runtime_environment(bad,self.sha)
    def test_bucket_contract_allows_withdrawal_and_preserves_tombstones(self):
        for name,role in [(plan.MEDIA,'media'),(plan.SECURITY,'security')]:
            request=plan.bucket_request(name)
            result=validate_bucket_policy(dict(request,kind='storage#bucket',projectNumber=plan.NUMBER,metageneration='1'),
                                          name=name,project_number=plan.NUMBER,role=role,location='ASIA-EAST1')
            self.assertEqual(result['live_object_deletion_policy'],'unretained')
        with self.assertRaises(ValueError):plan.bucket_request('woundai-flywheel-jackh001')
    def test_probe_precedes_image_publication_and_exact_artifacts_are_hashed(self):
        p=self.generate();cfg=json.loads((self.output/'cloudbuild.json').read_text())
        self.assertEqual(len(cfg['steps']),3)
        self.assertEqual(cfg['images'],[p['build']['image_tag']])
        self.assertIn('--network=none',cfg['steps'][-1]['args']);self.assertNotIn('push',cfg['steps'][-1]['args'])
        self.assertEqual(cfg['steps'][1]['args'][cfg['steps'][1]['args'].index('--tag')+1],cfg['images'][0])
        for path,sha in p['artifact_sha256'].items():self.assertEqual(plan.build.sha(self.output/path),sha)
        self.assertEqual(set(p['artifact_sha256']),{str(f.relative_to(self.output)) for f in self.output.rglob('*') if f.is_file() and f.name!='plan.json'})
    def test_source_extra_private_file_and_changed_source_refused_before_output(self):
        extra=self.context/'credentials.json';extra.write_text('{}')
        with self.assertRaises(ValueError):self.generate()
        self.assertFalse(self.output.exists());extra.unlink()
        (self.context/'Dockerfile').write_text('changed')
        with self.assertRaises(ValueError):self.generate()
        self.assertFalse(self.output.exists())
    def test_wrong_manifest_sha_and_symlink_probe_fail_closed(self):
        self.sha='0'*64
        with self.assertRaises(ValueError):self.generate()
        self.sha=plan.build.sha(self.context/'build-context-manifest.json')
        self.probe.unlink();self.probe.symlink_to(self.fixture)
        with self.assertRaises(ValueError):self.generate()
        self.assertFalse(self.output.exists())
    def test_output_never_overwrites_or_enters_context(self):
        self.output.mkdir();marker=self.output/'keep';marker.write_text('keep')
        with self.assertRaises(ValueError):self.generate()
        self.assertEqual(marker.read_text(),'keep')
        self.output=self.context/'nested'
        with self.assertRaises(ValueError):self.generate()
    def test_runtime_roles_are_object_scoped_not_iam_or_bucket_admin(self):
        p=self.generate()
        for name in ('woundaiLiteMediaObjects','woundaiLiteSecurityObjects'):
            role=json.loads((self.output/(name+'.json')).read_text());permissions=set(role['includedPermissions'])
            self.assertTrue(permissions<={'storage.buckets.get','storage.objects.get','storage.objects.create','storage.objects.delete','storage.objects.list'})
            self.assertIn('storage.objects.delete',permissions) # needed to replace live bytes with a seal
            if 'Security' in name:self.assertNotIn('storage.objects.list',permissions)
        bindings=p['resource_bindings'];self.assertEqual(len(bindings),5)
        runtime=[b for b in bindings if b['principal']==plan.RUNTIME]
        self.assertEqual({b['resource'] for b in runtime},{plan.MEDIA,plan.SECURITY,'projects/'+plan.NUMBER+'/secrets/'+plan.SECRET})
    def test_budget_does_not_hide_separate_withdrawal_lane(self):
        p=self.generate();limits=p['cost_limits']
        self.assertEqual(limits['general_admissions_per_utc_day'],2000)
        self.assertEqual(limits['separate_withdrawal_admissions_per_utc_day'],2000)
        self.assertEqual(limits['per_installation_daily_measurements'],5)
        self.assertIn('not a hard cost cap',limits['warning'])

if __name__=='__main__':unittest.main()
