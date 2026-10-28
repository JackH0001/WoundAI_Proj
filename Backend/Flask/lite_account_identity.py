"""Opt-in, short-session Apple identity bridge. No discovery or startup activation.

App Attest must bind every HTTP request to its installation. Apple ID-token
verification supplies the human subject; neither email nor client account IDs do.
Public-key snapshots and identity namespace/key are supplied by trusted startup.
No refresh-token exchange, Apple settings, research or quota writes occur here.
"""
import base64
import hashlib
import hmac
import secrets
import uuid
import jwt
from flask import Blueprint, g, jsonify, request
from audit_chain_contract import loads_json_object_strict
from lite_attest_state import StateUnavailable, CAS_ATTEMPTS
from lite_usage_ledger import UsageConflict, digest, _hex, _subject
from lite_subscription_runtime import SubscriptionRuntime
from lite_subscription_executor import key

PREFIX = '/api/v1/lite/account/'
ACCOUNT_PATHS = {PREFIX + p for p in ('challenge', 'confirm', 'status', 'logout')}


class IdentityRejected(ValueError): pass


class AppleIdentityVerifier:
    """Bounded trusted JWKS snapshot; never follows a JWT-provided URL/key."""
    issuer = 'https://appleid.apple.com'

    def __init__(self, *, audience, jwks, fetched_at, expires_at, clock):
        if (type(audience) is not str or not 1 <= len(audience) <= 128
                or type(jwks) is not dict or set(jwks) != {'keys'}
                or type(jwks['keys']) is not list or not 1 <= len(jwks['keys']) <= 8
                or type(fetched_at) is not int or type(expires_at) is not int
                or not 0 < expires_at - fetched_at <= 3600):
            raise ValueError('trusted bounded identity configuration required')
        self.audience, self.clock = audience, clock
        self.fetched_at, self.expires_at, self.keys = fetched_at, expires_at, {}
        for item in jwks['keys']:
            alg, kid = item.get('alg'), item.get('kid')
            if (alg not in ('RS256', 'ES256') or type(kid) is not str or not 1 <= len(kid) <= 128
                    or kid in self.keys or item.get('use') != 'sig'
                    or ('d' in item) or (alg == 'RS256' and item.get('kty') != 'RSA')
                    or (alg == 'ES256' and (item.get('kty') != 'EC' or item.get('crv') != 'P-256'))):
                raise ValueError('unsupported identity signing key')
            k = jwt.PyJWK.from_dict(item, algorithm=alg).key
            if alg == 'RS256' and k.key_size < 2048: raise ValueError('weak identity key')
            self.keys[kid] = (alg, k)

    def verify(self, signed, nonce):
        now = self.clock()
        if type(now) is not int or not self.fetched_at <= now < self.expires_at:
            raise StateUnavailable('fresh trusted Apple signing keys required')
        try:
            if type(signed) is not str or not 1 <= len(signed) <= 16000: raise ValueError()
            parts = signed.split('.')
            if len(parts) != 3: raise ValueError()
            def decode(part):
                return loads_json_object_strict(base64.b64decode(part + '=' * (-len(part) % 4), altchars=b'-_', validate=True).decode())
            header, raw = decode(parts[0]), decode(parts[1])
            if set(header) - {'alg', 'kid', 'typ'} or header.get('typ', 'JWT') != 'JWT': raise ValueError()
            alg, public = self.keys[header['kid']]
            if header['alg'] != alg: raise ValueError()
            claims = jwt.decode(signed, public, algorithms=[alg], audience=self.audience,
                issuer=self.issuer, options={'verify_exp': False, 'verify_iat': False,
                    'verify_nbf': False, 'require': ['iss', 'aud', 'sub', 'iat', 'exp', 'nonce']})
            if (claims != raw or claims['aud'] != self.audience or claims['nonce'] != nonce
                    or type(claims['sub']) is not str or not 1 <= len(claims['sub']) <= 255
                    or not claims['sub'].isascii() or not claims['sub'].isprintable()
                    or any(type(claims[x]) is not int for x in ('iat', 'exp'))
                    or not 0 <= claims['iat'] <= now + 30 or not now < claims['exp']
                    or not 0 < claims['exp'] - claims['iat'] <= 86400
                    or ('nbf' in claims and (type(claims['nbf']) is not int or claims['nbf'] > now))):
                raise ValueError()
            return dict(sub=claims['sub'], expires=claims['exp'], issued=claims['iat'])
        except Exception as exc:
            raise IdentityRejected('Apple identity, signature or nonce rejected') from exc


