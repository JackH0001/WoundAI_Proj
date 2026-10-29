"""Explicit Apple public-key provider. No credentials, discovery or startup calls.

Only Apple's fixed HTTPS endpoint is used. Cache bounds are engineering policy,
not an Apple freshness promise. Expired keys never become a fail-open fallback.
"""
import hashlib
import math
import ssl
import threading
import time
import urllib.request
from audit_chain_contract import loads_json_object_strict
from lite_account_identity import AppleIdentityVerifier, IdentityRejected, identity_header
from lite_attest_state import StateUnavailable

APPLE_KEYS_URL = 'https://appleid.apple.com/auth/keys'
MAX_KEYS_BYTES = 65536


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise StateUnavailable('Apple key redirect refused')


def fetch_apple_keys():
    """Public GET only; bounded reads, normal TLS verification, no redirects."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()), _NoRedirect())
    request = urllib.request.Request(APPLE_KEYS_URL, headers={'Accept':'application/json',
        'Accept-Encoding':'identity', 'User-Agent':'WoundAILite-IdentityKeys/1'}, method='GET')
    began = time.monotonic()
    with opener.open(request, timeout=5) as response:
        if (response.status != 200 or response.geturl() != APPLE_KEYS_URL
                or response.headers.get_content_type() != 'application/json'
                or response.headers.get('Content-Encoding','identity') != 'identity'):
            raise StateUnavailable('unexpected Apple key response')
        length = response.headers.get('Content-Length')
        if length is not None and (not length.isascii() or not length.isdigit() or not 1 <= int(length) <= MAX_KEYS_BYTES):
            raise StateUnavailable('bounded Apple key response required')
        body = bytearray()
        while True:
            if time.monotonic()-began > 10: raise StateUnavailable('Apple key deadline')
            part = response.read1(min(4096, MAX_KEYS_BYTES+1-len(body)))
            if not part: break
            body.extend(part)
            if len(body)>MAX_KEYS_BYTES: raise StateUnavailable('Apple key response too large')
        if time.monotonic()-began > 10 or not body or (length is not None and int(length)!=len(body)):
            raise StateUnavailable('Apple key response incomplete or late')
        return bytes(body)


class RefreshingAppleIdentityVerifier(AppleIdentityVerifier):
    """One in-process refresh at a time; unknown kid cannot cause per-call fetches.

    Workers/replicas have separate caches. Deployment must also bound replica
    count and ingress rates; this class is not a distributed rate limiter.
    """
    def __init__(self, *, audience, fetch=fetch_apple_keys, clock=lambda:int(time.time()),
                 monotonic=time.monotonic, ttl=900, retry_seconds=30):
        if (type(audience) is not str or not 1<=len(audience)<=128 or not audience.isascii()
                or not audience.isprintable() or not callable(fetch) or not callable(clock)
                or not callable(monotonic) or type(ttl) is not int or not 60<=ttl<=3600
                or type(retry_seconds) is not int or not 30<=retry_seconds<=300):
            raise ValueError('explicit bounded Apple key provider required')
        self.audience,self.clock,self.fetch,self.monotonic=audience,clock,fetch,monotonic
        self.ttl,self.retry_seconds=ttl,retry_seconds
        self._lock=threading.Lock();self._snapshot=None;self._deadline=0
        self._retry_at=0;self._last_wall=0;self._last_mono=0;self._last_error=False
        self._fetches=0;self._failures=0;self._body_sha256=None

    def _time(self):
        wall,mono=self.clock(),self.monotonic()
        if (type(wall) is not int or wall<0 or type(mono) not in (int,float)
                or not math.isfinite(mono) or mono<0 or wall<self._last_wall or mono<self._last_mono):
            raise StateUnavailable('Apple key clock regressed')
        self._last_wall,self._last_mono=wall,mono
        return wall,mono

    def _current(self, kid=None, force=False):
        if not self._lock.acquire(timeout=12): raise StateUnavailable('Apple key refresh busy')
        try:
            wall,mono=self._time();s=self._snapshot
            fresh=s is not None and s.fetched_at<=wall<s.expires_at and mono<self._deadline
            if not force and fresh and (kid is None or kid in s.keys): return s
            if mono<self._retry_at:
                raise StateUnavailable('Apple key refresh cooling down; preserve sign-in')
            self._retry_at=mono+self.retry_seconds;self._fetches+=1
            try:
                body=self.fetch()
                if type(body) is not bytes or not 1<=len(body)<=MAX_KEYS_BYTES: raise ValueError('key response size')
                obj=loads_json_object_strict(body.decode('utf-8'))
                ended,end_mono=self._time()
                if ended-wall>15 or end_mono-mono>15: raise ValueError('key fetch deadline')
                snapshot=AppleIdentityVerifier(audience=self.audience,jwks=obj,
                    fetched_at=wall,expires_at=wall+self.ttl,clock=self.clock)
                if ended>=snapshot.expires_at: raise ValueError('key expired while fetching')
            except Exception as exc:
                self._failures+=1;self._last_error=True
                raise StateUnavailable('fresh Apple keys unavailable; preserve local state') from exc
            self._snapshot=snapshot;self._deadline=mono+self.ttl;self._last_error=False
            self._body_sha256=hashlib.sha256(body).hexdigest()
            if kid is not None and kid not in snapshot.keys: raise IdentityRejected('Apple signing key unknown')
            return snapshot
        finally:
            self._lock.release()

    def refresh(self):
        """Explicit readiness/background probe, never extends an old snapshot."""
        self._current(force=True)
        return self.health()

    def verify(self, signed, nonce):
        header=identity_header(signed)
        snapshot=self._current(header['kid']);result=snapshot.verify(signed,nonce)
        self._still_current(snapshot)
        return result

    def event(self, signed):
        header=identity_header(signed)
        snapshot=self._current(header['kid']);result=snapshot.event(signed)
        self._still_current(snapshot)
        return result

    def verify_refresh(self, signed, nonce, original_issued):
        header=identity_header(signed)
        snapshot=self._current(header['kid']);result=snapshot.verify_refresh(signed,nonce,original_issued)
        self._still_current(snapshot)
        return result

    def _still_current(self, snapshot):
        with self._lock:
            wall,mono=self._time()
            if snapshot is not self._snapshot or not snapshot.fetched_at<=wall<snapshot.expires_at or mono>=self._deadline:
                raise StateUnavailable('Apple key snapshot changed during verification')

    def health(self):
        with self._lock:
            wall,mono=self._time();s=self._snapshot
            return dict(v=1,ready=s is not None and s.fetched_at<=wall<s.expires_at and mono<self._deadline,
                fetched_at=s.fetched_at if s else None,expires_at=s.expires_at if s else None,
                key_count=len(s.keys) if s else 0,body_sha256=self._body_sha256,
                fetches=self._fetches,failures=self._failures,last_refresh_failed=self._last_error)
