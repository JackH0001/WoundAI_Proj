"""The MMH exception is a separate exact plan; formal defaults remain strict."""
import copy
import hashlib
import tempfile
from pathlib import Path
import unittest

import create_mmh_private_service as creator
import check_mmh_runtime_preflight as pre
from plan_mmh_runtime import generate, validate, canonical
from test_check_mmh_runtime_preflight import plan, FakeCloud, COMMIT, BUILD
from test_create_mmh_private_service import GrantsCloud


def unlocked_plan():
    p = plan()['spec']
    return generate(p['image'], p['source_commit'], p['manifest_sha256'],
                    {k: '1' for k in pre.SECRET_NAMES}, 'mmh-unlocked-validation')


def no_retention(args, row):
    if args[:3] == ['storage', 'buckets', 'describe']:
        row.pop('retentionPolicy', None)
    return row


class DeploymentTests(unittest.TestCase):
    def test_modes_have_different_hashes_and_exact_contracts(self):
        p = unlocked_plan()
        self.assertTrue(validate(p))
        self.assertNotEqual(p['spec_sha256'], plan()['spec_sha256'])
        self.assertFalse(p['spec']['audit_requirement']['locked'])
        self.assertFalse(p['spec']['audit_requirement']['immutable_evidence'])
        self.assertEqual(p['spec']['audit_requirement']['retention_seconds'], 0)
        self.assertTrue(plan()['spec']['audit_requirement']['locked'])
        self.assertNotIn('WOUNDAI_AUDIT_MODE', plan()['spec']['environment'])

    def test_unknown_mode_rejected(self):
        spec = plan()['spec']
        for mode in ('unlocked', '', None, 'MMH-unlocked-validation'):
            with self.assertRaises(ValueError):
                generate(spec['image'], COMMIT, spec['manifest_sha256'],
                         {k: '1' for k in pre.SECRET_NAMES}, mode)

    def test_rehashed_weakened_plan_is_not_accepted(self):
        for change in (
            lambda s: s['environment'].update(WOUNDAI_AUDIT_MODE='unlocked'),
            lambda s: s['environment'].update(GOOGLE_CLOUD_PROJECT='other'),
            lambda s: s['environment'].update(WOUNDAI_INSTITUTION_ORG='other'),
            lambda s: s['environment'].update(WOUNDAI_SERVICE_PROFILE='lite'),
            lambda s: s['environment'].update(WOUNDAI_GCS_BUCKET='old-bucket'),
            lambda s: s.update(invocation='public'),
            lambda s: s['audit_requirement'].update(locked=True),
            lambda s: s['audit_requirement'].update(retention_seconds=86400),
            lambda s: s['planned_bucket_grants'][2]['permissions'].append('storage.objects.delete'),
        ):
            p = unlocked_plan(); change(p['spec'])
            p['spec_sha256'] = hashlib.sha256(canonical(p['spec'])).hexdigest()
            with self.assertRaises(ValueError): validate(p)

    def test_unlocked_metadata_green_formal_rejects_same_cloud(self):
        cloud = FakeCloud(); cloud.edit = no_retention
        p = pre.inspect(unlocked_plan(), BUILD, cloud, lambda: COMMIT)
        self.assertTrue(p['metadata_passed'])
        self.assertEqual(p['passed'], 13)
        self.assertFalse(p['deployable'])
        self.assertFalse(pre.inspect(plan(), BUILD, cloud, lambda: COMMIT)['metadata_passed'])

    def test_locked_or_partial_policy_is_not_an_automatic_upgrade(self):
        for policy in ({'isLocked': True, 'retentionPeriod': '220903200'},
                       {'isLocked': False, 'retentionPeriod': '220903200'}, {'isLocked': 'false'}):
            c = FakeCloud()
            c.edit = lambda a, r: dict(r, retentionPolicy=policy) if a[:3] == ['storage', 'buckets', 'describe'] else r
            self.assertFalse(pre.inspect(unlocked_plan(), BUILD, c, lambda: COMMIT)['metadata_passed'])

    def test_exception_still_requires_merged_source(self):
        c = FakeCloud(); c.edit = no_retention
        self.assertFalse(pre.inspect(unlocked_plan(), BUILD, c, lambda: 'f'*40)['metadata_passed'])

    def test_private_payload_contains_explicit_mode_and_unchanged_isolation(self):
        p = creator.payload(unlocked_plan())
        env = {v['name']: v.get('value') for v in p['template']['containers'][0]['env']}
        self.assertEqual(env['WOUNDAI_AUDIT_MODE'], 'mmh-unlocked-validation')
        self.assertEqual(env['GOOGLE_CLOUD_PROJECT'], 'woundai-jackh001')
        self.assertNotIn('K_SERVICE', env)  # injected by Cloud Run, not a spoofed user variable
        self.assertFalse(p['invokerIamDisabled'])
        self.assertEqual(p['template']['serviceAccount'], creator.SA)

    def test_create_path_keeps_iam_and_second_readback(self):
        for permission_ok in (False, True):
            with tempfile.TemporaryDirectory() as tmp:
                c = GrantsCloud(); c.edit = no_retention
                posts = []; effective = []
                def iam(*args):
                    effective.append(1)
                    return permission_ok
                def api(*args):
                    posts.append(args)
                    return {'name': creator.PARENT+'/operations/synthetic'}
                args = dict(confirm_sha=unlocked_plan()['spec_sha256'], run=c, main=lambda: COMMIT,
                            effective=iam, api=api, empty=lambda: True)
                if permission_ok:
                    result = creator.execute(unlocked_plan(), BUILD, Path(tmp)/'journal', **args)
                    self.assertEqual(result['state'], 'submitted_not_accepted')
                    self.assertEqual(len(posts), 1)
                else:
                    with self.assertRaises(ValueError):
                        creator.execute(unlocked_plan(), BUILD, Path(tmp)/'journal', **args)
                    self.assertEqual(posts, [])
                self.assertEqual(effective, [1])


if __name__ == '__main__':
    unittest.main(verbosity=2)
