"""Object fence protocol against an injected generation-aware GCS adapter.

No Google clients/credentials; this does not test real GCS or whole-owner DELETE.
The fake retains history deliberately to catch accidental historical readback.
"""
import copy
from dataclasses import replace
from pathlib import Path
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from google.api_core.exceptions import NotFound, PreconditionFailed, Forbidden
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'Backend/Flask'))
from lite_fenced_objects import FencedObjects, ObjectRef, PinnedWrite, ObjectSealed, GenerationConflict, MAX_BYTES
from lite_attest_state import StateUnavailable


class Bucket:
    name = 'synthetic-lite-fences'
    def __init__(self):
        self.lock = threading.Lock(); self.live = {}; self.history = {}; self.serial = 0; self.calls = []
        self.before_upload = None; self.before_download = None; self.reload_error = None
        self.upload_error = None; self.download_error = None; self.lose_response = False
    def blob(self, name, generation=None): return Blob(self, name, generation)
    def list_blobs(self, *, prefix, timeout=10, retry=None, max_results=None):
        with self.lock:
            names = sorted(name for name in self.live if name.startswith(prefix))
        if max_results is not None: names = names[:max_results]
        return [SimpleNamespace(name=name) for name in names]


class Blob:
    def __init__(self, bucket, name, generation):
        self.bucket = bucket; self.name = name; self.generation = generation
        self.metadata = None; self.size = None
    def reload(self, **kwargs):
        b = self.bucket
        with b.lock:
            b.calls.append(('reload', self.name, kwargs))
            if b.reload_error: raise b.reload_error
            if self.name not in b.live: raise NotFound('missing')
            self.generation, data, metadata = b.live[self.name]
            self.size = len(data); self.metadata = copy.deepcopy(metadata)
    def upload_from_string(self, data, **kwargs):
        b = self.bucket
        if b.before_upload: b.before_upload(self, data, kwargs)
        with b.lock:
            b.calls.append(('upload', self.name, kwargs))
            if b.upload_error: raise b.upload_error
            old = b.live.get(self.name, (0, None, None))[0]
            expected = kwargs.get('if_generation_match')
            if expected is not None and expected != old: raise PreconditionFailed('changed')
            b.serial += 1; self.generation = b.serial
            row = (self.generation, data, copy.deepcopy(self.metadata))
            b.live[self.name] = row; b.history[(self.name, self.generation)] = row
            if b.lose_response: raise TimeoutError('response lost after commit')
    def download_as_bytes(self, **kwargs):
        b = self.bucket
        if b.before_download:
            callback = b.before_download; b.before_download = None; callback()
        with b.lock:
            b.calls.append(('download', self.name, dict(kwargs, selected_generation=self.generation)))
            if b.download_error: raise b.download_error
            row = b.live.get(self.name) if self.generation is None else b.history.get((self.name, self.generation))
            if row is None: raise NotFound('gone')
            generation, data, _ = row
            if kwargs.get('if_generation_match', generation) != generation: raise PreconditionFailed('changed')
            return data


