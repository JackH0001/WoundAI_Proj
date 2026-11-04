"""Explicit shared-KEK custody candidate; opt-in startup only.

Each account still has an independent random data key. Only the wrapping KMS
key is shared. Closing an account fences its current key but cannot prove that
historical wrapped copies are unrecoverable. Shared KMS retirement therefore
belongs to a separate, inventory-reviewed operator workflow, never deletion.
"""
import os
import re
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from audit_chain_contract import loads_json_object_strict
from lite_account_lifecycle import AccountLifecycle
from lite_attest_state import StateUnavailable, CAS_ATTEMPTS
from lite_apple_grants import encoded
from lite_grant_key_custody import AccountGrantCipher, GoogleGrantKeyProvider, b64, unb64
from lite_subscription_executor import key
from lite_usage_ledger import digest, _hex


SHARED_SCHEME = 'shared-kms-envelope-1'


def shared_settings(spec, keyring):
    """Validate startup pins without opening a transport or selecting defaults."""
    if (type(spec) is not dict or set(spec) != {'scheme','crypto_key','write_version','read_versions'}
            or spec['scheme'] != SHARED_SCHEME):
        raise ValueError('explicit shared custody scheme required')
    crypto_key, write, versions = spec['crypto_key'], spec['write_version'], spec['read_versions']
    if (type(crypto_key) is not str or not re.fullmatch(
            re.escape(keyring)+r'/cryptoKeys/[A-Za-z0-9_-]{1,63}', crypto_key)
            or type(versions) is not list or not 1 <= len(versions) <= 64
            or any(type(v) is not str or not re.fullmatch(
                re.escape(crypto_key)+r'/cryptoKeyVersions/[1-9][0-9]{0,9}', v) for v in versions)
            or len(set(versions)) != len(versions) or type(write) is not str or write not in versions):
        raise ValueError('explicit bounded shared key versions required')
    return dict(crypto_key=crypto_key, write_version=write, read_versions=versions.copy())


def shared_mode(scope, keyring, spec):
    settings=shared_settings(spec,keyring)
    return dict(v=1,scheme=SHARED_SCHEME,scope=scope,keyring=keyring,crypto_key=settings['crypto_key'])


class RotatingGrantKeyProvider(GoogleGrantKeyProvider):
    """Only explicitly listed versions of one wrapping key are usable.

    Encrypt pins the version, not the key's mutable primary. Decrypt uses the
    CryptoKey endpoint required by KMS; authenticated context binds the expected
    version too. This adapter cannot disable, destroy, create or restore keys.
    """
    def __init__(self, *, session, keyring, crypto_key, write_version, read_versions):
        super().__init__(session=session, keyring=keyring)
        shared_settings(dict(scheme=SHARED_SCHEME,crypto_key=crypto_key,
            write_version=write_version,read_versions=read_versions),keyring)
        self.crypto_key, self.write_version = crypto_key, write_version
        self.read_versions = frozenset(read_versions)

    def version(self, scope, person):
        _hex(scope); _hex(person)
        return self.write_version

    def _name(self, name):
        if type(name) is not str or name not in self.read_versions:
            raise ValueError('unlisted shared key version')
        return name

    def wrap(self, version, plain, aad):
        if version != self.write_version:
            raise ValueError('only the pinned shared write version may wrap new data')
        return super().wrap(version, plain, aad)

    def schedule_destroy(self, version):
        raise StateUnavailable('account deletion must never destroy a shared key')


