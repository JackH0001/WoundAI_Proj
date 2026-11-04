"""Synthetic Apple grants + SQLite CAS + real AES, with a multi-version KMS double.

No network, actual tokens, keys or research records. The transport models KMS's
version-pinned encrypt and key-level decrypt independently of the new cipher.
"""
import copy
import json
import os
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from lite_attest_state import StateUnavailable, SQLiteStateStore
from lite_account_lifecycle import AccountClosing
from lite_grant_key_custody import AccountGrantCipher, b64, unb64, crc32c
from lite_rotating_grant_keys import RotatingGrantCipher, RotatingGrantKeyProvider
from lite_subscription_executor import key
from lite_usage_ledger import digest
from test_lite_grant_key_custody import KeyFixture, KMSSession
from test_lite_subscription_grace import rows
import test_lite_native_purchase as native


RING = 'projects/synthetic-only/locations/test/keyRings/lite'
KEK = RING+'/cryptoKeys/shared-lab'
V1, V2, V3 = [KEK+'/cryptoKeyVersions/'+str(n) for n in (1,2,3)]


class MultiVersionKMS(KMSSession):
    def request(self, method, url, *, json, timeout, allow_redirects):
        name, _, action = url.split('/v1/',1)[1].partition(':')
        if action not in ('encrypt','decrypt'):
            return super().request(method,url,json=json,timeout=timeout,allow_redirects=allow_redirects)
        assert method == 'POST' and timeout == (5,10) and allow_redirects is False
        self.calls.append((action,name)); self.hook(action,name)
        try:
            aad = unb64(json['additionalAuthenticatedData'],4096)
            if int(json['additionalAuthenticatedDataCrc32c']) != crc32c(aad): raise ValueError()
            if action == 'encrypt':
                if self.states[name] != 'ENABLED': raise ValueError()
                plain = unb64(json['plaintext'],32)
                if int(json['plaintextCrc32c']) != crc32c(plain): raise ValueError()
                nonce = os.urandom(12)
                wire = name.rsplit('/',1)[1].encode()+b':'+nonce+AESGCM(self.keys[name]).encrypt(nonce,plain,aad)
                out = dict(name=name,ciphertext=b64(wire),ciphertextCrc32c=str(crc32c(wire)),
                           verifiedPlaintextCrc32c=True,verifiedAdditionalAuthenticatedDataCrc32c=True)
            else:
                wire = unb64(json['ciphertext'],4096)
                if int(json['ciphertextCrc32c']) != crc32c(wire): raise ValueError()
                version, payload = wire.split(b':',1)
                v = name+'/cryptoKeyVersions/'+version.decode('ascii')
                if self.states[v] != 'ENABLED': raise ValueError()
                plain = AESGCM(self.keys[v]).decrypt(payload[:12],payload[12:],aad)
                out = dict(plaintext=b64(plain),plaintextCrc32c=str(crc32c(plain)))
        except Exception:
            return SimpleNamespace(status_code=400,content=b'{}')
        return SimpleNamespace(status_code=200,content=__import__('json').dumps(self.mutate(action,out)).encode())


def provider(kms, write=V1, read=None):
    return RotatingGrantKeyProvider(session=kms,keyring=RING,crypto_key=KEK,
        write_version=write,read_versions=read if read is not None else [V1,V2,V3])


class SharedFixture(KeyFixture):
    def make_cipher(self):
        if not isinstance(self.provider,RotatingGrantKeyProvider):
            self.kms=MultiVersionKMS()
            for v in (V1,V2,V3):self.kms.provision(v)
            self.provider=provider(self.kms)
        return RotatingGrantCipher(provider=self.provider,lifecycle=self.lifecycle,
            store=self.key_store,audit_store=self.key_audit)
    def rotate_to(self, version):
        self.provider=provider(self.kms,version)
        self.cipher=self.make_cipher();self.g.cipher=self.cipher
        # The original authority retains the same mode and indexed context.
        self.g.a.cipher=self.cipher
        return self.cipher


class SharedCustodyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):native.legacy.LinksTests.setUpClass()
    def setUp(self):
        network=patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden'))
        network.start();self.addCleanup(network.stop)
        self.f=SharedFixture(self);self.f.login();self.person=self.f.person
        self.envelope,self.context=self.f.snapshot();self.cipher=self.f.cipher
    def row(self):return self.cipher._read(self.person)[1]
    def rotate(self, target=V2):
        old=digest(self.row());self.cipher=self.f.rotate_to(target)
        return self.cipher.rewrap(self.person,expected_record_sha256=old)
    def erase(self):return self.cipher.destroy_after_revocation(self.f.d,self.person)
    def test_real_indexed_apple_grant_uses_v3_and_no_plaintext_at_rest(self):
        self.assertEqual(self.envelope['v'],3)
        self.assertEqual(self.cipher.open(self.envelope,self.context)['refresh_token'],'private-refresh-token')
        text=repr(rows(self.f.key_store)+rows(self.f.key_audit)+rows(self.f.g.store))
        for secret in ('private-refresh-token','private-access-token','apple-human-1',self.f.g.signed):
            self.assertNotIn(secret,text)
        self.assertNotIn(b64(self.f.kms.keys[V1]),text)
        self.assertNotIn(self.row()['wrapped_dek'],repr(rows(self.f.key_audit)))
    def test_shared_kek_has_distinct_account_keys_and_grant_contexts(self):
        first=self.row();self.f.login('second','apple-human-2');second=self.cipher._read(self.f.person)[1]
        e,c=self.f.snapshot()
        self.assertEqual(first['version'],second['version'])
        self.assertNotEqual(first['dek_id'],second['dek_id']);self.assertNotEqual(first['wrapped_dek'],second['wrapped_dek'])
        with self.assertRaises(StateUnavailable):self.cipher.open(self.envelope,c)
        with self.assertRaises(StateUnavailable):self.cipher.open(e,self.context)
    def test_same_account_devices_share_one_dek_without_reexchange_on_replay(self):
        before=self.row();self.f.login('device-two');self.assertEqual(self.row(),before)
        calls=len(self.f.g.calls);self.f.g.confirm();self.assertEqual(len(self.f.g.calls),calls)
        _,ctx=self.f.snapshot()
        with self.assertRaises(StateUnavailable):self.cipher.open(self.envelope,ctx)
    def test_rotation_preserves_original_envelope_and_ciphertext(self):
        original=copy.deepcopy(self.envelope);receipt=self.rotate()
        self.f.kms.states[V1]='DISABLED'
        self.assertEqual(self.cipher.open(original,self.context)['refresh_token'],'private-refresh-token')
        self.assertEqual(self.envelope,original);self.assertEqual(receipt['target_sha256'],digest(self.row()))
        self.assertEqual(self.row()['versions'],[V1,V2])
    def test_restart_reads_same_envelope_after_two_rotations(self):
        self.rotate();self.rotate(V3);self.f.kms.states[V1]=self.f.kms.states[V2]='DISABLED'
        restarted=self.f.make_cipher()
        self.assertEqual(restarted.open(self.envelope,self.context)['refresh_token'],'private-refresh-token')
        self.assertEqual(self.row()['versions'],[V1,V2,V3])
    def test_new_account_uses_new_write_version_old_account_needs_explicit_rewrap(self):
        self.cipher=self.f.rotate_to(V2);self.f.login('second','apple-human-2')
        self.assertEqual(self.row()['version'],V1);self.assertEqual(self.cipher._read(self.f.person)[1]['version'],V2)
    def test_repeated_confirmed_rotation_never_rewraps_or_reexchanges(self):
        source=digest(self.row());self.cipher=self.f.rotate_to(V2)
        receipt=self.cipher.rewrap(self.person,expected_record_sha256=source);calls=self.f.kms.calls.copy()
        self.assertEqual(self.f.make_cipher().rewrap(self.person,expected_record_sha256=source),receipt)
        self.assertEqual(self.f.kms.calls,calls)
    def test_backward_rotation_and_stale_source_are_refused(self):
        source=digest(self.row());self.rotate()
        with self.assertRaises(StateUnavailable):self.cipher.rewrap(self.person,expected_record_sha256='f'*64)
        self.cipher=self.f.rotate_to(V1)
        with self.assertRaises(StateUnavailable):self.cipher.rewrap(self.person,expected_record_sha256=digest(self.row()))
        self.assertEqual(self.row()['version'],V2)
    def test_lost_key_write_ack_reads_back_exact_target(self):
        source=digest(self.row());self.cipher=self.f.rotate_to(V2);cas=self.f.key_store.compare_exchange
        def lost(k,g,v):
            result=cas(k,g,v)
            if k==self.cipher._key(self.person):raise StateUnavailable('reply lost')
            return result
        with patch.object(self.f.key_store,'compare_exchange',side_effect=lost):
            result=self.cipher.rewrap(self.person,expected_record_sha256=source)
        self.assertEqual(result['target_sha256'],digest(self.row()))
    def test_failed_key_write_retries_saved_proposal_without_new_kms_work(self):
        source=digest(self.row());self.cipher=self.f.rotate_to(V2);cas=self.f.key_store.compare_exchange
        def fail(k,g,v):
            if k==self.cipher._key(self.person):raise StateUnavailable('write failed')
            return cas(k,g,v)
        with patch.object(self.f.key_store,'compare_exchange',side_effect=fail),self.assertRaises(StateUnavailable):
            self.cipher.rewrap(self.person,expected_record_sha256=source)
        calls=self.f.kms.calls.copy();self.assertEqual(self.row()['version'],V1)
        self.cipher.rewrap(self.person,expected_record_sha256=source)
        self.assertEqual(self.row()['version'],V2);self.assertEqual(self.f.kms.calls,calls)
    def test_lost_proposal_ack_reads_back(self):
        source=digest(self.row());self.cipher=self.f.rotate_to(V2);cas=self.f.key_store.compare_exchange
        def lost(k,g,v):
            result=cas(k,g,v)
            if k==key('shared-grant-rewrap-proposal',digest([self.cipher.scope,self.person,source,V2])):raise StateUnavailable('reply lost')
            return result
        with patch.object(self.f.key_store,'compare_exchange',side_effect=lost):
            self.cipher.rewrap(self.person,expected_record_sha256=source)
        self.assertEqual(self.row()['version'],V2)
    def test_receipt_write_failure_resumes_without_rewrapping(self):
        source=digest(self.row());self.cipher=self.f.rotate_to(V2);cas=self.f.key_audit.compare_exchange
        def fail(k,g,v):
            if k==key('shared-grant-rewrap-result',digest([self.cipher.scope,self.person,source,V2])):raise StateUnavailable('audit down')
            return cas(k,g,v)
        with patch.object(self.f.key_audit,'compare_exchange',side_effect=fail),self.assertRaises(StateUnavailable):
            self.cipher.rewrap(self.person,expected_record_sha256=source)
        calls=self.f.kms.calls.copy();self.cipher.rewrap(self.person,expected_record_sha256=source)
        self.assertEqual(self.f.kms.calls,calls)
    def test_audit_failure_before_rotation_leaves_key_and_provider_unchanged(self):
        source=digest(self.row());self.cipher=self.f.rotate_to(V2);before=rows(self.f.key_store),self.f.kms.calls.copy()
        with patch.object(self.f.key_audit,'compare_exchange',side_effect=StateUnavailable('audit down')),self.assertRaises(StateUnavailable):
            self.cipher.rewrap(self.person,expected_record_sha256=source)
        self.assertEqual((rows(self.f.key_store),self.f.kms.calls),before)
    def test_target_roundtrip_mismatch_never_changes_current_key(self):
        source=digest(self.row());self.cipher=self.f.rotate_to(V2);unwrap=self.f.provider.unwrap
        with patch.object(self.f.provider,'unwrap',side_effect=lambda v,w,a:b'X'*32 if v==V2 else unwrap(v,w,a)),self.assertRaises(StateUnavailable):
            self.cipher.rewrap(self.person,expected_record_sha256=source)
        self.assertEqual(self.row()['version'],V1)
    def test_disabled_target_does_not_break_existing_account(self):
        self.f.kms.states[V2]='DISABLED'
        with self.assertRaises(StateUnavailable):self.rotate()
        self.assertEqual(self.cipher.open(self.envelope,self.context)['refresh_token'],'private-refresh-token')
    def test_corrupt_proposal_cannot_be_applied(self):
        source=digest(self.row());self.cipher=self.f.rotate_to(V2)
        ident=digest([self.cipher.scope,self.person,source,V2]);pk=key('shared-grant-rewrap-proposal',ident)
        self.f.key_store.compare_exchange(pk,0,{'v':1})
        with self.assertRaises((StateUnavailable,AccountClosing)):self.cipher.rewrap(self.person,expected_record_sha256=source)
        self.assertEqual(self.row()['version'],V1)
    def test_closure_blocks_only_one_account_without_any_kms_destroy(self):
        backup=copy.deepcopy(self.row());self.f.login('other','apple-human-2');e,c=self.f.snapshot()
        self.f.close_revoke();receipt=self.erase()
        with self.assertRaises(StateUnavailable):self.cipher.open(self.envelope,self.context)
        self.assertEqual(self.cipher.open(e,c)['refresh_token'],'private-refresh-token')
        self.assertIsNone(self.row()['wrapped_dek']);self.assertEqual(receipt['historical_key_access'],'pending_retirement')
        for field in ('grant_key_unrecoverable','account_deleted','physical_backup_deletion_confirmed'):
            self.assertIs(receipt[field],False)
        self.assertNotIn('destroy',[a for a,_ in self.f.kms.calls])
        # Independent proof that an old copy is still decryptable: never call
        # the application fence and mistake access denial for cryptographic erase.
        self.assertEqual(len(self.f.provider.unwrap(V1,backup['wrapped_dek'],self.cipher._aad_for(backup))),32)
    def test_closure_replay_and_restart_do_not_repeat_revocation_or_mutation(self):
        self.f.close_revoke();receipt=self.erase();before=rows(self.f.key_store),rows(self.f.key_audit),self.f.g.calls.copy(),self.f.kms.calls.copy()
        self.cipher=self.f.make_cipher();self.f.g.a.cipher=self.cipher
        self.assertEqual(self.erase(),receipt)
        self.assertEqual((rows(self.f.key_store),rows(self.f.key_audit),self.f.g.calls,self.f.kms.calls),before)
    def test_apple_revocation_failure_prevents_key_closure(self):
        self.f.g.revoke_error=True;self.f.d.begin('device-one','d'*32)
        self.assertEqual(self.f.d.advance(self.person)['apple_revocation'],'pending')
        with self.assertRaises(StateUnavailable):self.erase()
        self.assertEqual(self.row()['phase'],'ready')
    def test_missing_key_never_claims_closure(self):
        self.f.close_revoke();original=self.f.key_store.load
        with patch.object(self.f.key_store,'load',side_effect=lambda k:None if k==self.cipher._key(self.person) else original(k)),self.assertRaises(StateUnavailable):
            self.erase()
    def test_closure_intent_survives_failed_tombstone_and_fences_original_copy(self):
        self.f.close_revoke();cas=self.f.key_store.compare_exchange
        with patch.object(self.f.key_store,'compare_exchange',return_value=False),self.assertRaises(StateUnavailable):self.erase()
        self.assertEqual(self.row()['phase'],'ready')
        with self.assertRaises(StateUnavailable):self.cipher.open(self.envelope,self.context)
        receipt=self.erase();self.assertEqual(receipt['current_key_access'],'closed')
    def test_rewrap_cannot_restart_after_account_closure(self):
        source=digest(self.row());self.f.close_revoke();self.erase();self.cipher=self.f.rotate_to(V2)
        with self.assertRaises((StateUnavailable,AccountClosing)):self.cipher.rewrap(self.person,expected_record_sha256=source)
        self.assertEqual(self.row()['phase'],'closed')
    def test_closure_during_rewrap_blocks_handoff_and_preserves_fence(self):
        source=digest(self.row());self.cipher=self.f.rotate_to(V2);wrap=self.f.provider.wrap
        def close(v,p,a):
            result=wrap(v,p,a)
            self.f.close_revoke();self.erase()
            return result
        with patch.object(self.f.provider,'wrap',side_effect=close),self.assertRaises((StateUnavailable,AccountClosing)):
            self.cipher.rewrap(self.person,expected_record_sha256=source)
        self.assertEqual(self.row()['phase'],'closed')
    def test_rewrap_cas_wins_just_after_closure_intent_closure_resumes(self):
        source=digest(self.row());self.cipher=self.f.rotate_to(V2);cas=self.f.key_store.compare_exchange
        def race(k,g,v):
            if k==self.cipher._key(self.person) and v['phase']=='ready':
                self.f.close_revoke()
                with patch.object(self.f.key_store,'compare_exchange',return_value=False),self.assertRaises(StateUnavailable):self.erase()
            return cas(k,g,v)
        with patch.object(self.f.key_store,'compare_exchange',side_effect=race),self.assertRaises(StateUnavailable):
            self.cipher.rewrap(self.person,expected_record_sha256=source)
        self.assertEqual(self.row()['version'],V2)
        receipt=self.erase();self.assertEqual(receipt['versions'],[V1,V2]);self.assertEqual(self.row()['phase'],'closed')
    def test_closed_key_does_not_recreate_even_if_old_current_row_restored(self):
        backup=copy.deepcopy(self.row());self.f.close_revoke();self.erase()
        generation,_=self.cipher._read(self.person);self.f.key_store.compare_exchange(self.cipher._key(self.person),generation,backup)
        with self.assertRaises(StateUnavailable):self.cipher.seal({'refresh_token':'late'},self.context)
        with self.assertRaises(StateUnavailable):self.cipher.open(self.envelope,self.context)
        self.assertEqual(self.erase()['current_key_access'],'closed')
    def test_closure_can_read_inventory_after_retired_version_removed_from_allowlist(self):
        self.f.close_revoke();receipt=self.erase()
        self.f.provider=provider(self.f.kms,V2,[V2]);self.cipher=self.f.make_cipher();self.f.g.a.cipher=self.cipher
        self.assertEqual(self.erase(),receipt)
    def test_envelope_tampering_and_legacy_shapes_never_fall_back(self):
        for change in ({'v':2},{'v':True},{'scope':'0'*64},{'person':'0'*64},{'dek_id':'0'*64},
                       {'ciphertext':b64(b'x'*50)},{'nonce':b64(b'x'*11)}):
            with self.subTest(change=change),self.assertRaises(StateUnavailable):self.cipher.open(dict(self.envelope,**change),self.context)
    def test_corrupt_current_row_and_missing_audit_never_reset(self):
        rev,c=self.cipher._read(self.person)
        self.f.key_store.compare_exchange(self.cipher._key(self.person),rev,dict(c,wrapped_dek=b64(b'bad')))
        with self.assertRaises(StateUnavailable):self.cipher.prepare(self.context)
    def test_deleted_current_row_is_not_recreated_by_open(self):
        original=self.f.key_store.load;calls=self.f.kms.calls.copy()
        with patch.object(self.f.key_store,'load',side_effect=lambda k:None if k==self.cipher._key(self.person) else original(k)),self.assertRaises(StateUnavailable):
            self.cipher.open(self.envelope,self.context)
        self.assertEqual(self.f.kms.calls,calls)
    def test_existing_per_account_mode_cannot_be_silently_reinterpreted(self):
        with self.assertRaises(ValueError):AccountGrantCipher(provider=self.f.provider,lifecycle=self.f.lifecycle,
            store=self.f.key_store,audit_store=self.f.key_audit)
    def test_missing_shared_key_stops_before_one_time_apple_code_exchange(self):
        self.f.kms.states[V1]='DISABLED';calls=self.f.g.calls.copy()
        with self.assertRaises(StateUnavailable):self.f.login('other','new-person')
        self.assertEqual(self.f.g.calls,calls)

    def test_missing_initialized_key_cannot_be_recreated_by_prepare(self):
        before=rows(self.f.key_store);original=self.f.key_store.load
        with patch.object(self.f.key_store,'load',side_effect=lambda k:None if k==self.cipher._key(self.person) else original(k)),self.assertRaises(StateUnavailable):
            self.cipher.prepare(self.context)
        self.assertEqual(rows(self.f.key_store),before)
    def test_two_rotation_workers_converge_on_one_proposal(self):
        source=digest(self.row());self.cipher=self.f.rotate_to(V2);wrap=self.f.provider.wrap
        gate=threading.Barrier(2)
        def hold(v,p,a):
            result=wrap(v,p,a)
            if v==V2:gate.wait(timeout=5)
            return result
        def execute():
            try:return self.cipher.rewrap(self.person,expected_record_sha256=source)
            except StateUnavailable:return None
        with patch.object(self.f.provider,'wrap',side_effect=hold),ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(execute) for _ in range(2)]
            results=[future.result(timeout=10) for future in futures]
        final=self.cipher.rewrap(self.person,expected_record_sha256=source)
        self.assertTrue(any(result==final for result in results))
        self.assertTrue(all(result is None or result==final for result in results))
        self.assertEqual(self.row()['version'],V2)
        self.assertEqual(self.cipher.open(self.envelope,self.context)['refresh_token'],'private-refresh-token')
    def test_read_handoff_rechecks_closure_even_if_mutable_checkpoint_fails(self):
        self.f.close_revoke();unwrap=self.f.provider.unwrap
        def close(v,w,a):
            plain=unwrap(v,w,a)
            with patch.object(self.f.key_store,'compare_exchange',return_value=False),self.assertRaises(StateUnavailable):self.erase()
            return plain
        with patch.object(self.f.provider,'unwrap',side_effect=close),self.assertRaises(StateUnavailable):
            self.cipher.open(self.envelope,self.context)
        self.assertEqual(self.row()['phase'],'ready')
    def test_audit_evidence_loss_prevents_open_without_repairing_it(self):
        k=self.cipher._proof_key(self.row());load=self.f.key_audit.load;before=rows(self.f.key_audit)
        with patch.object(self.f.key_audit,'load',side_effect=lambda x:None if x==k else load(x)),self.assertRaises(StateUnavailable):
            self.cipher.open(self.envelope,self.context)
        self.assertEqual(rows(self.f.key_audit),before)
    def test_rotation_preserves_account_registry_quota_and_original_grant(self):
        before=rows(self.f.b.registry.store),rows(self.f.f.h.f.ledger.store),rows(self.f.g.store),self.f.g.calls.copy()
        self.rotate()
        self.assertEqual((rows(self.f.b.registry.store),rows(self.f.f.h.f.ledger.store),rows(self.f.g.store),self.f.g.calls),before)
    def test_initial_write_failure_reuses_saved_data_key_before_apple_exchange(self):
        person=self.f.who('new-person');k=self.cipher._key(person);cas=self.f.key_store.compare_exchange
        def fail(name,g,value):
            if name==k:raise StateUnavailable('write unavailable')
            return cas(name,g,value)
        before=self.f.g.calls.copy()
        with patch.object(self.f.key_store,'compare_exchange',side_effect=fail),self.assertRaises(StateUnavailable):
            self.f.login('new-device','new-person')
        self.assertEqual(self.f.g.calls,before)
        pk=key('shared-grant-key-initial-proposal',digest([self.cipher.scope,person]))
        proposed=self.f.key_store.load(pk)[1]
        self.f.g.confirm()
        self.assertEqual(self.cipher._read(person)[1],proposed)
        self.assertEqual(len(self.f.g.calls),len(before)+1)
    def test_legacy_progress_cannot_silently_claim_shared_key_destruction(self):
        from lite_disposition_progress_probe import ProgressFixture
        from lite_disposition_progress import DispositionProgress
        with patch('lite_disposition_progress_probe.KeyFixture',SharedFixture):
            f=ProgressFixture(self,attach=False)
        before=rows(f.store('progress')),rows(f.store('progress-audit'))
        with self.assertRaises(ValueError):DispositionProgress(deletion=f.d,results=f.results,cipher=f.f.cipher,
            store=f.store('progress'),audit_store=f.store('progress-audit'))
        self.assertEqual((rows(f.store('progress')),rows(f.store('progress-audit'))),before)
    def test_native_composer_rejects_unapproved_scheme_before_state_writes(self):
        from test_lite_native_backend import Fixture
        f=Fixture(self);before=f.snapshot()
        with self.assertRaises(ValueError):f.compose(key_provider=provider(self.f.kms))
        self.assertEqual(f.snapshot(),before)

    def test_uncommitted_rewrap_is_in_closed_account_dependency_inventory(self):
        source=digest(self.row());self.cipher=self.f.rotate_to(V2);cas=self.f.key_store.compare_exchange
        def fail(k,g,v):
            if k==self.cipher._key(self.person):raise StateUnavailable('write failed')
            return cas(k,g,v)
        with patch.object(self.f.key_store,'compare_exchange',side_effect=fail),self.assertRaises(StateUnavailable):
            self.cipher.rewrap(self.person,expected_record_sha256=source)
        self.assertEqual(self.row()['version'],V1)
        self.f.close_revoke();receipt=self.erase()
        self.assertEqual(receipt['versions'],[V1,V2]);self.assertFalse(receipt['retirement_inventory_verified'])
    def test_lost_kms_wrap_reply_still_records_potential_new_dependency(self):
        source=digest(self.row());self.cipher=self.f.rotate_to(V2);wrap=self.f.provider.wrap
        def lost(v,p,a):wrap(v,p,a);raise StateUnavailable('provider reply lost')
        with patch.object(self.f.provider,'wrap',side_effect=lost),self.assertRaises(StateUnavailable):
            self.cipher.rewrap(self.person,expected_record_sha256=source)
        self.f.close_revoke();self.assertEqual(self.erase()['versions'],[V1,V2])
    def test_dependency_write_failure_blocks_kms_wrap(self):
        source=digest(self.row());self.cipher=self.f.rotate_to(V2);cas=self.f.key_store.compare_exchange
        dependency=key('shared-grant-key-dependencies',digest([self.cipher.scope,self.person]))
        before=self.f.kms.calls.count(('encrypt',V2))
        def fail(k,g,v):
            if k==dependency:raise StateUnavailable('write unavailable')
            return cas(k,g,v)
        with patch.object(self.f.key_store,'compare_exchange',side_effect=fail),self.assertRaises(StateUnavailable):
            self.cipher.rewrap(self.person,expected_record_sha256=source)
        self.assertEqual(self.f.kms.calls.count(('encrypt',V2)),before)


