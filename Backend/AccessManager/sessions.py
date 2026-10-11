"""Server-side, short-lived administration sessions. No password persistence.

Single-process runtime only: restart/scale-out loses the session and requires
login again. A deployment must explicitly accept that topology or inject a
shared store with the same atomic rotation/revocation contract.
"""
from dataclasses import dataclass, field
import hashlib
import hmac
import re
import secrets
import threading
import time

from flask import jsonify, request

from .accounts import AccountAdapter
from .authentication import BoundSession, LiveAdminAuthenticator, AuthenticationError
from .web import ORG

COOKIE = "__Host-mmh-management"
LOGIN_URL = "https://candidate---woundai-backend-mmhps20261007-z4kgfkob4a-de.a.run.app/api/auth/login"


@dataclass
class Session:
    subject: str
    expires: float
    nonce: str
    token: str = field(default="", repr=False)
    authenticating: bool = False


class SessionStore:
    def __init__(self, *, clock=time.time, ttl=900, capacity=100):
        if not 60 <= ttl <= 900 or not 1 <= capacity <= 1000:
            raise ValueError("invalid_session_limits")
        self.clock, self.ttl, self.capacity = clock, ttl, capacity
        self.lock = threading.RLock()
        self.rows = {}
        self.attempts = {}

    def _prune(self):
        now = self.clock()
        self.rows = {key:value for key,value in self.rows.items() if value.expires > now}
        self.attempts = {subject:[stamp for stamp in stamps if stamp>now-60]
                         for subject,stamps in self.attempts.items() if any(stamp>now-60 for stamp in stamps)}

    def get(self, sid, subject):
        with self.lock:
            self._prune()
            row = self.rows.get(sid)
            if row is None or row.subject != subject:
                raise AuthenticationError("session_expired")
            return Session(row.subject,row.expires,row.nonce,row.token,row.authenticating)

    def create(self, subject, token=""):
        with self.lock:
            self._prune()
            if len(self.rows) >= self.capacity:
                raise AuthenticationError("session_capacity")
            sid = secrets.token_urlsafe(32)
            self.rows[sid] = Session(subject,self.clock()+self.ttl,secrets.token_urlsafe(24),token)
            return sid

    def begin_login(self, sid, subject):
        with self.lock:
            row = self.get(sid,subject)
            if row.authenticating or row.token:
                raise AuthenticationError("login_in_progress_or_already_authenticated")
            stamps=self.attempts.setdefault(subject,[])
            if len(stamps)>=5:raise AuthenticationError("login_rate_limited")
            stamps.append(self.clock())
            self.rows[sid].authenticating = True

    def cancel_login(self, sid):
        with self.lock:
            if sid in self.rows:self.rows[sid].authenticating=False

    def rotate(self, sid, subject, token):
        with self.lock:
            old = self.get(sid,subject)
            if not old.authenticating:raise AuthenticationError("login_cancelled")
            # Removing the old session first frees capacity; delayed login
            # results cannot recreate a session already revoked by logout.
            del self.rows[sid]
            return self.create(subject,token)

    def revoke(self, sid):
        with self.lock:self.rows.pop(sid,None)


class TokenTransport:
    def __init__(self, http, token):self.http,self.token=http,token
    def get(self,url,**kwargs):return self.http.get(url,headers={"Authorization":"Bearer "+self.token},**kwargs)
    def post(self,url,**kwargs):return self.http.post(url,headers={"Authorization":"Bearer "+self.token},**kwargs)


