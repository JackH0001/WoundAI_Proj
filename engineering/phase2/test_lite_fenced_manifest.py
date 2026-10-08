"""Durable intent before object writes, process termination and withdrawal races.

SQLite CAS plus an injected GCS adapter. Not an HTTP deployment/Google proof.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import test_lite_fenced_objects as objects_fixture
from lite_fenced_objects import FencedObjects, ObjectRef, GenerationConflict
from lite_fenced_manifest import FencedManifest, FenceTicket, PendingWritePlans
from lite_privacy_state import OwnerWithdrawn
from lite_attest_state import SQLiteStateStore, StateUnavailable, _encode


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'plans.sqlite'
        self.state = SQLiteStateStore(self.path)
        self.bucket = objects_fixture.Bucket(); self.objects = FencedObjects(self.bucket)
        self.manifest = FencedManifest(self.state, bucket=self.bucket.name)
        self.ref = ObjectRef('a'*32, 'rgbd', 'b'*32)
    def stage(self):
        ticket = self.manifest.begin(self.ref.owner); pin = self.objects.pin(self.ref)
        self.manifest.plan(ticket, pin); return ticket, pin

    def test_registered_entrypoint_persists_plan_before_actual_storage_write(self):
        ticket = self.manifest.begin(self.ref.owner)
        def observe(blob, data, kwargs):
            state = FencedManifest(SQLiteStateStore(self.path), bucket=self.bucket.name)._load(self.ref.owner)[1]
            self.assertEqual(state['writers'][ticket.identifier]['rgbd:' + self.ref.identifier], [0, False])
        self.bucket.before_upload = observe
        self.manifest.write_registered(ticket, self.objects, self.ref, b'data')
        self.manifest.finish(ticket)
        self.assertEqual(self.objects.read_live(self.ref), b'data')

    def test_registered_entrypoint_unknown_storage_outcome_keeps_plan(self):
        ticket = self.manifest.begin(self.ref.owner); self.bucket.lose_response = True
        with self.assertRaises(StateUnavailable):
            self.manifest.write_registered(ticket, self.objects, self.ref, b'committed')
        with self.assertRaises(PendingWritePlans): self.manifest.finish(ticket)
        self.bucket.lose_response = False
        self.manifest.withdraw(self.ref.owner); self.manifest.seal_pending(self.ref.owner, self.objects)
        self.assertEqual(self.bucket.live[self.ref.name][1], b'')

    def test_unavailable_manifest_blocks_entrypoint_before_media_write(self):
        ticket = self.manifest.begin(self.ref.owner)
        with patch.object(self.state, 'compare_exchange', side_effect=StateUnavailable('offline')):
            with self.assertRaises(StateUnavailable):
                self.manifest.write_registered(ticket, self.objects, self.ref, b'data')
        self.assertEqual(self.bucket.live, {})

    def test_acknowledged_write_finishes_but_remains_for_completed_inventory(self):
        ticket, pin = self.stage(); self.objects.write(pin, b'data')
        self.manifest.acknowledge(ticket, pin); self.manifest.finish(ticket)
        self.assertEqual(self.manifest.withdraw(self.ref.owner), 0)
        self.assertEqual(self.objects.read_live(self.ref), b'data')  # Completion still needs inventory/seal.

    def test_unacknowledged_write_cannot_drop_recovery_plan(self):
        ticket, pin = self.stage(); self.objects.write(pin, b'written but no acknowledgement')
        with self.assertRaises(PendingWritePlans): self.manifest.finish(ticket)
        self.assertEqual(self.manifest.withdraw(self.ref.owner), 1)
        self.assertEqual(self.manifest.seal_pending(self.ref.owner, self.objects), 0)
        self.assertEqual(self.bucket.live[self.ref.name][1], b'')
        with self.assertRaises(GenerationConflict): self.objects.write(pin, b'late retry')

    def test_crashed_process_plan_survives_and_fences_not_yet_created_object(self):
        source = Path(__file__).resolve().parents[2]/'Backend/Flask'
        code = '''import json,sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from lite_attest_state import SQLiteStateStore
from lite_fenced_manifest import FencedManifest
from lite_fenced_objects import ObjectRef,PinnedWrite
m=FencedManifest(SQLiteStateStore(sys.argv[2]),bucket='synthetic-lite-fences')
r=ObjectRef('a'*32,'rgbd','b'*32)
t=m.begin(r.owner);m.plan(t,PinnedWrite('synthetic-lite-fences',r,0))
print(json.dumps(dict(owner=t.owner,identifier=t.identifier)),flush=True)
time.sleep(60)
'''
        process = subprocess.Popen([sys.executable, '-B', '-u', '-c', code, str(source), str(self.path)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            ticket = FenceTicket(**json.loads(process.stdout.readline()))
        finally:
            process.kill(); process.communicate(timeout=5)
        self.assertNotEqual(process.returncode, 0)
        restarted = FencedManifest(SQLiteStateStore(self.path), bucket=self.bucket.name)
        with self.assertRaises(PendingWritePlans): restarted.finish(ticket)
        self.assertEqual(restarted.withdraw(self.ref.owner), 1)
        restarted.seal_pending(self.ref.owner, self.objects)
        old = objects_fixture.PinnedWrite(self.bucket.name, self.ref, 0)
        with self.assertRaises(GenerationConflict): self.objects.write(old, b'late in-flight upload')
        self.assertEqual(restarted.withdraw(self.ref.owner), 0)

    def test_close_between_pin_and_plan_prevents_new_work(self):
        ticket = self.manifest.begin(self.ref.owner); pin = self.objects.pin(self.ref)
        self.manifest.withdraw(self.ref.owner)
        with self.assertRaises(OwnerWithdrawn): self.manifest.plan(ticket, pin)
        self.manifest.seal_pending(self.ref.owner, self.objects)
        self.assertEqual(self.bucket.live, {})

    def test_pending_plan_cannot_be_rebased_or_cross_owner_bucket(self):
        ticket, pin = self.stage()
        for invalid in (replace(pin, bucket='other'), replace(pin, ref=replace(self.ref, owner='c'*32)),
                        replace(pin, generation=1)):
            with self.assertRaises(StateUnavailable): self.manifest.plan(ticket, invalid)
        self.manifest.plan(ticket, pin)  # Same generation registration is idempotent.

    def test_seal_failure_retains_plans_for_retry_and_does_not_reopen_admission(self):
        ticket, pin = self.stage(); self.manifest.withdraw(self.ref.owner)
        self.bucket.upload_error = TimeoutError('unknown')
        with self.assertRaises(StateUnavailable): self.manifest.seal_pending(self.ref.owner, self.objects)
        self.assertEqual(self.manifest.withdraw(self.ref.owner), 1)
        with self.assertRaises(OwnerWithdrawn): self.manifest.begin(self.ref.owner)
        self.bucket.upload_error = None
        self.manifest.seal_pending(self.ref.owner, self.objects)
        with self.assertRaises(GenerationConflict): self.objects.write(pin, b'resumed')

    def test_manifest_acknowledgement_failure_is_recoverable_after_restart(self):
        ticket, pin = self.stage(); self.objects.write(pin, b'data')
        with patch.object(self.state, 'compare_exchange', side_effect=StateUnavailable('lost ack')):
            with self.assertRaises(StateUnavailable): self.manifest.acknowledge(ticket, pin)
        restarted = FencedManifest(SQLiteStateStore(self.path), bucket=self.bucket.name)
        restarted.withdraw(self.ref.owner); restarted.seal_pending(self.ref.owner, self.objects)
        self.assertEqual(self.bucket.live[self.ref.name][1], b'')

    def test_full_capacity_is_bounded_and_fits_persistent_state_limit(self):
        tickets = [self.manifest.begin(self.ref.owner) for _ in range(8)]
        for i, ticket in enumerate(tickets):
            for j in range(10):
                ref = replace(self.ref, identifier=f'{i*10+j:064x}')
                self.manifest.plan(ticket, self.objects.pin(ref))
        state = self.state.load(self.manifest._key(self.ref.owner))[1]
        self.assertLess(len(_encode(state)), 16384)
        with self.assertRaises(StateUnavailable):
            self.manifest.plan(tickets[0], self.objects.pin(replace(self.ref, identifier='f'*64)))
        self.manifest.withdraw(self.ref.owner); self.manifest.seal_pending(self.ref.owner, self.objects)
        self.assertEqual(len(self.bucket.live), 80)
        self.assertTrue(all(row[1] == b'' for row in self.bucket.live.values()))

    def test_plan_and_withdraw_race_have_one_durable_order(self):
        for _ in range(12):
            with tempfile.TemporaryDirectory() as folder:
                m = FencedManifest(SQLiteStateStore(Path(folder)/'plans.sqlite'), bucket=self.bucket.name)
                ticket = m.begin(self.ref.owner); pin = self.objects.pin(self.ref); barrier = threading.Barrier(2)
                def register():
                    barrier.wait(5)
                    try: m.plan(ticket, pin); return True
                    except OwnerWithdrawn: return False
                def withdraw(): barrier.wait(5); m.withdraw(self.ref.owner)
                with ThreadPoolExecutor(max_workers=2) as pool:
                    registered = pool.submit(register); closed = pool.submit(withdraw)
                    admitted = registered.result(5); closed.result(5)
                state = m._load(self.ref.owner)[1]
                self.assertEqual(bool(state['writers'][ticket.identifier]), admitted)
                with self.assertRaises(OwnerWithdrawn): m.plan(ticket, pin)

    def test_recovery_requires_closed_admission_and_matching_bucket(self):
        self.stage()
        with self.assertRaises(StateUnavailable): self.manifest.seal_pending(self.ref.owner, self.objects)
        self.manifest.withdraw(self.ref.owner)
        other = objects_fixture.Bucket(); other.name = 'other-bucket'
        with self.assertRaises(StateUnavailable): self.manifest.seal_pending(self.ref.owner, FencedObjects(other))
        self.assertEqual(self.bucket.live, {})

    def test_disappeared_or_malformed_manifest_is_not_completed(self):
        ticket, _ = self.stage()
        with patch.object(self.state, 'load', return_value=None):
            with self.assertRaises(StateUnavailable): self.manifest.finish(ticket)
        revision, state = self.manifest._load(self.ref.owner)
        state['writers'][ticket.identifier]['invalid'] = [0, False]
        self.state.compare_exchange(self.manifest._key(self.ref.owner), revision, state)
        with self.assertRaises(StateUnavailable): self.manifest.withdraw(self.ref.owner)

if __name__ == '__main__': unittest.main()
