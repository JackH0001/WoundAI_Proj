"""Synthetic CAS races: current proposal erasure is not backup crypto-erasure."""
import unittest
import json
from unittest.mock import patch
from lite_attest_state import StateUnavailable
from lite_account_lifecycle import AccountClosing
from lite_subscription_executor import key
from lite_usage_ledger import digest
from test_lite_rotating_grant_keys import SharedFixture, V2, V3, native, rows


def wrapped(value):
    if isinstance(value, (bytes, str)):
        try: return wrapped(json.loads(value))
        except (ValueError, TypeError): return 0
    if isinstance(value, dict):
        return int(bool(value.get('wrapped_dek'))) + sum(wrapped(v) for v in value.values())
    if isinstance(value, (tuple, list)): return sum(wrapped(v) for v in value)
    return 0


class ProposalErasureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): native.legacy.LinksTests.setUpClass()
    def setUp(self):
        self.f = SharedFixture(self); self.f.login()
        self.person = self.f.person; self.c = self.f.cipher
    def rotate(self, target=V2):
        source = digest(self.c._read(self.person)[1])
        self.c = self.f.rotate_to(target)
        return self.c.rewrap(self.person, expected_record_sha256=source)
    def close(self):
        self.f.close_revoke()
        return self.c.destroy_after_revocation(self.f.d, self.person)
    def test_close_removes_initial_and_all_rewrap_current_copies(self):
        self.rotate(); self.rotate(V3)
        self.assertEqual(wrapped(rows(self.f.key_store)), 4)
        receipt = self.close()
        self.assertEqual(wrapped(rows(self.f.key_store)), 0)
        self.assertFalse(receipt['grant_key_unrecoverable'])
        self.assertEqual(wrapped(rows(self.f.key_audit)), 0)
        self.assertEqual(receipt['current_proposal_tombstones'], 3)

    def test_failed_tombstone_blocks_receipt_and_restart_completes(self):
        self.rotate(); self.f.close_revoke()
        pk = key('shared-grant-key-initial-proposal', digest([self.c.scope, self.person]))
        cas = self.f.key_store.compare_exchange
        def fail(k, g, v):
            if k == pk: raise StateUnavailable('injected unavailable')
            return cas(k, g, v)
        with patch.object(self.f.key_store, 'compare_exchange', side_effect=fail):
            with self.assertRaises(StateUnavailable): self.c.destroy_after_revocation(self.f.d, self.person)
        # Hash ordering can clean the other proposal before reaching this fault.
        self.assertGreater(wrapped(self.f.key_store.load(pk)[1]), 0)
        self.assertIn(wrapped(rows(self.f.key_store)), (1, 2))
        self.c = self.f.make_cipher(); self.f.g.a.cipher = self.c
        self.c.destroy_after_revocation(self.f.d, self.person)
        self.assertEqual(wrapped(rows(self.f.key_store)), 0)

    def test_lost_tombstone_reply_is_verified_and_replay_is_noop(self):
        self.rotate(); self.f.close_revoke(); cas = self.f.key_store.compare_exchange
        def lost(k, g, v):
            result = cas(k, g, v)
            if v.get('schema') == 'shared-proposal-tombstone/1': raise StateUnavailable('reply lost')
            return result
        with patch.object(self.f.key_store, 'compare_exchange', side_effect=lost):
            receipt = self.c.destroy_after_revocation(self.f.d, self.person)
        before = rows(self.f.key_store), rows(self.f.key_audit), list(self.f.kms.calls)
        self.assertEqual(self.c.destroy_after_revocation(self.f.d, self.person), receipt)
        self.assertEqual(before, (rows(self.f.key_store), rows(self.f.key_audit), self.f.kms.calls))

    def test_late_proposal_write_after_closure_cannot_repopulate_slot(self):
        source = digest(self.c._read(self.person)[1]); self.c = self.f.rotate_to(V2)
        pk = key('shared-grant-rewrap-proposal', digest([self.c.scope, self.person, source, V2]))
        cas = self.f.key_store.compare_exchange; fired = []
        def race(k, g, v):
            if k == pk and v.get('record') and not fired:
                fired.append(True); self.close()
            return cas(k, g, v)
        with patch.object(self.f.key_store, 'compare_exchange', side_effect=race):
            with self.assertRaises((StateUnavailable, AccountClosing)):
                self.c.rewrap(self.person, expected_record_sha256=source)
        self.assertTrue(fired)
        self.assertEqual(wrapped(rows(self.f.key_store)), 0)
        self.assertEqual(self.f.key_store.load(pk)[1]['schema'], 'shared-proposal-tombstone/1')

    def test_late_registry_write_loses_to_closed_generation(self):
        source = digest(self.c._read(self.person)[1]); self.c = self.f.rotate_to(V2)
        ik = self.c._proposal_index_key(self.person); cas = self.f.key_store.compare_exchange
        fired = []
        def race(k, g, v):
            if k == ik and v.get('phase') == 'open' and not fired:
                fired.append(True); self.close()
            return cas(k, g, v)
        with patch.object(self.f.key_store, 'compare_exchange', side_effect=race):
            with self.assertRaises((StateUnavailable, AccountClosing)):
                self.c.rewrap(self.person, expected_record_sha256=source)
        self.assertTrue(fired); self.assertEqual(wrapped(rows(self.f.key_store)), 0)

    def test_legacy_missing_inventory_cannot_claim_complete_erasure(self):
        self.rotate(); self.f.close_revoke(); load = self.f.key_store.load
        ik = self.c._proposal_index_key(self.person)
        with patch.object(self.f.key_store, 'load', side_effect=lambda k: None if k == ik else load(k)):
            with self.assertRaisesRegex(StateUnavailable, 'explicit migration'):
                self.c.destroy_after_revocation(self.f.d, self.person)

    def test_other_account_current_key_and_proposals_are_unchanged(self):
        self.f.login('another', 'apple-human-2'); person2 = self.f.person
        envelope, context = self.f.snapshot()
        old = self.c._read(person2)
        self.close()
        self.assertEqual(self.c._read(person2), old)
        self.assertEqual(wrapped(rows(self.f.key_store)), 2)
        self.assertEqual(self.c.open(envelope, context)['refresh_token'], 'private-refresh-token')


if __name__ == '__main__': unittest.main()
