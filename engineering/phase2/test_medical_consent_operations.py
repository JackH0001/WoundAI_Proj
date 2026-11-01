"""Predeployment operations: synthetic data only, local + fake GCS SDK boundary."""
import concurrent.futures
import hashlib
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path, PureWindowsPath
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'Backend/Flask'))
from store import LocalStore, ImmutableConflict
from medical_consent_order import (ConsentOrder, ConsentOrderError, ROOT, EVENTS, IMAGE_INDEX,
    INDEX_MARKER, MEDIA_OWNERS, MEDIA_IMAGE_OWNERS, subject, record_media_owner)
from medical_consent_migration import plan_migration, apply_migration, snapshot, MIGRATIONS
from medical_consent_maintenance import cleanup_run, RUNS, main
from medical_media_cleanup import cleanup_subject, inventory
from medical_fake_gcs import make_store

POLICY = dict(id='synthetic-only-zero-delay', retention_seconds=0)
IID = 'a'*16
CODE = 'WD-SYNTHETIC'


def order(store): return ConsentOrder(store, lambda: [], now=lambda: 100)
def event(o, action='withdraw', intent='withdraw-synthetic-001', revision=None, iid=IID):
    return o.apply(CODE, 'default', 'default:doctor', action, intent, revision, [iid])
