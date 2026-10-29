"""Opt-in per-account envelope keys. No provisioning, route, or default activation.

Only an injected KMS transport can wrap keys. No plaintext DEK is persisted or
cached. KMS key versions must have been provisioned and reviewed separately.
Deletion of ciphertext is not key destruction; scheduled destruction is not
completion, and KMS logical destruction is not physical backup deletion.
"""
import base64
import os
import re
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from audit_chain_contract import loads_json_object_strict
from lite_account_lifecycle import AccountLifecycle
from lite_attest_state import StateUnavailable
from lite_apple_grants import encoded
from lite_subscription_executor import key
from lite_subscription_runtime import SubscriptionRuntime
from lite_usage_ledger import digest, _hex


def b64(value): return base64.b64encode(value).decode('ascii')


def unb64(value, maximum):
    if type(value) is not str or len(value) > 4 * ((maximum + 2) // 3):
        raise ValueError('invalid bounded binary field')
    result = base64.b64decode(value, validate=True)
    if not result or len(result) > maximum: raise ValueError('invalid binary size')
    return result


def crc32c(value):
    crc = 0xffffffff
    for byte in value:
        crc ^= byte
        for _ in range(8): crc = (crc >> 1) ^ (0x82f63b78 if crc & 1 else 0)
    return crc ^ 0xffffffff


class GoogleGrantKeyProvider:
    """Bounded REST adapter using an explicitly supplied authenticated session.

    This adapter cannot create, restore, rotate or import a key. Runtime and
    destruction workers must use separate least-privilege sessions in production.
    It never looks up ambient credentials or makes calls during construction.
    """
    def __init__(self, *, session, keyring):
        if (not callable(getattr(session, 'request', None)) or type(keyring) is not str
                or not re.fullmatch(r'projects/[a-z0-9-]+/locations/[a-z0-9-]+/keyRings/[A-Za-z0-9_-]+',keyring)):
            raise ValueError('explicit KMS session and key ring required')
        self.session, self.keyring = session, keyring

    def version(self, scope, person):
        _hex(scope); _hex(person)
        # Deterministic full SHA-256 rendered as 52 base32 characters, under the
        # resource ID length limit. Neither the Apple subject nor email is used.
        ident = base64.b32encode(bytes.fromhex(digest([scope,person]))).decode().rstrip('=').lower()
        return self.keyring+'/cryptoKeys/g-'+ident+'/cryptoKeyVersions/1'

    def _name(self, name):
        prefix = re.escape(self.keyring)
        if type(name) is not str or not re.fullmatch(prefix+r'/cryptoKeys/g-[a-z2-7]{52}/cryptoKeyVersions/1',name):
            raise ValueError('pinned account key version required')
        return name

    def _call(self, method, name, action='', body=None):
        try:
            response = self.session.request(method, 'https://cloudkms.googleapis.com/v1/'+name+action,
                                           json=body, timeout=(5,10), allow_redirects=False)
            if response.status_code != 200 or len(response.content) > 32768:
                raise ValueError()
            return loads_json_object_strict(response.content.decode('utf-8'))
        except Exception:
            # Provider replies can contain key resources and request material.
            raise StateUnavailable('grant key provider unavailable; no fallback') from None

    def describe(self, version):
        row = self._call('GET', self._name(version))
        if (row.get('name') != version or row.get('algorithm') != 'GOOGLE_SYMMETRIC_ENCRYPTION'
                or row.get('protectionLevel') != 'SOFTWARE'
                or row.get('state') not in ('ENABLED','DISABLED','DESTROY_SCHEDULED','DESTROYED')
                or row.get('reimportEligible',False) is not False
                or any(row.get(f) for f in ('importJob','importTime','importFailureReason'))):
            raise StateUnavailable('generated, non-reimportable account key required')
        return row['state']

    def wrap(self, version, plain, aad):
        if type(plain) is not bytes or len(plain) != 32 or type(aad) is not bytes or len(aad) > 4096:
            raise ValueError('bounded DEK required')
        row = self._call('POST',self._name(version),':encrypt',dict(plaintext=b64(plain),
            additionalAuthenticatedData=b64(aad),plaintextCrc32c=str(crc32c(plain)),
            additionalAuthenticatedDataCrc32c=str(crc32c(aad))))
        try:
            wrapped = unb64(row['ciphertext'],4096)
            if (row['name'] != version or row['verifiedPlaintextCrc32c'] is not True
                    or row['verifiedAdditionalAuthenticatedDataCrc32c'] is not True
                    or str(row['ciphertextCrc32c']) != str(crc32c(wrapped))): raise ValueError()
            return b64(wrapped)
        except Exception: raise StateUnavailable('grant key wrap integrity unconfirmed') from None

    def unwrap(self, version, wrapped, aad):
        raw = unb64(wrapped,4096)
        name = self._name(version).rsplit('/cryptoKeyVersions/',1)[0]
        row = self._call('POST',name,':decrypt',dict(ciphertext=b64(raw),additionalAuthenticatedData=b64(aad),
            ciphertextCrc32c=str(crc32c(raw)),additionalAuthenticatedDataCrc32c=str(crc32c(aad))))
        try:
            plain = unb64(row['plaintext'],32)
            if len(plain) != 32 or str(row['plaintextCrc32c']) != str(crc32c(plain)): raise ValueError()
            return plain
        except Exception: raise StateUnavailable('grant key unwrap integrity unconfirmed') from None

    def schedule_destroy(self, version):
        row = self._call('POST',self._name(version),':destroy',{})
        if row.get('name') != version or row.get('state') != 'DESTROY_SCHEDULED':
            raise StateUnavailable('key destruction request unconfirmed; read back before retry')


class AccountGrantCipher:
    """One persisted wrapped DEK and one pinned KMS version per scoped account.

    No legacy ciphertext fallback. Registration/provisioning and legacy migration
    must be reviewed before enabling this cipher in a service composition.
    """
    immutable = staticmethod(SubscriptionRuntime.immutable)

    def __init__(self, *, provider, lifecycle, store, audit_store):
        if not isinstance(provider,GoogleGrantKeyProvider) or not isinstance(lifecycle,AccountLifecycle):
            raise ValueError('explicit account lifecycle and key provider required')
        if store is audit_store or (getattr(store,'path',None) is not None
                and getattr(store,'path',None)==getattr(audit_store,'path',None)):
            raise ValueError('wrapped keys and audit must be separate')
        self.provider,self.lifecycle,self.store,self.audit = provider,lifecycle,store,audit_store
        self.scope = lifecycle.scope
        self.mode = dict(v=1,scheme='account-kms-envelope-1',scope=self.scope,keyring=provider.keyring)
        self.immutable(store,key('account-grant-cipher-config',self.scope),self.mode)

    def _key(self, person):
        _hex(person)
        return key('account-grant-dek',digest([self.scope,person]))

    def _context(self, context):
        c = context.get('grant_context') if type(context) is dict and 'grant_context' in context else context
        if type(c) is not dict or c.get('authority') != self.lifecycle.binding:
            raise StateUnavailable('grant authority differs from key custody')
        person = c.get('identity_sha256'); _hex(person); _hex(c.get('grant_id'))
        if self.lifecycle.member(person,c['grant_id']) is None:
            raise StateUnavailable('indexed grant required before key custody')
        return person

    def _read(self, person):
        row = self.store.load(self._key(person))
        if row is None: return None
        c = row[1]
        try:
            if (set(c) != {'v','scope','person','version','wrapped_dek','phase','closure'}
                    or type(c['v']) is not int or c['v'] != 1 or c['scope'] != self.scope
                    or c['person'] != person or c['version'] != self.provider.version(self.scope,person)
                    or c['phase'] not in ('ready','destroying','destroyed')): raise ValueError()
            unb64(c['wrapped_dek'],4096)
            if (c['phase']=='ready') != (c['closure'] is None): raise ValueError()
            if c['closure'] is not None: _hex(c['closure'])
        except (KeyError,TypeError,ValueError):
            raise StateUnavailable('account key custody corrupt; never reset') from None
        return row

    def _aad(self, person):
        return encoded(dict(v=1,scope=self.scope,person=person,version=self.provider.version(self.scope,person)))

    def _dek(self, person, *, create=False):
        if self.audit.load(key('account-grant-key-destroy-intent',digest([self.scope,person]))) is not None:
            raise StateUnavailable('durable account key destruction intent fences all copies')
        row = self._read(person)
        if row is None:
            if not create: raise StateUnavailable('account data key missing; never recreate for decryption')
            # Grant admission already precedes exchange. A late Apple response
            # during closure must still be encrypted so its token can be revoked.
            # The deletion worker cannot erase keys until every grant is revoked.
            root = self.lifecycle._read(person)
            if root is None: raise StateUnavailable('indexed account required')
            version = self.provider.version(self.scope,person)
            if self.provider.describe(version) != 'ENABLED': raise StateUnavailable('account key is not usable')
            wrapped = self.provider.wrap(version,os.urandom(32),self._aad(person))
            initial = dict(v=1,scope=self.scope,person=person,version=version,wrapped_dek=wrapped,phase='ready',closure=None)
            try:self.store.compare_exchange(self._key(person),0,initial)
            except StateUnavailable:pass
            row = self._read(person)
            if row is None: raise StateUnavailable('wrapped key persistence unconfirmed')
        c = row[1]
        if c['phase'] != 'ready': raise StateUnavailable('account key destruction fenced')
        if self.provider.describe(c['version']) != 'ENABLED': raise StateUnavailable('account key is not usable')
        plain = self.provider.unwrap(c['version'],c['wrapped_dek'],self._aad(person))
        if type(plain) is not bytes or len(plain) != 32: raise StateUnavailable('invalid account data key')
        self.immutable(self.audit,key('account-grant-key-ready',digest([self.scope,person])),
            dict(v=1,scope=self.scope,person=person,version=c['version'],wrapped_dek_sha256=digest(c['wrapped_dek'])))
        return c,plain

    def seal(self, value, context):
        person = self._context(context); plain = encoded(value)
        if len(plain) > 10000: raise StateUnavailable('grant exceeds vault bound')
        c,dek = self._dek(person,create=True)
        nonce = os.urandom(12)
        aad = encoded(dict(v=2,scope=self.scope,person=person,version=c['version'],context=context))
        encrypted = AESGCM(dek).encrypt(nonce,plain,aad)
        # No plaintext key cache. Python cannot guarantee zeroization of memory;
        # deployment additionally requires bounded worker drain before erasure.
        del dek
        live = self._read(person)
        if live is None or live[1] != c:
            raise StateUnavailable('account key changed during sealing')
        return dict(v=2,scope=self.scope,person=person,version=c['version'],nonce=b64(nonce),ciphertext=b64(encrypted))

    def prepare(self, context):
        """Resolve provisioning and durable key custody before exchanging a code."""
        person = self._context(context)
        self.lifecycle.require_active(person)
        _,dek = self._dek(person,create=True)
        del dek

    def open(self, envelope, context):
        try:
            person = self._context(context)
            if (type(envelope) is not dict or set(envelope) != {'v','scope','person','version','nonce','ciphertext'}
                    or type(envelope['v']) is not int or envelope['v'] != 2 or envelope['scope'] != self.scope
                    or envelope['person'] != person or envelope['version'] != self.provider.version(self.scope,person)):
                raise ValueError()
            nonce,encrypted = unb64(envelope['nonce'],12),unb64(envelope['ciphertext'],10016)
            if len(nonce) != 12 or len(encrypted)<16: raise ValueError()
            c,dek = self._dek(person)
            aad = encoded(dict(v=2,scope=self.scope,person=person,version=c['version'],context=context))
            plain = AESGCM(dek).decrypt(nonce,encrypted,aad);del dek
            live = self._read(person)
            if live is None or live[1] != c: raise ValueError()
            return loads_json_object_strict(plain.decode('utf-8'))
        except Exception: raise StateUnavailable('account grant authentication failed; no legacy fallback') from None

    def destroy_after_revocation(self, deletion, person):
        """Trusted executor only; Apple revocation and the frozen plan are checked.

        No HTTP route. This reports only grant-key disposition, never full account
        deletion. Resource creation, data deletion and audit purging are absent.
        """
        from lite_account_deletion import AccountDeletion
        if (not isinstance(deletion,AccountDeletion) or deletion.lifecycle is not self.lifecycle
                or deletion.grants.cipher is not self): raise ValueError('matching deletion authority required')
        plan,job = deletion._ensure(person)
        deletion._record_scan(job[1])
        if job[1]['phase'] != 'revoked': raise StateUnavailable('all Apple grants must be confirmed revoked first')
        if plan['total'] == 0: raise StateUnavailable('empty inventory requires separate disposition review')
        closure = digest(plan)
        row = self._read(person)
        if row is None: raise StateUnavailable('wrapped key missing; no erasure claim')
        rev,c = row
        intent = dict(v=1,scope=self.scope,person=person,version=c['version'],closure=closure,
                      wrapped_dek_sha256=digest(c['wrapped_dek']))
        self.immutable(self.audit,key('account-grant-key-destroy-intent',digest([self.scope,person])),intent)
        if c['phase']=='ready':
            try:self.store.compare_exchange(self._key(person),rev,dict(c,phase='destroying',closure=closure))
            except StateUnavailable:pass
        row = self._read(person)
        if row is None or row[1]['phase']=='ready' or row[1]['closure']!=closure:
            raise StateUnavailable('key destruction fence unconfirmed')
        rev,c = row
        state = self.provider.describe(c['version'])
        if c['phase']=='destroyed' and state!='DESTROYED':
            raise StateUnavailable('destroyed key state contradicts provider')
        if state in ('ENABLED','DISABLED'):
            self.provider.schedule_destroy(c['version'])
            state = self.provider.describe(c['version'])
        if state not in ('DESTROY_SCHEDULED','DESTROYED'):
            raise StateUnavailable('key destruction not confirmed')
        receipt = dict(v=1,scope=self.scope,person=person,closure=closure,version=c['version'],kms_state=state,
            grant_key_unrecoverable=(state=='DESTROYED'),physical_backup_deletion_confirmed=False,
            account_deleted=False,data_disposition='pending')
        self.immutable(self.audit,key('account-grant-key-destroy-observed',digest(receipt)),receipt)
        if state=='DESTROYED' and c['phase']!='destroyed':
            try:self.store.compare_exchange(self._key(person),rev,dict(c,phase='destroyed'))
            except StateUnavailable:pass
            saved = self._read(person)
            if saved is None or saved[1]!=dict(c,phase='destroyed'):
                raise StateUnavailable('destroyed key checkpoint unconfirmed')
        return receipt