class FenceTests(unittest.TestCase):
    def setUp(self):
        self.bucket = Bucket(); self.store = FencedObjects(self.bucket)
        self.ref = ObjectRef('a' * 32, 'rgbd', 'b' * 32)

    def test_real_sdk_resumable_crc_error_cannot_delete_a_newer_seal(self):
        import io
        from google.cloud.storage import Client
        from google.auth.credentials import AnonymousCredentials
        from google.cloud.storage.exceptions import DataCorruption
        from unittest.mock import Mock
        client = Client(project='synthetic-project', credentials=AnonymousCredentials())
        bucket = client.bucket('synthetic-lite-fences')
        blob = FencedObjects(bucket)._write_blob(self.ref.name)
        upload = Mock(); upload.finished = False
        upload.resumable_url = 'https://storage.googleapis.com/upload/storage/v1/b/synthetic-lite-fences/o?upload_id=synthetic'
        upload.transmit_next_chunk.side_effect = DataCorruption(Mock(), 'synthetic checksum mismatch')
        with patch.object(blob, '_initiate_resumable_upload', return_value=(upload, Mock())), \
                patch.object(bucket, 'delete_blob') as remote_delete:
            with self.assertRaises(Exception) as failure:
                blob._do_resumable_upload(client, io.BytesIO(b'payload'), 'application/octet-stream', 7,
                                         None, 0, None, None, None, checksum='crc32c', retry=None)
            self.assertIsInstance(failure.exception, StateUnavailable)
            upload.transmit_next_chunk.assert_called_once()
            remote_delete.assert_not_called()

    def test_first_write_then_replacement_uses_exact_generations(self):
        first = self.store.pin(self.ref); self.assertEqual(first.generation, 0)
        generation = self.store.write(first, b'first')
        second = self.store.pin(self.ref); self.assertEqual(second.generation, generation)
        self.assertGreater(self.store.write(second, b'second'), generation)
        self.assertEqual(self.store.read_live(self.ref), b'second')
        uploads = [x[2] for x in self.bucket.calls if x[0] == 'upload']
        self.assertEqual([x['if_generation_match'] for x in uploads], [0, generation])
        for call in self.bucket.calls:
            self.assertIsNone(call[2]['retry']); self.assertEqual(call[2]['timeout'], 10)

    def test_seal_missing_object_blocks_delayed_creation(self):
        old = self.store.pin(self.ref)
        self.store.seal(self.ref)
        with self.assertRaises(GenerationConflict): self.store.write(old, b'late')
        self.assertEqual(self.bucket.live[self.ref.name][1], b'')
        with self.assertRaises(ObjectSealed): self.store.pin(self.ref)
        with self.assertRaises(ObjectSealed): self.store.read_live(self.ref)

    def test_seal_existing_object_erases_live_bytes_and_blocks_old_replacement(self):
        self.store.write(self.store.pin(self.ref), b'sensitive')
        old = self.store.pin(self.ref); self.store.seal(self.ref)
        with self.assertRaises(GenerationConflict): self.store.write(old, b'resurrected')
        self.assertEqual(self.bucket.live[self.ref.name][1], b'')

    def test_marker_survives_new_adapter_and_seal_retry_is_idempotent(self):
        old = self.store.pin(self.ref); generation = self.store.seal(self.ref)
        restarted = FencedObjects(self.bucket)
        self.assertEqual(restarted.seal(self.ref), generation)
        with self.assertRaises(GenerationConflict): restarted.write(old, b'resumed after restart')

    def test_lost_write_response_retains_unknown_outcome_until_sealed(self):
        pin = self.store.pin(self.ref); self.bucket.lose_response = True
        with self.assertRaises(StateUnavailable): self.store.write(pin, b'committed')
        self.bucket.lose_response = False
        self.store.seal(self.ref)
        with self.assertRaises(GenerationConflict): self.store.write(pin, b'retry')
        self.assertEqual(self.bucket.live[self.ref.name][1], b'')

    def test_lost_seal_response_is_not_success_but_retry_confirms_marker(self):
        self.bucket.lose_response = True
        with self.assertRaises(StateUnavailable): self.store.seal(self.ref)
        self.bucket.lose_response = False
        self.assertGreater(self.store.seal(self.ref), 0)
        self.assertEqual(self.bucket.live[self.ref.name][1], b'')

    def test_paused_upload_resumes_after_seal_and_is_rejected(self):
        pin = self.store.pin(self.ref); entered = threading.Event(); resume = threading.Event(); result = []
        def pause(blob, data, kwargs):
            if data:
                entered.set()
                if not resume.wait(5): raise RuntimeError('test wait timed out')
        self.bucket.before_upload = pause
        def writer():
            try: self.store.write(pin, b'late bytes'); result.append('written')
            except GenerationConflict: result.append('fenced')
            except Exception as exc: result.append(type(exc).__name__)
        thread = threading.Thread(target=writer); thread.start()
        try:
            self.assertTrue(entered.wait(5)); self.store.seal(self.ref)
        finally:
            resume.set(); thread.join(5)
        self.assertFalse(thread.is_alive()); self.assertEqual(result, ['fenced'])
        self.assertEqual(self.bucket.live[self.ref.name][1], b'')

    def test_sealer_retries_when_writer_wins_the_first_generation(self):
        pin = self.store.pin(self.ref); called = False
        def race(blob, data, kwargs):
            nonlocal called
            if not data and not called:
                called = True; self.store.write(pin, b'won before seal')
        self.bucket.before_upload = race
        self.store.seal(self.ref)
        self.assertTrue(called); self.assertEqual(self.bucket.live[self.ref.name][1], b'')

    def test_seal_between_reload_and_readback_cannot_read_historical_payload(self):
        self.store.write(self.store.pin(self.ref), b'sensitive')
        self.bucket.before_download = lambda: self.store.seal(self.ref)
        with self.assertRaises(StateUnavailable): self.store.read_live(self.ref)
        self.assertEqual(self.bucket.live[self.ref.name][1], b'')
        self.assertTrue(all(x[2]['selected_generation'] is None for x in self.bucket.calls if x[0] == 'download'))

    def test_unknown_storage_failures_never_become_missing_or_success(self):
        for error in (Forbidden('denied'), TimeoutError('unknown')):
            self.bucket.reload_error = error
            with self.assertRaises(StateUnavailable): self.store.pin(self.ref)
            with self.assertRaises(StateUnavailable): self.store.seal(self.ref)
        self.bucket.reload_error = None
        pin = self.store.pin(self.ref); self.bucket.upload_error = Forbidden('denied')
        with self.assertRaises(StateUnavailable): self.store.write(pin, b'data')
        with self.assertRaises(StateUnavailable): self.store.seal(self.ref)

    def test_readback_missing_after_successful_metadata_is_not_absence(self):
        self.store.write(self.store.pin(self.ref), b'data')
        self.bucket.download_error = NotFound('generation gone')
        with self.assertRaises(StateUnavailable): self.store.read_live(self.ref)

    def test_invalid_metadata_and_wrong_owner_fail_closed_before_overwrite(self):
        self.store.write(self.store.pin(self.ref), b'data')
        original = copy.deepcopy(self.bucket.live[self.ref.name])
        for change in ({'owner': 'c' * 32}, {'kind': 'index'}, {'state': 'unknown'}, {'extra': 'field'}, {'sha256': None}):
            generation, data, metadata = copy.deepcopy(original); metadata.update(change)
            self.bucket.live[self.ref.name] = (generation, data, metadata)
            before = len([x for x in self.bucket.calls if x[0] == 'upload'])
            with self.assertRaises(StateUnavailable): self.store.seal(self.ref)
            self.assertEqual(len([x for x in self.bucket.calls if x[0] == 'upload']), before)

    def test_scope_and_pin_binding_rejected_before_storage(self):
        for ref in (replace(self.ref, owner='../x'), replace(self.ref, identifier='../x'), replace(self.ref, kind='audit')):
            with self.assertRaises(ValueError): self.store.pin(ref)
        pin = self.store.pin(self.ref)
        for invalid in (replace(pin, bucket='other'), replace(pin, generation=True), replace(pin, generation=-1)):
            with self.assertRaises(ValueError): self.store.write(invalid, b'data')
        self.assertEqual([x for x in self.bucket.calls if x[0] == 'upload'], [])

    def test_empty_or_oversized_payload_cannot_impersonate_marker(self):
        pin = self.store.pin(self.ref)
        for data in (b'', b'a' * (MAX_BYTES + 1), 'text'):
            with self.assertRaises(ValueError): self.store.write(pin, data)
        self.assertEqual(self.bucket.live, {})

    def test_other_owner_and_other_object_are_unchanged(self):
        others = [replace(self.ref, owner='c' * 32), replace(self.ref, kind='label')]
        for ref in others: self.store.write(self.store.pin(ref), b'keep')
        before = copy.deepcopy(self.bucket.live); self.store.seal(self.ref)
        for ref in others: self.assertEqual(self.bucket.live[ref.name], before[ref.name])

    def test_conflicting_write_does_not_retry_with_new_generation(self):
        pin = self.store.pin(self.ref); self.store.write(pin, b'winner')
        before = len([x for x in self.bucket.calls if x[0] == 'upload'])
        with self.assertRaises(GenerationConflict): self.store.write(pin, b'loser')
        self.assertEqual(len([x for x in self.bucket.calls if x[0] == 'upload']), before + 1)
        self.assertEqual(self.store.read_live(self.ref), b'winner')

if __name__ == '__main__': unittest.main()
