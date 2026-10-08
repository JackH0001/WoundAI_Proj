"""Durable assertion admission, not yet connected to public routes.

Registration rows must be created by the trusted registration flow AFTER Apple
attestation and receipt checks. This module cannot turn anonymous input into a
registered key. SQLite is for local disk/tests, never Cloud Run ephemeral disk or
GCS FUSE. GCS uses one generation-guarded object per key for counter+challenges.
"""
import base64
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import re
import secrets
import sqlite3
import time

from google.api_core.exceptions import NotFound, PreconditionFailed

from audit_chain_contract import loads_json_object_strict
from lite_attest_assertion import AssertionPolicy, AssertionRejected, verify_assertion
from lite_attest_request import client_data

MAX_STATE = 16384
CHALLENGE_TTL = 120
MAX_CHALLENGES = 4
MAX_WITHDRAWAL_CHALLENGES = 2
CAS_ATTEMPTS = 8


class StateUnavailable(RuntimeError): pass
class AdmissionRejected(ValueError): pass
class ChallengeLimit(AdmissionRejected): pass


def _key_name(key_id):
    if type(key_id) is not bytes or len(key_id) != 32:
        raise AdmissionRejected('invalid key identifier')
    return key_id.hex()


def _revision(value, *, creating=False):
    if type(value) is not int or value < (0 if creating else 1):
        raise StateUnavailable('invalid storage revision')
    return value


