import copy
import hashlib
import importlib.util
from pathlib import Path
import unittest

from plan_review_runtime import generate, validate, resources, canonical, IMAGE_ROOT, SECRETS, BUCKETS, SERVICE


class ReviewPlanTests(unittest.TestCase):
    def plan(self):
        return generate(IMAGE_ROOT + '@sha256:' + 'a'*64, 'b'*40, 'c'*64, {key:'1' for key in SECRETS})

    def test_bound_plan_is_still_not_authorization(self):
        plan = self.plan(); self.assertTrue(validate(plan))
        self.assertFalse(plan['deployable']); self.assertFalse(plan['apply_authorized'])
        self.assertEqual(plan['spec']['bootstrap_invocation'], 'private')
        self.assertTrue(plan['spec']['release_invoker_binding']['separate_approval_required'])
        self.assertFalse(plan['spec']['iap_enabled'])
        self.assertEqual(plan['spec']['release_invoker_binding']['resource'], SERVICE)

    def test_exact_backend_profile_matches_injected_environment(self):
        path = Path(__file__).resolve().parents[1]/'Backend/Flask/review_service_profile.py'
        spec = importlib.util.spec_from_file_location('review_profile_fixture', path)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        env = dict(resources()['environment'], K_SERVICE=SERVICE)
        self.assertEqual(env, module.EXPECTED)
        self.assertTrue(module.review_environment(env))
        self.assertNotIn('K_SERVICE', self.plan()['spec']['environment'])

    def test_no_other_buckets_secrets_or_project_grants(self):
        spec = self.plan()['spec']
        self.assertEqual([b['name'] for b in spec['buckets']], list(BUCKETS))
        for b in spec['buckets']:
            self.assertTrue(b['uniform_bucket_level_access'])
            self.assertEqual(b['public_access_prevention'], 'enforced')
            self.assertEqual(b['lifecycle_rules'], [])
            self.assertFalse(b['retention_locked'])
        for grant in spec['bucket_runtime_grants'][1:]:
            self.assertNotIn('storage.objects.delete', grant['permissions'])
        self.assertEqual({s['secret'] for s in spec['secret_runtime_grants']}, set(SECRETS.values()))

    def test_mutated_policy_rejected_even_after_rehash(self):
        changes = [(['project'],'other'), (['service'],'woundai-backend-mmhps20261007'),
            (['org'],'mmhps20261007'), (['runtime_service_account'],'broad@other'),
            (['environment','WOUNDAI_ENABLE_LITE_API'],'1'), (['environment','WOUNDAI_STORE'],'local'),
            (['environment','WOUNDAI_GCS_BUCKET'],'woundai-existing-media'),
            (['environment','WOUNDAI_AUDIT_MODE'],'mmh-unlocked-validation'),
            (['bootstrap_invocation'],'public'), (['iap_enabled'],True),
            (['release_invoker_binding','separate_approval_required'],False),
            (['release_invoker_binding','resource'],'woundai-backend'),
            (['existing_resources_may_be_modified'],True), (['mmh_credentials_may_be_copied'],True),
            (['secret_values_may_be_exported'],True), (['limits','max_instances'],10)]
        for path,value in changes:
            plan=self.plan(); node=plan['spec']
            for key in path[:-1]: node=node[key]
            node[path[-1]]=value
            plan['spec_sha256']=hashlib.sha256(canonical(plan['spec'])).hexdigest()
            with self.subTest(path=path), self.assertRaises(ValueError): validate(plan)

    def test_permissions_retention_and_public_bucket_tampering_refused(self):
        for edit in range(5):
            plan=self.plan(); spec=plan['spec']
            if edit==0:spec['bucket_runtime_grants'][1]['permissions'].append('storage.objects.delete')
            if edit==1:spec['buckets'][0]['public_access_prevention']='inherited'
            if edit==2:spec['buckets'][2]['retention_locked']=True
            if edit==3:spec['buckets'][0]['lifecycle_rules']=[{'delete_after_days':30}]
            if edit==4:spec['secret_versions']['ADMIN_PASSWORD']='woundai-mmhps20261007-admin-password:1'
            spec_hash=hashlib.sha256(canonical(spec)).hexdigest();plan['spec_sha256']=spec_hash
            with self.subTest(edit=edit), self.assertRaises(ValueError):validate(plan)

    def test_incomplete_extra_or_authorized_document_refused(self):
        for edit in range(4):
            plan=self.plan()
            if edit==0:plan['deployable']=True
            if edit==1:plan['apply_authorized']=True
            if edit==2:plan['spec']['environment']['WOUNDAI_DEMO_SEED_USER']='review01'
            if edit==3:plan['spec']['secret_versions']['EXTRA']='other:1'
            with self.subTest(edit=edit), self.assertRaises(ValueError):validate(plan)

    def test_mutable_image_short_source_and_secret_values_refused(self):
        for image in [IMAGE_ROOT+':latest','other@sha256:'+'a'*64,None]:
            with self.assertRaises(ValueError):generate(image,'b'*40,'c'*64,{k:'1' for k in SECRETS})
        for source in ['b'*7,'B'*40,'b'*40+'\n']:
            with self.assertRaises(ValueError):generate(IMAGE_ROOT+'@sha256:'+'a'*64,source,'c'*64,{k:'1' for k in SECRETS})
        for version in ['latest','0','01',1,True,None,'must-not-inline']:
            values={k:'1' for k in SECRETS};values['ADMIN_PASSWORD']=version
            with self.assertRaises(ValueError):generate(IMAGE_ROOT+'@sha256:'+'a'*64,'b'*40,'c'*64,values)


if __name__ == '__main__':unittest.main(verbosity=2)
