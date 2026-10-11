"""Synthetic session lifecycle tests; never contact Google or real accounts."""
import time
import unittest
from unittest.mock import Mock

from .sessions import SessionController, SessionStore, COOKIE, LOGIN_URL
from .authentication import AuthenticationError, SESSION_URL
from .web import create_app, ORG
from .test_iap_policy import MemoryAdapter, MemoryAudit
from .test_accounts import MemoryAccountsHttp

ORIGIN="https://manager.example.test"


class Backend(MemoryAccountsHttp):
    def __init__(self):super().__init__();self.role="admin";self.login_calls=0
    def get(self,url,**kwargs):
        if url==SESSION_URL:
            return Mock(status_code=200,json=lambda:dict(identity=ORG+":admin",role=self.role,org=ORG,
                         session_id="synthetic-jti",expires_at=int(time.time())+600))
        return super().get(url,**kwargs)
    def post(self,url,**kwargs):
        if url==LOGIN_URL:
            self.login_calls+=1
            valid=kwargs["json"]=={"username":ORG+":admin","password":"synthetic-test"}
            return Mock(status_code=200 if valid else 401,json=lambda:{"access_token":"synthetic-backend-bearer"})
        return super().post(url,**kwargs)


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.now=[1000.0];self.store=SessionStore(clock=lambda:self.now[0])
        self.backend=Backend()
        self.claims=dict(email="admin@example.test",sub="accounts.google.com:test")
        def verify(assertion):
            if assertion!="synthetic-signed-header":raise AuthenticationError("invalid")
            return self.claims
        self.controller=SessionController(origin=ORIGIN,verify_iap=verify,backend_http=self.backend,
            admin_emails=["admin@example.test"],csrf_secret=b"x"*32,store=self.store)
        self.app=create_app(origin=ORIGIN,policy_adapter=MemoryAdapter(),audit=MemoryAudit(),
            list_accounts=self.backend.get,admin_emails=["admin@example.test"],session_controller=self.controller)
        self.client=self.app.test_client()
        self.headers={"X-Goog-IAP-JWT-Assertion":"synthetic-signed-header"}

    def get(self,path="/api/session"):
        return self.client.get(path,base_url=ORIGIN,headers=self.headers)

    def post(self,path,data,csrf=None):
        headers=dict(self.headers,Origin=ORIGIN)
        if csrf:headers["X-MMH-Session-CSRF"]=csrf
        return self.client.post(path,base_url=ORIGIN,headers=headers,json=data)

    def login(self):
        state=self.get().json
        return self.post("/api/session/login",{"username":"admin","password":"synthetic-test"},state["session_csrf"])

    def test_prelogin_state_and_cookie_no_tokens(self):
        response=self.get();data=response.json
        self.assertTrue(data["google_verified"]);self.assertFalse(data["app_authenticated"])
        cookie=response.headers["Set-Cookie"]
        for attr in (COOKIE,"Secure","HttpOnly","SameSite=Strict","Path=/"):self.assertIn(attr,cookie)
        self.assertNotIn("Domain=",cookie)
        self.assertNotIn("token",str(data));self.assertEqual(response.headers["Cache-Control"],"no-store")

    def test_login_rotates_cookie_and_restores_status(self):
        self.get();before=self.client.get_cookie(COOKIE,domain="manager.example.test").value
        result=self.login();self.assertEqual(result.status_code,200)
        after=self.client.get_cookie(COOKIE,domain="manager.example.test").value
        self.assertNotEqual(before,after);self.assertNotIn(before,self.store.rows)
        self.assertNotIn("synthetic-backend-bearer",str(result.json))
        self.assertTrue(self.get().json["app_authenticated"])
        self.assertEqual(self.get('/api/members').status_code,200)

    def test_logout_removes_server_session_not_google(self):
        self.login();state=self.get().json
        sid=self.client.get_cookie(COOKIE,domain="manager.example.test").value
        result=self.post('/api/session/logout',{},state["session_csrf"])
        self.assertEqual(result.status_code,200);self.assertFalse(result.json["google_session_ended"])
        self.assertNotIn(sid,self.store.rows)
        self.assertFalse(self.get().json["app_authenticated"])

    def test_old_csrf_cannot_logout_rotated_session(self):
        first=self.get().json["session_csrf"]
        self.assertEqual(self.login().status_code,200)
        self.assertEqual(self.post('/api/session/logout',{},first).status_code,403)
        self.assertTrue(self.get().json["app_authenticated"])

    def test_expired_session_cannot_restore_login(self):
        self.login();self.now[0]+=901
        self.assertFalse(self.get().json["app_authenticated"])
        self.assertEqual(self.get('/api/members').status_code,401)

    def test_restart_loses_login_and_clear_status(self):
        self.login();self.store.rows.clear()
        self.assertFalse(self.get().json["app_authenticated"])

    def test_role_downgrade_revokes_local_session(self):
        self.login();self.backend.role="nurse"
        self.assertFalse(self.get().json["app_authenticated"])
        self.assertEqual(self.get('/api/members').status_code,401)

    def test_invalid_google_cannot_read_or_login(self):
        self.headers={}
        self.assertEqual(self.get().status_code,401)
        self.assertEqual(self.post('/api/session/login',{},'anything').status_code,401)
        self.assertEqual(self.backend.login_calls,0)

    def test_missing_csrf_and_bad_origin_no_backend_login(self):
        state=self.get().json
        self.assertEqual(self.post('/api/session/login',{}).status_code,401)
        response=self.client.post('/api/session/login',base_url=ORIGIN,
            headers=dict(self.headers,Origin="https://other.example.test",**{"X-MMH-Session-CSRF":state["session_csrf"]}),json={})
        self.assertEqual(response.status_code,401);self.assertEqual(self.backend.login_calls,0)

    def test_wrong_credentials_not_stored_and_retry_allowed(self):
        state=self.get().json
        result=self.post('/api/session/login',dict(username="admin",password="wrong"),state["session_csrf"])
        self.assertEqual(result.status_code,401)
        self.assertTrue(all(not value.token for value in self.store.rows.values()))
        self.assertEqual(self.login().status_code,200)

    def test_nonadmin_valid_credentials_refused(self):
        self.backend.role="physician"
        self.assertEqual(self.login().status_code,401)
        self.assertFalse(self.get().json["app_authenticated"])

    def test_subject_switch_never_reuses_app_session(self):
        self.login();self.claims["sub"]="accounts.google.com:other"
        self.assertFalse(self.get().json["app_authenticated"])

    def test_delayed_login_cannot_resurrect_revoked_session(self):
        self.get();sid=self.client.get_cookie(COOKIE,domain="manager.example.test").value
        original=self.backend.post
        def revoked(*args,**kwargs):
            result=original(*args,**kwargs);self.store.revoke(sid);return result
        self.backend.post=revoked
        self.assertEqual(self.login().status_code,401)
        self.assertTrue(all(not row.token for row in self.store.rows.values()))

    def test_duplicate_login_does_not_clear_first_inflight_flag(self):
        state=self.get().json;sid=self.client.get_cookie(COOKIE,domain="manager.example.test").value
        self.store.begin_login(sid,self.claims['sub'])
        self.assertEqual(self.post('/api/session/login',dict(username="admin",password="synthetic-test"),state['session_csrf']).status_code,401)
        self.assertTrue(self.store.rows[sid].authenticating)
        self.assertEqual(self.backend.login_calls,0)

    def test_login_limit_is_subject_bound_across_cookies(self):
        for i in range(6):
            state=self.get().json
            self.post('/api/session/login',dict(username="admin",password="wrong"),state['session_csrf'])
            self.client.delete_cookie(COOKIE,domain="manager.example.test")
        self.assertEqual(self.backend.login_calls,5)

    def test_store_capacity_expiry_and_token_repr(self):
        store=SessionStore(clock=lambda:self.now[0],capacity=1)
        sid=store.create("sub","never-display")
        self.assertNotIn("never-display",repr(store.get(sid,"sub")))
        with self.assertRaises(AuthenticationError):store.create("other")
        self.now[0]+=901;self.assertTrue(store.create("other"))


if __name__=="__main__":unittest.main()