def _encode(state):
    try:
        data = json.dumps(state, allow_nan=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
        if type(state) is not dict or len(data) > MAX_STATE:
            raise ValueError('invalid state size/type')
        return data
    except (ValueError, TypeError, RecursionError) as exc:
        raise StateUnavailable('invalid security state') from exc


def _decode(data):
    try:
        if type(data) is not bytes or len(data) > MAX_STATE:
            raise ValueError('invalid state size/type')
        return loads_json_object_strict(data.decode('utf-8'))
    except (ValueError, TypeError, RecursionError) as exc:
        raise StateUnavailable('unreadable security state') from exc


class SQLiteStateStore:
    """Local durable CAS, with database transactions across threads/processes."""
    def __init__(self, path):
        self.path = str(path)
        if self.path == ':memory:':
            raise ValueError('security state requires persistent local storage')
        with self._connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS attest_state (key_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, data BLOB NOT NULL)')

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            db.execute('PRAGMA synchronous=FULL')
            with db:
                yield db
        finally:
            db.close()

    def load(self, key_id):
        name = _key_name(key_id)
        try:
            with self._connect() as db:
                row = db.execute('SELECT revision,data FROM attest_state WHERE key_id=?', (name,)).fetchone()
            return None if row is None else (_revision(row[0]), _decode(row[1]))
        except sqlite3.Error as exc:
            raise StateUnavailable('security state read failed') from exc

    def compare_exchange(self, key_id, expected_revision, state):
        name = _key_name(key_id); _revision(expected_revision, creating=True); data = _encode(state)
        try:
            with self._connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if expected_revision == 0:
                    cursor = db.execute('INSERT OR IGNORE INTO attest_state VALUES (?,1,?)', (name, data))
                else:
                    cursor = db.execute('UPDATE attest_state SET revision=revision+1,data=? WHERE key_id=? AND revision=?',
                                        (data, name, expected_revision))
                return cursor.rowcount == 1
        except sqlite3.Error as exc:
            raise StateUnavailable('security state update failed') from exc


class GCSStateStore:
    """Injected trusted bucket; never creates credentials/clients at import time.

    Provision a mutable security-state namespace separately from locked audit
    epochs. No delete/list API is exposed. Replacement requires generation CAS.
    A timeout, including commit-with-lost-response, is unavailable, not success.
    """
    def __init__(self, bucket, *, prefix='lite_security/v1'):
        if type(prefix) is not str or re.fullmatch(r'[a-z0-9_-]+(?:/[a-z0-9_-]+)*', prefix) is None:
            raise ValueError('invalid security state prefix')
        self.bucket, self.prefix = bucket, prefix

    def _name(self, key_id): return self.prefix + '/' + _key_name(key_id) + '.json'

    def load(self, key_id):
        name = self._name(key_id)
        try:
            blob = self.bucket.blob(name)
            try:
                blob.reload(timeout=10, retry=None)
            except NotFound:
                return None
            generation = _revision(blob.generation)
            if type(blob.size) is not int or not 0 < blob.size <= MAX_STATE:
                raise StateUnavailable('invalid security object size')
            # Pin BOTH metadata and content to one immutable generation. Reading
            # an old version is safe: its later CAS cannot overwrite a newer one.
            data = self.bucket.blob(name, generation=generation).download_as_bytes(
                if_generation_match=generation, checksum='crc32c', timeout=10, retry=None)
            return generation, _decode(data)
        except StateUnavailable:
            raise
        except Exception as exc:
            # A 404 after metadata is a lost generation, never a missing key.
            raise StateUnavailable('security state read failed') from exc

    def compare_exchange(self, key_id, expected_revision, state):
        name = self._name(key_id); _revision(expected_revision, creating=True); data = _encode(state)
        try:
            blob = self.bucket.blob(name)
            blob.upload_from_string(data, content_type='application/json',
                                    if_generation_match=expected_revision, checksum='crc32c',
                                    timeout=10, retry=None)
            generation = _revision(blob.generation)
            if generation <= expected_revision:
                raise StateUnavailable('storage revision did not advance')
            return True
        except PreconditionFailed:
            return False
        except StateUnavailable:
            raise
        except Exception as exc:
            raise StateUnavailable('security state update outcome unknown') from exc


@dataclass(frozen=True)
class Challenge:
    identifier: str
    nonce: bytes
    expires_at: int


@dataclass(frozen=True)
class Admission:
    installation: str
    key_id: bytes
    counter: int
    request_id: str
    client_data_sha256: str


class AssertionAdmission:
    def __init__(self, store, *, policy, audience, environment='production', clock=time.time):
        if not isinstance(policy, AssertionPolicy):
            raise ValueError('trusted assertion policy required')
        if type(audience) is not str or re.fullmatch(r'[a-z0-9-]{1,64}', audience) is None:
            raise ValueError('invalid audience')
        allowed = {'production': {2, 4}, 'development': {3}}
        if environment not in allowed or not policy.allowed_categories <= allowed[environment]:
            raise ValueError('incompatible environment policy')
        self.store, self.policy, self.audience, self.environment, self.clock = store, policy, audience, environment, clock

    def _now(self):
        value = self.clock()
        if type(value) not in (int, float) or not 0 <= value < 2**53:
            raise StateUnavailable('invalid server clock')
        return int(value)

    def _load(self, key_id, *, allow_revoked=False):
        row = self.store.load(key_id)
        if row is None:
            raise AdmissionRejected('unregistered key')
        generation, state = row
        _revision(generation)
        try:
            fields = {'v', 'key_id', 'installation', 'audience', 'app_id', 'environment', 'public_key',
                      'receipt_sha256', 'status', 'counter', 'challenges'}
            if type(state) is not dict or set(state) not in (fields, fields | {'registration'}) or type(state['v']) is not int or state['v'] != 1:
                raise ValueError('state schema')
            if 'registration' in state:
                proof = state['registration']
                if (type(proof) is not dict or set(proof) != {'challenge_id', 'attestation_sha256'} or
                        type(proof['challenge_id']) is not str or re.fullmatch(r'[0-9a-f]{32}', proof['challenge_id']) is None or
                        type(proof['attestation_sha256']) is not str or re.fullmatch(r'[0-9a-f]{64}', proof['attestation_sha256']) is None):
                    raise ValueError('registration proof reference')
            if (state['key_id'] != _key_name(key_id) or state['audience'] != self.audience or
                    state['app_id'] != self.policy.app_id or state['environment'] != self.environment):
                raise ValueError('state identity')
            if (type(state['installation']) is not str or re.fullmatch(r'[A-Za-z0-9_-]{1,128}', state['installation']) is None or
                    type(state['receipt_sha256']) is not str or re.fullmatch(r'[0-9a-f]{64}', state['receipt_sha256']) is None):
                raise ValueError('state registration')
            if type(state['counter']) is not int or not 0 <= state['counter'] <= 0xffffffff:
                raise ValueError('state counter')
            if state['status'] not in ('active', 'revoked') or type(state['public_key']) is not str:
                raise ValueError('state status/key')
            point = base64.b64decode(state['public_key'], validate=True)
            if len(point) != 65 or point[0] != 4 or hashlib.sha256(point).digest() != key_id:
                raise ValueError('state public key')
            challenges = state['challenges']
            if type(challenges) is not dict or len(challenges) > MAX_CHALLENGES + MAX_WITHDRAWAL_CHALLENGES:
                raise ValueError('state challenges')
            for identifier, value in challenges.items():
                if (type(identifier) is not str or re.fullmatch(r'[0-9a-f]{32}', identifier) is None or
                        type(value) is not dict or set(value) not in ({'nonce', 'issued_at', 'expires_at'}, {'nonce', 'issued_at', 'expires_at', 'purpose'})):
                    raise ValueError('challenge schema')
                if value.get('purpose', 'assert') not in ('assert', 'withdraw'):
                    raise ValueError('challenge purpose')
                if (type(value['nonce']) is not str or len(base64.b64decode(value['nonce'], validate=True)) != 32 or
                        type(value['issued_at']) is not int or type(value['expires_at']) is not int or
                        not 0 <= value['issued_at'] < value['expires_at'] <= value['issued_at'] + CHALLENGE_TTL):
                    raise ValueError('challenge bounds')
            if (sum(v.get('purpose', 'assert') == 'assert' for v in challenges.values()) > MAX_CHALLENGES or
                    sum(v.get('purpose', 'assert') == 'withdraw' for v in challenges.values()) > MAX_WITHDRAWAL_CHALLENGES):
                raise ValueError('challenge lane capacity')
        except (ValueError, TypeError, KeyError) as exc:
            raise StateUnavailable('invalid registered security state') from exc
        if state['status'] == 'revoked' and not allow_revoked:
            raise AdmissionRejected('revoked key')
        return generation, state, point

    def issue_challenge(self, key_id, *, purpose='assert'):
        if purpose not in ('assert', 'withdraw'):
            raise AdmissionRejected('unsupported challenge purpose')
        for _ in range(CAS_ATTEMPTS):
            generation, state, _point = self._load(key_id)
            now = self._now()
            if any(value['issued_at'] > now for value in state['challenges'].values()):
                raise StateUnavailable('server clock precedes issued challenge')
            state['challenges'] = {k: v for k, v in state['challenges'].items() if v['expires_at'] > now}
            limit = MAX_WITHDRAWAL_CHALLENGES if purpose == 'withdraw' else MAX_CHALLENGES
            if sum(v.get('purpose', 'assert') == purpose for v in state['challenges'].values()) >= limit:
                raise ChallengeLimit('outstanding challenge limit')
            identifier, nonce = secrets.token_hex(16), secrets.token_bytes(32)
            if identifier in state['challenges']:
                continue
            state['challenges'][identifier] = {'nonce': base64.b64encode(nonce).decode('ascii'),
                                              'issued_at': now, 'expires_at': now + CHALLENGE_TTL, 'purpose': purpose}
            if self.store.compare_exchange(key_id, generation, state):
                return Challenge(identifier, nonce, now + CHALLENGE_TTL)
        raise StateUnavailable('challenge update contention')

    def admit(self, key_id, challenge_id, assertion, *, request_id, method, path, content_type, body):
        if type(challenge_id) is not str or re.fullmatch(r'[0-9a-f]{32}', challenge_id) is None:
            raise AdmissionRejected('invalid challenge identifier')
        for _ in range(CAS_ATTEMPTS):
            generation, state, point = self._load(key_id)
            challenge = state['challenges'].get(challenge_id)
            now = self._now()
            if challenge is None or not challenge['issued_at'] <= now < challenge['expires_at']:
                raise AdmissionRejected('challenge missing, consumed or expired')
            if challenge.get('purpose', 'assert') == 'withdraw' and (method != 'DELETE' or path != '/api/v1/lite/data/' + state['installation']):
                raise AdmissionRejected('withdrawal challenge is deletion-only')
            try:
                data = client_data(audience=state['audience'], installation=state['installation'],
                                   key_id=key_id, challenge=base64.b64decode(challenge['nonce'], validate=True),
                                   request_id=request_id, method=method, path=path, content_type=content_type, body=body)
                verified = verify_assertion(assertion, public_key_x962=point, expected_key_id=key_id,
                                            client_data=data, previous_counter=state['counter'], policy=self.policy)
            except (ValueError, AssertionRejected) as exc:
                raise AdmissionRejected('assertion not valid for this request') from exc
            state['counter'] = verified.counter
            del state['challenges'][challenge_id]
            if not challenge['issued_at'] <= self._now() < challenge['expires_at']:
                raise AdmissionRejected('challenge expired during verification')
            # This is the linearization point: persist counter AND consumption
            # together, and return no admission if persistence is uncertain.
            if self.store.compare_exchange(key_id, generation, state):
                return Admission(state['installation'], key_id, verified.counter, request_id,
                                 hashlib.sha256(data).hexdigest())
        raise StateUnavailable('admission update contention')

    def revoke(self, key_id):
        """Trusted control-plane operation. No unauthenticated HTTP exposure."""
        for _ in range(CAS_ATTEMPTS):
            generation, state, _point = self._load(key_id, allow_revoked=True)
            if state['status'] == 'revoked':
                return
            state['status'], state['challenges'] = 'revoked', {}
            if self.store.compare_exchange(key_id, generation, state):
                return
        raise StateUnavailable('revocation update contention')
