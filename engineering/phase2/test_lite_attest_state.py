"""Real SQLite/process concurrency + GCS conditional-transport doubles.

Rows below simulate ALREADY verified registration. No Apple or cloud service is
called, and the synthetic receipt digest is not evidence of receipt validation.
"""
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import multiprocessing
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import cbor2
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from google.api_core.exceptions import NotFound, PreconditionFailed, Forbidden

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'Backend/Flask'))
from lite_attest_assertion import AssertionPolicy, CATEGORY, VERSION
from lite_attest_request import client_data
from lite_attest_state import (SQLiteStateStore, GCSStateStore, AssertionAdmission,
                              StateUnavailable, AdmissionRejected, ChallengeLimit,
                              MAX_STATE, CHALLENGE_TTL, MAX_CHALLENGES)


def policy(): return AssertionPolicy('ABCDEFGHIJ.com.woundai.lite', frozenset({2, 4}), frozenset({'33'}))
def engine(store, clock=lambda: 1000): return AssertionAdmission(store, policy=policy(), audience='lite-test', clock=clock)


def process_admit(path, key_id, challenge_id, assertion, request, event, queue):
    service = engine(SQLiteStateStore(path))
    event.wait(10)
    try:
        service.admit(key_id, challenge_id, assertion, **request)
        queue.put('accepted')
    except AdmissionRejected:
        queue.put('rejected')
    except Exception as exc:
        queue.put(type(exc).__name__)


class FakeBucket:
    def __init__(self):
        self.lock = threading.Lock(); self.live = {}; self.versions = {}; self.calls = []
        self.serial = 100; self.fail_reload = None; self.fail_download = None; self.fail_upload = None
        self.lose_response = False; self.on_download = None

    def blob(self, name, generation=None): return FakeBlob(self, name, generation)


