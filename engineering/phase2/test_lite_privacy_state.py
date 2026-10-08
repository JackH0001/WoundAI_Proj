"""Offline writer-drain tests; real SQLite CAS and injected GCS transport only."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'Backend/Flask'))
from lite_privacy_state import PrivacyState, OwnerWithdrawn, WriterLimit
from lite_attest_state import SQLiteStateStore, GCSStateStore, StateUnavailable
from test_lite_attest_state import FakeBucket


class PrivacyTests(unittest.TestCase):
    owner = 'a' * 32

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'privacy.sqlite'
        self.store = SQLiteStateStore(self.path); self.privacy = PrivacyState(self.store)

    def test_withdrawal_waits_for_each_writer_and_permanently_closes_admission(self):
        a=self.privacy.begin_write(self.owner); b=self.privacy.begin_write(self.owner)
        self.assertEqual(self.privacy.withdraw(self.owner),2)
        with self.assertRaises(OwnerWithdrawn):self.privacy.begin_write(self.owner)
        self.privacy.finish_write(a)
        self.assertTrue(self.privacy.is_withdrawn(self.owner))
        with self.assertRaises(OwnerWithdrawn):self.privacy.begin_write(self.owner)
        self.assertEqual(self.privacy.withdraw(self.owner),1)
        self.privacy.finish_write(b)
        self.assertTrue(self.privacy.is_withdrawn(self.owner))
        with self.assertRaises(OwnerWithdrawn):self.privacy.begin_write(self.owner)
        self.assertEqual(self.privacy.withdraw(self.owner),0)
        self.assertTrue(self.privacy.is_withdrawn(self.owner))
        with self.assertRaises(OwnerWithdrawn):self.privacy.begin_write(self.owner)

    def test_restart_keeps_unreleased_writer_pending_without_clock_expiry(self):
        ticket=self.privacy.begin_write(self.owner)
        self.assertEqual(self.privacy.withdraw(self.owner),1)
        restarted=PrivacyState(SQLiteStateStore(self.path))
        for _ in range(5):self.assertEqual(restarted.withdraw(self.owner),1)
        restarted.finish_write(ticket);self.assertEqual(restarted.withdraw(self.owner),0)

    def test_release_is_idempotent_and_never_reopens_owner(self):
        ticket=self.privacy.begin_write(self.owner);self.privacy.withdraw(self.owner)
        self.privacy.finish_write(ticket);self.privacy.finish_write(ticket)
        self.assertEqual(self.privacy.withdraw(self.owner),0)
        self.assertTrue(self.privacy.is_withdrawn(self.owner))

    def test_owner_isolation_and_idle_withdrawal(self):
        ticket=self.privacy.begin_write('other')
        self.assertEqual(self.privacy.withdraw(self.owner),0)
        self.assertFalse(self.privacy.is_withdrawn('other'))
        self.privacy.finish_write(ticket)

    def test_writer_limit_prevents_unbounded_security_state(self):
        tickets=[self.privacy.begin_write(self.owner) for _ in range(8)]
        with self.assertRaises(WriterLimit):self.privacy.begin_write(self.owner)
        self.privacy.finish_write(tickets.pop())
        self.privacy.begin_write(self.owner)
        self.assertEqual(self.privacy.withdraw(self.owner),8)

    def test_unknown_commit_result_remains_pending_not_success(self):
        original=self.store.compare_exchange
        def lost(*args):
            original(*args);raise StateUnavailable('lost acknowledgement')
        with patch.object(self.store,'compare_exchange',side_effect=lost):
            with self.assertRaises(StateUnavailable):self.privacy.begin_write(self.owner)
        self.assertEqual(self.privacy.withdraw(self.owner),1)
        with self.assertRaises(OwnerWithdrawn):self.privacy.begin_write(self.owner)

    def test_failed_release_never_claims_writer_drained(self):
        ticket=self.privacy.begin_write(self.owner);self.privacy.withdraw(self.owner)
        with patch.object(self.store,'compare_exchange',side_effect=StateUnavailable('unavailable')):
            with self.assertRaises(StateUnavailable):self.privacy.finish_write(ticket)
        self.assertEqual(self.privacy.withdraw(self.owner),1)

    def test_failed_revocation_does_not_claim_acceptance(self):
        with patch.object(self.store,'compare_exchange',return_value=False):
            with self.assertRaises(StateUnavailable):self.privacy.withdraw(self.owner)
        self.assertFalse(self.privacy.is_withdrawn(self.owner))

    def test_invalid_state_and_missing_store_are_fail_closed(self):
        for changed in [dict(v=True),dict(withdrawn='false'),dict(writers=['bad']),dict(owner='other'),
                        dict(writers=['1'*32,'1'*32]),dict(extra=1)]:
            state=dict(v=1,owner=self.owner,withdrawn=False,writers=[]);state.update(changed)
            with self.subTest(changed=changed),patch.object(self.store,'load',return_value=(1,state)):
                for operation in [self.privacy.begin_write,self.privacy.withdraw,self.privacy.is_withdrawn]:
                    with self.assertRaises(StateUnavailable):operation(self.owner)
        with patch.object(self.store,'load',side_effect=StateUnavailable('offline')):
            with self.assertRaises(StateUnavailable):self.privacy.withdraw(self.owner)

    def test_independent_sqlite_workers_keep_all_tickets(self):
        def begin(_):return PrivacyState(SQLiteStateStore(self.path)).begin_write(self.owner)
        with ThreadPoolExecutor(max_workers=8) as pool:tickets=list(pool.map(begin,range(8)))
        self.assertEqual(len({t.identifier for t in tickets}),8)
        self.assertEqual(self.privacy.withdraw(self.owner),8)
        with ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(self.privacy.finish_write,tickets))
        self.assertEqual(self.privacy.withdraw(self.owner),0)

    def test_racing_withdrawal_and_admission_have_a_single_order(self):
        bucket=FakeBucket()
        for backend in ('sqlite','gcs'):
            for attempt in range(10):
                owner=f'{backend}-{attempt}';barrier=threading.Barrier(2)
                def coordinator():
                    return PrivacyState(SQLiteStateStore(self.path) if backend=='sqlite'
                        else GCSStateStore(bucket,prefix='privacy'))
                def begin():
                    barrier.wait(5)
                    try:return coordinator().begin_write(owner)
                    except OwnerWithdrawn:return None
                def withdraw():
                    barrier.wait(5);return coordinator().withdraw(owner)
                with ThreadPoolExecutor(max_workers=2) as pool:
                    writer=pool.submit(begin);withdrawal=pool.submit(withdraw)
                    ticket,count=writer.result(5),withdrawal.result(5)
                self.assertEqual(count,0 if ticket is None else 1)
                self.assertTrue(coordinator().is_withdrawn(owner))
                if ticket is not None:coordinator().finish_write(ticket)
                self.assertEqual(coordinator().withdraw(owner),0)

    def test_independent_gcs_workers_cannot_overwrite_revocation(self):
        bucket=FakeBucket()
        def state():return PrivacyState(GCSStateStore(bucket,prefix='privacy'))
        tickets=[]
        with ThreadPoolExecutor(max_workers=8) as pool:
            tickets=list(pool.map(lambda _:state().begin_write(self.owner),range(8)))
        self.assertEqual(state().withdraw(self.owner),8)
        with ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(lambda t:state().finish_write(t),tickets))
        self.assertEqual(state().withdraw(self.owner),0)
        with self.assertRaises(OwnerWithdrawn):state().begin_write(self.owner)


if __name__=='__main__':unittest.main()
