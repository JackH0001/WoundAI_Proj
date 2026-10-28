"""Explicit native Sign in with Apple token transport; never installed at startup.

An authorization-code POST is not retried: a lost response may have consumed it.
This provider has no research, quota, session-extension or account-delete powers.
"""
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from audit_chain_contract import loads_json_object_strict
from lite_account_identity import IdentityRejected
from lite_attest_state import StateUnavailable

TOKEN_URL = 'https://appleid.apple.com/auth/token'
REVOKE_URL = 'https://appleid.apple.com/auth/revoke'
MAX_RESPONSE = 16384


class GrantRejected(IdentityRejected): pass
class GrantUncertain(StateUnavailable): pass


def bounded_secret(value, maximum=4096):
    if (type(value) is not str or not 1 <= len(value) <= maximum
            or not value.isascii() or not value.isprintable()):
        raise GrantRejected('bounded Apple credential required')
    return value


class AppleClientSecret:
    """Caller supplies a protected P-256 key; never discovers credentials on disk."""
    def __init__(self, *, team, key_id, client_id, private_key, clock):
        if (not all(type(s) is str and re.fullmatch(r'[A-Z0-9]{10}', s) for s in (team, key_id))
                or not isinstance(private_key, ec.EllipticCurvePrivateKey)
                or not isinstance(private_key.curve, ec.SECP256R1) or not callable(clock)):
            raise ValueError('explicit Apple signing configuration required')
        self.team, self.key_id, self.client_id = team, key_id, bounded_secret(client_id, 128)
        self._key, self.clock = private_key, clock

    def __call__(self):
        now = self.clock()
        if type(now) is not int or now < 0: raise StateUnavailable('invalid Apple client clock')
        return jwt.encode(dict(iss=self.team, sub=self.client_id, aud='https://appleid.apple.com',
            iat=now, exp=now+300), self._key, algorithm='ES256', headers={'kid': self.key_id})


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise GrantUncertain('Apple credential redirect refused')


def post_apple_form(url, fields):
    """Fixed HTTPS, ordinary TLS trust, no proxies/redirects/retries/body logging."""
    if url not in (TOKEN_URL, REVOKE_URL): raise ValueError('fixed Apple endpoint required')
    body = urllib.parse.urlencode(fields).encode('ascii')
    if len(body) > 32768: raise ValueError('Apple form too large')
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()), _NoRedirect())
        request = urllib.request.Request(url, data=body, method='POST', headers={
            'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json',
            'Accept-Encoding': 'identity'})
        started = time.monotonic()
        try:
            response = opener.open(request, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            status = response.code
            if (status not in (200, 400) or response.geturl() != url
                    or response.headers.get('Content-Encoding', 'identity') != 'identity'):
                raise ValueError('response policy')
            if not (url == REVOKE_URL and status == 200) and response.headers.get_content_type() != 'application/json':
                raise ValueError('response type')
            length = response.headers.get('Content-Length')
            if length is not None and (not length.isascii() or not length.isdigit() or int(length) > MAX_RESPONSE):
                raise ValueError('response size')
            data = bytearray()
            while True:
                if time.monotonic()-started > 10: raise ValueError('response deadline')
                chunk = response.read1(min(4096, MAX_RESPONSE+1-len(data)))
                if not chunk: break
                data.extend(chunk)
                if len(data) > MAX_RESPONSE: raise ValueError('response size')
            if time.monotonic()-started > 10 or (length is not None and len(data) != int(length)):
                raise ValueError('response incomplete')
            return status, bytes(data)
    except Exception:
        # Neither Apple's error_description nor transport exception may expose a token.
        raise GrantUncertain('Apple token response unconfirmed; keep saved intent') from None


class AppleOAuthProvider:
    def __init__(self, *, client_id, client_secret, post=post_apple_form):
        self.client_id = bounded_secret(client_id, 128)
        if not callable(client_secret) or not callable(post): raise ValueError('explicit Apple provider required')
        self._client_secret, self.post = client_secret, post

    def _post(self, url, fields):
        try:
            status, body = self.post(url, dict(fields, client_id=self.client_id,
                client_secret=bounded_secret(self._client_secret(), 4096)))
            if type(body) is not bytes or len(body) > MAX_RESPONSE: raise ValueError()
            if status == 400:
                error = loads_json_object_strict(body.decode('utf-8'))
                if error.get('error') == 'invalid_grant': raise GrantRejected('Apple authorization no longer valid')
                raise ValueError()
            if type(status) is not int or status != 200: raise ValueError()
            return body
        except GrantRejected:
            raise GrantRejected('Apple authorization rejected') from None
        except Exception:
            raise GrantUncertain('Apple token response unconfirmed; keep saved intent') from None

    def exchange(self, code):
        raw = self._post(TOKEN_URL, dict(grant_type='authorization_code', code=bounded_secret(code)))
        try:
            value = loads_json_object_strict(raw.decode('utf-8'))
            required = {'access_token', 'refresh_token', 'id_token', 'token_type', 'expires_in'}
            if not required <= set(value) or value['token_type'] != 'Bearer': raise ValueError()
            if type(value['expires_in']) is not int or not 0 < value['expires_in'] <= 86400: raise ValueError()
            for field in ('access_token', 'refresh_token'): bounded_secret(value[field], 2048)
            bounded_secret(value['id_token'], 6000)
            return {k: value[k] for k in required}
        except Exception:
            raise GrantUncertain('Apple token response invalid; explicit sign-in recovery required') from None

    def revoke(self, refresh_token):
        body = self._post(REVOKE_URL, dict(token=bounded_secret(refresh_token, 2048), token_type_hint='refresh_token'))
        if body.strip(): raise GrantUncertain('Apple revocation response unconfirmed')
        # Apple documents 200 for both revoked and already-invalid tokens.
        return True