class FakeBlob:
    def __init__(self, bucket, name, generation):
        self.bucket, self.name, self.generation, self.size = bucket, name, generation, None

    def reload(self, **kwargs):
        b = self.bucket
        with b.lock:
            b.calls.append(('reload', self.name, kwargs))
            if b.fail_reload: raise b.fail_reload
            if self.name not in b.live: raise NotFound('missing')
            self.generation, data = b.live[self.name]; self.size = len(data)

    def download_as_bytes(self, **kwargs):
        b = self.bucket
        if b.on_download:
            callback = b.on_download; b.on_download = None; callback()
        with b.lock:
            b.calls.append(('download', self.name, dict(kwargs, selected_generation=self.generation)))
            if b.fail_download: raise b.fail_download
            if self.generation is None:
                generation, data = b.live[self.name]
            else:
                generation = self.generation; data = b.versions[(self.name, generation)]
            expected = kwargs.get('if_generation_match')
            if expected is not None and expected != generation: raise PreconditionFailed('changed')
            return data

    def upload_from_string(self, data, **kwargs):
        b = self.bucket
        with b.lock:
            b.calls.append(('upload', self.name, kwargs))
            if b.fail_upload: raise b.fail_upload
            old = b.live.get(self.name, (0, None))[0]
            expected = kwargs.get('if_generation_match')
            if expected is not None and expected != old: raise PreconditionFailed('changed')
            b.serial += 1; self.generation = b.serial
            b.live[self.name] = (self.generation, data); b.versions[(self.name, self.generation)] = data
            if b.lose_response: raise TimeoutError('committed but response lost')


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / 'state.sqlite')
        self.store = SQLiteStateStore(self.path); self.service = engine(self.store)
        self.private = ec.generate_private_key(ec.SECP256R1())
        self.point = self.private.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        self.key_id = hashlib.sha256(self.point).digest()
        self.row = dict(v=1, key_id=self.key_id.hex(), installation='server-created-install', audience='lite-test',
                        app_id=policy().app_id, environment='production',
                        public_key=base64.b64encode(self.point).decode(), receipt_sha256=hashlib.sha256(b'synthetic receipt').hexdigest(),
                        status='active', counter=0, challenges={})
        self.assertTrue(self.store.compare_exchange(self.key_id, 0, self.row))
        self.request = dict(request_id='00000000-0000-0000-0000-000000000001', method='POST',
                            path='/api/v1/lite/annotation/revision', content_type='application/json', body=b'{"revision":1}')

    def signed(self, challenge, counter=1, request=None, owner='server-created-install'):
        data = client_data(audience='lite-test', installation=owner, key_id=self.key_id,
                           challenge=challenge.nonce, **(self.request if request is None else request))
        auth = hashlib.sha256(policy().app_id.encode()).digest() + b'\x81' + counter.to_bytes(4, 'big')
        auth += cbor2.dumps({CATEGORY: 2, VERSION: '33'})
        signature = self.private.sign(hashlib.sha256(auth + hashlib.sha256(data).digest()).digest(), ec.ECDSA(hashes.SHA256()))
        return cbor2.dumps({'signature': signature, 'authenticatorData': auth})

    def admit(self, challenge, assertion=None, service=None, **request):
        return (service or self.service).admit(self.key_id, challenge.identifier,
                self.signed(challenge) if assertion is None else assertion, **dict(self.request, **request))

    def test_atomic_consumption_survives_restart(self):
        challenge = self.service.issue_challenge(self.key_id)
        self.assertEqual(len(challenge.nonce), 32)
        accepted = self.admit(challenge)
        self.assertEqual((accepted.installation, accepted.counter), ('server-created-install', 1))
        restarted = engine(SQLiteStateStore(self.path))
        with self.assertRaises(AdmissionRejected): self.admit(challenge, service=restarted)
        row = self.store.load(self.key_id)[1]
        self.assertEqual(row['counter'], 1); self.assertNotIn(challenge.identifier, row['challenges'])

    def test_invalid_signature_or_body_does_not_consume(self):
        c = self.service.issue_challenge(self.key_id); before = self.store.load(self.key_id)
        with self.assertRaises(AdmissionRejected): self.admit(c, body=b'{"revision":2}')
        self.assertEqual(self.store.load(self.key_id), before)
        with self.assertRaises(AdmissionRejected): self.admit(c, assertion=b'\xa0')
        self.assertEqual(self.store.load(self.key_id), before)
        self.admit(c)

    def test_owner_comes_from_registration_not_caller(self):
        c = self.service.issue_challenge(self.key_id)
        with self.assertRaises(AdmissionRejected): self.admit(c, self.signed(c, owner='someone-else'))
        deletion = dict(self.request, method='DELETE', path='/api/v1/lite/data/someone-else', content_type='', body=b'')
        # A valid signature for a different POST owner cannot authorize DELETE.
        with self.assertRaises(AdmissionRejected): self.admit(c, **deletion)

    def test_expired_challenge_and_future_clock(self):
        c = self.service.issue_challenge(self.key_id)
        for now in (999, 1000 + CHALLENGE_TTL):
            with self.assertRaises(AdmissionRejected): self.admit(c, service=engine(self.store, lambda: now))
        self.assertEqual(self.store.load(self.key_id)[1]['counter'], 0)

    def test_expiry_during_verification_does_not_commit(self):
        c = self.service.issue_challenge(self.key_id)
        times = iter((1000, 1120))
        with self.assertRaises(AdmissionRejected): self.admit(c, service=engine(self.store, lambda: next(times)))
        self.assertEqual(self.store.load(self.key_id)[1]['counter'], 0)

    def test_pending_bound_and_expired_pruning(self):
        issued = [self.service.issue_challenge(self.key_id) for _ in range(MAX_CHALLENGES)]
        self.assertEqual(len({c.nonce for c in issued}), MAX_CHALLENGES)
        with self.assertRaises(ChallengeLimit): self.service.issue_challenge(self.key_id)
        c = engine(self.store, lambda: 1120).issue_challenge(self.key_id)
        self.assertEqual(list(self.store.load(self.key_id)[1]['challenges']), [c.identifier])

    def test_unknown_key_does_not_create_state(self):
        other = b'x' * 32
        with self.assertRaises(AdmissionRejected): self.service.issue_challenge(other)
        self.assertIsNone(self.store.load(other))

    def test_counter_must_advance_even_with_fresh_challenge(self):
        self.admit(self.service.issue_challenge(self.key_id))
        c = self.service.issue_challenge(self.key_id)
        with self.assertRaises(AdmissionRejected): self.admit(c)
        self.assertEqual(self.admit(c, self.signed(c, counter=2)).counter, 2)

    def test_consumed_challenge_cannot_be_resigned_with_higher_counter(self):
        c = self.service.issue_challenge(self.key_id); self.admit(c)
        with self.assertRaises(AdmissionRejected): self.admit(c, self.signed(c, counter=2))

    def test_sqlite_stale_revision_cannot_overwrite_newer_state(self):
        revision, row = self.store.load(self.key_id)
        self.assertTrue(self.store.compare_exchange(self.key_id, revision, dict(row, counter=2)))
        self.assertFalse(self.store.compare_exchange(self.key_id, revision, dict(row, counter=0)))
        self.assertEqual(self.store.load(self.key_id)[1]['counter'], 2)

    def test_revoke_is_persistent_and_idempotent(self):
        c = self.service.issue_challenge(self.key_id)
        self.service.revoke(self.key_id); self.service.revoke(self.key_id)
        restarted = engine(SQLiteStateStore(self.path))
        with self.assertRaises(AdmissionRejected): self.admit(c, service=restarted)
        with self.assertRaises(AdmissionRejected): restarted.issue_challenge(self.key_id)
        self.assertEqual(self.store.load(self.key_id)[1]['challenges'], {})
        self.assertFalse(self.store.compare_exchange(self.key_id, 0, self.row))

    def test_parallel_same_assertion_has_one_winner(self):
        c = self.service.issue_challenge(self.key_id); assertion = self.signed(c)
        barrier = threading.Barrier(8)
        def worker(_):
            service = engine(SQLiteStateStore(self.path)); barrier.wait(timeout=10)
            try: self.admit(c, assertion, service=service); return 'accepted'
            except AdmissionRejected: return 'rejected'
        with ThreadPoolExecutor(max_workers=8) as pool: results = list(pool.map(worker, range(8)))
        self.assertEqual(results.count('accepted'), 1); self.assertEqual(results.count('rejected'), 7)

    def test_gcs_independent_services_have_one_winner(self):
        bucket = FakeBucket(); store = GCSStateStore(bucket)
        self.assertTrue(store.compare_exchange(self.key_id, 0, self.row))
        c = engine(store).issue_challenge(self.key_id); assertion = self.signed(c)
        barrier = threading.Barrier(8)
        def worker(_):
            service = engine(GCSStateStore(bucket)); barrier.wait(timeout=10)
            try: self.admit(c, assertion, service=service); return 'accepted'
            except AdmissionRejected: return 'rejected'
        with ThreadPoolExecutor(max_workers=8) as pool: results = list(pool.map(worker, range(8)))
        self.assertEqual(results.count('accepted'), 1); self.assertEqual(results.count('rejected'), 7)
        self.assertEqual(store.load(self.key_id)[1]['counter'], 1)

    def test_different_challenges_with_same_counter_have_one_winner(self):
        challenges = [self.service.issue_challenge(self.key_id) for _ in range(2)]
        barrier = threading.Barrier(2)
        def worker(c):
            service = engine(SQLiteStateStore(self.path)); assertion = self.signed(c)
            barrier.wait(timeout=10)
            try: self.admit(c, assertion, service=service); return 'accepted'
            except AdmissionRejected: return 'rejected'
        with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(worker, challenges))
        self.assertEqual(sorted(results), ['accepted', 'rejected'])

    def test_separate_processes_have_one_winner(self):
        c = self.service.issue_challenge(self.key_id); assertion = self.signed(c)
        context = multiprocessing.get_context('spawn'); event = context.Event(); queue = context.Queue()
        children = [context.Process(target=process_admit, args=(self.path, self.key_id, c.identifier,
                    assertion, self.request, event, queue)) for _ in range(4)]
        try:
            for child in children: child.start()
            event.set(); results = [queue.get(timeout=20) for _ in children]
            for child in children: child.join(20); self.assertEqual(child.exitcode, 0)
            self.assertEqual(results.count('accepted'), 1); self.assertEqual(results.count('rejected'), 3)
        finally:
            for child in children:
                if child.is_alive(): child.terminate(); child.join()
            queue.close()

    def test_cas_conflict_reloads_revocation_before_admission(self):
        c = self.service.issue_challenge(self.key_id); actual = self.store
        class RacingStore:
            fired = False
            def load(self, key): return actual.load(key)
            def compare_exchange(inner, key, revision, state):
                if not inner.fired:
                    inner.fired = True; engine(actual).revoke(key)
                return actual.compare_exchange(key, revision, state)
        with self.assertRaises(AdmissionRejected): self.admit(c, service=engine(RacingStore()))
        self.assertEqual(actual.load(self.key_id)[1]['counter'], 0)

    def test_commit_then_lost_response_never_returns_admission(self):
        c = self.service.issue_challenge(self.key_id); actual = self.store
        class UncertainStore:
            def load(self, key): return actual.load(key)
            def compare_exchange(inner, key, revision, state):
                actual.compare_exchange(key, revision, state)
                raise StateUnavailable('response lost')
        with self.assertRaises(StateUnavailable): self.admit(c, service=engine(UncertainStore()))
        with self.assertRaises(AdmissionRejected): self.admit(c)
        self.assertEqual(actual.load(self.key_id)[1]['counter'], 1)

    def test_write_failure_never_returns_admission_and_preserves_challenge(self):
        c = self.service.issue_challenge(self.key_id); actual = self.store
        class FailedStore:
            def load(self, key): return actual.load(key)
            def compare_exchange(self, *args): raise StateUnavailable('disk unavailable')
        with self.assertRaises(StateUnavailable): self.admit(c, service=engine(FailedStore()))
        self.assertEqual(self.admit(c).counter, 1)

    def test_corrupt_registered_state_never_uses_defaults(self):
        for field, value in [('counter', True), ('counter', -1), ('counter', 2**32), ('audience', 'another'),
                             ('app_id', 'OTHERTEAM1.com.woundai.lite'), ('environment', 'development'),
                             ('key_id', 'x' * 64), ('public_key', 'bad'), ('status', 'unknown'),
                             ('installation', '../other'), ('receipt_sha256', ''), ('challenges', [])]:
            revision, _ = self.store.load(self.key_id)
            self.store.compare_exchange(self.key_id, revision, dict(self.row, **{field: value}))
            with self.subTest(field=field), self.assertRaises(StateUnavailable): self.service.issue_challenge(self.key_id)

    def test_clock_rollback_and_invalid_clock_refused(self):
        self.service.issue_challenge(self.key_id)
        for value in (999, float('nan'), float('inf'), -1, True):
            with self.subTest(value=value), self.assertRaises(StateUnavailable):
                engine(self.store, lambda: value).issue_challenge(self.key_id)

    def test_security_state_is_not_an_in_memory_database(self):
        with self.assertRaises(ValueError): SQLiteStateStore(':memory:')