def raw_snapshot(st):
    return {str(p.relative_to(st.root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in Path(st.root).rglob('*') if p.is_file()}


class Migration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.st = LocalStore(self.tmp.name); self.o = order(self.st)
        event(self.o); self.st.put_blob('images/'+IID+'.jpg', b'original synthetic RGB')
        self.st.put_blob('depth_maps/'+IID+'.f32', b'original synthetic depth')
        Path(self.st._p(INDEX_MARKER)).unlink()
    def apply(self, plan=None, **kw):
        plan = plan or plan_migration(self.o)
        return apply_migration(self.o, plan, plan['sha256'], writers_paused=True, **kw)
    def test_dry_plan_does_not_write_or_modify_originals(self):
        before = raw_snapshot(self.st); p = plan_migration(self.o)
        self.assertTrue(p['apply_allowed']); self.assertEqual(before, raw_snapshot(self.st))
    def test_windows_relative_keys_produce_same_plan_and_preserve_originals(self):
        # Real files/store reads, with only the platform's relative-path spelling
        # substituted. Preserve the concrete class before patching Path (3.11).
        host_path = type(Path(self.st.root))
        class WindowsSpelling:
            def __init__(self, value): self.path = host_path(value)
            def __getattr__(self, key): return getattr(self.path, key)
            def __lt__(self, other): return self.path < other.path
            def rglob(self, pattern):
                return (WindowsSpelling(p) for p in self.path.rglob(pattern))
            def relative_to(self, root):
                return PureWindowsPath(*self.path.relative_to(root).parts)
        expected = plan_migration(self.o); before = raw_snapshot(self.st)
        with patch('pathlib.Path', side_effect=WindowsSpelling):
            actual = plan_migration(self.o)
            self.assertEqual(actual, expected)
            result = self.apply(actual)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(before, {k: raw_snapshot(self.st)[k] for k in before})
    def test_apply_additive_marker_last_originals_preserved_replay_identical(self):
        before = raw_snapshot(self.st); p = plan_migration(self.o); writes = []
        original = self.st.put_json_immutable
        def traced(key, value): writes.append(key); return original(key, value)
        with patch.object(self.st, 'put_json_immutable', side_effect=traced): result = self.apply(p)
        self.assertEqual(writes[-1], INDEX_MARKER)
        self.assertEqual(result['owners'], 1)
        self.assertEqual(before, {k: raw_snapshot(self.st)[k] for k in before})
        after = raw_snapshot(self.st); self.assertEqual(self.apply(p), result)
        self.assertEqual(after, raw_snapshot(self.st))
    def test_no_pause_no_plan_or_wrong_digest_refused(self):
        p = plan_migration(self.o)
        with self.assertRaises(ValueError): apply_migration(self.o, p, p['sha256'])
        with self.assertRaises(ValueError): apply_migration(self.o, p, '0'*64, writers_paused=True)
        with self.assertRaises(ValueError): apply_migration(self.o, None, None, writers_paused=True)
        self.assertFalse(self.st.exists(INDEX_MARKER))
    def test_tampered_or_stale_plan_no_marker(self):
        p = plan_migration(self.o); p['owners'][0]['org'] = 'other'
        with self.assertRaises(ValueError): self.apply(p)
        p = plan_migration(self.o); self.st.put_blob('images/'+'b'*16+'.jpg', b'unresolved')
        with self.assertRaises(ValueError): self.apply(p)
        self.assertFalse(self.st.exists(INDEX_MARKER))
    def test_in_place_media_change_invalidates_plan(self):
        p = plan_migration(self.o)
        self.st.put_blob('images/'+IID+'.jpg', b'changed synthetic bytes same key')
        with self.assertRaises(ValueError): self.apply(p)
        self.assertFalse(self.st.exists(INDEX_MARKER))
    def test_unknown_media_never_guesses_owner_or_deletes(self):
        self.st.put_blob('staging/'+'b'*16+'.jpg', b'unknown')
        before = raw_snapshot(self.st); p = plan_migration(self.o)
        self.assertFalse(p['apply_allowed']); self.assertEqual(p['blockers'][0]['reason'], 'unresolved media owner')
        with self.assertRaises(ValueError): self.apply(p)
        self.assertEqual(before, raw_snapshot(self.st))
    def test_partial_owner_publish_resume(self):
        p = plan_migration(self.o); original = self.st.put_json_immutable
        def fail(key, value):
            if key.startswith(MEDIA_OWNERS+'/'): raise IOError('synthetic interruption')
            return original(key, value)
        with patch.object(self.st, 'put_json_immutable', side_effect=fail), self.assertRaises(IOError): self.apply(p)
        self.assertFalse(self.st.exists(INDEX_MARKER)); self.assertEqual(self.apply(p)['status'], 'completed')
    def test_input_change_during_apply_prevents_marker(self):
        p = plan_migration(self.o); original = self.st.put_json_immutable
        def fail(key, value):
            result = original(key, value)
            if key.startswith(MEDIA_OWNERS+'/'): self.st.put_blob('images/'+'c'*16+'.jpg', b'late writer')
            return result
        with patch.object(self.st, 'put_json_immutable', side_effect=fail), self.assertRaises(ValueError): self.apply(p)
        self.assertFalse(self.st.exists(INDEX_MARKER))
    def test_corrupt_subject_or_orphan_event_blocks_migration_only(self):
        Path(self.st._p(self.o._slot(CODE, 1))).write_text('{bad')
        with self.assertRaises(ValueError): plan_migration(self.o)
        self.assertFalse(self.st.exists(INDEX_MARKER))
    def test_extra_reverse_reference_is_not_silently_certified(self):
        self.st.put_json_immutable(IMAGE_INDEX+'/'+'b'*16+'/'+subject('WD-ORPHAN')+'.json',
                                   dict(code='WD-ORPHAN', org='default', image_id='b'*16))
        with self.assertRaisesRegex(ValueError, 'unsupported'): self.apply()
        self.assertFalse(self.st.exists(INDEX_MARKER))
    def test_uncommitted_root_not_certified(self):
        Path(self.st._p(self.o._slot(CODE, 1))).unlink()
        with self.assertRaisesRegex(ValueError, 'uncommitted'): plan_migration(self.o)
    def test_orphan_event_not_certified(self):
        Path(self.st._p(ROOT+'/'+subject(CODE)+'.json')).unlink()
        with self.assertRaisesRegex(ValueError, 'orphan'): plan_migration(self.o)
    def test_conflicting_owner_receipt_not_overwritten(self):
        path = MEDIA_OWNERS+'/'+subject(CODE)+'/'+IID+'.json'
        self.st.put_json_immutable(path, dict(schema='medical-media-owner/1', code=CODE, org='other', image_id=IID))
        before = raw_snapshot(self.st)
        with self.assertRaisesRegex(ValueError, 'conflicting'): plan_migration(self.o)
        self.assertEqual(before, raw_snapshot(self.st))
    def test_legacy_only_subject_requires_separate_disposition(self):
        old = ConsentOrder(self.st, lambda: [dict(code='WD-LEGACY', action='withdraw', image_ids=[])])
        with self.assertRaisesRegex(ValueError, 'unsealed'): plan_migration(old)
    def test_symlink_media_directory_rejected(self):
        Path(self.st._p('staging')).symlink_to(self.tmp.name, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'): plan_migration(self.o)
    def test_cli_requires_digest_before_getting_store(self):
        with patch('medical_consent_maintenance.get_store') as get, self.assertRaises(SystemExit):
            main(['index-backfill', '--apply', '--writers-paused'])
        get.assert_not_called()
    def test_protected_output_create_only_and_readonly(self):
        out = Path(self.tmp.name)/'plan-evidence.json'
        with patch('medical_consent_maintenance.get_store', return_value=self.st):
            self.assertEqual(main(['index-backfill', '--output', str(out)]), 0)
            self.assertEqual(out.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(SystemExit): main(['index-backfill', '--output', str(out)])


class HistoricalStaging(unittest.TestCase):
    def setUp(self):
        from test_p0_4_staging import StagingTests
        self.fixture = StagingTests(); self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        self.fixture.stage()
        self.st, self.keys = self.fixture.st, self.fixture.keys
        self.o = order(self.st)
        # Simulate pre-owner-index staging; only synthetic receipts are removed.
        for base in (MEDIA_OWNERS, MEDIA_IMAGE_OWNERS):
            for p in Path(self.st._p(base)).rglob('*.json'): p.unlink()
    def test_valid_care_binding_backfills_owner_without_consent(self):
        p = plan_migration(self.o, self.keys); self.assertTrue(p['apply_allowed']); self.assertEqual(p['states'], [])
        apply_migration(self.o, p, p['sha256'], writers_paused=True, care_keys=self.keys)
        self.assertEqual(self.o.owned_images(self.fixture.code, self.fixture.org)[0], {self.fixture.iid})
        self.assertEqual(self.o.state(self.fixture.code, self.fixture.org)['sequence'], 0)
        self.assertTrue(self.st.exists('staging/'+self.fixture.iid+'.jpg'))
    def test_missing_key_blocks_and_preserves(self):
        before = raw_snapshot(self.st); p = plan_migration(self.o)
        self.assertFalse(p['apply_allowed']); self.assertEqual(raw_snapshot(self.st), before)
    def test_tampered_binding_blocks(self):
        path = Path(self.st._p('staging_meta/bind/'+self.fixture.iid+'.json'))
        data = json.loads(path.read_text()); data['subject_binding'] = '0'*64; path.write_text(json.dumps(data))
        self.assertFalse(plan_migration(self.o, self.keys)['apply_allowed'])
    def test_missing_care_does_not_fall_back_to_queue(self):
        for p in Path(self.st._p('receipts/care')).glob('*.json'): p.unlink()
        self.assertFalse(plan_migration(self.o, self.keys)['apply_allowed'])
    def test_imageless_root_still_fixes_institution_scope(self):
        self.o.apply(self.fixture.code, 'other', 'other:doctor', 'withdraw', 'withdraw-other-org-001', None, [])
        p = plan_migration(self.o, self.keys)
        self.assertFalse(p['apply_allowed'])
        self.assertIn('conflicting subject institution', [b['reason'] for b in p['blockers']])


class GCSBoundary(unittest.TestCase):
    def setUp(self):
        self.st = make_store(); self.o = order(self.st); self.c = self.st._client
        self.receipt = event(self.o)
        self.key = 'images/'+IID+'.jpg'
        self.st._bucket.blob(self.st._k(self.key)).upload_from_string(b'old generation')
        self.st._bucket.blob(self.st._k(self.key)).upload_from_string(b'new generation')
    def clean(self): return cleanup_subject(self.o, CODE, 'default', POLICY, apply=True, now=101)
    def audit(self): return {k: v['data'] for k, v in self.c.rows.items() if k[0] == 'fake-audit'}
    def test_gcs_migration_production_adapter_and_cas(self):
        p = plan_migration(self.o); before = dict(self.c.rows)
        result = apply_migration(self.o, p, p['sha256'], writers_paused=True)
        self.assertEqual(result['status'], 'completed')
        for k, v in before.items(): self.assertEqual(self.c.rows[k], v)
        self.assertTrue(all(pre == 0 for b, n, pre in self.c.uploads if b == 'fake-audit'))
    def test_all_generations_deleted_only_with_pin_audit_unchanged(self):
        before = self.audit(); self.assertEqual(len(inventory(self.st, IID)), 2)
        self.assertEqual(self.clean()['status'], 'media_versions_absent_at_check')
        self.assertFalse(inventory(self.st, IID)); self.assertEqual(len(self.c.deletes), 2)
        self.assertTrue(all(k[2] == pin for k, pin in self.c.deletes))
        self.assertEqual(before, {k: self.audit()[k] for k in before})
    def test_soft_versions_survive_policy_disabled_when_inventory_available(self):
        self.st._bucket._properties = {'softDeletePolicy': {'retentionDurationSeconds': '604800'}}
        self.assertEqual(self.clean()['status'], 'recoverable_media_pending')
        self.st._bucket._properties = {'softDeletePolicy': {'retentionDurationSeconds': '0'}}
        self.assertEqual(self.clean()['status'], 'recoverable_media_pending')
        self.assertEqual(len(inventory(self.st, IID, soft_deleted=True)), 2)
    def test_soft_inventory_denied_never_claims_complete(self):
        def deny(bucket, prefix, versions, soft):
            if soft: raise PermissionError('synthetic denied soft list')
        self.c.before_list = deny
        with self.assertRaises(PermissionError): self.clean()
        self.assertFalse(any(k[1].endswith('outcome.json') for k in self.c.rows))
        self.assertEqual(self.o.state(CODE, 'default')['sequence'], 1)
        self.assertEqual(len(inventory(self.st, IID)), 2)
    def test_real_gcs_policy_zero_error_blocks_claim_and_delete(self):
        from google.api_core.exceptions import BadRequest
        before = self.audit()
        def reject(bucket, prefix, versions, soft):
            if soft: raise BadRequest('Soft delete policy is required to list soft-deleted versions')
        self.c.before_list = reject
        with self.assertRaisesRegex(PermissionError, 'policy review required'): self.clean()
        self.assertEqual(self.audit(), before)
        self.assertEqual(self.c.deletes, [])
        self.assertEqual(self.o.state(CODE, 'default')['sequence'], 1)
        self.assertEqual(len(inventory(self.st, IID)), 2)
    def test_lazy_soft_listing_failure_also_blocks_claim(self):
        from google.api_core.exceptions import BadRequest
        original = self.c.list_blobs
        def lazy(*args, **kwargs):
            if kwargs.get('soft_deleted'):
                def fail():
                    raise BadRequest('Soft delete policy is required to list soft-deleted versions')
                    yield
                return fail()
            return original(*args, **kwargs)
        with patch.object(self.c, 'list_blobs', side_effect=lazy), self.assertRaises(PermissionError): self.clean()
        self.assertEqual(self.o.state(CODE, 'default')['sequence'], 1)
        self.assertEqual(self.c.deletes, [])
    def test_restore_wins_cas_prevents_all_deletes(self):
        def before(blob, data, pre):
            if '/medical_consent_events/' in blob.name and b'"action":"purge"' in data:
                self.c.before_upload = None
                event(order(self.st), 'restore', 'restore-before-claim-001', self.receipt['revision'])
        self.c.before_upload = before
        with self.assertRaises(ConsentOrderError): self.clean()
        self.assertEqual(len(inventory(self.st, IID)), 2); self.assertEqual(self.c.deletes, [])
    def test_purge_wins_new_consent_cannot_revive_old_new_media_survive(self):
        def before(blob, pin):
            self.c.before_delete = None
            event(order(self.st), 'restore', 'restore-after-claim-001', self.o.state(CODE, 'default')['revision'])
            new = 'b'*16
            record_media_owner(self.st, CODE, 'default', new)
            self.st._bucket.blob(self.st._k('images/'+new+'.jpg')).upload_from_string(b'newly consented synthetic')
        self.c.before_delete = before; self.clean()
        self.assertEqual(self.o.state(CODE, 'default')['purged_image_ids'], [IID])
        self.assertTrue(self.st.exists('images/'+'b'*16+'.jpg')); self.assertFalse(inventory(self.st, IID))
    def test_generation_replaced_after_inventory_not_deleted_as_old(self):
        def before(blob, pin):
            self.c.before_delete = None
            self.st._bucket.blob(blob.name).upload_from_string(b'late generation')
        self.c.before_delete = before
        with self.assertRaisesRegex(IOError, 'media remains'): self.clean()
        self.assertEqual(self.st.get_blob(self.key), b'late generation')
        self.assertEqual(self.clean()['status'], 'media_versions_absent_at_check')
    def test_two_cleanup_executions_race_same_claim(self):
        gate = threading.Barrier(2)
        def before(blob, data, pre):
            if '/medical_consent_events/' in blob.name and b'"action":"purge"' in data: gate.wait(timeout=5)
        self.c.before_upload = before
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(cleanup_subject, order(self.st), CODE, 'default', POLICY, apply=True, now=101) for _ in range(2)]
            results = [f.result(timeout=10) for f in futures]
        self.assertTrue(all(r['status'] == 'media_versions_absent_at_check' for r in results))
        self.assertEqual(self.o.state(CODE, 'default')['sequence'], 2)
        self.assertEqual(len(self.c.deletes), 2)
    def test_object_hold_keeps_pending_and_resumes_after_hold_removed(self):
        target = next(k for k in self.c.rows if k[0] == 'fake-media')
        self.c.rows[target]['hold'] = True
        result = cleanup_run(self.o, POLICY, apply=True, run_id='synthetic-hold-001', now=101)
        self.assertEqual(result['status'], 'needs_attention')
        self.assertTrue(self.c.rows[target]['hold'])
        self.c.rows[target]['hold'] = False
        self.assertEqual(cleanup_run(self.o, POLICY, apply=True, run_id='synthetic-hold-002', now=101)['status'], 'checked')
    def test_audit_unavailable_no_deletion(self):
        self.st._audit_bucket._properties = {}
        with self.assertRaises(PermissionError): self.clean()
        self.assertEqual(len(inventory(self.st, IID)), 2)
    def test_same_execution_replay_readonly_next_execution_finds_late_write(self):
        result = cleanup_run(self.o, POLICY, apply=True, run_id='synthetic-run-001', now=101)
        self.st._bucket.blob(self.st._k(self.key)).upload_from_string(b'late writer')
        self.assertTrue(cleanup_run(self.o, POLICY, apply=True, run_id='synthetic-run-001', now=102)['replayed'])
        self.assertEqual(self.st.get_blob(self.key), b'late writer')
        second = cleanup_run(self.o, POLICY, apply=True, run_id='synthetic-run-002', now=102)
        self.assertEqual(second['status'], 'checked'); self.assertFalse(inventory(self.st, IID))
        self.assertEqual(result['status'], 'checked')
    def test_bounds_policy_reuse_and_run_id_fail_before_cleanup(self):
        for n in (0, True, 10001):
            with self.subTest(bound=n), self.assertRaises(ValueError): cleanup_run(self.o, POLICY, apply=True, run_id='synthetic-bound-001', max_subjects=n)
        with self.assertRaises(ValueError): cleanup_run(self.o, POLICY, apply=True)
        cleanup_run(self.o, POLICY, apply=True, run_id='synthetic-bound-001', now=101)
        with self.assertRaises(ValueError): cleanup_run(self.o, dict(POLICY, retention_seconds=10), apply=True, run_id='synthetic-bound-001')
    def test_subject_batch_exceeded_does_not_delete_first_subject(self):
        self.o.apply('WD-SECOND', 'default', 'default:doctor', 'withdraw', 'withdraw-second-001', None, ['b'*16])
        with self.assertRaisesRegex(ValueError, 'bound exceeded'):
            cleanup_run(self.o, POLICY, apply=True, run_id='synthetic-bound-002', max_subjects=1)
        self.assertEqual(self.c.deletes, [])
    def test_crash_before_final_run_receipt_resumes_without_new_claim(self):
        original = self.st.put_json_immutable
        def fail(key, value):
            if key.startswith(RUNS+'/') and key.endswith('.outcome.json'): raise IOError('synthetic worker stopped')
            return original(key, value)
        with patch.object(self.st, 'put_json_immutable', side_effect=fail), self.assertRaises(IOError):
            cleanup_run(self.o, POLICY, apply=True, run_id='synthetic-crash-001', now=101)
        result = cleanup_run(self.o, POLICY, apply=True, run_id='synthetic-crash-001', now=102)
        self.assertEqual(result['status'], 'checked'); self.assertEqual(self.o.state(CODE, 'default')['sequence'], 2)
    def test_faulty_subject_does_not_prevent_other_cleanup(self):
        self.o.apply('WD-SECOND', 'default', 'default:doctor', 'withdraw', 'withdraw-second-001', None, ['b'*16])
        # Deliberately corrupt only a synthetic fake object, not via the store.
        key = self.st._k(self.o._slot('WD-SECOND', 1))
        for k, v in self.c.rows.items():
            if k[1] == key: v['data'] = b'{bad'
        result = cleanup_run(self.o, POLICY, apply=True, run_id='synthetic-fault-001', now=101)
        self.assertEqual(result['status'], 'needs_attention'); self.assertFalse(inventory(self.st, IID))
        self.assertEqual(sum(r['status'] == 'blocked_fault' for r in result['results']), 1)
    def test_corrupt_execution_outcome_is_not_a_success_replay(self):
        cleanup_run(self.o, POLICY, apply=True, run_id='synthetic-corrupt-001', now=101)
        key = self.st._k(RUNS+'/synthetic-corrupt-001.outcome.json')
        for k, v in self.c.rows.items():
            if k[1] == key:
                d = json.loads(v['data']); d['results'] = None; v['data'] = json.dumps(d).encode()
        with self.assertRaisesRegex(ValueError, 'receipt mismatch'):
            cleanup_run(self.o, POLICY, apply=True, run_id='synthetic-corrupt-001', now=101)
    def test_dry_worker_zero_writes(self):
        before = dict(self.c.rows); result = cleanup_run(self.o, POLICY, now=101)
        self.assertEqual(result['mode'], 'dry_run'); self.assertEqual(before, self.c.rows)


if __name__ == '__main__': unittest.main(verbosity=2)
