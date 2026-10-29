"""Deletion-only reauthentication. Never renews service or exchanges an Apple code.

The installed device and a fresh Apple identity must agree with the *existing*
registry subject. A CAS-committed proof authorizes only the original deletion;
it survives a lost response, without retaining the JWT or creating a new grant.
"""
import hashlib
import hmac
import secrets
from lite_account_identity import IdentityRejected
from lite_attest_state import CAS_ATTEMPTS, StateUnavailable
from lite_subscription_executor import key
from lite_usage_ledger import digest, _hex


class DeletionIdentityExpired(IdentityRejected): pass


class DeletionIdentity:
    def __init__(self, coordinator):
        self.d = coordinator
        self.b = coordinator.bridge

    def _key(self, installation, operation):
        _hex(operation, 32)
        return key('deletion-identity', digest([self.d.scope, installation, operation]))

    def _binding(self, installation, operation, account_hash):
        _hex(operation, 32); _hex(account_hash)
        a = self.b.registry.registration(installation)
        if (not a['subject'].startswith('apple-')
                or not hmac.compare_digest(digest(a['subject']), account_hash)):
            raise IdentityRejected('existing account required')
        person = a['subject'][6:]; _hex(person)
        row = self.d.lifecycle._read(person)
        if row is None: raise IdentityRejected('existing lifecycle required')
        if row[1]['phase'] == 'closing':
            self.d.lifecycle.progress(person, installation, operation)
        return dict(v=1, scope=self.d.scope, installation=installation, operation=operation,
                    account_sha256=account_hash, person=person)

    def _read(self, installation, operation):
        row = self.d.store.load(self._key(installation, operation))
        if row is None: return None
        try:
            c = row[1]; b = c['binding']; q = c['challenge']
            if (type(c) is not dict or set(c) != {'binding','challenge','phase','token_sha256','verified_at'}
                    or b != self._binding(installation, operation, b['account_sha256'])
                    or type(q) is not dict or set(q) != {'v','installation','operation','reauth_operation',
                        'account_sha256','nonce','state','created_at','expires_at','audience','purpose'}
                    or type(q['v']) is not int or q['v'] != 1 or q['installation'] != installation
                    or q['operation'] != operation or q['account_sha256'] != b['account_sha256']
                    or q['audience'] != self.b.verifier.audience or q['purpose'] != 'continue_deletion'
                    or type(q['created_at']) is not int or type(q['expires_at']) is not int
                    or not 0 <= q['created_at'] <= self.b.now() or q['expires_at'] != q['created_at']+300
                    or c['phase'] not in ('challenge','verified')): raise ValueError()
            for f, n in (('reauth_operation',32),('state',64),('nonce',64)): _hex(q[f],n)
            if c['phase'] == 'verified':
                _hex(c['token_sha256'])
                if type(c['verified_at']) is not int or not q['created_at'] <= c['verified_at'] < q['expires_at']: raise ValueError()
            elif c['token_sha256'] is not None or c['verified_at'] is not None: raise ValueError()
        except (ValueError, TypeError, KeyError):
            raise StateUnavailable('deletion identity checkpoint invalid; never reset') from None
        return row

    def challenge(self, installation, operation, reauth_operation, account_hash):
        _hex(reauth_operation,32)
        binding = self._binding(installation, operation, account_hash)
        # A saved attempt ID can never be recycled for a different request.
        opkey = key('deletion-identity-attempt', digest([self.d.scope,installation,reauth_operation]))
        self.d.immutable(self.d.audit, opkey, binding)
        for _ in range(CAS_ATTEMPTS):
            old = self._read(installation, operation)
            if old:
                c = old[1]
                if c['binding'] != binding: raise IdentityRejected('account changed')
                if c['challenge']['reauth_operation'] == reauth_operation:
                    if c['phase'] == 'challenge' and self.b.now() >= c['challenge']['expires_at']:
                        raise DeletionIdentityExpired('explicit new verification required')
                    return c['challenge']
                if c['phase'] == 'verified': raise IdentityRejected('resume original verified deletion')
                # New attempts replace only expired challenges. This avoids an
                # old UI instance fencing a still-valid prompt on another task.
                if self.b.now() < c['challenge']['expires_at']:
                    raise IdentityRejected('unexpired verification already pending')
            used = key('deletion-identity-challenge', digest([self.d.scope,installation,reauth_operation]))
            saved = self.d.audit.load(used)
            n = self.b.now()
            q = dict(v=1,installation=installation,operation=operation,reauth_operation=reauth_operation,
                     account_sha256=account_hash,nonce=secrets.token_hex(32),state=secrets.token_hex(32),
                     created_at=n,expires_at=n+300,audience=self.b.verifier.audience,purpose='continue_deletion')
            if saved is None:
                self.d.audit.compare_exchange(used,0,q)
                saved = self.d.audit.load(used)
            if saved is None: raise StateUnavailable('verification challenge not durable')
            q = saved[1]
            # Immutable challenge is written first. A lost checkpoint write can
            # reuse it, but an expired/superseded attempt never gets a new nonce.
            if (type(q) is not dict or q.get('installation') != installation
                    or q.get('operation') != operation or q.get('reauth_operation') != reauth_operation
                    or q.get('account_sha256') != account_hash): raise StateUnavailable('saved challenge differs')
            if n >= q['expires_at']: raise DeletionIdentityExpired('expired attempt cannot return')
            c = dict(binding=binding,challenge=q,phase='challenge',token_sha256=None,verified_at=None)
            if self.d.store.compare_exchange(self._key(installation,operation),old[0] if old else 0,c):
                return self._read(installation,operation)[1]['challenge']
        raise StateUnavailable('deletion verification contention')

    def confirm(self, installation, operation, reauth_operation, account_hash, signed, state):
        _hex(reauth_operation,32)
        if type(signed) is not str or not 1 <= len(signed) <= 16000: raise IdentityRejected('bounded signed identity required')
        binding = self._binding(installation,operation,account_hash)
        for _ in range(CAS_ATTEMPTS):
            row = self._read(installation,operation)
            if row is None: raise IdentityRejected('saved verification required')
            rev,c = row; q = c['challenge']
            if (c['binding'] != binding or q['reauth_operation'] != reauth_operation
                    or q['state'] != state): raise IdentityRejected('verification binding differs')
            if c['phase'] == 'verified':
                if not hmac.compare_digest(c['token_sha256'],digest(signed)): raise IdentityRejected('proof differs')
                break
            if self.b.now() >= q['expires_at']: raise DeletionIdentityExpired('explicit new verification required')
            claims = self.b.verifier.verify(signed,q['nonce'])
            person = hmac.new(self.b.secret,(self.b.namespace+'\0'+self.b.verifier.issuer+'\0'+claims['sub']).encode(),hashlib.sha256).hexdigest()
            if claims['issued'] < q['created_at']-30 or not hmac.compare_digest(person,binding['person']):
                raise IdentityRejected('same Apple account required')
            n = self.b.now()
            if n >= q['expires_at']: raise DeletionIdentityExpired('verification expired before commit')
            verified = dict(c,phase='verified',token_sha256=digest(signed),verified_at=n)
            if self.d.store.compare_exchange(self._key(installation,operation),rev,verified): break
        else: raise StateUnavailable('deletion proof contention')
        if not self.resume(installation,operation): raise StateUnavailable('deletion proof unconfirmed')
        return self.d.status(installation,operation)

    def resume(self, installation, operation):
        row = self._read(installation,operation)
        if row is None or row[1]['phase'] != 'verified': return False
        c = row[1]
        # Audit must be durable before closure; failure can be retried with the
        # saved operation, even after the Apple JWT expires. No service login.
        proof = dict(v=1,binding=c['binding'],challenge_sha256=digest(c['challenge']),
                     token_sha256=c['token_sha256'],verified_at=c['verified_at'])
        self.d.immutable(self.d.audit,key('deletion-identity-proof',digest(proof)),proof)
        self.d.lifecycle.close(c['binding']['person'],installation,operation)
        return True
