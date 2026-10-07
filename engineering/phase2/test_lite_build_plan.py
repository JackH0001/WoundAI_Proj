"""Offline build-plan boundaries and source integrity; no gcloud execution."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('plan',ROOT/'tools/plan_lite_cloud_build.py')
plan=importlib.util.module_from_spec(spec);spec.loader.exec_module(plan)

class BuildPlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.context=self.base/'context';self.context.mkdir()
        source=b'FROM reviewed-image\n';(self.context/'Dockerfile').write_bytes(source)
        m={'schema':'lite.build-context/1','git_head':'a'*40,'working_tree_dirty':True,
           'files':{'Dockerfile':{'sha256':hashlib.sha256(source).hexdigest(),'bytes':len(source)}}}
        (self.context/'build-context-manifest.json').write_text(json.dumps(m));self.sha=plan.sha(self.context/'build-context-manifest.json')
        self.probe=self.base/'probe.py';self.probe.write_text('print("synthetic")')
        self.fixture=self.base/'fixture.json';self.fixture.write_text('{}')
        self.output=self.base/'plan'
    def generate(self):return plan.generate(self.context,self.sha,self.probe,self.fixture,self.output)
    def policy(self,member='user:reviewer@example.invalid'):
        return [{'type':'project','id':plan.PROJECT,'policy':{'bindings':[{'role':'roles/viewer','members':[member]}]}}]
    def test_build_is_scoped_and_cannot_deploy_or_push(self):
        result=self.generate();build=result['cloudbuild']
        self.assertEqual(build['serviceAccount'],f'projects/{plan.PROJECT}/serviceAccounts/{plan.EMAIL}')
        self.assertEqual(build['logsBucket'],'gs://'+plan.BUCKET)
        self.assertEqual(build['options'],{'logging':'GCS_ONLY','machineType':'E2_STANDARD_2'})
        self.assertEqual(build['timeout'],'1200s');self.assertNotIn('images',build)
        self.assertEqual(build['steps'][1]['args'][:3],['build','--platform','linux/amd64'])
        argv=build['steps'][2]['args']
        for flag in ['--network=none','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges']:
            self.assertIn(flag,argv)
        self.assertFalse(any('push' in step['args'] or 'deploy' in step['args'] for step in build['steps']))
        self.assertEqual(len(result['provision_commands']),4)
        for cmd in result['provision_commands']+[result['submit_command']]:
            self.assertIn('--project',cmd);self.assertEqual(cmd[cmd.index('--project')+1],plan.PROJECT)
        role=json.loads((self.output/'build-role.json').read_text())
        self.assertEqual(set(role['includedPermissions']),{'storage.buckets.get','storage.objects.get','storage.objects.list','storage.objects.create','storage.objects.delete'})
    def test_remote_verifier_checks_exact_source_and_probe(self):
        result=self.generate();submission=self.output/'submission';code=result['cloudbuild']['steps'][0]['args'][1]
        import os
        old=Path.cwd()
        try:
            os.chdir(submission);exec(code,{})
            (submission/'probe.py').write_text('modified')
            with self.assertRaises(AssertionError):exec(code,{})
        finally:os.chdir(old)
    def test_wrong_manifest_hash_and_changed_file_block_generation(self):
        self.sha='0'*64
        with self.assertRaises(ValueError):self.generate()
        self.sha=plan.sha(self.context/'build-context-manifest.json');(self.context/'Dockerfile').write_text('tampered')
        with self.assertRaises(ValueError):self.generate()
        self.assertFalse(self.output.exists())
    def test_extra_file_symlink_and_missing_file_rejected(self):
        extra=self.context/'private.env';extra.write_text('synthetic')
        with self.assertRaises(ValueError):self.generate()
        extra.unlink();extra.symlink_to(self.probe)
        with self.assertRaises(ValueError):self.generate()
        extra.unlink();(self.context/'Dockerfile').unlink()
        with self.assertRaises(ValueError):self.generate()
    def test_existing_output_and_nested_output_refused(self):
        self.output.mkdir()
        with self.assertRaises(ValueError):self.generate()
        self.output=self.context/'plan'
        with self.assertRaises(ValueError):self.generate()
    def test_project_id_and_number_ancestor_formats_supported(self):
        p=self.policy();self.assertFalse(plan.check_ancestor_policy(p)['direct_or_ambiguous_grants'])
        p[0]['id']=plan.NUMBER;plan.check_ancestor_policy(p)
    def test_ambiguous_principals_rejected_even_if_conditional(self):
        for member in ['group:g@example.invalid','domain:example.invalid','allUsers','allAuthenticatedUsers',
                       'principal://example/subject','principalSet://example/pool','deleted:serviceAccount:old@example.invalid','unknown:example']:
            p=self.policy(member);p[0]['policy']['bindings'][0]['condition']={'expression':'false'}
            with self.subTest(member=member),self.assertRaises(ValueError):plan.check_ancestor_policy(p)
    def test_direct_grant_including_case_variant_rejected(self):
        for member in ['serviceAccount:'+plan.EMAIL, 'serviceAccount:'+plan.EMAIL.upper()]:
            with self.subTest(member=member),self.assertRaises(ValueError):plan.check_ancestor_policy(self.policy(member))
    def test_empty_or_wrong_project_evidence_rejected(self):
        p=self.policy();p[0]['id']='other-project'
        for value in [[],{},p,[{'type':'project','id':plan.PROJECT,'policy':{}}]]:
            with self.subTest(value=value),self.assertRaises(ValueError):plan.check_ancestor_policy(value)

if __name__=='__main__':unittest.main()
