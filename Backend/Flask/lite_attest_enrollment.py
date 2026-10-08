"""Atomic enrollment into the same durable row used for assertion admission.

No HTTP routes: callers must bound/rate-limit unauthenticated challenge requests.
A client cannot choose an installation or claim legacy anonymous data. Trust roots
and verification time are exclusively controlled by the two production verifiers.
"""
import base64
from dataclasses import dataclass
import hashlib
import hmac
import re
import secrets
import time

from lite_attest_registration import verify_attestation, MAX_ATTESTATION
from lite_attest_receipt import verify_registration_receipt
from lite_attest_state import (AssertionAdmission, AdmissionRejected, StateUnavailable,
                               Challenge, CAS_ATTEMPTS, CHALLENGE_TTL, _key_name, _revision)


@dataclass(frozen=True)
class Enrollment:
    installation: str
    key_id: bytes


class RegistrationService:
    def __init__(self, store, *, policy, audience, environment='production', clock=time.time):
        # Reuse trusted configuration and clock validation, not client input.
        self.admission = AssertionAdmission(store, policy=policy, audience=audience,
                                            environment=environment, clock=clock)
        self.store = store

    def _pending(self, key_id, row):
        revision, state = row
        _revision(revision)
        try:
            required = {'v', 'status', 'key_id', 'app_id', 'environment', 'audience',
                        'installation', 'challenge_id', 'nonce', 'issued_at', 'expires_at'}
            service = self.admission
            if (type(state) is not dict or set(state) != required or type(state['v']) is not int or
                    state['v'] != 1 or state['status'] != 'pending' or state['key_id'] != _key_name(key_id) or
                    state['app_id'] != service.policy.app_id or state['environment'] != service.environment or
                    state['audience'] != service.audience):
                raise ValueError('pending identity/schema')
            for field in ('installation', 'challenge_id'):
                if type(state[field]) is not str or re.fullmatch(r'[0-9a-f]{32}', state[field]) is None:
                    raise ValueError('pending identifier')
            if type(state['nonce']) is not str:
                raise ValueError('pending nonce')
            nonce = base64.b64decode(state['nonce'], validate=True)
            if len(nonce) != 32 or base64.b64encode(nonce).decode('ascii') != state['nonce']:
                raise ValueError('pending nonce')
            if (type(state['issued_at']) is not int or type(state['expires_at']) is not int or
                    not 0 <= state['issued_at'] < state['expires_at'] == state['issued_at'] + CHALLENGE_TTL):
                raise ValueError('pending time bounds')
        except (ValueError, TypeError, KeyError) as exc:
            raise StateUnavailable('invalid pending registration') from exc
        return revision, state, nonce

    def issue_challenge(self, key_id):
        _key_name(key_id)
        service = self.admission
        for _ in range(CAS_ATTEMPTS):
            row = self.store.load(key_id)
            now = service._now()
            revision = 0
            if row is not None:
                if row[1].get('status') != 'pending':
                    service._load(key_id)  # Reject revoked/corrupt rows, never replace.
                    raise AdmissionRejected('key already registered')
                revision, state, nonce = self._pending(key_id, row)
                if now < state['issued_at']:
                    raise StateUnavailable('clock precedes registration challenge')
                if now < state['expires_at']:
                    return Challenge(state['challenge_id'], nonce, state['expires_at'])
            nonce = secrets.token_bytes(32)
            state = dict(v=1, status='pending', key_id=key_id.hex(), app_id=service.policy.app_id,
                         environment=service.environment, audience=service.audience,
                         installation=secrets.token_hex(16), challenge_id=secrets.token_hex(16),
                         nonce=base64.b64encode(nonce).decode('ascii'), issued_at=now,
                         expires_at=now + CHALLENGE_TTL)
            if self.store.compare_exchange(key_id, revision, state):
                return Challenge(state['challenge_id'], nonce, state['expires_at'])
        raise StateUnavailable('registration challenge contention')

    def register(self, key_id, challenge_id, encoded):
        _key_name(key_id)
        if (type(challenge_id) is not str or re.fullmatch(r'[0-9a-f]{32}', challenge_id) is None or
                type(encoded) is not bytes or not 1 <= len(encoded) <= MAX_ATTESTATION):
            raise AdmissionRejected('invalid enrollment input')
        digest = hashlib.sha256(encoded).hexdigest()
        service = self.admission
        for _ in range(CAS_ATTEMPTS):
            row = self.store.load(key_id)
            if row is None:
                raise AdmissionRejected('registration challenge not issued')
            if row[1].get('status') != 'pending':
                _revision_number, state, _point = service._load(key_id)
                proof = state.get('registration')
                # Return an existing receipt after a lost acknowledgement. This
                # grants no assertion admission and never resets counter/owner.
                if (proof is not None and proof['challenge_id'] == challenge_id and
                        hmac.compare_digest(proof['attestation_sha256'], digest)):
                    return Enrollment(state['installation'], key_id)
                raise AdmissionRejected('key already registered with another proof')
            revision, state, nonce = self._pending(key_id, row)
            if (state['challenge_id'] != challenge_id or
                    not state['issued_at'] <= service._now() < state['expires_at']):
                raise AdmissionRejected('registration challenge mismatch or expired')
            attested = verify_attestation(encoded, expected_key_id=key_id, challenge=nonce,
                                           policy=service.policy, environment=service.environment)
            receipt = verify_registration_receipt(attested, app_id=service.policy.app_id, challenge=nonce)
            active = dict(v=1, key_id=key_id.hex(), installation=state['installation'],
                          audience=state['audience'], app_id=state['app_id'], environment=state['environment'],
                          public_key=base64.b64encode(attested.public_key_x962).decode('ascii'),
                          receipt_sha256=receipt.sha256, status='active', counter=0, challenges={},
                          registration=dict(challenge_id=challenge_id, attestation_sha256=digest))
            if not state['issued_at'] <= service._now() < state['expires_at']:
                raise AdmissionRejected('registration challenge expired during verification')
            # One CAS replaces pending challenge with verified key/owner. There
            # is no interval with a consumed challenge but no registration row.
            if self.store.compare_exchange(key_id, revision, active):
                return Enrollment(state['installation'], key_id)
        raise StateUnavailable('registration update contention')
