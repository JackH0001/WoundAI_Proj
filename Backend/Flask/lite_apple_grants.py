"""Opt-in encrypted Apple grant custody, not account deletion/session renewal.

Mutable ciphertext belongs in a dedicated security store, never research audit.
Audit contains only intent hashes and outcomes. Unknown single-use exchanges are
not replayed. Revoke fences precede external calls and survive lost responses.
"""
import base64
import hashlib
import hmac
import json
import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from audit_chain_contract import loads_json_object_strict
from lite_account_identity import AppleIdentityVerifier, IdentityRejected
from lite_apple_oauth import AppleOAuthProvider, GrantRejected, GrantUncertain, bounded_secret
from lite_attest_state import CAS_ATTEMPTS, StateUnavailable
from lite_subscription_executor import key
from lite_subscription_runtime import SubscriptionRuntime
from lite_usage_ledger import digest, _hex, _subject


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


class GrantCipher:
    """AES-256-GCM with context authentication and explicit key-version selection."""
    def __init__(self, *, keys, current):
        if type(keys) is not dict or not 1 <= len(keys) <= 8 or current not in keys:
            raise ValueError('explicit bounded grant keyring required')
        for name, secret in keys.items():
            _subject(name)
            if type(secret) is not bytes or len(secret) != 32: raise ValueError('AES-256 key required')
        self._keys, self.current = dict(keys), current
        self.fingerprints = {n: hashlib.sha256(s).hexdigest() for n, s in keys.items()}

    def seal(self, value, context):
        plain = encoded(value)
        if len(plain) > 10000: raise StateUnavailable('grant exceeds vault bound')
        nonce = os.urandom(12)
        aad = encoded(dict(v=1, key_id=self.current, context=context))
        cipher = AESGCM(self._keys[self.current]).encrypt(nonce, plain, aad)
        return dict(v=1, key_id=self.current, nonce=base64.b64encode(nonce).decode(),
            ciphertext=base64.b64encode(cipher).decode())

    def open(self, envelope, context):
        try:
            if (type(envelope) is not dict or set(envelope) != {'v','key_id','nonce','ciphertext'}
                    or type(envelope['v']) is not int or envelope['v'] != 1): raise ValueError()
            nonce = base64.b64decode(envelope['nonce'], validate=True)
            cipher = base64.b64decode(envelope['ciphertext'], validate=True)
            if len(nonce) != 12 or not 16 <= len(cipher) <= 10016: raise ValueError()
            aad = encoded(dict(v=1, key_id=envelope['key_id'], context=context))
            data = AESGCM(self._keys[envelope['key_id']]).decrypt(nonce, cipher, aad)
            return loads_json_object_strict(data.decode('utf-8'))
        except Exception:
            raise StateUnavailable('grant vault authentication failed; never reset') from None


