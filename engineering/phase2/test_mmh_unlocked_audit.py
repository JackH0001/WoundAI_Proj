"""Offline behavioral checks for the explicitly scoped mutable MMH audit mode."""
import json
import os
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'Backend' / 'Flask'))
import store as s
from google.api_core.exceptions import PreconditionFailed
from test_audit_chain_concurrency import _FakeGcs, _FakeBlob, make_gcs_store

ENV = {
    'WOUNDAI_AUDIT_MODE': 'mmh-unlocked-validation', 'WOUNDAI_STORE': 'gcs',
    'WOUNDAI_INSTITUTION_ORG': 'mmhps20261007', 'WOUNDAI_SERVICE_PROFILE': 'medical',
    'WOUNDAI_ENABLE_LITE_API': '0', 'GOOGLE_CLOUD_PROJECT': 'woundai-jackh001',
    'K_SERVICE': 'woundai-backend-mmhps20261007',
    'WOUNDAI_GCS_BUCKET': s.MmhUnlockedValidationStore._mmh_buckets[0],
    'WOUNDAI_SECURITY_BUCKET': s.MmhUnlockedValidationStore._mmh_buckets[1],
    'WOUNDAI_AUDIT_BUCKET': s.MmhUnlockedValidationStore._mmh_buckets[2],
}


class SnapshotBlob(_FakeBlob):
    def __init__(self, fake, name):
        super().__init__(fake, name)
        self.listed_generation = fake.generations[name]

    @property
    def generation(self):
        return self.listed_generation

    def download_as_bytes(self, if_generation_match=None):
        if if_generation_match != self.store.generations.get(self.name):
            raise PreconditionFailed('generation changed')
        return super().download_as_bytes()


class Fake(_FakeGcs):
    def bucket(self, name):
        bucket = super().bucket(name)
        class Blob(_FakeBlob):
            def exists(self):
                return self.name in self.store.objects
        bucket.blob = lambda key: Blob(self, key)
        return bucket

    def list_blobs(self, bucket_name, prefix=''):
        return [SnapshotBlob(self, b.name) for b in super().list_blobs(bucket_name, prefix)]


def fixture():
    fake = Fake(PreconditionFailed)
    st = make_gcs_store(fake)
    st.__class__ = s.MmhUnlockedValidationStore
    st._bucket_name, st._security_bucket_name, st._audit_bucket_name = st._mmh_buckets
    st._security_bucket = fake.bucket(st._security_bucket_name)
    st._audit_bucket._properties = {}
    return st, fake


def record(seq, prev):
    r = dict(chain_v=s.CHAIN_V, seq=seq, prev=prev, nonce='%032x' % (seq + 1),
             ts='2026-10-08T00:00:00Z', actor='mmhps20261007:admin', role='admin',
             org='mmhps20261007', action='synthetic_validation', code='TEST', result='ok')
    r['hash'] = s.audit_hash(r)
    return r


class UnlockedAuditTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, ENV, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        s.reset_store()
        self.addCleanup(s.reset_store)
        self.st, self.fake = fixture()

    def append(self):
        return self.st.append_next_chained('audit.jsonl', record)

    def test_unlocked_append_and_receipt_remain_conditional(self):
        self.append(); self.append()
        self.assertTrue(self.st.put_blob_immutable('receipts/synthetic.json', b'{}'))
        self.assertFalse(self.st.put_blob_immutable('receipts/synthetic.json', b'{}'))
        with self.assertRaises(Exception):
            self.st.put_blob_immutable('receipts/synthetic.json', b'changed')
        uploads = [c for c in self.fake.calls if c[0] == 'upload']
        self.assertTrue(uploads)
        self.assertTrue(all(c[2] == 0 for c in uploads))

    def test_every_append_reads_all_historical_bytes(self):
        self.append(); self.append()
        self.fake.calls.clear()
        self.append()
        reads = [c[1] for c in self.fake.calls if c[0] == 'download']
        for n in (0, 1):
            self.assertIn('flywheel/audit.jsonl/%020d.jsonl' % n, reads)

    def seed(self, count):
        previous = "GENESIS"
        for seq in range(count):
            row = record(seq, previous)
            self.fake.put_object('flywheel/audit.jsonl/%020d.jsonl' % seq,
                                 json.dumps(row).encode())
            previous = row['hash']

    def test_parallel_reads_bounded_ordered_and_complete(self):
        self.seed(9)
        original = self.st._read_admission_blob
        lock = threading.Lock()
        barrier = threading.Barrier(4, timeout=5)
        active = peak = 0
        finished = []
        def read(blob):
            nonlocal active, peak
            seq = int(blob.name.rsplit('/', 1)[1].split('.')[0])
            with lock:
                active += 1
                peak = max(peak, active)
            try:
                if seq < 4: barrier.wait()
                # Intentionally finish out of order; validation must remain ordered.
                time.sleep((4 - seq % 4) * .005)
                return original(blob)
            finally:
                with lock:
                    finished.append(seq)
                    active -= 1
        with patch.object(self.st, '_read_admission_blob', read):
            self.append()
        self.assertEqual(peak, 4)
        self.assertEqual(active, 0)
        self.assertEqual(sorted(finished), list(range(9)))
        self.assertEqual(len(self.fake.objects), 10)
        self.assertEqual(self.st._audit_prefix_cache[next(iter(self.st._audit_prefix_cache))].count, 9)

    def test_slow_first_read_does_not_block_next_group(self):
        self.seed(12)
        original = self.st._read_admission_blob
        next_group_started = threading.Event()
        def read(blob):
            seq = int(blob.name.rsplit('/', 1)[1].split('.')[0])
            if seq == 0:
                self.assertTrue(next_group_started.wait(3),
                                "reader waited for the entire first group")
            if seq == 4:
                next_group_started.set()
            return original(blob)
        with patch.object(self.st, '_read_admission_blob', read):
            self.append()
        self.assertEqual(len(self.fake.objects), 13)

    def test_pending_window_is_bounded_when_consumer_pauses(self):
        self.seed(40)
        blobs = self.fake.list_blobs(self.st._audit_bucket_name,
                                    prefix='flywheel/audit.jsonl/')
        original = self.st._read_admission_blob
        started = []
        lock = threading.Lock()
        def read(blob):
            with lock:
                started.append(blob.name)
            return original(blob)
        with patch.object(self.st, '_read_admission_blob', read):
            stream = self.st._read_admission_batch(blobs, 0)
            next(stream)
            # One consumed result plus a window of no more than eight reads.
            time.sleep(.05)
            self.assertLessEqual(len(started), 9)
            stream.close()
        self.assertLessEqual(len(started), 9)

    def test_parallel_network_failure_never_appends_and_latches(self):
        self.seed(9)
        original = self.st._read_admission_blob
        def read(blob):
            if blob.name.endswith('00000000000000000002.jsonl'):
                raise IOError('synthetic unavailable')
            return original(blob)
        with patch.object(self.st, '_read_admission_blob', read):
            with self.assertRaises(IOError): self.append()
        self.assertEqual(len(self.fake.objects), 9)
        with self.assertRaises(PermissionError): self.append()

    def test_parallel_middle_broken_link_never_appends(self):
        self.seed(9)
        bad = record(5, '0' * 64)
        self.fake.put_object('flywheel/audit.jsonl/00000000000000000005.jsonl',
                             json.dumps(bad).encode())
        with self.assertRaisesRegex(IOError, 'broken link'): self.append()
        self.assertEqual(len(self.fake.objects), 9)

    def test_parallel_next_batch_tamper_is_not_skipped(self):
        self.seed(9)
        self.fake.objects['flywheel/audit.jsonl/00000000000000000008.jsonl'] = b'{}'
        with self.assertRaises(IOError): self.append()
        self.assertEqual(len(self.fake.objects), 9)

    def test_parallel_manifest_change_after_reads_still_latches(self):
        self.seed(9)
        original = self.st._read_admission_blob
        def read(blob):
            data = original(blob)
            if blob.name.endswith('00000000000000000008.jsonl'):
                self.fake.put_object(blob.name, data)
            return data
        with patch.object(self.st, '_read_admission_blob', read):
            with self.assertRaises(IOError): self.append()
        self.assertEqual(len(self.fake.objects), 9)
        with self.assertRaises(PermissionError): self.append()

    def test_formal_gate_still_rejects_unlocked_subclass(self):
        with self.assertRaises(PermissionError): self.st.require_locked_audit_epoch()
        self.st.__class__ = s.GcsStore
        with self.assertRaises(PermissionError): self.append()

    def test_formal_seven_year_retention_without_lock_still_rejected(self):
        self.st._audit_bucket._properties = {'retentionPolicy': {
            'retentionPeriod': '220903200', 'isLocked': False}}
        with self.assertRaises(PermissionError): self.st.require_locked_audit_epoch()
        self.st.__class__ = s.GcsStore
        with self.assertRaises(PermissionError): self.append()

    def test_each_scope_variable_missing_or_wrong_rejected_before_sdk(self):
        for key in ENV:
            for value in (None, 'wrong'):
                with self.subTest(key=key, value=value), patch.dict(os.environ, ENV, clear=True):
                    if value is None: os.environ.pop(key)
                    else: os.environ[key] = value
                    with patch.object(s.GcsStore, '__init__', side_effect=AssertionError('SDK reached')):
                        with self.assertRaises(PermissionError):
                            s.MmhUnlockedValidationStore(
                                os.environ.get('WOUNDAI_GCS_BUCKET'), 'flywheel',
                                os.environ.get('WOUNDAI_AUDIT_BUCKET'),
                                os.environ.get('WOUNDAI_SECURITY_BUCKET'))

    def test_prefix_and_bucket_permutation_rejected(self):
        media, security, audit = self.st._mmh_buckets
        for args in [(media, 'other', audit, security), (audit, 'flywheel', media, security),
                     (media, 'flywheel', media, security)]:
            with self.assertRaises(PermissionError): self.st.validate_scope(*args)

    def test_unknown_mode_rejected_even_with_cached_local_store(self):
        s.reset_store(object())
        os.environ['WOUNDAI_AUDIT_MODE'] = 'unlocked'
        with self.assertRaises(RuntimeError): s.get_store()

    def test_cannot_reuse_cached_other_store(self):
        s.reset_store(object())
        with patch.object(s, '_refuse_cloud_in_test_process'):
            with self.assertRaises(RuntimeError): s.get_store()

    def test_factory_selects_scoped_class(self):
        with patch.object(s, '_refuse_cloud_in_test_process'), \
                patch.object(s.MmhUnlockedValidationStore, '__init__', return_value=None):
            self.assertIsInstance(s.get_store(), s.MmhUnlockedValidationStore)

    def test_unreadable_or_unexpected_retention_never_downgrades(self):
        for policy in [{'isLocked': True, 'retentionPeriod': '220903200'},
                       {'isLocked': False, 'retentionPeriod': '220903200'},
                       {'isLocked': 'false'}, {'retentionPeriod': 0}]:
            with self.subTest(policy=policy):
                self.st._audit_bucket._properties = {'retentionPolicy': policy}
                with self.assertRaises(PermissionError): self.st.require_audit_write_policy()
        self.st._audit_bucket._properties = {}
        with patch.object(self.st._audit_bucket, 'reload', side_effect=IOError('unreachable')):
            with self.assertRaises(PermissionError): self.st.require_audit_write_policy()

    def test_replaced_generation_latches_failure_across_retries(self):
        self.append(); self.append()
        name = sorted(self.fake.objects)[0]
        self.fake.put_object(name, self.fake.objects[name])
        for _ in range(2):
            with self.assertRaises((IOError, PermissionError)): self.append()
        self.assertEqual(len(self.fake.objects), 2)
        self.assertIn('writes blocked', self.st.describe(retention=self.st.retention_info()))

    def test_scope_change_after_initialization_blocks_receipts_and_chain(self):
        os.environ['WOUNDAI_INSTITUTION_ORG'] = 'other'
        with self.assertRaises(PermissionError): self.st.put_blob_immutable('receipts/test.json', b'{}')
        with self.assertRaises(PermissionError): self.append()
        self.assertEqual(self.fake.objects, {})

    def test_bad_middle_bytes_even_without_generation_change_rejected(self):
        self.append(); self.append()
        name = sorted(self.fake.objects)[0]
        self.fake.objects[name] = b'{}'
        with self.assertRaises(IOError): self.append()
        self.assertEqual(len(self.fake.objects), 2)

    def test_restart_fully_validates_chain(self):
        self.append(); self.append()
        self.st._audit_prefix_cache.clear()
        name = sorted(self.fake.objects)[0]
        self.fake.put_object(name, b'{}')
        with self.assertRaises(IOError): self.append()

    def test_missing_or_noncanonical_slot_rejected(self):
        for bad_name in ('flywheel/audit.jsonl/nope.jsonl', 'flywheel/audit.jsonl/00000000000000000002.jsonl'):
            self.st, self.fake = fixture()
            self.fake.put_object(bad_name, b'{}')
            with self.assertRaises(IOError): self.append()

    def test_generation_changes_during_download_rejected(self):
        self.append()
        original = SnapshotBlob.download_as_bytes
        def racing(blob, **kwargs):
            blob.store.put_object(blob.name, blob.store.objects[blob.name])
            return original(blob, **kwargs)
        with patch.object(SnapshotBlob, 'download_as_bytes', racing):
            with self.assertRaises(PreconditionFailed): self.append()
        self.assertEqual(len(self.fake.objects), 1)

    def test_generation_changes_after_download_rejected(self):
        self.append()
        original = SnapshotBlob.download_as_bytes
        def racing(blob, **kwargs):
            data = original(blob, **kwargs)
            blob.store.put_object(blob.name, data)
            return data
        with patch.object(SnapshotBlob, 'download_as_bytes', racing):
            with self.assertRaises(IOError): self.append()
        self.assertEqual(len(self.fake.objects), 1)

    def test_runtime_cannot_delete_move_or_generic_append_audit(self):
        for action in (lambda: self.st.delete('audit.jsonl/a'),
                       lambda: self.st.move('audit.jsonl/a', 'images/a'),
                       lambda: self.st.append_line('audit.jsonl', '{}')):
            with self.assertRaises(PermissionError): action()

    def test_health_disclosure_uses_real_retention_and_explicit_mode(self):
        self.assertEqual(self.st.audit_write_mode, 'mmh-unlocked-validation')
        text = self.st.describe(retention=self.st.retention_info())
        self.assertIn('no retention policy', text)
        self.assertIn('not WORM', text)
        self.assertNotIn('LOCKED', text)


if __name__ == '__main__':
    unittest.main(verbosity=2)