class RotatingGrantCipher(AccountGrantCipher):
    """Opt-in candidate for synthetic integration, with no v1/v2 fallback.

    The existing indexed Apple authority accepts this subtype, so login,
    revocation and failure handling can be tested with actual application code.
    Startup must select the separate shared-mode configuration and progress
    catalog. Independent KEK retirement remains a separate operator workflow.
    """
    def __init__(self, *, provider, lifecycle, store, audit_store, readiness=None):
        if not isinstance(provider, RotatingGrantKeyProvider) or not isinstance(lifecycle, AccountLifecycle):
            raise ValueError('explicit shared key provider and account lifecycle required')
        from lite_disposition_progress import aliases
        if aliases(store, audit_store) or readiness is not None:
            raise ValueError('independent custody audit; no per-account KMS provisioning')
        self.provider, self.lifecycle, self.store, self.audit = provider, lifecycle, store, audit_store
        self.scope, self.readiness = lifecycle.scope, None
        # Write version changes do not alter the envelope's stable identity.
        # Changing the shared key resource or scheme requires explicit migration.
        self.mode = dict(v=1, scheme='shared-kms-envelope-1', scope=self.scope,
                         keyring=provider.keyring, crypto_key=provider.crypto_key)
        self.immutable(store, key('account-grant-cipher-config', self.scope), self.mode)

    def _proof_key(self, c): return key('shared-grant-key-row', digest(c))

    def _proof(self, c):
        # Audit retains hashes and version inventory, never a usable wrapped DEK.
        return dict(v=1, scope=self.scope, person=c['person'], record_sha256=digest(c),
                    dek_id=c['dek_id'], versions=c['versions'], phase=c['phase'], closure=c['closure'])

    def _validate(self, c, person):
        try:
            if (type(c) is not dict or set(c) != {'v','scope','person','dek_id','version',
                    'wrapped_dek','phase','closure','versions'} or type(c['v']) is not int or c['v'] != 2
                    or c['scope'] != self.scope or c['person'] != person
                    or c['phase'] not in ('ready','closed')): raise ValueError()
            _hex(c['dek_id'])
            if c['phase'] == 'ready': self.provider._name(c['version'])
            if (type(c['versions']) is not list or not 1 <= len(c['versions']) <= 64
                    or c['versions'] != sorted(set(c['versions']))
                    or c['version'] not in c['versions']): raise ValueError()
            # Historical dependencies may be retired/unlisted for reads, but
            # must still be versions of the same explicitly pinned CryptoKey.
            for version in c['versions']:
                if type(version) is not str or not re.fullmatch(re.escape(self.provider.crypto_key)
                        +r'/cryptoKeyVersions/[1-9][0-9]{0,9}', version): raise ValueError()
            if c['phase'] == 'ready':
                if c['closure'] is not None: raise ValueError()
                unb64(c['wrapped_dek'], 4096)
            else:
                _hex(c['closure'])
                if c['wrapped_dek'] is not None: raise ValueError()
            proof = self.audit.load(self._proof_key(c))
            if proof is None or proof[1] != self._proof(c): raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise StateUnavailable('shared key custody or evidence corrupt; never reset') from None

    def _read(self, person):
        row = self.store.load(self._key(person))
        if row is not None: self._validate(row[1], person)
        return row

    def _save(self, person, generation, c):
        self.immutable(self.audit, self._proof_key(c), self._proof(c))
        try: self.store.compare_exchange(self._key(person), generation, c)
        except StateUnavailable: pass
        # A lost response is not permission to recreate keys or overwrite peers.
        return self._read(person)

    def _dependencies(self, person):
        row = self.store.load(key('shared-grant-key-dependencies',digest([self.scope,person])))
        if row is None: return None
        c = row[1]
        if (type(c) is not dict or set(c) != {'v','scope','person','versions'}
                or type(c['v']) is not int or c['v'] != 1 or c['scope'] != self.scope or c['person'] != person
                or type(c['versions']) is not list or not 1 <= len(c['versions']) <= 64
                or any(type(v) is not str or not re.fullmatch(re.escape(self.provider.crypto_key)
                    +r'/cryptoKeyVersions/[1-9][0-9]{0,9}',v) for v in c['versions'])
                or c['versions'] != sorted(set(c['versions']))):
            raise StateUnavailable('shared key dependency inventory corrupt')
        proof = self.audit.load(key('shared-grant-key-dependencies',digest(c)))
        if proof is None or proof[1] != c: raise StateUnavailable('shared key dependencies lack audit evidence')
        return row

    def _reserve_version(self, person, version):
        # Must happen BEFORE every KMS wrap, including failed/uncommitted ones.
        # Otherwise deleting the current key row could overlook a pending V2
        # proposal still decryptable after the committed V1 is retired.
        self.provider._name(version)
        k = key('shared-grant-key-dependencies',digest([self.scope,person]))
        for _ in range(CAS_ATTEMPTS):
            self._require_no_destroy_intent(person)
            row = self._dependencies(person)
            versions = [] if row is None else row[1]['versions']
            if version in versions: self._require_no_destroy_intent(person); return
            updated = dict(v=1,scope=self.scope,person=person,versions=sorted(versions+[version]))
            if len(updated['versions']) > 64: raise StateUnavailable('dependency inventory requires review')
            self.immutable(self.audit,key('shared-grant-key-dependencies',digest(updated)),updated)
            try: self.store.compare_exchange(k,0 if row is None else row[0],updated)
            except StateUnavailable: pass
            saved = self._dependencies(person)
            if saved is not None and version in saved[1]['versions']:
                self._require_no_destroy_intent(person); return
        raise StateUnavailable('dependency reservation unconfirmed; do not wrap')

    def _aad_for(self, c):
        return encoded(dict(v=2, scope=self.scope, person=c['person'],
                            dek_id=c['dek_id'], version=c['version']))

    def _dek(self, person, *, create=False):
        self._require_no_destroy_intent(person)
        row = self._read(person)
        initial_key = key('shared-grant-key-initialized', digest([self.scope,person]))
        if row is None:
            if not create: raise StateUnavailable('shared account data key missing; never recreate')
            if self.audit.load(initial_key) is not None:
                raise StateUnavailable('previously initialized key missing; explicit recovery required')
            if self.lifecycle._read(person) is None: raise StateUnavailable('indexed account required')
            pk = key('shared-grant-key-initial-proposal', digest([self.scope,person]))
            saved = self.store.load(pk)
            if saved is None:
                version = self.provider.write_version
                if self.provider.describe(version) != 'ENABLED': raise StateUnavailable('shared key unavailable')
                c = dict(v=2, scope=self.scope, person=person, dek_id=os.urandom(32).hex(),
                         version=version, wrapped_dek=None, phase='ready', closure=None, versions=[version])
                self._reserve_version(person, version)
                plain = os.urandom(32)
                c['wrapped_dek'] = self.provider.wrap(version, plain, self._aad_for(c)); del plain
                self._require_no_destroy_intent(person)
                self.immutable(self.audit, self._proof_key(c), self._proof(c))
                try: self.store.compare_exchange(pk, 0, c)
                except StateUnavailable: pass
                saved = self.store.load(pk)
            if saved is None: raise StateUnavailable('initial shared key proposal unconfirmed')
            c = saved[1]; self._validate(c, person)
            if c['phase'] != 'ready' or c['versions'] != [c['version']]:
                raise StateUnavailable('initial shared key proposal differs')
            self._require_no_destroy_intent(person)
            row = self._save(person, 0, c)
            if row is None: raise StateUnavailable('shared wrapped key persistence unconfirmed')
        c = row[1]
        if c['phase'] != 'ready': raise StateUnavailable('shared account key closed')
        self.immutable(self.audit, initial_key, dict(v=1,scope=self.scope,person=person,dek_id=c['dek_id']))
        if self.provider.describe(c['version']) != 'ENABLED': raise StateUnavailable('shared key unavailable')
        plain = self.provider.unwrap(c['version'], c['wrapped_dek'], self._aad_for(c))
        if type(plain) is not bytes or len(plain) != 32: raise StateUnavailable('invalid shared data key')
        self._require_current(person, c)
        return c, plain

    def seal(self, value, context):
        person = self._context(context); plain = encoded(value)
        if len(plain) > 10000: raise StateUnavailable('grant exceeds vault bound')
        c, dek = self._dek(person, create=True); nonce = os.urandom(12)
        aad = encoded(dict(v=3, scope=self.scope, person=person, dek_id=c['dek_id'], context=context))
        encrypted = AESGCM(dek).encrypt(nonce, plain, aad); del dek
        self._require_current(person, c)
        return dict(v=3, scope=self.scope, person=person, dek_id=c['dek_id'],
                    nonce=b64(nonce), ciphertext=b64(encrypted))

    def open(self, envelope, context):
        try:
            person = self._context(context)
            if (type(envelope) is not dict or set(envelope) != {'v','scope','person','dek_id','nonce','ciphertext'}
                    or type(envelope['v']) is not int or envelope['v'] != 3
                    or envelope['scope'] != self.scope or envelope['person'] != person): raise ValueError()
            _hex(envelope['dek_id'])
            nonce, encrypted = unb64(envelope['nonce'], 12), unb64(envelope['ciphertext'], 10016)
            if len(nonce) != 12 or len(encrypted) < 16: raise ValueError()
            c, dek = self._dek(person)
            if envelope['dek_id'] != c['dek_id']: raise ValueError()
            aad = encoded(dict(v=3, scope=self.scope, person=person, dek_id=c['dek_id'], context=context))
            plain = AESGCM(dek).decrypt(nonce, encrypted, aad); del dek
            tokens = loads_json_object_strict(plain.decode('utf-8'))
            self._require_current(person, c)
            return tokens
        except Exception:
            raise StateUnavailable('shared grant authentication failed; no fallback') from None

    def rewrap(self, person, *, expected_record_sha256):
        """Explicit private operation, one account and one source generation.

        An audit intent precedes KMS. Retry reads the same intent and can confirm
        a lost write acknowledgement. No inventory scanning, key creation or
        retirement is performed. Rotation cannot reverse to an older version.
        """
        _hex(person); _hex(expected_record_sha256)
        self.lifecycle.require_active(person); self._require_no_destroy_intent(person)
        target = self.provider.write_version
        ident = digest([self.scope, person, expected_record_sha256, target])
        ik = key('shared-grant-rewrap-intent', ident)
        rk = key('shared-grant-rewrap-result', ident)
        row = self._read(person)
        if row is None or row[1]['phase'] != 'ready': raise StateUnavailable('ready account key required')
        generation, c = row
        pk = key('shared-grant-rewrap-proposal', ident)
        proposal = self.store.load(pk)
        if proposal is not None:
            return self._finish_rewrap(person, expected_record_sha256, ident, ik, rk, proposal[1])
        if digest(c) != expected_record_sha256:
            # Never infer a lost result from a matching version alone.
            raise StateUnavailable('rewrap source changed; inspect original operation')
        if int(target.rsplit('/',1)[1]) <= int(c['version'].rsplit('/',1)[1]):
            raise StateUnavailable('rewrap requires a later pinned version')
        self.immutable(self.audit, ik, dict(v=1,scope=self.scope,person=person,
            source=expected_record_sha256,target=target))
        if self.provider.describe(target) != 'ENABLED': raise StateUnavailable('target shared key unavailable')
        original, dek = self._dek(person)
        if original != c: raise StateUnavailable('rewrap source changed during read')
        updated = dict(c, version=target, versions=sorted(set(c['versions']+[target])))
        if len(updated['versions']) > 64: raise StateUnavailable('key dependency inventory requires review')
        self._reserve_version(person, target)
        updated['wrapped_dek'] = self.provider.wrap(target, dek, self._aad_for(updated))
        # Read-back decrypt before changing the account's only current key.
        check = self.provider.unwrap(target, updated['wrapped_dek'], self._aad_for(updated))
        if check != dek: raise StateUnavailable('rewrapped key differs')
        del check, dek
        self.lifecycle.require_active(person); self._require_current(person, c)
        # Keep the retryable wrapped target in the key store, not in audit.
        # A proposal cannot authorize an account or bypass its closure intent.
        self.immutable(self.audit, self._proof_key(updated), self._proof(updated))
        proposal = dict(v=1, operation=ident, source_sha256=expected_record_sha256, record=updated)
        try: self.store.compare_exchange(pk, 0, proposal)
        except StateUnavailable: pass
        saved = self.store.load(pk)
        if saved is None: raise StateUnavailable('rewrap proposal persistence unconfirmed')
        return self._finish_rewrap(person, expected_record_sha256, ident, ik, rk, saved[1])

    def _finish_rewrap(self, person, source, ident, ik, rk, proposal):
        target = self.provider.write_version
        if (type(proposal) is not dict or set(proposal) != {'v','operation','source_sha256','record'}
                or type(proposal['v']) is not int or proposal['v'] != 1 or proposal['operation'] != ident
                or proposal['source_sha256'] != source):
            raise StateUnavailable('rewrap proposal differs')
        updated = proposal['record']; self._validate(updated, person)
        intent = self.audit.load(ik)
        if (updated['phase'] != 'ready' or updated['version'] != target or intent is None
                or intent[1] != dict(v=1,scope=self.scope,person=person,source=source,target=target)):
            raise StateUnavailable('rewrap proposal is not bound to its original intent')
        self.lifecycle.require_active(person); self._require_no_destroy_intent(person)
        row = self._read(person)
        if row is None: raise StateUnavailable('rewrap source missing')
        generation, current = row
        if current != updated:
            if (digest(current) != source or current['phase'] != 'ready'
                    or current['dek_id'] != updated['dek_id']
                    or int(current['version'].rsplit('/',1)[1]) >= int(target.rsplit('/',1)[1])
                    or updated['versions'] != sorted(set(current['versions']+[target]))):
                raise StateUnavailable('rewrap source changed; inspect original operation')
            self._require_current(person, current)
            row = self._save(person, generation, updated)
            if row is None or row[1] != updated:
                raise StateUnavailable('rewrap write unconfirmed; keep original intent')
        self._require_current(person, updated)
        receipt = dict(v=1, operation=ident, source_sha256=source, target_sha256=digest(updated))
        self.immutable(self.audit, rk, receipt)
        return receipt

    def destroy_after_revocation(self, deletion, person):
        """Fence one account, retain an honest backup-pending receipt.

        Never call KMS destroy: other accounts depend on the same key. Even a
        disabled key is restorable; this implementation makes no erasure claim.
        """
        from lite_account_deletion import AccountDeletion
        if (not isinstance(deletion,AccountDeletion) or deletion.lifecycle is not self.lifecycle
                or deletion.grants.cipher is not self): raise ValueError('matching deletion authority required')
        plan, job = deletion._ensure(person); deletion._record_scan(job[1])
        if job[1]['phase'] != 'revoked' or plan['total'] == 0:
            raise StateUnavailable('all indexed Apple grants must be confirmed revoked first')
        closure = digest(plan); row = self._read(person)
        if row is None: raise StateUnavailable('missing key inventory; no disposition claim')
        generation, c = row
        ik = key('account-grant-key-destroy-intent', digest([self.scope,person]))
        intent = self.audit.load(ik)
        if c['phase'] == 'ready':
            value = dict(v=2, scope=self.scope, person=person, closure=closure)
            self.immutable(self.audit, ik, value)
            dependencies = self._dependencies(person)
            if dependencies is None or not set(c['versions']).issubset(dependencies[1]['versions']):
                raise StateUnavailable('complete key dependency reservation required')
            updated = dict(c, phase='closed', closure=closure, wrapped_dek=None,
                           versions=dependencies[1]['versions'])
            row = self._save(person, generation, updated)
            if row is None or row[1] != updated:
                raise StateUnavailable('shared account key closure unconfirmed')
            c = row[1]; intent = self.audit.load(ik)
        receipt = self.closure_receipt(person, closure)
        self.immutable(self.audit,key('shared-grant-key-closure-observed',digest(receipt)),receipt)
        return receipt

    def closure_receipt(self, person, closure):
        """Read-only evidence for public progress; no provider call or repair."""
        _hex(person); _hex(closure)
        row = self._read(person)
        if row is None: raise StateUnavailable('closed shared key inventory missing')
        c = row[1]
        intent = self.audit.load(key('account-grant-key-destroy-intent',digest([self.scope,person])))
        if (c['closure'] != closure or c['phase'] != 'closed' or intent is None
                or intent[1] != dict(v=2,scope=self.scope,person=person,closure=closure)):
            raise StateUnavailable('shared closure evidence differs')
        receipt = dict(v=2, scheme='shared-kms-envelope-1', scope=self.scope, person=person,
            closure=closure, dek_id=c['dek_id'], versions=c['versions'],
            current_key_access='closed', historical_key_access='pending_retirement',
            retirement_inventory_verified=False,
            grant_key_unrecoverable=False, physical_backup_deletion_confirmed=False,
            account_deleted=False, data_disposition='pending')
        return receipt

    def validate_closure_receipt(self, person, closure, receipt):
        expected = self.closure_receipt(person, closure)
        proof = self.audit.load(key('shared-grant-key-closure-observed',digest(expected)))
        if type(receipt) is not dict or digest(receipt) != digest(expected) or proof is None or digest(proof[1]) != digest(expected):
            raise StateUnavailable('shared key closure receipt differs from original evidence')
