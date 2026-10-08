import copy
import unittest
from plan_mmh_runtime import generate, validate, SECRET_NAMES, IMAGE_ROOT, canonical
import hashlib


class PlanTests(unittest.TestCase):
    def plan(self):
        return generate(IMAGE_ROOT + '@sha256:' + 'a' * 64, 'b' * 40, 'c' * 64,
                        {k: '1' for k in SECRET_NAMES})

    def test_valid_plan_is_not_permission_to_deploy(self):
        p = self.plan()
        self.assertTrue(validate(p)); self.assertFalse(p['deployable'])
        self.assertEqual(p['spec']['invocation'], 'private')
        self.assertTrue(p['spec']['audit_requirement']['separate_irreversible_approval'])

    def test_image_must_be_exact_digest_in_medical_repository(self):
        for image in [IMAGE_ROOT + ':latest', IMAGE_ROOT + '@sha256:' + 'A'*64,
                      'other/' + IMAGE_ROOT + '@sha256:' + 'a'*64, None]:
            with self.subTest(image=image), self.assertRaises(ValueError):
                generate(image, 'b'*40, 'c'*64, {k:'1' for k in SECRET_NAMES})

    def test_source_and_manifest_require_full_lowercase_hash(self):
        for commit, manifest in [('b'*7,'c'*64), ('B'*40,'c'*64), ('b'*40,'c'*63)]:
            with self.assertRaises(ValueError):
                generate(IMAGE_ROOT+'@sha256:'+'a'*64, commit, manifest, {k:'1' for k in SECRET_NAMES})

    def test_secret_versions_fail_closed(self):
        for value in ['latest', '', '0', '-1', '01', '1\n', 1, True, None]:
            versions={k:'1' for k in SECRET_NAMES}; versions['ADMIN_PASSWORD']=value
            with self.subTest(value=value), self.assertRaises(ValueError):
                generate(IMAGE_ROOT+'@sha256:'+'a'*64,'b'*40,'c'*64,versions)
        with self.assertRaises(ValueError):
            generate(IMAGE_ROOT+'@sha256:'+'a'*64,'b'*40,'c'*64,{})

    def test_tampering_still_refused_after_hash_is_recomputed(self):
        original=self.plan()
        edits=[(['project'],'other'),(['service'],'woundai-backend'),
               (['runtime_service_account'],'421209514056-compute@developer.gserviceaccount.com'),
               (['environment','WOUNDAI_INSTITUTION_ORG'],'default'),
               (['environment','WOUNDAI_SECURITY_BUCKET'],original['spec']['environment']['WOUNDAI_GCS_BUCKET']),
               (['environment','WOUNDAI_STORE'],'local'),
               (['environment','WOUNDAI_ENABLE_LITE_API'],'1'),
               (['invocation'],'public'),(['existing_services_may_be_modified'],True),
               (['audit_requirement','locked'],False),(['audit_requirement','retention_seconds'],86400),
               (['audit_requirement','separate_irreversible_approval'],False),
               (['resources','max_instances'],10),
               (['secret_versions','ADMIN_PASSWORD'],'woundai-admin-password:1')]
        for path,value in edits:
            p=copy.deepcopy(original); target=p['spec']
            for k in path[:-1]:target=target[k]
            target[path[-1]]=value
            p['spec_sha256']=hashlib.sha256(canonical(p['spec'])).hexdigest()
            with self.subTest(path=path), self.assertRaises(ValueError):validate(p)

    def test_missing_or_added_runtime_setting_refused(self):
        for remove in [True,False]:
            p=self.plan()
            if remove:p['spec']['environment'].pop('WOUNDAI_INSTITUTION_ORG')
            else:p['spec']['environment']['JWT_SECRET_KEY']='must-not-inline'
            with self.assertRaises(ValueError):validate(p)

    def test_widening_audit_or_security_permissions_refused(self):
        for index in [1,2]:
            p=self.plan();p['spec']['planned_bucket_grants'][index]['permissions'].append('storage.objects.delete')
            p['spec_sha256']=hashlib.sha256(canonical(p['spec'])).hexdigest()
            with self.assertRaises(ValueError):validate(p)
            self.assertNotIn('storage.objects.delete',self.plan()['spec']['planned_bucket_grants'][index]['permissions'])

    def test_deployable_flag_cannot_be_flipped(self):
        p=self.plan();p['deployable']=True
        with self.assertRaises(ValueError):validate(p)


if __name__ == '__main__':
    unittest.main(verbosity=2)