class SharedProviderTests(unittest.TestCase):
    def setUp(self):self.k=MultiVersionKMS();self.k.provision(V1);self.k.provision(V2);self.p=provider(self.k)
    def test_version_bound_aad_and_crc_roundtrip(self):
        wrapped=self.p.wrap(V1,b'x'*32,b'aad-v1')
        self.assertEqual(self.p.unwrap(V1,wrapped,b'aad-v1'),b'x'*32)
        with self.assertRaises(StateUnavailable):self.p.unwrap(V2,wrapped,b'aad-v2')
        self.assertEqual(self.k.calls[0],('encrypt',V1));self.assertEqual(self.k.calls[1],('decrypt',KEK))
    def test_only_pinned_write_version_and_listed_read_versions(self):
        for v in (V2,V1+'?x=1',V1.replace('/1','/8')):
            with self.assertRaises(ValueError):self.p.wrap(v,b'x'*32,b'aad')
        with self.assertRaises(ValueError):self.p.describe(V1.replace('synthetic-only','other'))
        self.assertEqual(self.k.calls,[])
    def test_destroy_cannot_reach_transport(self):
        with self.assertRaises(StateUnavailable):self.p.schedule_destroy(V1)
        self.assertEqual(self.k.calls,[])
    def test_unbounded_duplicate_cross_key_versions_rejected(self):
        for read in ([],[V1,V1],[V1]*65,[V1,V2.replace('shared-lab','another')],[V1+'/../2']):
            with self.subTest(read=read),self.assertRaises(ValueError):provider(self.k,read=read)
    def test_wrong_response_version_and_crc_rejected(self):
        for mutate in (lambda a,r:dict(r,name=V2),lambda a,r:dict(r,ciphertextCrc32c='0')):
            self.k.mutate=mutate
            with self.assertRaises(StateUnavailable):self.p.wrap(V1,b'x'*32,b'aad')


if __name__=='__main__':unittest.main()
