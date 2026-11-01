"""Real AES/SQLite and a local KMS REST double. No cloud resource or user data."""
import base64
import copy
import hashlib
import hmac
import json
import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from lite_grant_key_custody import AccountGrantCipher, GoogleGrantKeyProvider, b64, unb64, crc32c
from lite_apple_grants import GrantCipher
from lite_account_identity import AccountIdentityBridge, IdentityRejected
from lite_attest_state import SQLiteStateStore, StateUnavailable
from lite_apple_oauth import TOKEN_URL, REVOKE_URL
from lite_subscription_executor import key
from lite_usage_ledger import digest
from test_lite_account_lifecycle import Fixture
from test_lite_account_deletion import coordinator
from test_lite_subscription_grace import rows
import test_lite_native_purchase as native


class KMSSession:
    """Only synthetic wrapping keys exist; exported material never reaches state."""
    def __init__(self):
        self.keys={}; self.states={}; self.calls=[]; self.hook=lambda action,name:None
        self.metadata={}; self.mutate=lambda action,body:body
    def provision(self, version):
        self.keys[version]=AESGCM.generate_key(bit_length=256);self.states[version]='ENABLED'
    def destroy_now(self,version):
        self.states[version]='DESTROYED';del self.keys[version]
    def request(self, method,url,*,json,timeout,allow_redirects):
        assert url.startswith('https://cloudkms.googleapis.com/v1/')
        assert timeout==(5,10) and allow_redirects is False
        path=url.split('/v1/',1)[1];name,_,action=path.partition(':')
        action=action or 'get';self.calls.append((action,name));self.hook(action,name)
        version = name+'/cryptoKeyVersions/1' if action=='decrypt' else name
        if version not in self.states:return SimpleNamespace(status_code=404,content=b'{}')
        state=self.states[version]
        if action in ('get','destroy'):
            if action=='destroy':self.states[version]=state='DESTROY_SCHEDULED'
            out=dict(name=version,state=state,algorithm='GOOGLE_SYMMETRIC_ENCRYPTION',protectionLevel='SOFTWARE',
                     reimportEligible=False,**self.metadata)
        elif action=='encrypt':
            assert method=='POST'
            if state!='ENABLED':return SimpleNamespace(status_code=400,content=b'{}')
            plain=unb64(json['plaintext'],32);aad=unb64(json['additionalAuthenticatedData'],4096)
            assert json['plaintextCrc32c']==str(crc32c(plain))
            assert json['additionalAuthenticatedDataCrc32c']==str(crc32c(aad))
            nonce=os.urandom(12);raw=nonce+AESGCM(self.keys[version]).encrypt(nonce,plain,aad)
            out=dict(name=version,ciphertext=b64(raw),ciphertextCrc32c=str(crc32c(raw)),
                     verifiedPlaintextCrc32c=True,verifiedAdditionalAuthenticatedDataCrc32c=True)
        elif action=='decrypt':
            if state!='ENABLED':return SimpleNamespace(status_code=400,content=b'{}')
            raw=unb64(json['ciphertext'],4096);aad=unb64(json['additionalAuthenticatedData'],4096)
            assert json['ciphertextCrc32c']==str(crc32c(raw))
            assert json['additionalAuthenticatedDataCrc32c']==str(crc32c(aad))
            plain=AESGCM(self.keys[version]).decrypt(raw[:12],raw[12:],aad)
            out=dict(plaintext=b64(plain),plaintextCrc32c=str(crc32c(plain)))
        else:raise AssertionError(action)
        # Deliberately don't retain request bodies: encrypt carries a plaintext DEK.
        out=self.mutate(action,out)
        return SimpleNamespace(status_code=200,content=__import__('json').dumps(out).encode())


