"""Synthetic-only first-version staging, lost replies and durable recovery."""
import base64
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import google_crc32c
import lite_operational_materials as custody
import lite_runtime_custody as stage


class Cloud:
    def __init__(self, plan):
        self.plan = plan
        self.project_policy = {'version': 3, 'etag': 'project-1', 'bindings': [
            {'role': 'roles/owner', 'members': ['user:' + plan['owner']]}]}
        self.policy = {'version': 3, 'etag': 'empty-1', 'bindings': []}
        self.project = dict(projectId=plan['project'], projectNumber=plan['project_number'])
        self.definitions = {'roles/owner': dict(name='roles/owner', includedPermissions=[
            'secretmanager.versions.access', 'secretmanager.versions.add', 'resourcemanager.projects.setIamPolicy'])}
        self.meta = {r: dict(name=t['name'], replication={'userManaged': {'replicas': [{'location': 'asia-east1'}]}},
                            labels=dict(app='woundailite', stage='owner-sandbox', purpose=r), etag='secret-1', createTime='fixture')
                     for r, t in plan['targets'].items()}
        self.versions = {r: [] for r in stage.ROLES}; self.payloads = {}; self.calls = []
        self.fault = None; self.before = None

    def request(self, method, host, path, body=None):
        self.calls.append((method, host, path))
        if self.before: self.before(method, path)
        if host == 'cloudresourcemanager.googleapis.com':
            return copy.deepcopy(self.project_policy if method == 'POST' else self.project)
        if host == 'iam.googleapis.com': return copy.deepcopy(self.definitions[path[len('/v1/'):]])
        role = next(r for r in stage.ROLES if path.startswith('/v1/' + self.meta[r]['name']))
        suffix = path[len('/v1/' + self.meta[role]['name']):]
        if method == 'GET':
            if suffix == '': return copy.deepcopy(self.meta[role])
            if suffix.startswith(':getIamPolicy'): return copy.deepcopy(self.policy)
            if suffix.startswith('/versions?'): return {'versions': copy.deepcopy(self.versions[role])}
            if suffix == '/versions/1': return copy.deepcopy(self.versions[role][0]) if self.versions[role] else None
        if suffix == ':addVersion':
            if self.fault == 'before-add':
                self.fault = None; raise TimeoutError('synthetic transport interrupted before commit')
            data = base64.b64decode(body['payload']['data'], validate=True)
            if int(body['payload']['dataCrc32c']) != google_crc32c.value(data): raise ValueError('checksum')
            v = dict(name=self.meta[role]['name'] + '/versions/' + str(len(self.versions[role]) + 1),
                     state='ENABLED', etag='version-1', clientSpecifiedPayloadChecksum=True)
            self.versions[role].append(v); self.payloads[role] = data
            if self.fault == 'missing-checksum':
                self.fault = None; v['clientSpecifiedPayloadChecksum'] = False
            if self.fault in ('after-add', 'kill-after-add'):
                fault, self.fault = self.fault, None
                if fault == 'kill-after-add': raise SystemExit(77)
                raise TimeoutError('synthetic reply lost')
            if self.fault == 'wrong-add-reply':
                self.fault = None; return dict(v, etag='wrong')
            return copy.deepcopy(v)
        if suffix == '/versions/1:disable':
            v = self.versions[role][0]
            if v['etag'] != body['etag']: raise ValueError('CAS changed')
            if self.fault == 'disable-unavailable': raise TimeoutError('synthetic disable outage')
            v.update(state='DISABLED', etag='version-2')
            if self.fault == 'disable-reply-lost':
                self.fault = None; raise TimeoutError('synthetic disable reply lost')
            return copy.deepcopy(v)
        raise AssertionError('unexpected endpoint')


