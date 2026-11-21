"""Exercise the private offline IAM command from the existing owner enrollment fixture."""
import copy
from datetime import datetime
import io
import os
import sqlite3
from contextlib import redirect_stderr, redirect_stdout
import unittest
from unittest.mock import patch

import lite_owner_enrollment_iam as iam
import test_lite_owner_enrollment_command as enrollment
from lite_operational_materials import canonical, read_private, sha


class OwnerPolicyTests(unittest.TestCase):
    def setUp(self):
        fixture = enrollment.EnrollmentCommandTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        _, self.scope = fixture.prepare()
        self.scope['window_state'] = 'open'
        self.root = fixture.root
        self.now = self.scope['starts']
        self.policy = dict(version=1, etag='etag-0', bindings=[
            dict(role='roles/storage.legacyBucketOwner', members=['projectOwner:synthetic-only'])])
        self.role = dict(status=200, body=dict(name='projects/synthetic-only/roles/woundaiLiteSecurityObjects',
                         stage='GA', includedPermissions=sorted(iam.BUCKET_READ + iam.OBJECT_ACCESS)))
        self.observation = dict(status=200, requested_policy_version=3, bucket=self.scope['bucket'], body=self.policy)

    def grant(self, *, scope=None, observed=None, role=None, now=None, etag='etag-0', pin=None):
        raw = canonical(self.scope if scope is None else scope)
        return iam.draft(raw, pin or sha(raw), self.observation if observed is None else observed, etag,
                         role=self.role if role is None else role, clock=lambda: self.now if now is None else now)

    def recover(self, grant, policy):
        raw = canonical(self.scope)
        original = canonical(grant)
        return iam.draft(raw, sha(raw), dict(self.observation, body=policy), policy['etag'],
                         original=(original, sha(original)), clock=lambda: self.scope['until'] + 86400)

    def test_time_and_resource_truth_table_for_generated_condition(self):
        grant = self.grant()
        binding, = grant['receipt']['added']
        self.assertEqual(binding['members'], ['serviceAccount:' + self.scope['service_account']])
        self.assertEqual(binding['role'], self.role['body']['name'])
        self.assertNotIn('storage.objects.list', self.role['body']['includedPermissions'])
        # Evaluate this bounded CEL subset with named parameters and a timestamp function.
        expression = binding['condition']['expression']
        sql = expression.replace('&&', ' AND ').replace('||', ' OR ')
        sql = sql.replace('resource.type', ':kind').replace('resource.name', ':name').replace('request.time', ':now')
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.create_function('timestamp', 1, lambda s: int(datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp()))
        bucket = 'projects/_/buckets/' + self.scope['bucket']
        objects = [bucket + '/objects/' + s for s in self.scope['objects']]
        for when in (self.now-1, self.now, self.scope['until']-1, self.scope['until']):
            for kind in ('Bucket', 'Object'):
                for name in (bucket, *objects, objects[0] + '.other', bucket + '/objects/foreign', bucket + '-other'):
                    expected = self.now <= when < self.scope['until'] and (
                        kind == 'Bucket' and name == bucket or kind == 'Object' and name in objects)
                    with self.subTest(time=when, kind=kind, name=name):
                        allowed, = db.execute('SELECT ' + sql, dict(now=when, kind='storage.googleapis.com/' + kind,
                                                                    name=name)).fetchone()
                        self.assertEqual(bool(allowed), expected)

    def test_requires_existing_exact_enabled_role(self):
        body = self.role['body']
        for bad in (dict(status=403, body=None), dict(status=404, body=None),
                    dict(status=200, body=dict(body, deleted=True)), dict(status=200, body=dict(body, stage='DISABLED')),
                    dict(status=200, body=dict(body, includedPermissions=body['includedPermissions'] + ['storage.objects.list'])),
                    dict(status=200, body=dict(body, name='roles/storage.admin'))):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.grant(role=bad)

    def test_private_scope_hash_exact_object_paths_and_limited_window(self):
        with self.assertRaises(ValueError):
            self.grant(pin='0'*64)
        security, budget = self.scope['objects']
        for changes in (dict(project='wrong'), dict(bucket=self.scope['bucket'] + '-foreign'),
                        dict(service_account='anyone@example.invalid'), dict(region='other'),
                        dict(objects=[security + '*', budget]), dict(objects=[security, budget.replace('/owner/', '/x/')]),
                        dict(objects={security: True, budget: True}), dict(objects=[budget, security]), dict(until=self.now+901), dict(starts=True),
                        dict(starts=86399, until=86401), dict(object_permissions=['storage.objects.list'])):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.grant(scope=dict(self.scope, **changes))
        with self.assertRaises(ValueError):
            self.grant(now=self.scope['until'])

    def test_requires_current_etag_and_v3_readback_for_exact_bucket(self):
        with self.assertRaises(ValueError):
            self.grant(etag='stale')
        for changes in (dict(status=403), dict(bucket='other'), dict(requested_policy_version=1),
                        dict(body=dict(self.policy, resourceId='projects/_/buckets/other')),
                        dict(body=dict(self.policy, bindings=[dict(role='roles/test_withcond_hash', members=[])]))):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.grant(observed=dict(self.observation, **changes))

    def test_public_or_preexisting_principal_grants_and_title_conflicts_stop(self):
        binding, = self.grant()['receipt']['added']
        peer = 'user:peer@example.invalid'
        for existing in ([dict(role='roles/viewer', members=['allUsers'])],
                         [dict(role='roles/viewer', members=binding['members'])],
                         [dict(binding, members=[peer])], [binding, binding]):
            with self.subTest(existing=existing), self.assertRaises(ValueError):
                self.grant(observed=dict(self.observation, body=dict(self.policy, version=3, bindings=existing)))

    def test_lost_write_response_preserves_peer_changes_and_recovery_replays(self):
        grant = self.grant()
        self.assertEqual(grant['policy']['etag'], 'etag-0')
        committed = copy.deepcopy(grant['policy'])
        peer = dict(role='roles/viewer', members=['user:peer@example.invalid'])
        committed['bindings'].append(peer)
        committed.update(etag='etag-2', auditConfigs=[dict(service='storage.googleapis.com')])
        recovery = self.recover(grant, committed)
        self.assertEqual(recovery['recovery']['removed'], 1)
        self.assertEqual(recovery['policy'], dict(committed, bindings=self.policy['bindings'] + [peer]))
        replay = self.recover(grant, dict(recovery['policy'], etag='etag-3'))
        self.assertEqual(replay['recovery']['removed'], 0)
        self.assertEqual(replay['policy']['etag'], 'etag-3')
        never_applied = self.recover(grant, self.policy)
        self.assertEqual(never_applied['recovery']['removed'], 0)
        self.assertEqual(never_applied['policy'], self.policy)
        self.assertEqual(never_applied['recovery']['before'], never_applied['recovery']['after'])

    def test_preexisting_identical_grant_is_retained(self):
        existing = dict(self.grant()['policy'], etag='etag-1')
        grant = self.grant(observed=dict(self.observation, body=existing), etag='etag-1')
        self.assertEqual(grant['receipt']['added'], [])
        self.assertEqual(self.recover(grant, existing)['policy'], existing)

    def test_changed_grant_or_wrong_recovery_receipt_stops(self):
        grant = self.grant()
        changed = copy.deepcopy(grant['policy'])
        changed['bindings'][-1]['members'].append('user:peer@example.invalid')
        with self.assertRaises(ValueError):
            self.recover(grant, changed)
        for changes in (dict(added=self.policy['bindings']), dict(principal='other'),
                        dict(schema='lite-bootstrap-iam-delta/1'), dict(proposal='0'*64)):
            altered = dict(grant, receipt=dict(grant['receipt'], **changes))
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.recover(altered, self.policy)

    @unittest.skipUnless(os.name == 'posix', 'private-file interface requires POSIX')
    def test_operator_command_writes_exclusive_private_files_and_redacts_identifiers(self):
        for name, value in [('scope', self.scope), ('observed', self.observation), ('role', self.role)]:
            path = self.root / name
            path.write_bytes(canonical(value))
            path.chmod(0o600)
        common = ['--scope', str(self.root/'scope'), '--scope-sha256', sha(canonical(self.scope)),
                  '--policy', str(self.root/'observed'), '--etag', self.policy['etag']]
        out = self.root/'grant'
        args = ['grant', *common, '--role', str(self.root/'role'), '--output', str(out)]
        with redirect_stdout(io.StringIO()) as stdout, redirect_stderr(io.StringIO()) as stderr:
            with patch.object(iam.time, 'time', return_value=self.now):
                self.assertEqual(iam.main(args), 0)
                self.assertEqual(iam.main(args), 1)
            original = read_private(out)
            self.assertEqual(out.stat().st_mode & 0o777, 0o600)
            with patch.object(iam.time, 'time', return_value=self.scope['until'] + 1):
                reverse = ['recover', *common, '--original', str(out), '--original-sha256', sha(original),
                           '--output', str(self.root/'recovery')]
                self.assertEqual(iam.main(reverse), 0)
            self.assertNotIn(self.scope['objects'][0], stdout.getvalue() + stderr.getvalue())
            self.assertEqual(read_private(out), original)