class AppleGrantAuthority:
    immutable = staticmethod(SubscriptionRuntime.immutable)

    def __init__(self, *, provider, verifier, store, audit_store, cipher, namespace, identity_key, lifecycle=None):
        if (not isinstance(provider, AppleOAuthProvider) or not isinstance(verifier, AppleIdentityVerifier)
                or not isinstance(cipher, GrantCipher) or provider.client_id != verifier.audience
                or type(identity_key) is not bytes or len(identity_key) != 32):
            raise ValueError('matching trusted Apple grant configuration required')
        _subject(namespace)
        if identity_key in cipher._keys.values(): raise ValueError('identity and encryption keys must differ')
        if store is audit_store or (getattr(store, 'path', None) is not None
                and getattr(store, 'path', None) == getattr(audit_store, 'path', None)):
            raise ValueError('mutable credential vault must be separate from audit')
        self.provider, self.verifier, self.store, self.audit, self.cipher = provider, verifier, store, audit_store, cipher
        self.namespace, self._identity_key = namespace, identity_key
        self.binding = dict(namespace=namespace, issuer=verifier.issuer, audience=verifier.audience,
            identity_key_sha256=hashlib.sha256(identity_key).hexdigest())
        self.lifecycle = lifecycle
        mode_key = key('apple-grant-inventory-required',namespace)
        # A previous vault may contain grants from failed/unconfirmed sign-ins,
        # even when no human-account row exists. Never infer an empty inventory
        # from only the currently active account/session.
        if (lifecycle is not None and store.load(mode_key) is None
                and store.load(key('apple-grant-config',namespace)) is not None):
            raise StateUnavailable('legacy grant vault requires explicit inventory migration')
        self.immutable(store, key('apple-grant-config', namespace), dict(v=1, binding=self.binding))
        if lifecycle is not None:
            from lite_account_lifecycle import AccountLifecycle
            if not isinstance(lifecycle,AccountLifecycle) or lifecycle.binding != self.binding:
                raise ValueError('matching account lifecycle required')
            self.immutable(store,mode_key,dict(v=1,binding=self.binding))
        elif store.load(mode_key) is not None:
            raise StateUnavailable('account grant inventory cannot be silently disabled')
        for kid, fingerprint in cipher.fingerprints.items():
            self.immutable(store, key('apple-grant-key', digest([namespace, kid])), dict(v=1, sha256=fingerprint))

    def _id(self, installation, operation):
        _subject(installation); _hex(operation, 32)
        return digest([self.namespace, installation, operation])

    def _row(self, identity):
        row = self.store.load(key('apple-grant', identity))
        if row is None: return None
        c = row[1]
        try:
            if (set(c) != {'v','context','phase','envelope'} or type(c['v']) is not int or c['v'] != 1
                    or set(c['context']) != {'authority','grant_id','code_sha256','token_sha256','identity_sha256','issued_at'}
                    or c['context']['authority'] != self.binding or c['context']['grant_id'] != identity
                    or type(c['context']['issued_at']) is not int or c['context']['issued_at'] < 0
                    or c['phase'] not in ('claimed','ready','blocked','uncertain','rejected','revoked')): raise ValueError()
            for f in ('code_sha256','token_sha256','identity_sha256'): _hex(c['context'][f])
            if (c['phase'] in ('ready','blocked')) != (c['envelope'] is not None): raise ValueError()
        except Exception:
            raise StateUnavailable('grant state corrupt; never reset') from None
        return row

    def _revoking(self, identity):
        row = self.audit.load(key('apple-grant-revoke-intent', identity))
        if row is not None and row[1] != dict(v=1, grant_id=identity):
            raise StateUnavailable('grant revocation intent corrupt')
        return row is not None

    def _lifecycle_required(self):
        if self.lifecycle is None and self.store.load(key('apple-grant-inventory-required',self.namespace)) is not None:
            raise StateUnavailable('account grant inventory required by durable policy')

    def _active(self, person, identity):
        self._lifecycle_required()
        if self.lifecycle is not None:
            self.lifecycle.require_active(person)
            if self.lifecycle.member(person,identity) is None:
                raise StateUnavailable('existing grant requires reviewed inventory backfill')

    def _receipt(self, identity, row):
        c = row[1]
        self._active(c['context']['identity_sha256'],identity)
        if c['phase'] != 'ready' or self._revoking(identity): raise IdentityRejected('Apple grant is not active')
        validation = self._validation(identity)
        if validation is not None and validation[1]['phase'] != 'valid':
            if validation[1]['phase'] == 'validating':
                if self.verifier.clock() >= validation[1]['checked_at']+120:
                    raise GrantUncertain('Apple validation outcome unknown; explicit sign-in required')
                raise StateUnavailable('Apple validation in progress')
            raise GrantRejected('Apple refresh must be recovered with explicit sign-in')
        self.cipher.open(c['envelope'], c['context'])  # Missing/wrong keys fail closed.
        receipt = dict(v=1, state='grant_ready', grant_id=identity,
            identity_sha256=c['context']['identity_sha256'], original_issued_at=c['context']['issued_at'])
        self.immutable(self.audit, key('apple-grant-ready', identity), receipt)
        if self._revoking(identity): raise IdentityRejected('Apple grant revocation pending')
        return receipt

    def capture(self, installation, operation, nonce, signed, authorization_code):
        """Called only after App Attest/nonce/state checks by the identity bridge.

        Only the process that durably creates the claim may use the code. A
        claimed/uncertain operation requires explicit fresh Apple sign-in; it is
        never silently reused, even after a crash before external dispatch.
        """
        identity = self._id(installation, operation)
        bounded_secret(authorization_code)
        claims = self.verifier.verify(signed, nonce)
        person = hmac.new(self._identity_key,
            (self.namespace+'\0'+self.verifier.issuer+'\0'+claims['sub']).encode(), hashlib.sha256).hexdigest()
        self._lifecycle_required()
        if self.lifecycle is not None:
            if self._row(identity) is not None and self.lifecycle.member(person,identity) is None:
                raise StateUnavailable('existing grant requires reviewed inventory backfill')
            self.lifecycle.reserve_grant(person,installation,operation)
        context = dict(authority=self.binding, grant_id=identity, code_sha256=digest(authorization_code),
            token_sha256=digest(signed), identity_sha256=person, issued_at=claims['issued'])
        if self._revoking(identity): raise IdentityRejected('Apple grant revoked')
        old = self._row(identity)
        if old is not None:
            if old[1]['context'] != context: raise IdentityRejected('saved Apple grant differs')
            if old[1]['phase'] == 'ready': return self._receipt(identity, old)
            raise GrantUncertain('saved code cannot be replayed; explicit Apple sign-in required')
        # A code cannot be consumed under two different login operations/devices.
        self.immutable(self.audit, key('apple-code-claim', digest([self.namespace, digest(authorization_code)])),
            dict(v=1, grant_id=identity, context_sha256=digest(context)))
        claimed = dict(v=1, context=context, phase='claimed', envelope=None)
        k = key('apple-grant', identity)
        if not self.store.compare_exchange(k, 0, claimed):
            return self.capture(installation, operation, nonce, signed, authorization_code)
        # Do not dispatch if a revocation won after the claim.
        if self._revoking(identity): raise IdentityRejected('Apple grant revocation pending')
        self._active(person,identity)
        row = self._row(identity)
        if row is None or row[1] != claimed: raise StateUnavailable('grant claim unconfirmed')
        try:
            tokens = self.provider.exchange(authorization_code)
        except GrantRejected:
            self.store.compare_exchange(k, row[0], dict(claimed, phase='rejected'))
            raise GrantRejected('Apple code rejected; explicit sign-in required') from None
        except Exception:
            self.store.compare_exchange(k, row[0], dict(claimed, phase='uncertain'))
            raise GrantUncertain('Apple exchange uncertain; do not reuse code') from None
        # Keep returned credentials encrypted even on subject/nonce mismatch, so
        # a trusted operator can revoke them; they never authorize a session.
        valid = False
        try:
            exchanged = self.verifier.verify(tokens['id_token'], nonce)
            valid = exchanged['sub'] == claims['sub'] and exchanged['issued'] >= claims['issued']-30
        except (IdentityRejected, StateUnavailable):
            pass
        stored = dict(claimed, phase='ready' if valid else 'blocked', envelope=self.cipher.seal(tokens, context))
        try:
            self.store.compare_exchange(k, row[0], stored)
        except StateUnavailable:
            pass  # Resolve a possible committed-but-unacknowledged write below.
        current = self._row(identity)
        if current is None or current[1] != stored:
            raise GrantUncertain('Apple credential persistence unconfirmed; preserve revocation intent')
        return self._receipt(identity, current)

    def require(self, installation, operation, person):
        identity = self._id(installation, operation)
        row = self._row(identity)
        if row is None or row[1]['context']['identity_sha256'] != person:
            raise IdentityRejected('matching Apple grant required')
        return self._receipt(identity, row)

    def _validation(self, identity):
        row = self.store.load(key('apple-grant-validation',identity))
        if row is None: return None
        c=row[1]
        if (type(c) is not dict or set(c)!={'v','grant_id','phase','checked_at'}
                or type(c['v']) is not int or c['v']!=1 or c['grant_id']!=identity
                or c['phase'] not in ('validating','valid','uncertain','rejected')
                or type(c['checked_at']) is not int or not 0<=c['checked_at']<=self.verifier.clock()):
            raise StateUnavailable('Apple validation state corrupt')
        return row

    def fresh_until(self, installation, operation, person):
        receipt=self.require(installation,operation,person)
        identity=receipt['grant_id'];row=self._validation(identity)
        if row is None: return receipt['original_issued_at']+86400
        c=row[1]
        proof=self.audit.load(key('apple-grant-validated',digest([identity,c['checked_at']])))
        if proof is None or proof[1]!=c: raise StateUnavailable('Apple validation audit unconfirmed')
        return c['checked_at']+86400

    def validate_for_session(self, installation, operation, person, nonce):
        """At most one refresh validation per day; immutable original iat is retained.

        An uncertain external exchange is not repeated automatically. Its saved
        grant remains available to the revocation coordinator. Explicit fresh
        sign-in is required; neither a new family nor a quota reset is invented.
        """
        if self.verifier.clock()<self.fresh_until(installation,operation,person): return
        identity=self._id(installation,operation);row=self._row(identity)
        previous=self._validation(identity);n=self.verifier.clock()
        claimed=dict(v=1,grant_id=identity,phase='validating',checked_at=n)
        k=key('apple-grant-validation',identity)
        if not self.store.compare_exchange(k,previous[0] if previous else 0,claimed):
            raise StateUnavailable('Apple validation concurrent; retry saved renewal')
        current=self._validation(identity)
        if current is None or current[1]!=claimed: raise StateUnavailable('Apple validation claim unconfirmed')
        if self._revoking(identity):
            self.store.compare_exchange(k,current[0],dict(claimed,phase='rejected'))
            raise IdentityRejected('grant revocation pending')
        try: self._active(person,identity)
        except Exception:
            # No external refresh was dispatched; release this validation claim
            # so closure can still revoke the already captured credential.
            self.store.compare_exchange(k,current[0],dict(claimed,phase='rejected'))
            raise
        tokens=self.cipher.open(row[1]['envelope'],row[1]['context'])
        try:
            updated=self.provider.refresh(tokens['refresh_token'])
        except GrantRejected:
            self.store.compare_exchange(k,current[0],dict(claimed,phase='rejected'))
            raise
        except Exception:
            self.store.compare_exchange(k,current[0],dict(claimed,phase='uncertain'))
            raise GrantUncertain('Apple refresh unconfirmed; explicit sign-in required') from None
        # Preserve a returned credential even if identity validation later fails.
        # Otherwise revoking only the old token could falsely appear complete.
        # This encrypted response is part of future vault-version disposition;
        # it is not a renewal authorization or a research/audit payload.
        output_context=dict(grant_context=row[1]['context'],checked_at=n)
        output_key=key('apple-grant-refresh-output',digest([identity,n]))
        try:
            output=dict(v=1,context=output_context,envelope=self.cipher.seal(updated,output_context))
            try:self.store.compare_exchange(output_key,0,output)
            except StateUnavailable:pass
            recorded=self.store.load(output_key)
            if (recorded is None or set(recorded[1])!={'v','context','envelope'}
                    or type(recorded[1]['v']) is not int or recorded[1]['v']!=1
                    or recorded[1]['context']!=output_context
                    or self.cipher.open(recorded[1]['envelope'],output_context)!=updated):
                raise StateUnavailable('refresh output persistence unconfirmed')
            claims=self.verifier.verify_refresh(updated['id_token'],nonce,row[1]['context']['issued_at'])
            hashed=hmac.new(self._identity_key,(self.namespace+'\0'+self.verifier.issuer+'\0'+claims['sub']).encode(),hashlib.sha256).hexdigest()
            if not hmac.compare_digest(hashed,person): raise GrantRejected('Apple refresh subject differs')
        except IdentityRejected:
            self.store.compare_exchange(k,current[0],dict(claimed,phase='uncertain'))
            raise GrantRejected('Apple refresh rejected; explicit sign-in required') from None
        except Exception:
            self.store.compare_exchange(k,current[0],dict(claimed,phase='uncertain'))
            raise GrantUncertain('Apple refresh unconfirmed; explicit sign-in required') from None
        # A revoke may have changed the vault while Apple was responding. Never
        # overwrite its tombstone or revive it. Keep the original grant context.
        stored=dict(row[1],envelope=self.cipher.seal(updated,row[1]['context']))
        try:self.store.compare_exchange(key('apple-grant',identity),row[0],stored)
        except StateUnavailable:pass
        saved=self._row(identity)
        if saved is None or saved[1]!=stored:
            raise GrantUncertain('refreshed credential persistence unconfirmed')
        valid=dict(claimed,phase='valid')
        self.immutable(self.audit,key('apple-grant-validated',digest([identity,n])),valid)
        try:self.store.compare_exchange(k,current[0],valid)
        except StateUnavailable:pass
        if self._validation(identity)[1]!=valid: raise StateUnavailable('refresh validation unconfirmed')
        self.require(installation,operation,person)

    def revoke(self, installation, operation):
        """Trusted deletion coordinator only; no public route or deletion claim.

        An unknown exchange without a token remains pending/manual, never done.
        Apple revocation may be repeated after response loss (200 is idempotent).
        Removing current ciphertext is NOT erasure of retained object versions.
        """
        identity = self._id(installation, operation)
        self.immutable(self.audit, key('apple-grant-revoke-intent', identity), dict(v=1, grant_id=identity))
        for _ in range(CAS_ATTEMPTS):
            row = self._row(identity)
            if row is None: raise GrantUncertain('no captured grant; manual revocation must be considered')
            c = row[1]
            if c['phase'] == 'revoked':
                receipt = dict(v=1, state='apple_token_revoked', grant_id=identity)
                self.immutable(self.audit, key('apple-grant-revoked', identity), receipt)
                return receipt
            validation = self._validation(identity)
            if validation is not None and validation[1]['phase'] in ('validating','uncertain'):
                raise GrantUncertain('refresh outcome must be resolved before confirmed revocation')
            if c['envelope'] is None: raise GrantUncertain('exchange has no confirmed token; revocation pending')
            tokens = self.cipher.open(c['envelope'], c['context'])
            self.provider.revoke(tokens['refresh_token'])
            self.store.compare_exchange(key('apple-grant', identity), row[0], dict(c, phase='revoked', envelope=None))
        raise StateUnavailable('revocation persistence unconfirmed; retry same intent')