def fixture():
    scope = dict(project='synthetic-project', environment='owner-sandbox', namespace='synthetic-native',
                 audience='com.woundai.lite', issuer='https://appleid.apple.com', media_bucket='synthetic-media')
    material = custody.Material(scope, 'a' * 32, 1000,
                                {r: hashlib.sha256(('runtime-custody-fixture-' + r).encode()).digest() for r in custody.ROLES})
    plan = dict(schema='lite-runtime-custody-plan/1', source='b' * 40,
                tool_sha256=custody.sha(Path(stage.__file__).read_bytes()), custody_sha256=custody.sha(Path(custody.__file__).read_bytes()),
                project=scope['project'], project_number='123456789012', region='asia-east1', owner='owner@example.invalid',
                scope=scope, manifest_sha256=stage.digest(material.manifest()), backup_sha256='d' * 64, receipt_sha256='e' * 64,
                project_policy_sha256='f' * 64, iam_review_sha256='1' * 64, created_at=1000, expires_at=2000,
                targets={r: dict(name='projects/123456789012/secrets/' + stage.NAMES[r],
                                 metadata_sha256='2' * 64, policy_sha256='3' * 64) for r in stage.ROLES})
    cloud = Cloud(plan)
    plan['project_policy_sha256'] = stage.digest(cloud.project_policy)
    for r in stage.ROLES:
        plan['targets'][r]['metadata_sha256'] = stage.digest(cloud.meta[r])
        plan['targets'][r]['policy_sha256'] = stage.digest(cloud.policy)
    return material, plan, cloud


class RuntimeCustodyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.path = self.root / 'audit.jsonl'
        self.material, self.plan, self.cloud = fixture(); self.now = 1100
        self.journal = None

    def approval(self, mode='import', **kwargs):
        return dict(schema='lite-runtime-custody-approval/1', plan_sha256=stage.digest(self.plan), mode=mode,
                    approved=True, issued_at=self.now, expires_at=self.now + 600, **kwargs)

    def importer(self, *, create=True, mode='import', transport=None):
        if self.journal: self.journal.close()
        self.journal = stage.Journal(self.path, self.plan, create=create)
        self.addCleanup(self.journal.close)
        return stage.Importer(self.plan, self.approval(mode), self.journal, transport or self.cloud,
                              mode=mode, clock=lambda: self.now)

    def mutations(self, suffix):
        return [c for c in self.cloud.calls if c[0] == 'POST' and c[2].endswith(suffix)]

    def test_three_payloads_match_backup_and_end_disabled(self):
        result = self.importer().stage(self.material)
        self.assertTrue(result['all_three_imported']); self.assertFalse(result['service_enabled'])
        self.assertEqual(self.cloud.payloads, self.material.runtime_payloads())
        self.assertEqual(len(self.mutations(':addVersion')), 3)
        self.assertEqual(len(self.mutations(':disable')), 3)
        self.assertTrue(all(v[0]['state'] == 'DISABLED' for v in self.cloud.versions.values()))

    def test_operator_observer_and_disposition_seeds_never_uploaded(self):
        self.importer().stage(self.material)
        raw = b''.join(self.cloud.payloads.values())
        for r in ('operator', 'observer', 'disposition_operator', 'disposition_drain'):
            self.assertNotIn(self.material.keys[r], raw)
            self.assertNotIn(custody.b64(self.material.keys[r]).encode(), raw)

    def test_audit_contains_no_private_payload_password_or_bearer(self):
        self.importer().stage(self.material); raw = self.path.read_bytes()
        for key in self.material.keys.values():
            self.assertNotIn(key, raw); self.assertNotIn(custody.b64(key).encode(), raw)
        self.assertNotIn(b'payload', raw.replace(b'verified_payloads', b''))
        self.assertNotIn(b'Bearer', raw)

    def test_second_import_cannot_reuse_or_replace_journal(self):
        self.importer().stage(self.material)
        original = self.path.read_bytes()
        with self.assertRaises(FileExistsError): self.importer()
        self.assertEqual(self.path.read_bytes(), original)
        with self.assertRaises(stage.Stop): self.importer(create=False).stage(self.material)
        self.assertEqual(len(self.mutations(':addVersion')), 3)

    def test_lost_add_reply_disables_without_retry_or_claiming_verified(self):
        self.cloud.fault = 'after-add'
        with self.assertRaises(TimeoutError): self.importer().stage(self.material)
        self.assertEqual(len(self.mutations(':addVersion')), 1)
        self.assertEqual(self.cloud.versions['identity'][0]['state'], 'DISABLED')
        result = self.journal.rows[-1]['result']
        self.assertFalse(result['all_three_imported']); self.assertEqual(result['verified_payloads'], 0)
        self.assertFalse(result['unresolved'])

    def test_missing_add_result_stays_unresolved_and_is_never_recreated(self):
        self.cloud.fault = 'before-add'
        with self.assertRaises(TimeoutError): self.importer().stage(self.material)
        result = self.importer(create=False, mode='reconcile').reconcile()
        self.assertEqual(result['unresolved'], ['identity'])
        self.assertEqual(len(self.mutations(':addVersion')), 1)

    def test_late_first_version_is_disabled_on_later_reconciliation(self):
        self.cloud.fault = 'before-add'
        with self.assertRaises(TimeoutError): self.importer().stage(self.material)
        self.cloud.versions['identity'] = [dict(name=self.plan['targets']['identity']['name'] + '/versions/1',
            etag='late', state='ENABLED', clientSpecifiedPayloadChecksum=True)]
        result = self.importer(create=False, mode='reconcile').reconcile()
        self.assertEqual(result['unresolved'], [])
        self.assertEqual(result['verified_payloads'], 0)
        self.assertEqual(self.cloud.versions['identity'][0]['state'], 'DISABLED')
        self.assertEqual(len(self.mutations(':addVersion')), 1)

    def test_disable_lost_reply_is_confirmed_without_second_disable(self):
        self.cloud.fault = 'disable-reply-lost'
        with self.assertRaises(TimeoutError): self.importer().stage(self.material)
        self.assertEqual(len(self.mutations(':disable')), 1)
        self.assertEqual(self.journal.rows[-1]['result']['unresolved'], [])

    def test_disable_outage_is_reported_and_recovers_without_reimport(self):
        self.cloud.fault = 'disable-unavailable'
        with self.assertRaises(TimeoutError): self.importer().stage(self.material)
        self.assertEqual(self.journal.rows[-1]['result']['unresolved'], ['identity'])
        self.cloud.fault = None
        result = self.importer(create=False, mode='reconcile').reconcile()
        self.assertEqual(result['unresolved'], [])
        self.assertEqual(len(self.mutations(':addVersion')), 1)

    def test_expired_import_can_only_receive_new_reconciliation_approval(self):
        self.cloud.fault = 'disable-unavailable'
        with self.assertRaises(TimeoutError): self.importer().stage(self.material)
        self.now = 3000; self.cloud.fault = None
        with self.assertRaises(stage.Stop): stage.authorization(self.plan, self.approval(), 'import', self.now)
        result = self.importer(create=False, mode='reconcile').reconcile()
        self.assertEqual(result['unresolved'], [])

    def test_recovery_cannot_add_or_enable_access_delete_change_iam(self):
        importer = self.importer(mode='reconcile')
        leaf = '/v1/' + self.plan['targets']['identity']['name']
        for method, path in [('POST', leaf + ':addVersion'), ('POST', leaf + '/versions/1:enable'),
                             ('GET', leaf + '/versions/1:access'), ('DELETE', leaf), ('POST', leaf + ':setIamPolicy')]:
            with self.subTest(path=path), self.assertRaises(stage.Stop): importer.call(method, path)
        self.assertEqual(self.cloud.calls, [])

    def test_forward_budget_preserves_eighty_recovery_calls_across_restart(self):
        self.importer()
        for _ in range(stage.FORWARD_CALLS): self.journal.reserve('GET', '/synthetic', recovery=False)
        with self.assertRaises(stage.Stop): self.journal.reserve('GET', '/synthetic', recovery=False)
        self.importer(create=False, mode='reconcile')
        for _ in range(stage.MAX_CALLS - stage.FORWARD_CALLS): self.journal.reserve('GET', '/synthetic', recovery=True)
        with self.assertRaises(stage.Stop): self.journal.reserve('GET', '/synthetic', recovery=True)

    def test_all_three_targets_checked_before_any_upload(self):
        self.cloud.versions['research_issuer'] = [dict(name=self.plan['targets']['research_issuer']['name'] + '/versions/1')]
        with self.assertRaises(stage.Stop): self.importer().stage(self.material)
        self.assertEqual(self.mutations(':addVersion'), [])

    def test_project_policy_drift_prevents_any_upload(self):
        self.cloud.project_policy['etag'] = 'changed'
        with self.assertRaises(stage.Stop): self.importer().stage(self.material)
        self.assertEqual(self.mutations(':addVersion'), [])

    def test_new_ancestor_prevents_upload_even_with_same_project_policy(self):
        self.cloud.project['parent'] = dict(type='organization', id='123456')
        with self.assertRaises(stage.Stop): self.importer().stage(self.material)
        self.assertEqual(self.mutations(':addVersion'), [])

    def test_custom_role_now_granting_payload_access_is_rejected(self):
        role = 'projects/synthetic-project/roles/metadataViewer'
        self.cloud.project_policy['bindings'].append(dict(role=role, members=['serviceAccount:reader@example.invalid']))
        self.plan['project_policy_sha256'] = stage.digest(self.cloud.project_policy)
        self.cloud.definitions[role] = dict(name=role, includedPermissions=['secretmanager.versions.access'])
        with self.assertRaises(stage.Stop): self.importer().stage(self.material)
        self.assertEqual(self.mutations(':addVersion'), [])

    def test_direct_service_access_prevents_any_upload_even_if_digest_matches(self):
        self.cloud.policy['bindings'] = [{'role': 'roles/secretmanager.secretAccessor', 'members': ['allUsers']}]
        for v in self.plan['targets'].values(): v['policy_sha256'] = stage.digest(self.cloud.policy)
        with self.assertRaises(stage.Stop): self.importer().stage(self.material)
        self.assertEqual(self.mutations(':addVersion'), [])

    def test_wrong_region_fails_even_if_snapshot_is_repinned(self):
        self.cloud.meta['identity']['replication']['userManaged']['replicas'][0]['location'] = 'us-central1'
        self.plan['targets']['identity']['metadata_sha256'] = stage.digest(self.cloud.meta['identity'])
        with self.assertRaises(stage.Stop): self.importer().stage(self.material)
        self.assertEqual(self.mutations(':addVersion'), [])

    def test_version_two_never_disabled_by_recovery(self):
        self.cloud.fault = 'before-add'
        with self.assertRaises(TimeoutError): self.importer().stage(self.material)
        self.cloud.versions['identity'] = [dict(name=self.plan['targets']['identity']['name'] + '/versions/2')]
        result = self.importer(create=False, mode='reconcile').reconcile()
        self.assertEqual(result['unresolved'], ['identity']); self.assertEqual(self.mutations(':disable'), [])

    def test_changed_identity_material_never_uploads(self):
        wrong = copy.deepcopy(self.material); wrong.keys['identity'] = secrets.token_bytes(32)
        with self.assertRaises(stage.Stop): self.importer().stage(wrong)
        self.assertEqual(self.cloud.calls, [])

    def test_wrong_add_reply_not_promoted_to_verified_payload(self):
        self.cloud.fault = 'wrong-add-reply'
        with self.assertRaises(stage.Stop): self.importer().stage(self.material)
        self.assertEqual(self.journal.rows[-1]['result']['verified_payloads'], 0)
        self.assertEqual(self.cloud.versions['identity'][0]['state'], 'DISABLED')

    def test_missing_checksum_is_still_disabled_but_never_verified(self):
        self.cloud.fault = 'missing-checksum'
        with self.assertRaises(stage.Stop): self.importer().stage(self.material)
        self.assertEqual(self.journal.rows[-1]['result']['verified_payloads'], 0)
        self.assertEqual(self.cloud.versions['identity'][0]['state'], 'DISABLED')

    def test_wrong_approval_hash_mode_or_expiry_blocks_before_provider(self):
        for field, value in [('plan_sha256', '0' * 64), ('mode', 'reconcile'), ('approved', False), ('expires_at', 1099)]:
            approval = self.approval(); approval[field] = value
            with self.subTest(field=field), self.assertRaises(stage.Stop): stage.authorization(self.plan, approval, 'import', self.now)
        self.assertEqual(self.cloud.calls, [])

    def test_other_roles_or_apple_secret_names_are_rejected(self):
        for mutate in (lambda p: p['targets'].update(operator=copy.deepcopy(p['targets']['identity'])),
                       lambda p: p['targets']['identity'].update(name='projects/123456789012/secrets/woundai-lite-native-sandbox-apple-login')):
            plan = copy.deepcopy(self.plan); mutate(plan)
            with self.assertRaises(stage.Stop): stage.validate(plan)

    def test_journal_lock_blocks_second_process(self):
        self.importer(); plan = self.root / 'plan.json'; plan.write_bytes(custody.canonical(self.plan))
        code = 'import sys,json; from pathlib import Path; from lite_runtime_custody import Journal; Journal(Path(sys.argv[1]),json.load(open(sys.argv[2])),create=False)'
        p = subprocess.run([sys.executable, '-c', code, str(self.path), str(plan)], capture_output=True, timeout=10)
        self.assertNotEqual(p.returncode, 0); self.assertIn(b'BlockingIOError', p.stderr)
        self.assertEqual(self.mutations(':addVersion'), [])

    def test_new_process_reopens_persisted_attempt_without_creating_new_claim(self):
        self.importer(); self.journal.emit('add-intent', role='identity', sha256='a' * 64); self.journal.close()
        plan = self.root / 'plan.json'; plan.write_bytes(custody.canonical(self.plan))
        code = ('import sys,json; from pathlib import Path; from lite_runtime_custody import Journal; '
                'j=Journal(Path(sys.argv[1]),json.load(open(sys.argv[2])),create=False); '
                'print(sum(r["event"]=="add-intent" for r in j.rows)); j.close()')
        p = subprocess.run([sys.executable, '-c', code, str(self.path), str(plan)], capture_output=True, timeout=10)
        self.assertEqual(p.returncode, 0, p.stderr); self.assertEqual(p.stdout.strip(), b'1')

    def test_symlink_partial_or_wrong_plan_journal_is_rejected(self):
        target = self.root / 'target'; target.write_text('private')
        self.path.symlink_to(target)
        with self.assertRaises((OSError, custody.CustodyError)): self.importer(create=False)
        self.path.unlink(); self.path.write_bytes(b'{"partial":'); self.path.chmod(0o600)
        with self.assertRaises(stage.Stop): self.importer(create=False)
        self.path.write_bytes(custody.canonical(dict(event='claimed', plan='0' * 64)) + b'\n')
        with self.assertRaises(stage.Stop): self.importer(create=False)

    def test_provider_does_not_retry_transient_network_error_or_expose_token(self):
        transport = object.__new__(stage.GoogleTransport); transport._token = secrets.token_urlsafe(32)
        with patch.object(stage.http.client, 'HTTPSConnection') as connection:
            connection.return_value.request.side_effect = TimeoutError('SECRET RESPONSE MUST NOT APPEAR')
            with self.assertRaises(stage.Stop) as caught: transport.request('GET', 'secretmanager.googleapis.com', '/v1/test')
        self.assertEqual(connection.return_value.request.call_count, 1)
        self.assertNotIn('SECRET RESPONSE', str(caught.exception)); self.assertNotIn(transport._token, str(caught.exception))

    def test_hard_elapsed_budget_stops_without_provider(self):
        importer = self.importer(); importer.started = -1000; importer.monotonic = lambda: 1000
        with self.assertRaises(stage.Stop): importer.policies()
        self.assertEqual(self.cloud.calls, [])

    def test_full_synthetic_backup_offline_verify_never_connects_or_decrypts(self):
        password = secrets.token_urlsafe(32); backup = custody.seal(self.material, password)
        receipt = custody.canonical(custody.recovery_receipt(backup))
        self.plan.update(backup_sha256=custody.sha(backup), receipt_sha256=custody.sha(receipt))
        files = {}
        for name, raw in [('plan', custody.canonical(self.plan)), ('backup', backup), ('receipt', receipt)]:
            p = self.root / (name + '.json'); p.write_bytes(raw); p.chmod(0o600); files[name] = p
        with patch.object(stage, 'GoogleTransport', side_effect=AssertionError('no cloud')), \
             patch.object(custody, 'restore', side_effect=AssertionError('no decryption')), \
             patch('sys.stdout', new_callable=io.StringIO) as out:
            stage.main(['verify', '--plan', str(files['plan']), '--plan-sha256', stage.digest(self.plan),
                        '--backup', str(files['backup']), '--receipt', str(files['receipt'])])
        self.assertEqual(json.loads(out.getvalue())['cloud_calls'], 0)
        self.assertFalse(json.loads(out.getvalue())['decrypted'])

    def test_corrupt_encrypted_backup_or_receipt_rejected_without_password(self):
        backup = custody.seal(self.material, secrets.token_urlsafe(32))
        receipt = custody.canonical(custody.recovery_receipt(backup))
        self.plan.update(backup_sha256=custody.sha(backup), receipt_sha256=custody.sha(receipt))
        for b, r in [(backup + b' ', receipt), (backup, receipt + b' '), (receipt, backup)]:
            with self.assertRaises(stage.Stop): stage.verify_backup(self.plan, b, r)


if __name__ == '__main__': unittest.main()