class GCSCASTests(unittest.TestCase):
    def setUp(self):
        self.bucket = FakeBucket(); self.store = GCSStateStore(self.bucket); self.key = b'k' * 32

    def test_creation_and_stale_replacement_are_conditional(self):
        self.assertTrue(self.store.compare_exchange(self.key, 0, {'value': 1}))
        revision, row = self.store.load(self.key)
        self.assertEqual(row, {'value': 1})
        self.assertFalse(self.store.compare_exchange(self.key, 0, {'value': 99}))
        self.assertTrue(self.store.compare_exchange(self.key, revision, {'value': 2}))
        self.assertFalse(self.store.compare_exchange(self.key, revision, {'value': 3}))
        self.assertEqual(self.store.load(self.key)[1]['value'], 2)

    def test_metadata_content_generation_and_checksums_are_pinned(self):
        self.store.compare_exchange(self.key, 0, {'value': 1})
        revision = self.store.load(self.key)[0]
        self.bucket.on_download = lambda: self.store.compare_exchange(self.key, revision, {'value': 2})
        old_generation, old_value = self.store.load(self.key)
        self.assertEqual((old_generation, old_value), (revision, {'value': 1}))
        self.assertFalse(self.store.compare_exchange(self.key, old_generation, {'value': 3}))
        for kind, _, options in self.bucket.calls:
            self.assertIsNone(options['retry']); self.assertEqual(options['timeout'], 10)
            if kind in ('download', 'upload'): self.assertEqual(options['checksum'], 'crc32c')
            if kind == 'download': self.assertEqual(options['selected_generation'], options['if_generation_match'])

    def test_only_initial_not_found_means_missing(self):
        self.assertIsNone(self.store.load(self.key))
        self.store.compare_exchange(self.key, 0, {'value': 1})
        self.bucket.fail_download = NotFound('generation disappeared')
        with self.assertRaises(StateUnavailable): self.store.load(self.key)
        self.bucket.fail_reload = Forbidden('IAM denied')
        with self.assertRaises(StateUnavailable): self.store.load(self.key)

    def test_gcs_timeout_after_commit_is_uncertain_not_success(self):
        self.bucket.lose_response = True
        with self.assertRaises(StateUnavailable): self.store.compare_exchange(self.key, 0, {'value': 1})
        self.assertEqual(self.store.load(self.key)[1], {'value': 1})

    def test_other_write_failures_are_not_contention(self):
        for error in (Forbidden('IAM denied'), TimeoutError('timeout')):
            self.bucket.fail_upload = error
            with self.assertRaises(StateUnavailable): self.store.compare_exchange(self.key, 0, {'value': 1})
        self.assertIsNone(self.store.load(self.key))

    def test_duplicate_json_or_oversized_blob_rejected(self):
        name = self.store._name(self.key)
        for raw in (b'{"counter":1,"counter":0}', b'{"counter":NaN}', b'[]', b'x' * (MAX_STATE + 1)):
            self.bucket.live[name] = (101, raw); self.bucket.versions[(name, 101)] = raw
            with self.subTest(raw=raw[:20]), self.assertRaises(StateUnavailable): self.store.load(self.key)

    def test_namespace_and_revision_are_not_client_paths(self):
        for prefix in ('../audit', '/lite_security', 'lite//keys', 'lite/../keys'):
            with self.assertRaises(ValueError): GCSStateStore(self.bucket, prefix=prefix)
        for key in ('../audit', b'k' * 31, None):
            with self.assertRaises(AdmissionRejected): self.store.load(key)
        for revision in (-1, True, '0', None):
            with self.assertRaises(StateUnavailable): self.store.compare_exchange(self.key, revision, {'value': 1})
        self.assertEqual(self.bucket.calls, [])

    def test_missing_or_stale_generation_metadata_is_refused(self):
        self.store.compare_exchange(self.key, 0, {'value': 1})
        original = FakeBlob.reload
        for generation in (None, '101', 0, True):
            def reload(blob, **kwargs):
                original(blob, **kwargs); blob.generation = generation
            with patch.object(FakeBlob, 'reload', reload), self.assertRaises(StateUnavailable): self.store.load(self.key)
        original_upload = FakeBlob.upload_from_string
        def upload(blob, data, **kwargs):
            original_upload(blob, data, **kwargs); blob.generation = kwargs['if_generation_match']
        with patch.object(FakeBlob, 'upload_from_string', upload), self.assertRaises(StateUnavailable):
            self.store.compare_exchange(b'z' * 32, 0, {'value': 1})
        revision = self.store.load(self.key)[0]
        with patch.object(FakeBlob, 'upload_from_string', upload), self.assertRaises(StateUnavailable):
            self.store.compare_exchange(self.key, revision, {'value': 2})


if __name__ == '__main__': unittest.main()
