"""B1 preflight rejects stale/incomplete evidence and an already different scope."""
import unittest

from plan_lite_review_human import APP_COMMIT, IMAGE, IAP, MMH_IAP, DEMO_IAP, NUMBER, make_plan


def evidence():
    a7 = dict(status='phase A closed negative-access acceptance passed', source_commit=APP_COMMIT,
              image_digest=IMAGE, http_rejections=11, actual_identity_bucket_permission_checks=16,
              final_readonly_assertions=86, temporary_grants_removed=3)
    boundary = dict(at='2026-10-25T00:00:00+00:00', cloud_mutations=0, research_object_reads=0,
                    project_parent=None, access_audit_bucket={'http': 404},
                    owner_discovery=dict(http=200, email_matches_approved_owner=True,
                                         verified_email=True, subject_sha256='c'*64),
                    settings={k: dict(http=200, has_custom_client=False, digest='d'*64)
                              for k in (f'projects/{NUMBER}', f'projects/{NUMBER}/iap_web', IAP, MMH_IAP, DEMO_IAP)},
                    policies={k: dict(http=200, binding_count=0, digest='e'*64) for k in (IAP, MMH_IAP, DEMO_IAP)})
    return a7, boundary


class HumanPlan(unittest.TestCase):
    def setUp(self):
        self.a7, self.boundary = evidence()
        from datetime import datetime
        self.now = int(datetime.fromisoformat(self.boundary['at']).timestamp())

    def build(self):
        return make_plan('a'*40, {'source': 'b'*64}, self.a7, self.boundary, self.now)

    def test_b1_does_not_grant_source_access_or_add_data(self):
        value = self.build()
        self.assertFalse(value['execution_authorized'])
        self.assertFalse(value['deployable'])
        self.assertEqual(value['source_storage']['new_grants'], [])
        self.assertTrue(all(value['source_storage'][key] is False for key in ('list', 'get', 'write', 'delete')))
        self.assertEqual(value['configuration']['catalogue_records'], [])
        self.assertEqual(value['human']['roles'], ['lite.review.metadata'])
        self.assertEqual(value['audit']['runtime_permissions'], ['storage.objects.create'])
        self.assertEqual(value['oauth']['settings_resource'], IAP)
        self.assertFalse(value['oauth']['project_branding_or_test_users_change'])
        self.assertFalse(value['services']['allUsers'])

    def test_wrong_application_or_partial_a7_stops_before_plan(self):
        for key, value in (('source_commit', 'f'*40), ('image_digest', 'sha256:'+'f'*64),
                           ('http_rejections', 10), ('actual_identity_bucket_permission_checks', 15),
                           ('final_readonly_assertions', 85), ('temporary_grants_removed', 2)):
            with self.subTest(key=key):
                original = self.a7[key]; self.a7[key] = value
                with self.assertRaises(ValueError): self.build()
                self.a7[key] = original

    def test_unverified_or_other_owner_cannot_get_role(self):
        for key, value in (('verified_email', False), ('email_matches_approved_owner', False),
                           ('http', 401), ('subject_sha256', 'accounts.google.com:123')):
            with self.subTest(key=key):
                owner = self.boundary['owner_discovery']; original = owner[key]; owner[key] = value
                with self.assertRaises(ValueError): self.build()
                owner[key] = original

    def test_snapshot_expiry_and_future_are_not_silently_refreshed(self):
        self.now += 86401
        with self.assertRaises(ValueError): self.build()
        self.now -= 86402
        with self.assertRaises(ValueError): self.build()

    def test_shared_or_existing_oauth_config_blocks_adoption(self):
        for scope in (f'projects/{NUMBER}', f'projects/{NUMBER}/iap_web', IAP):
            with self.subTest(scope=scope):
                self.boundary['settings'][scope]['has_custom_client'] = True
                with self.assertRaises(ValueError): self.build()
                self.boundary['settings'][scope]['has_custom_client'] = False

    def test_incomplete_baseline_does_not_authorize_modification(self):
        self.boundary['settings']['unrelated-MMH'] = dict(http=403, digest='f'*64)
        with self.assertRaises(ValueError): self.build()

    def test_bucket_collision_and_existing_human_grant_require_new_review(self):
        self.boundary['access_audit_bucket']['http'] = 200
        with self.assertRaises(ValueError): self.build()
        self.boundary['access_audit_bucket']['http'] = 404
        self.boundary['policies'][IAP]['binding_count'] = 1
        with self.assertRaises(ValueError): self.build()

    def test_changed_organization_or_nonreadonly_discovery_stops(self):
        for key, value in (('project_parent', 'organizations/123'), ('cloud_mutations', 1), ('research_object_reads', 1)):
            with self.subTest(key=key):
                original = self.boundary[key]; self.boundary[key] = value
                with self.assertRaises(ValueError): self.build()
                self.boundary[key] = original

    def test_plan_does_not_disclose_raw_subject_or_any_secret(self):
        from plan_lite_review_human import canonical
        self.boundary['owner_discovery']['private_subject'] = 'accounts.google.com:987654321'
        self.boundary['owner_discovery']['secret'] = 'not-for-the-packet'
        raw = canonical(self.build())
        self.assertNotIn(b'987654321', raw)
        self.assertNotIn(b'not-for-the-packet', raw)

    def test_missing_source_pin_rejected(self):
        for commit, files in (('HEAD', {'a': 'b'*64}), ('a'*40, {}), ('a'*40, {'a': 'bad'})):
            with self.subTest(commit=commit), self.assertRaises(ValueError):
                make_plan(commit, files, self.a7, self.boundary, self.now)

    def test_missing_mmh_or_demo_baseline_cannot_be_called_isolated(self):
        for section in ('settings', 'policies'):
            for scope in (MMH_IAP, DEMO_IAP):
                with self.subTest(section=section, scope=scope):
                    value = self.boundary[section].pop(scope)
                    with self.assertRaises(ValueError): self.build()
                    self.boundary[section][scope] = value

    def test_existing_service_scoped_mmh_oauth_is_preserved(self):
        self.boundary['settings'][MMH_IAP]['has_custom_client'] = True
        self.assertFalse(self.build()['oauth']['reuse_mmh_client'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