class KeyFixture(Fixture):
    def __init__(self,host):
        super().__init__(host);root=self.f.h.f.root
        self.kms=KMSSession()
        self.provider=GoogleGrantKeyProvider(session=self.kms,keyring='projects/synthetic-only/locations/test/keyRings/lite')
        self.key_store=SQLiteStateStore(root/'account-keys.sqlite')
        self.key_audit=SQLiteStateStore(root/'account-key-audit.sqlite')
        self.cipher=self.make_cipher()
        self.g.store=SQLiteStateStore(root/'account-key-grant-vault.sqlite');self.g.cipher=self.cipher
        self.g.a=self.authority()
        old=self.b
        self.b=self.f.b=AccountIdentityBridge(runtime=old.runtime,verifier=old.verifier,store=old.store,audit_store=old.audit,
            namespace=old.namespace,identity_key=old.secret,grants=self.g.a,lifecycle=self.lifecycle)
        self.d=coordinator(self)
    def make_cipher(self):
        return AccountGrantCipher(provider=self.provider,lifecycle=self.lifecycle,store=self.key_store,audit_store=self.key_audit)
    def who(self,subject):
        return hmac.new(self.b.secret,(self.b.namespace+'\0'+self.b.verifier.issuer+'\0'+subject).encode(),hashlib.sha256).hexdigest()
    def login(self,device='device-one',subject='apple-human-1',provision=True):
        person=self.who(subject);version=self.provider.version(self.cipher.scope,person)
        if provision and version not in self.kms.states:self.kms.provision(version)
        return super().login(device,subject)
    def snapshot(self):
        c=copy.deepcopy(self.g.a._row(self.g.a._id(self.g.c['installation'],self.g.c['operation']))[1])
        return c['envelope'],c['context']
    def close_revoke(self):
        self.d.begin('device-one','d'*32);return self.d.advance(self.who('apple-human-1'))


class CustodyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):native.legacy.LinksTests.setUpClass()
    def setUp(self):
        self.f=KeyFixture(self);self.f.login();self.person=self.f.person
        self.envelope,self.context=self.f.snapshot();self.cipher=self.f.cipher
        self.version=self.f.provider.version(self.cipher.scope,self.person)
    def erase(self):return self.cipher.destroy_after_revocation(self.f.d,self.person)
    def test_real_grant_capture_uses_v2_account_key_without_plaintext_state(self):
        self.assertEqual(self.envelope['v'],2)
        self.assertEqual(self.cipher.open(self.envelope,self.context)['refresh_token'],'private-refresh-token')
        raw=repr(rows(self.f.key_store)+rows(self.f.key_audit)+rows(self.f.g.store)+rows(self.f.g.audit))
        for secret in ('private-refresh-token','private-access-token','apple-human-1',self.f.g.signed):self.assertNotIn(secret,raw)
        self.assertNotIn(b64(self.f.kms.keys[self.version]),raw)
    def test_no_cross_account_key_and_same_account_all_devices_share_one_dek(self):
        first=rows(self.f.key_store);self.f.login('device-two');self.assertEqual(first,rows(self.f.key_store))
        self.f.login('device-other','apple-human-2');e,c=self.f.snapshot()
        self.assertNotEqual(e['version'],self.envelope['version'])
        with self.assertRaises(StateUnavailable):self.cipher.open(self.envelope,c)
    def test_aad_binds_grant_and_scope(self):
        self.f.login('device-two');_,c=self.f.snapshot()
        with self.assertRaises(StateUnavailable):self.cipher.open(self.envelope,c)
        bad=dict(self.envelope,scope='0'*64)
        with self.assertRaises(StateUnavailable):self.cipher.open(bad,self.context)
    def test_corrupt_ciphertext_refused(self):
        raw=bytearray(unb64(self.envelope['ciphertext'],10016));raw[0]^=1
        with self.assertRaises(StateUnavailable):self.cipher.open(dict(self.envelope,ciphertext=b64(bytes(raw))),self.context)
    def test_legacy_envelope_never_falls_back(self):
        old=GrantCipher(keys={'old':b'x'*32},current='old').seal({'refresh_token':'legacy'},self.context)
        with self.assertRaises(StateUnavailable):self.cipher.open(old,self.context)
    def test_legacy_authority_cannot_downgrade_new_vault(self):
        self.f.g.cipher=GrantCipher(keys={'old':b'v'*32},current='old')
        with self.assertRaises(StateUnavailable):self.f.authority()
    def test_existing_legacy_vault_requires_explicit_migration(self):
        # Same authority/lifecycle, old global-key vault already initialized.
        current=self.f.g.store
        self.f.g.store=SQLiteStateStore(self.f.f.h.f.root/'indexed-grant-vault.sqlite')
        with self.assertRaisesRegex(StateUnavailable,'key migration'):self.f.authority()
        self.f.g.store=current
    def test_missing_key_refuses_before_apple_exchange(self):
        before=len(self.f.g.calls)
        with self.assertRaises(StateUnavailable):self.f.login('new','apple-human-new',provision=False)
        self.assertEqual(len(self.f.g.calls),before)
    def test_provisioned_resource_must_be_generated_not_imported(self):
        self.f.kms.metadata={'importTime':'2026-01-01T00:00:00Z'}
        with self.assertRaises(StateUnavailable):self.cipher.open(self.envelope,self.context)
    def test_reimportable_key_refused(self):
        self.f.kms.mutate=lambda action,r:dict(r,reimportEligible=True) if action=='get' else r
        with self.assertRaises(StateUnavailable):self.cipher.open(self.envelope,self.context)
    def test_disabled_key_is_not_erasure(self):
        self.f.kms.states[self.version]='DISABLED'
        with self.assertRaises(StateUnavailable):self.cipher.open(self.envelope,self.context)
        with self.assertRaises((StateUnavailable,IdentityRejected)):self.erase()
    def test_active_account_cannot_schedule_destruction(self):
        with self.assertRaises(IdentityRejected):self.erase()
        self.assertNotIn(('destroy',self.version),self.f.kms.calls)
    def test_pending_revocation_prevents_key_destruction(self):
        self.f.d.begin('device-one','d'*32)
        with self.assertRaises(StateUnavailable):self.erase()
        self.f.g.revoke_error=True;self.f.d.advance(self.person)
        with self.assertRaises(StateUnavailable):self.erase()
        self.assertNotIn(('destroy',self.version),self.f.kms.calls)
    def test_schedule_not_completion_and_both_backups_and_other_account_checked(self):
        self.f.login('device-other','apple-human-2');other,ctx=self.f.snapshot()
        self.f.close_revoke();r=self.erase()
        self.assertEqual(r['kms_state'],'DESTROY_SCHEDULED');self.assertFalse(r['grant_key_unrecoverable'])
        with self.assertRaises(StateUnavailable):self.cipher.open(self.envelope,self.context)
        # A scheduled key could be restored by an operator, but custody stays fenced.
        self.f.kms.states[self.version]='ENABLED'
        with self.assertRaises(StateUnavailable):self.cipher.open(self.envelope,self.context)
        self.erase();self.f.kms.destroy_now(self.version);r=self.erase()
        self.assertTrue(r['grant_key_unrecoverable']);self.assertFalse(r['account_deleted'])
        self.assertFalse(r['physical_backup_deletion_confirmed'])
        self.assertEqual(self.cipher.open(other,ctx)['refresh_token'],'private-refresh-token')
    def test_restored_wrapped_key_backup_cannot_decrypt_after_provider_destruction(self):
        backup=copy.deepcopy(self.cipher._read(self.person)[1])
        self.f.close_revoke();self.erase();self.f.kms.destroy_now(self.version);self.erase()
        k=self.cipher._key(self.person);rev,_=self.f.key_store.load(k)
        self.f.key_store.compare_exchange(k,rev,backup)
        with self.assertRaises(StateUnavailable):self.f.make_cipher().open(self.envelope,self.context)
        # Independent check bypasses our local policy fence entirely: even a
        # raw backup of the wrapping envelope cannot be unwrapped by KMS.
        with self.assertRaises(StateUnavailable):
            self.f.provider.unwrap(self.version,backup['wrapped_dek'],self.cipher._aad(self.person))
    def test_scheduled_key_restored_with_old_database_still_fenced_by_audit(self):
        backup=copy.deepcopy(self.cipher._read(self.person)[1])
        self.f.close_revoke();self.erase();self.f.kms.states[self.version]='ENABLED'
        k=self.cipher._key(self.person);rev,_=self.f.key_store.load(k)
        self.f.key_store.compare_exchange(k,rev,backup)
        with self.assertRaises(StateUnavailable):self.f.make_cipher().open(self.envelope,self.context)
    def test_dek_removed_from_current_state_is_not_claimed_erased(self):
        self.f.close_revoke()
        with patch.object(self.cipher,'_read',return_value=None),self.assertRaises(StateUnavailable):self.erase()
        self.assertEqual(self.f.kms.states[self.version],'ENABLED')
    def test_schedule_reply_loss_resumes_by_readback_without_double_destroy(self):
        self.f.close_revoke();real=self.f.kms.mutate
        def lose(action,row):
            if action=='destroy':raise OSError('lost synthetic response')
            return row
        self.f.kms.mutate=lose
        with self.assertRaises(StateUnavailable):self.erase()
        self.f.kms.mutate=real;self.assertEqual(self.erase()['kms_state'],'DESTROY_SCHEDULED')
        self.assertEqual(self.f.kms.calls.count(('destroy',self.version)),1)
    def test_audit_failure_never_dispatches_destroy(self):
        self.f.close_revoke()
        with patch.object(self.f.key_audit,'compare_exchange',side_effect=StateUnavailable('audit unavailable')):
            with self.assertRaises(StateUnavailable):self.erase()
        self.assertNotIn(('destroy',self.version),self.f.kms.calls)
    def test_fence_write_loss_reads_back_before_destroy(self):
        self.f.close_revoke();real=self.f.key_store.compare_exchange
        def lost(k,rev,c):
            r=real(k,rev,c)
            if c.get('phase')=='destroying':raise StateUnavailable('lost')
            return r
        with patch.object(self.f.key_store,'compare_exchange',side_effect=lost):r=self.erase()
        self.assertEqual(r['kms_state'],'DESTROY_SCHEDULED')
    def test_failed_fence_write_does_not_dispatch(self):
        self.f.close_revoke()
        with patch.object(self.f.key_store,'compare_exchange',return_value=False):
            with self.assertRaises(StateUnavailable):self.erase()
        self.assertNotIn(('destroy',self.version),self.f.kms.calls)
    def test_destroyed_provider_checkpoint_loss_is_recoverable(self):
        self.f.close_revoke();self.erase();self.f.kms.destroy_now(self.version)
        real=self.f.key_store.compare_exchange
        def lost(k,rev,c):
            r=real(k,rev,c)
            if c.get('phase')=='destroyed':raise StateUnavailable('lost')
            return r
        with patch.object(self.f.key_store,'compare_exchange',side_effect=lost):r=self.erase()
        self.assertTrue(r['grant_key_unrecoverable']);self.assertEqual(self.erase(),r)
    def test_destroyed_state_provider_contradiction_fails(self):
        self.f.close_revoke();self.erase();self.f.kms.destroy_now(self.version);self.erase()
        self.f.kms.states[self.version]='DISABLED'
        with self.assertRaises(StateUnavailable):self.erase()
    def test_old_cipher_instance_cannot_cache_key_across_destruction(self):
        old=self.f.make_cipher();old.open(self.envelope,self.context)
        self.f.close_revoke();self.erase();self.f.kms.destroy_now(self.version);self.erase()
        with self.assertRaises(StateUnavailable):old.open(self.envelope,self.context)
    def test_revocation_keeps_usage_registry_and_all_original_ciphertext_snapshots(self):
        stores=[self.f.b.registry.store,self.f.f.h.f.ledger.store]
        before=[rows(s) for s in stores];original=copy.deepcopy(self.envelope)
        self.f.close_revoke();self.erase();self.f.kms.destroy_now(self.version);self.erase()
        self.assertEqual(before,[rows(s) for s in stores]);self.assertEqual(original,self.envelope)
        self.assertFalse(self.f.d.status('device-one','d'*32)['account_deleted'])
    def test_refresh_outputs_use_same_account_key(self):
        self.f.f.h.a.now+=86401
        # Synthetic refresh token carries a current JWT with the original authentication time.
        old=self.context['issued_at'];c=self.f.g.c
        self.f.f.verifier.fetched_at=self.f.f.now()-1;self.f.f.verifier.expires_at=self.f.f.now()+3599
        self.f.g.token=self.f.f.token(c,iat=self.f.f.now(),auth_time=old)
        self.f.g.a.validate_for_session(c['installation'],c['operation'],self.person,c['nonce'])
        output=self.f.g.store.load(key('apple-grant-refresh-output',digest([self.context['grant_id'],self.f.f.now()])))[1]
        self.assertEqual(output['envelope']['version'],self.version)
        self.assertEqual(self.cipher.open(output['envelope'],output['context'])['refresh_token'],'private-refresh-token')
        # Worker already has a saved intake, so expired ordinary login is irrelevant.
        self.f.lifecycle.close(self.person,'device-one','d'*32)
        self.f.d.advance(self.person);self.erase();self.f.kms.destroy_now(self.version);self.erase()
        with self.assertRaises(StateUnavailable):self.cipher.open(output['envelope'],output['context'])
    def test_two_writers_resolve_one_durable_dek(self):
        # A separate fresh synthetic person has indexed grant admission but no key yet.
        person=self.f.who('concurrent-person');self.f.lifecycle.initialize_new(person,existing_account=False)
        node=self.f.lifecycle.reserve_grant(person,'concurrent','a'*32)
        self.f.kms.provision(self.f.provider.version(self.cipher.scope,person))
        ctx=dict(self.context,identity_sha256=person,grant_id=node['grant_id'])
        barrier=threading.Barrier(2);real=self.f.provider.wrap
        def parallel(*args):
            result=real(*args);barrier.wait(5);return result
        with patch.object(self.f.provider,'wrap',side_effect=parallel),ThreadPoolExecutor(2) as pool:
            results=list(pool.map(lambda n:self.cipher.seal({'value':n},ctx),[1,2]))
        self.assertEqual([self.cipher.open(e,ctx)['value'] for e in results],[1,2])
        self.assertEqual(len({e['version'] for e in results}),1)
    def test_wrapped_key_write_reply_loss_does_not_replace_winning_key(self):
        person=self.f.who('new-person');version=self.f.provider.version(self.cipher.scope,person)
        real=self.f.key_store.compare_exchange
        def lost(k,rev,c):
            r=real(k,rev,c)
            if c.get('person')==person:raise StateUnavailable('lost response')
            return r
        with patch.object(self.f.key_store,'compare_exchange',side_effect=lost):self.f.login('new','new-person')
        envelope,ctx=self.f.snapshot();self.assertEqual(envelope['version'],version)
        self.assertEqual(self.cipher.open(envelope,ctx)['refresh_token'],'private-refresh-token')
    def test_failed_key_persistence_stops_before_apple(self):
        before=len(self.f.g.calls)
        with patch.object(self.f.key_store,'compare_exchange',side_effect=StateUnavailable('key store down')):
            with self.assertRaises(StateUnavailable):self.f.login('new','new-person')
        self.assertEqual(len(self.f.g.calls),before)
    def test_late_exchange_after_closure_is_preserved_for_revocation(self):
        def hook(url):
            if url==TOKEN_URL:self.f.d.begin('device-one','d'*32)
        self.f.g.hook=hook
        with self.assertRaises(IdentityRejected):self.f.login('device-two')
        self.f.g.hook=lambda _:None
        self.assertEqual(self.f.d.advance(self.person)['grants_revoked'],2)
        self.assertEqual(self.erase()['kms_state'],'DESTROY_SCHEDULED')
    def test_unwrap_returned_after_fence_never_releases_plaintext(self):
        self.f.close_revoke();real=self.f.provider.unwrap
        def race(*args):
            value=real(*args);self.erase();return value
        with patch.object(self.f.provider,'unwrap',side_effect=race),self.assertRaises(StateUnavailable):
            self.cipher.open(self.envelope,self.context)
    def test_corrupt_key_row_is_never_reset(self):
        k=self.cipher._key(self.person);rev,c=self.f.key_store.load(k)
        self.f.key_store.compare_exchange(k,rev,dict(c,person='0'*64))
        with self.assertRaises(StateUnavailable):self.cipher.open(self.envelope,self.context)
    def test_new_wrapping_attempt_after_destruction_cannot_recreate(self):
        self.f.close_revoke();self.erase();self.f.kms.destroy_now(self.version);self.erase()
        with self.assertRaises(StateUnavailable):self.cipher.seal({'refresh_token':'new'},self.context)


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.s=KMSSession();self.p=GoogleGrantKeyProvider(session=self.s,keyring='projects/test/locations/test/keyRings/lite')
        self.v=self.p.version('a'*64,'b'*64);self.s.provision(self.v)
    def test_crc_reference(self):self.assertEqual(crc32c(b'123456789'),0xe3069283)
    def test_pinned_version_encrypt_and_integrity_checked_roundtrip(self):
        wrapped=self.p.wrap(self.v,b'x'*32,b'context')
        self.assertEqual(self.p.unwrap(self.v,wrapped,b'context'),b'x'*32)
        self.assertEqual(self.s.calls[0],('encrypt',self.v))
    def test_path_cannot_escape_keyring_or_use_other_version(self):
        for name in [self.v.replace('/1','/2'),self.v.replace('projects/test','projects/other'),self.v+'?x=1']:
            with self.assertRaises(ValueError):self.p.describe(name)
        self.assertEqual(self.s.calls,[])
    def test_wrapping_crc_failure(self):
        self.s.mutate=lambda action,r:dict(r,ciphertextCrc32c='0')
        with self.assertRaises(StateUnavailable):self.p.wrap(self.v,b'x'*32,b'aad')
    def test_wrong_encrypt_version_rejected(self):
        self.s.mutate=lambda action,r:dict(r,name=self.v[:-1]+'2')
        with self.assertRaises(StateUnavailable):self.p.wrap(self.v,b'x'*32,b'aad')
    def test_decrypt_crc_failure(self):
        wrapped=self.p.wrap(self.v,b'x'*32,b'aad')
        self.s.mutate=lambda action,r:dict(r,plaintextCrc32c='0')
        with self.assertRaises(StateUnavailable):self.p.unwrap(self.v,wrapped,b'aad')
    def test_transport_exceptions_are_sanitized(self):
        self.s.hook=lambda *_:(_ for _ in ()).throw(OSError('sensitive-token-material'))
        with self.assertRaises(StateUnavailable) as e:self.p.describe(self.v)
        self.assertNotIn('sensitive',str(e.exception))
    def test_key_metadata_mismatch_rejected(self):
        for change in ({'protectionLevel':'EXTERNAL'},{'algorithm':'RSA_SIGN_PKCS1_2048_SHA256'},{'state':'PENDING_GENERATION'}):
            self.s.mutate=lambda action,r,change=change:dict(r,**change)
            with self.assertRaises(StateUnavailable):self.p.describe(self.v)


if __name__=='__main__':unittest.main()