class AccountIdentityBridge:
    """Immutable human/device association; CAS sessions prevent late reactivation.

    Existing installations cannot silently switch people. A separate reviewed
    unlink/account-deletion procedure is required; logout never erases research.
    """
    def __init__(self, *, runtime, verifier, store, audit_store, namespace, identity_key):
        if not isinstance(runtime, SubscriptionRuntime) or not isinstance(verifier, AppleIdentityVerifier):
            raise ValueError('trusted runtime and Apple verifier required')
        _subject(namespace)
        if type(identity_key) is not bytes or len(identity_key) != 32: raise ValueError('32-byte identity key required')
        self.runtime, self.registry, self.verifier = runtime, runtime.registry, verifier
        self.store, self.audit, self.namespace, self.secret = store, audit_store, namespace, identity_key
        self.config = dict(v=1, namespace=namespace, issuer=verifier.issuer, audience=verifier.audience,
            identity_key_sha256=hashlib.sha256(identity_key).hexdigest(), session_seconds=900)
        self.immutable(store, key('human-identity-config', namespace), self.config)

    def now(self):
        n = self.verifier.clock()
        if type(n) is not int or n < 0: raise StateUnavailable('invalid identity clock')
        return n

    immutable = staticmethod(SubscriptionRuntime.immutable)

    def _key(self, installation):
        return key('human-session', digest([self.namespace, _subject(installation)]))

    def _read(self, installation):
        row = self.store.load(self._key(installation))
        if row is None: return None
        c = row[1]
        try:
            if (set(c) != {'v','installation_hash','operation','nonce','state','created_at','expires_at','phase','account','token_sha256','session_expires_at'}
                    or type(c['v']) is not int or c['v'] != 1 or c['installation_hash'] != digest(installation)
                    or c['phase'] not in ('challenge','verified','active','ended')
                    or any(type(c[x]) is not int for x in ('created_at','expires_at','session_expires_at'))
                    or not 0 <= c['created_at'] <= self.now() or c['expires_at'] != c['created_at'] + 300): raise ValueError()
            _hex(c['operation'],32); _hex(c['nonce']); _hex(c['state'])
            if c['account'] is not None:
                if set(c['account']) != {'v','subject','token','identity_sha256'} or c['account']['v'] != 1: raise ValueError()
                _hex(c['account']['identity_sha256']); _subject(c['account']['subject'])
                if str(uuid.UUID(c['account']['token'])) != c['account']['token']: raise ValueError()
                _hex(c['token_sha256'])
                if not c['created_at'] < c['session_expires_at'] <= c['expires_at'] + 900: raise ValueError()
            elif c['phase'] in ('verified','active') or c['token_sha256'] is not None or c['session_expires_at'] != 0: raise ValueError()
        except (ValueError, TypeError, KeyError) as exc: raise StateUnavailable('identity state corrupt; never reset') from exc
        return row

    def challenge(self, installation, operation):
        _hex(operation,32)
        for _ in range(CAS_ATTEMPTS):
            old = self._read(installation)
            if old and old[1]['operation'] == operation:
                c=old[1]
                if c['phase'] != 'challenge' or self.now() >= c['expires_at']: raise IdentityRejected('start a new explicit sign-in')
                self.immutable(self.audit,key('human-operation',digest([self.namespace,installation,operation])),dict(v=1,challenge_sha256=digest(c)))
                return self._public(c, installation)
            # Old operation IDs cannot recreate a session after logout/restart.
            opkey=key('human-operation',digest([self.namespace,installation,operation]))
            if self.audit.load(opkey) is not None or self.audit.load(key('human-logout',digest([self.namespace,installation,operation]))) is not None: raise IdentityRejected('old sign-in operation')
            n=self.now()
            c=dict(v=1,installation_hash=digest(installation),operation=operation,nonce=secrets.token_hex(32),
                state=secrets.token_hex(32),created_at=n,expires_at=n+300,phase='challenge',account=None,
                token_sha256=None,session_expires_at=0)
            if self.store.compare_exchange(self._key(installation),old[0] if old else 0,c):
                self.immutable(self.audit,opkey,dict(v=1,challenge_sha256=digest(c)))
                return self._public(c,installation)
        raise StateUnavailable('sign-in concurrent change')

    def _public(self,c,installation):
        return dict(v=1,installation=installation,operation=c['operation'],nonce=c['nonce'],state=c['state'],
                    created_at=c['created_at'],expires_at=c['expires_at'],audience=self.verifier.audience)

    def confirm(self, installation, operation, signed, state):
        _hex(operation,32)
        if type(signed) is not str or not 1 <= len(signed) <= 16000: raise IdentityRejected('bounded identity required')
        for _ in range(CAS_ATTEMPTS):
            row=self._read(installation)
            if row is None or row[1]['operation'] != operation: raise IdentityRejected('current saved challenge required')
            rev,c=row
            if (c['state'] != state or c['phase'] == 'ended' or
                    self.audit.load(key('human-logout',digest([self.namespace,installation,operation]))) is not None): raise IdentityRejected('sign-in state rejected')
            token_hash=digest(signed)
            if c['phase'] == 'challenge':
                if self.now() >= c['expires_at']: raise IdentityRejected('sign-in expired')
                claims=self.verifier.verify(signed,c['nonce'])
                if claims['issued'] < c['created_at']-30: raise IdentityRejected('token predates challenge')
                identity=hmac.new(self.secret, (self.namespace+'\0'+self.verifier.issuer+'\0'+claims['sub']).encode(),hashlib.sha256).hexdigest()
                k=key('human-account',identity);account=self.store.load(k)
                if account is None:
                    a=dict(v=1,identity_sha256=identity,subject='apple-'+identity,token=str(uuid.uuid4()))
                    self.store.compare_exchange(k,0,a);account=self.store.load(k)
                if account is None: raise StateUnavailable('account creation unconfirmed')
                a=account[1]
                if set(a)!={'v','identity_sha256','subject','token'} or a['v']!=1 or a['identity_sha256']!=identity or a['subject']!='apple-'+identity:
                    raise StateUnavailable('account identity corrupt')
                if str(uuid.UUID(a['token']))!=a['token']: raise StateUnavailable('account token corrupt')
                updated=dict(c,phase='verified',account=a,token_sha256=token_hash,
                             session_expires_at=min(claims['expires'],self.now()+900))
                if not self.store.compare_exchange(self._key(installation),rev,updated): continue
                continue
            if c['token_sha256'] != token_hash or self.now() >= c['session_expires_at']:
                raise IdentityRejected('saved sign-in differs or expired')
            a=c['account'];proof=digest(['apple-device-binding/1',self.namespace,installation,a])
            registration=self.registry.store.load(self.registry.key(installation))
            if registration is not None:
                existing=self.registry.registration(installation)
                if existing['subject']!=a['subject'] or existing['token']!=a['token']:
                    raise IdentityRejected('installation belongs to another account; no implicit switch')
                proof=existing['proof']
            evidence=dict(v=1,installation_hash=digest(installation),operation=operation,account_sha256=digest(a['subject']),
                app_account_token=a['token'],identity_token_sha256=token_hash,registration_proof=proof,
                session_expires_at=c['session_expires_at'])
            self.immutable(self.audit,key('human-verified',digest([self.namespace,installation,operation])),evidence)
            self.registry.provision(installation,a['subject'],a['token'],None,proof)
            # Logout/new challenge may race with provisioning. The immutable
            # mapping may remain, but it cannot authorize an ended session.
            if c['phase']=='verified':
                if not self.store.compare_exchange(self._key(installation),rev,dict(c,phase='active')): continue
            self.require_session(installation)
            if self._read(installation)[1]['operation']!=operation: raise IdentityRejected('session changed')
            return self.status(installation)
        raise StateUnavailable('sign-in confirmation raced; keep local evidence')

    def require_session(self, installation):
        row=self._read(installation)
        if row is None or row[1]['phase']!='active' or self.now()>=row[1]['session_expires_at']:
            raise IdentityRejected('sign-in required')
        c=row[1]
        if self.audit.load(key('human-logout',digest([self.namespace,installation,c['operation']]))) is not None:
            raise IdentityRejected('session signed out')
        a=self.registry.registration(installation)
        if a['subject']!=c['account']['subject'] or a['token']!=c['account']['token']:
            raise IdentityRejected('account mapping differs')
        return c

    def status(self, installation):
        c=self.require_session(installation)
        return dict(v=1,state='signed_in',installation=installation,operation=c['operation'],
            account_sha256=digest(c['account']['subject']),app_account_token=c['account']['token'],
            expires_at=c['session_expires_at'])

    def logout(self, installation, operation):
        _hex(operation,32)
        receipt=dict(v=1,state='signed_out',installation=installation,operation=operation)
        self.immutable(self.audit,key('human-logout',digest([self.namespace,installation,operation])),receipt)
        for _ in range(CAS_ATTEMPTS):
            row=self._read(installation)
            if row is None or row[1]['operation']!=operation or row[1]['phase']=='ended': return receipt
            if self.store.compare_exchange(self._key(installation),row[0],dict(row[1],phase='ended')): return receipt
        raise StateUnavailable('logout unconfirmed')


