"""Bounded provider payload transport for the private disposition service only."""
import io
import json
from urllib.parse import urlencode, urlsplit
import requests
from requests.structures import CaseInsensitiveDict
from urllib3.response import HTTPResponse
from lite_attest_state import StateUnavailable
from lite_apple_oauth import REVOKE_URL, MAX_RESPONSE
from lite_provider_budget import Allocation, BudgetUnavailable

HOSTS = dict(gcs={'storage.googleapis.com'}, kms={'cloudkms.googleapis.com'}, apple={'appleid.apple.com'})


class BudgetedSession:
    """One call is one reserved attempt; no redirect, compression or retry.

    The underlying Google AuthorizedSession must disable credential-refresh
    request replay. Metadata credential acquisition is control-plane traffic.
    Bodies are buffered within a reserved bound before exposing them to SDKs.
    Deadlines are checked at boundaries; a supervisor is still needed to stop
    an in-flight OS/network call. This is not a hard wall-clock guarantee.
    """
    def __init__(self, session, allocation, *, provider, response_limit=65536, authorized=False):
        if (not isinstance(allocation, Allocation) or provider not in HOSTS
                or not callable(getattr(session,'request',None)) or type(authorized) is not bool
                or type(response_limit) is not int or not 1 <= response_limit <= 1024**2):
            raise ValueError('explicit provider session and allocation required')
        self.session, self.allocation, self.provider = session, allocation, provider
        self.response_limit, self.authorized = response_limit, authorized
        self.is_mtls = False

    def request(self, method, url, **kw):
        u = urlsplit(url)
        if (u.scheme != 'https' or u.hostname not in HOSTS[self.provider] or u.username is not None
                or u.password is not None or u.port not in (None,443) or u.fragment
                or len(url) > 16384):
            raise BudgetUnavailable('provider endpoint outside approved transport')
        if set(kw)-{'headers','data','json','params','timeout','stream','allow_redirects'}:
            raise BudgetUnavailable('unsupported provider transport options')
        if kw.get('json') is not None:
            if kw.get('data') is not None: raise BudgetUnavailable('ambiguous request body')
            body = json.dumps(kw.pop('json'),separators=(',',':'),allow_nan=False).encode()
            kw['data'] = body
            headers = CaseInsensitiveDict(kw.get('headers') or {})
            headers.setdefault('Content-Type','application/json');kw['headers'] = headers
        else:
            kw.pop('json',None)
            body = kw.get('data')
            if isinstance(body,str): body = body.encode(); kw['data'] = body
            if body is not None and type(body) is not bytes:
                raise BudgetUnavailable('streaming request body is not bounded')
        token = self.allocation.begin(provider=self.provider,method=method,
            send_bytes=len(body or b''),response_limit=self.response_limit)
        headers = CaseInsensitiveDict(kw.get('headers') or {})
        headers['Accept-Encoding'] = 'identity'
        kw.update(headers=headers,stream=True,allow_redirects=False)
        remaining = token.timeout(5)
        supplied = kw.get('timeout',5)
        values = supplied if isinstance(supplied,tuple) else (supplied,supplied)
        if len(values) != 2 or any(type(v) not in (int,float) or not 0 < v < float('inf') for v in values):
            raise BudgetUnavailable('finite provider timeout required')
        kw['timeout'] = tuple(min(v,remaining) for v in values)
        if self.authorized: kw['max_allowed_time'] = remaining
        response = None
        try:
            response = self.session.request(method,url,**kw)
            if response.url != url and urlsplit(response.url)[:2] != u[:2]:
                raise BudgetUnavailable('provider response origin differs')
            if 300 <= response.status_code < 400 or response.headers.get('Content-Encoding','identity') != 'identity':
                raise BudgetUnavailable('provider redirect or compression refused')
            length = response.headers.get('Content-Length')
            if length is not None:
                if not length.isascii() or not length.isdigit() or int(length) > self.response_limit:
                    raise BudgetUnavailable('provider response exceeds reservation')
                length = int(length)
            data = bytearray()
            while length is None or len(data) < length:
                n = token.read_size()
                if length is not None: n = min(n,length-len(data))
                chunk = response.raw.read(n,decode_content=False)
                if not chunk: break
                token.received_bytes(len(chunk)); data.extend(chunk)
            if length is not None and len(data) != length:
                raise BudgetUnavailable('provider response incomplete')
            token.finish()
            response.close()
            # Preserve both normal Response content and the SDK's raw/single-shot
            # interfaces. Neither can issue more network reads after this point.
            response._content = bytes(data); response._content_consumed = True
            response.raw = HTTPResponse(body=io.BytesIO(data),headers=response.headers,
                preload_content=False,decode_content=False)
            return response
        except Exception:
            if response is not None: response.close()
            raise BudgetUnavailable('provider attempt unconfirmed; keep original intent') from None

    def close(self): self.session.close()


def apple_revoker(allocation):
    session = requests.Session(); session.trust_env = False
    transport = BudgetedSession(session,allocation,provider='apple',response_limit=MAX_RESPONSE)
    def post(url, fields):
        if url != REVOKE_URL: raise StateUnavailable('private worker can only revoke Apple grants')
        response = transport.request('POST',url,data=urlencode(fields).encode('ascii'),
            headers={'Content-Type':'application/x-www-form-urlencoded','Accept':'application/json'},timeout=5)
        if response.status_code not in (200,400): raise StateUnavailable('Apple revocation unconfirmed')
        if response.status_code == 400 and response.headers.get('Content-Type','').split(';')[0] != 'application/json':
            raise StateUnavailable('Apple revocation response type invalid')
        return response.status_code, response.content
    return post
