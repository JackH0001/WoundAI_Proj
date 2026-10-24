"""Two independent trust checks; no MMH accounts, cookies or unsigned identity."""
import time
import jwt
from .contracts import CAPS, IAP_AUDIENCE, PORTAL_SA, Viewer, ReviewError, require

class IapVerifier:
    def __init__(self, public_http, roles):
        # An explicitly empty map is a closed bootstrap/containment configuration.
        # A valid Google signature never grants access without a listed subject.
        # Missing or malformed configuration still prevents startup.
        require(type(roles) is dict, 'review_roles_not_configured')
        require(all(type(k) is str and k.startswith('accounts.google.com:') and
                    type(v) is list and set(v)<=CAPS and 'lite.review.metadata' in v for k,v in roles.items()))
        self.http, self.roles = public_http, roles

    def __call__(self, token):
        try:
            require(type(token) is str and 0<len(token)<=16384)
            head=jwt.get_unverified_header(token)
            require(head.get('alg')=='ES256' and type(head.get('kid')) is str)
            response=self.http.get('https://www.gstatic.com/iap/verify/public_key',timeout=10,allow_redirects=False)
            require(response.status_code==200)
            key=response.json().get(head['kid']); require(type(key) is str)
            claims=jwt.decode(token,key,algorithms=['ES256'],audience=IAP_AUDIENCE,
                issuer='https://cloud.google.com/iap',options={'require':['iat','exp','sub','aud','iss']})
            require(type(claims['iat']) is int and type(claims['exp']) is int and
                    0<claims['exp']-claims['iat']<=660 and claims['sub'] in self.roles)
            return Viewer(claims['sub'],frozenset(self.roles[claims['sub']]))
        except Exception:
            raise ReviewError('review_sign_in_required',401) from None

class ReaderIdentity:
    def __init__(self, verify_google, audience, portal_subject):
        require(type(audience) is str and audience.startswith('https://woundai-lite-review-reader-') and audience.endswith('.run.app'))
        require(type(portal_subject) is str and portal_subject.isdigit())
        self.verify,self.audience,self.subject=verify_google,audience,portal_subject

    def __call__(self, token):
        try:
            require(type(token) is str and token.startswith('Bearer ') and len(token)<16384)
            claims=self.verify(token[7:],self.audience)
            require(claims.get('aud')==self.audience and claims.get('sub')==self.subject and
                claims.get('email')==PORTAL_SA and claims.get('email_verified') is True and
                claims.get('iss') in {'https://accounts.google.com','accounts.google.com'} and
                type(claims.get('exp')) is int and type(claims.get('iat')) is int and
                claims['iat']<=time.time()<claims['exp'] and claims['exp']-claims['iat']<=3600)
        except Exception:
            raise ReviewError('review_service_identity_required',401) from None