def blueprint(bridge):
    if not isinstance(bridge,AccountIdentityBridge): raise ValueError('trusted identity bridge required')
    bp=Blueprint('lite_account_identity',__name__)
    def action(kind):
        try:
            admission=getattr(g,'lite_admission',None)
            if admission is None:return jsonify(error='lite_attestation_required'),401
            body=loads_json_object_strict(request.get_data().decode())
            fields={'anon_id'} | ({'operation'} if kind!='status' else set()) | ({'identity_token','state'} if kind=='confirm' else set())
            if request.mimetype!='application/json' or set(body)!=fields: raise IdentityRejected('exact account fields required')
            if body['anon_id']!=admission.installation:return jsonify(error='lite_owner_mismatch'),403
            if kind=='confirm':result=bridge.confirm(admission.installation,body['operation'],body['identity_token'],body['state'])
            elif kind=='status':result=bridge.status(admission.installation)
            else:result=getattr(bridge,kind)(admission.installation,body['operation'])
            return jsonify(result)
        except IdentityRejected:return jsonify(error='lite_sign_in_required',keep_local=True),401
        except (ValueError,TypeError,KeyError,UsageConflict):return jsonify(error='lite_account_conflict',keep_local=True),409
        except Exception:return jsonify(error='lite_account_unconfirmed',keep_local=True),503
    for kind in ('challenge','confirm','status','logout'):
        bp.add_url_rule(PREFIX+kind,kind,lambda kind=kind:action(kind),methods=['POST'])
    @bp.after_request
    def no_cache(r):r.headers['Cache-Control']='no-store';return r
    return bp
