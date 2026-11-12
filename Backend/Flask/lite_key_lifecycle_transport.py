"""Explicit short-lived bearer transport for private KMS lifecycle operations.

No ADC, token refresh, redirects, environment proxy/netrc, hooks or HTTP retries.
Not a credential authority or a replacement for dispatch CAS, IAM and drain.
"""
import math
import re
import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from lite_attest_state import StateUnavailable


class SingleAttemptKMSTransport:
    def __init__(self,*,version,access_token,expires_at,clock=time.time):
        pattern=r'projects/[a-z0-9-]+/locations/[a-z0-9-]+/keyRings/[A-Za-z0-9_-]+/cryptoKeys/[A-Za-z0-9_-]+/cryptoKeyVersions/[1-9][0-9]{0,9}'
        if (type(version) is not str or not re.fullmatch(pattern,version)
                or type(access_token) is not str or not re.fullmatch(r'[A-Za-z0-9._~+/=-]{16,8192}',access_token)
                or type(expires_at) not in (int,float) or not math.isfinite(expires_at)
                or not callable(clock)):
            raise ValueError('explicit version, bearer and expiry required')
        self._version,self._token,self._expires,self._clock=version,access_token,expires_at,clock
        self._base='https://cloudkms.googleapis.com/v1/'+version
        self._session=requests.Session();self._session.trust_env=False
        self._session.adapters.clear()
        self._session.mount('https://',HTTPAdapter(max_retries=Retry(total=0,connect=0,read=0,redirect=0,status=0,other=0)))
        self._closed=False

    def _check(self):
        n=self._clock()
        if (self._closed or type(n) not in (int,float) or not math.isfinite(n)
                or not 0<=n<2**53 or not 20<self._expires-n<=3600):
            raise StateUnavailable('lifecycle bearer unavailable; acquire separately before a new reviewed operation')
        s=self._session
        if (type(s) is not requests.Session or s.trust_env is not False or s.auth is not None
                or s.verify is not True or s.cert is not None or s.proxies or s.cookies
                or s.params or s.hooks!={'response':[]} or set(s.adapters)!={'https://'}):
            raise StateUnavailable('lifecycle transport policy differs')
        a=s.adapters['https://'];r=getattr(a,'max_retries',None)
        if (type(a) is not HTTPAdapter or type(r) is not Retry
                or any(type(getattr(r,k,None)) is not int or getattr(r,k)!=0
                       for k in ('total','connect','read','redirect','status','other'))):
            raise StateUnavailable('lifecycle transport must never replay an HTTP request')

    def request(self,method,url,*,params,json,timeout,allow_redirects,stream):
        self._check()
        ok=(method=='GET' and url==self._base and params=={} and json is None
            or method=='PATCH' and url==self._base and params=={'updateMask':'state'}
                and json=={'name':self._version,'state':'DISABLED'}
            or method=='POST' and url in (self._base+':destroy',self._base+':restore') and params=={} and json=={})
        if not ok or timeout!=(5,10) or allow_redirects is not False or stream is not True:
            raise StateUnavailable('only pinned lifecycle HTTP operations are allowed')
        try:
            # Plain requests.Session: there is no credential refresh recursion.
            # TLS verification remains enabled; an unsuccessful response is final.
            return self._session.request(method,url,params=params,json=json,timeout=(5,10),
                allow_redirects=False,stream=True,verify=True,
                headers={'Authorization':'Bearer '+self._token,'Accept':'application/json'})
        except Exception:
            raise StateUnavailable('lifecycle HTTP outcome unavailable; do not repeat mutation') from None

    def close(self):
        self._closed=True;self._token=None;self._session.close()