class SessionController:
    def __init__(self, *, origin, verify_iap, backend_http, admin_emails, csrf_secret, store=None):
        if not isinstance(csrf_secret,bytes) or len(csrf_secret)<32:
            raise ValueError("csrf_secret_required")
        if not origin.startswith("https://") or origin.endswith("/"):
            raise ValueError("https_origin_required")
        self.origin,self.verify_iap,self.http=origin,verify_iap,backend_http
        self.allowed=frozenset(e.lower() for e in admin_emails)
        self.secret,self.store=csrf_secret,store or SessionStore()
        self.authenticate=LiveAdminAuthenticator(verify_iap=self.google,resolve_session=self.resolve,
                                                  backend_http=backend_http,admin_emails=self.allowed)

    def google(self, assertion):
        claims=self.verify_iap(assertion)
        if claims["email"].lower() not in self.allowed:raise AuthenticationError("administrator_access_denied")
        return claims

    def claims(self, req):return self.google(req.headers.get("X-Goog-IAP-JWT-Assertion"))

    def resolve(self, req):
        subject=self.claims(req)["sub"]
        row=self.store.get(req.cookies.get(COOKIE),subject)
        if not row.token:raise AuthenticationError("app_login_required")
        return BoundSession(subject,row.token,row.nonce)

    def account_adapter(self, req, principal):
        bound=self.resolve(req)
        if bound.google_subject != principal.google_subject:raise AuthenticationError("session_mismatch")
        return AccountAdapter(TokenTransport(self.http,bound.app_token))

    def csrf(self, sid, subject):
        return hmac.new(self.secret,(self.origin+"\0"+sid+"\0"+subject).encode(),hashlib.sha256).hexdigest()

    def check_post(self):
        claims=self.claims(request);sid=request.cookies.get(COOKIE)
        self.store.get(sid,claims["sub"])
        supplied=request.headers.get("X-MMH-Session-CSRF","")
        if (request.headers.get("Origin") != self.origin or not request.is_json
                or not supplied.isascii() or not hmac.compare_digest(supplied,self.csrf(sid,claims["sub"]))):
            raise AuthenticationError("session_csrf_rejected")
        return sid,claims

    def cookie(self, response, sid):
        response.set_cookie(COOKIE,sid,secure=True,httponly=True,samesite="Strict",path="/",max_age=self.store.ttl)
        return response

    def mount(self, app):
        @app.get("/api/session")
        def session_status():
            try:
                claims=self.claims(request);sid=request.cookies.get(COOKIE)
                try:row=self.store.get(sid,claims["sub"])
                except AuthenticationError:
                    sid=self.store.create(claims["sub"]);row=self.store.get(sid,claims["sub"])
                principal=None
                if row.token:
                    try:principal=self.authenticate(request)
                    except AuthenticationError:
                        self.store.revoke(sid);sid=self.store.create(claims["sub"])
                result=jsonify(google_verified=True,google_email=claims["email"],
                               app_authenticated=principal is not None,
                               app_identity=principal.app_identity if principal else None,
                               session_csrf=self.csrf(sid,claims["sub"]),
                               ttl_seconds=max(0,int(self.store.get(sid,claims["sub"]).expires-self.store.clock())))
                return self.cookie(result,sid)
            except Exception:
                return jsonify(error="administrator_verification_failed"),401

        @app.post("/api/session/login")
        def login():
            sid=None;begun=False
            try:
                sid,claims=self.check_post()
                data=request.get_json(silent=True)
                if (not isinstance(data,dict) or set(data)!={"username","password"}
                        or not isinstance(data["username"],str) or not isinstance(data["password"],str)
                        or not 1<=len(data["password"])<=1024):
                    return jsonify(error="invalid_login_fields"),400
                user=data["username"].removeprefix(ORG+":")
                if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,30}",user):
                    return jsonify(error="invalid_login_fields"),400
                self.store.begin_login(sid,claims["sub"])
                begun=True
                response=self.http.post(LOGIN_URL,json={"username":ORG+":"+user,"password":data["password"]},
                                        timeout=20,allow_redirects=False)
                if response.status_code!=200:raise AuthenticationError("app_login_failed")
                token=response.json().get("access_token")
                if not isinstance(token,str) or not token or len(token)>16384:raise AuthenticationError("app_login_failed")
                provisional=BoundSession(claims["sub"],token,"pending")
                verify=LiveAdminAuthenticator(verify_iap=self.google,resolve_session=lambda req:provisional,
                                              backend_http=self.http,admin_emails=self.allowed)
                principal=verify(request)
                if principal.app_identity!=ORG+":"+user:raise AuthenticationError("identity_mismatch")
                new_sid=self.store.rotate(sid,claims["sub"],token)
                return self.cookie(jsonify(status="authenticated",app_identity=principal.app_identity),new_sid)
            except Exception:
                return jsonify(error="app_login_failed"),401
            finally:
                if sid and begun:self.store.cancel_login(sid)

        @app.post("/api/session/logout")
        def logout():
            try:
                sid,claims=self.check_post();self.store.revoke(sid)
                response=jsonify(status="signed_out",google_session_ended=False)
                response.delete_cookie(COOKIE,secure=True,httponly=True,samesite="Strict",path="/")
                return response
            except Exception:
                return jsonify(error="session_csrf_rejected"),403
