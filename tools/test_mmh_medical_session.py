"""Medical session reuse contract: signed JWTs and isolated synthetic account state."""
from datetime import timedelta
from pathlib import Path
import secrets
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"Backend"/"Flask"))
from flask import Flask
from flask_jwt_extended import JWTManager, create_access_token, decode_token
import auth_users
import api_users
import institution_context as context
from account_versions import revision
from store import LocalStore

ORG = "mmhps20261007"

class MedicalSessionTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.store=LocalStore(tmp.name)
        for target in ("institution_context.BOUND_ORG", "auth_users.BOUND_ORG"):
            p=patch(target, ORG);p.start();self.addCleanup(p.stop)
        p=patch.object(auth_users,"_store",return_value=self.store);p.start();self.addCleanup(p.stop)
        auth_users.upsert_user(ORG,"dr01","physician",secrets.token_urlsafe(24))
        self.row=auth_users.get_user(ORG,"dr01")
        self.app=Flask(__name__);self.app.config["JWT_SECRET_KEY"]=secrets.token_urlsafe(48)
        JWTManager(self.app)  # The route must also verify without the global hook.
        self.app.register_blueprint(api_users.users_bp);self.client=self.app.test_client()

    def token(self, row=None, **changes):
        row=row or self.row
        if "revision" in row: row=auth_users.get_user(ORG,"dr01")
        claims=context.account_session_claims(row, ORG, "dr01");claims.update(changes)
        with self.app.app_context():
            return create_access_token(identity=ORG+":dr01",additional_claims=claims)

    def get(self, token=None, path="/api/v1/auth/session"):
        return self.client.get(path,headers={"Authorization":"Bearer "+(token or self.token())})

    def test_physician_self_only_minimal_response_no_write(self):
        before=list(self.store.read_lines_fresh("users.jsonl"))
        with patch.object(api_users.fw,"audit",side_effect=AssertionError("unexpected write")), \
             patch.object(api_users.fw,"audit_intent",side_effect=AssertionError("unexpected write")):
            result=self.get()
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.headers["Cache-Control"],"no-store")
        self.assertEqual(set(result.json),{"identity","org","user","role","role_zh","display_name","perms","expires_at","session_contract"})
        self.assertEqual(result.json["identity"],ORG+":dr01")
        self.assertIn("gt.verify",result.json["perms"])
        self.assertEqual(before,list(self.store.read_lines_fresh("users.jsonl")))

    def test_all_medical_roles_keep_their_own_permissions(self):
        for role in ("physician","nurse","assistant","engineer","admin"):
            row=auth_users.upsert_user(ORG,"dr01",role)
            result=self.get(self.token(row))
            self.assertEqual(result.status_code,200)
            self.assertEqual(result.json["perms"],sorted(k for k in auth_users.PERMS if auth_users.can(role,k)))

    def test_physician_still_cannot_use_manager_or_list_accounts(self):
        self.assertEqual(self.get(path="/api/v1/users/session").status_code,403)
        self.assertEqual(self.get(path="/api/v1/users").status_code,403)

    def test_disabled_and_reenabled_old_token_rejected(self):
        old=self.token()
        auth_users.upsert_user(ORG,"dr01","physician",disabled=True)
        self.assertEqual(self.get(old).status_code,401)
        auth_users.upsert_user(ORG,"dr01","physician",disabled=False)
        self.assertEqual(self.get(old).status_code,401)

    def test_role_changed_and_restored_old_token_rejected(self):
        old=self.token()
        auth_users.upsert_user(ORG,"dr01","nurse")
        self.assertEqual(self.get(old).status_code,401)
        row=auth_users.upsert_user(ORG,"dr01","physician")
        self.assertEqual(self.get(old).status_code,401)
        self.assertEqual(self.get(self.token(row)).status_code,200)

    def test_password_reset_invalidates_revision_bound_token(self):
        old=self.token()
        auth_users.upsert_user(ORG,"dr01","physician",secrets.token_urlsafe(24))
        self.assertEqual(self.get(old).status_code,401)

    def test_legacy_token_not_eligible_for_session_reuse(self):
        with self.app.app_context():
            old=create_access_token(identity=ORG+":dr01",additional_claims={"org":ORG,"user":"dr01","role":"physician"})
        self.assertEqual(self.get(old).status_code,401)

    def test_wrong_or_missing_revision_and_otc_rejected(self):
        for changes in ({"account_revision":None},{"account_revision":"0"*64},{"account_revision":123},{"typ":"otc"}):
            self.assertEqual(self.get(self.token(**changes)).status_code,401)

    def test_foreign_org_is_not_looked_up(self):
        token=self.token(org="other")
        with patch.object(auth_users,"get_user",side_effect=AssertionError("foreign lookup")):
            self.assertEqual(self.get(token).status_code,401)

    def test_subject_mismatch_rejected(self):
        self.assertEqual(self.get(self.token(sub=ORG+":other")).status_code,401)

    def test_lite_role_not_given_medical_session(self):
        row=auth_users.upsert_user(ORG,"dr01","lite")
        self.assertEqual(self.get(self.token(row)).status_code,403)

    def test_store_unavailable_is_safe_failure(self):
        token=self.token()
        with patch.object(auth_users,"get_user",side_effect=OSError("private detail")):
            result=self.get(token)
        self.assertEqual(result.status_code,503)
        self.assertEqual(result.json,{"error":"session_verification_unavailable"})
        self.assertEqual(result.headers["Cache-Control"],"no-store")

    def test_expired_and_unsigned_credentials_rejected(self):
        with self.app.app_context():
            old=create_access_token(identity=ORG+":dr01",additional_claims=context.account_session_claims(self.row,ORG,"dr01"),expires_delta=timedelta(seconds=-1))
        self.assertEqual(self.get(old).status_code,401)
        self.assertEqual(self.client.get("/api/v1/auth/session").status_code,401)

    def test_jwt_rejection_responses_are_not_cacheable(self):
        for headers in ({}, {"Authorization": "Bearer not-a-token"}):
            result = self.client.get("/api/v1/auth/session", headers=headers)
            self.assertNotEqual(result.status_code, 200)
            self.assertEqual(result.headers["Cache-Control"], "no-store")
        with self.app.app_context():
            token = create_access_token(identity=ORG+":dr01",
                additional_claims=context.account_session_claims(self.row, ORG, "dr01"),
                expires_delta=timedelta(seconds=-1))
        result = self.get(token)
        self.assertEqual(result.status_code, 401)
        self.assertEqual(result.headers["Cache-Control"], "no-store")

    def test_legacy_service_not_enabled_and_claim_shape_unchanged(self):
        token=self.token()
        with patch.object(context,"BOUND_ORG",None):
            self.assertEqual(self.get(token).status_code,503)
            self.assertEqual(set(context.account_session_claims(self.row,ORG,"dr01")),{"role","org","user"})

    def test_revision_enforced_by_shared_token_guard(self):
        claims=context.account_session_claims(self.row,ORG,"dr01");claims["sub"]=ORG+":dr01"
        self.assertTrue(context.token_matches_institution(claims,auth_users.get_user))
        auth_users.upsert_user(ORG,"dr01","physician",display_name="updated synthetic account")
        self.assertFalse(context.token_matches_institution(claims,auth_users.get_user))

    def test_cross_institution_issuance_refused(self):
        with self.assertRaises(ValueError):context.account_session_claims(self.row,"other","dr01")

class MedicalSessionFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # The real Flask app creates logs/database paths at import time.
        # Keep integration fixtures out of the checkout and production stores.
        cls.runtime = tempfile.TemporaryDirectory(prefix="mmh-session-test-")
        cls.env = patch.dict(os.environ, {"WOUNDAI_RUNTIME_DIR": cls.runtime.name})
        cls.env.start()
        cls.addClassCleanup(cls.env.stop)
        cls.addClassCleanup(cls.runtime.cleanup)

    setUp = MedicalSessionTests.setUp
    # Only own tests run here; inherited route tests already run above.
    def test_password_login_and_global_guard_use_account_revision(self):
        import app as backend
        password=secrets.token_urlsafe(24)
        auth_users.upsert_user(ORG,"dr01","physician",password)
        client=backend.app.test_client()
        with patch.object(backend,"BOUND_ORG",ORG), patch.object(api_users.fw,"audit"):
            reply=client.post("/api/auth/login",json={"username":ORG+":dr01","password":password})
        self.assertEqual(reply.status_code,200)
        token=reply.json["access_token"]
        with backend.app.app_context(): claims=decode_token(token)
        self.assertEqual(claims["account_revision"],revision(auth_users.get_user(ORG,"dr01")))
        self.assertEqual(client.get("/api/v1/auth/session",headers={"Authorization":"Bearer "+token}).status_code,200)
        auth_users.upsert_user(ORG,"dr01","physician",disabled=True)
        self.assertNotEqual(client.get("/api/v1/auth/session",headers={"Authorization":"Bearer "+token}).status_code,200)

    def test_global_guard_failure_is_not_cacheable(self):
        import app as backend
        with backend.app.app_context():
            token = create_access_token(identity=ORG+":dr01",
                additional_claims=context.account_session_claims(self.row, ORG, "dr01"))
        auth_users.upsert_user(ORG, "dr01", "nurse")
        response = backend.app.test_client().get("/api/v1/auth/session",
            headers={"Authorization": "Bearer "+token})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertNotIn("perms", response.json)

    def test_otc_and_exchange_preserve_revision_and_reject_changed_account(self):
        import app as backend
        with backend.app.app_context():
            token=create_access_token(identity=ORG+":dr01",additional_claims=context.account_session_claims(self.row,ORG,"dr01"))
        client=backend.app.test_client(); headers={"Authorization":"Bearer "+token}
        with patch.object(api_users.fw,"audit"):
            issued=client.post("/api/v1/auth/onetime",headers=headers)
            self.assertEqual(issued.status_code,200)
            with backend.app.app_context(): claims=decode_token(issued.json["code"])
            self.assertEqual(claims["account_revision"],revision(self.row))
            exchanged=client.post("/api/auth/exchange",json={"code":issued.json["code"]})
            self.assertEqual(exchanged.status_code,200)
            with backend.app.app_context(): claims=decode_token(exchanged.json["access_token"])
            self.assertEqual(claims["account_revision"],revision(self.row))
            issued=client.post("/api/v1/auth/onetime",headers=headers)
            auth_users.upsert_user(ORG,"dr01","physician",secrets.token_urlsafe(24))
            self.assertEqual(client.post("/api/auth/exchange",json={"code":issued.json["code"]}).status_code,401)

    def test_otc_second_read_race_does_not_upgrade_changed_account(self):
        import app as backend
        with backend.app.app_context():
            otc=create_access_token(identity=ORG+":dr01",additional_claims={**context.account_session_claims(self.row,ORG,"dr01"),"typ":"otc"})
        auth_users.upsert_user(ORG,"dr01","physician",secrets.token_urlsafe(24))
        new=auth_users.get_user(ORG,"dr01")
        with patch.object(auth_users,"get_user",side_effect=[self.row,new]),patch.object(api_users.fw,"audit"):
            reply=backend.app.test_client().post("/api/auth/exchange",json={"code":otc})
        self.assertEqual(reply.status_code,401)
        self.assertNotIn("access_token",reply.json)


if __name__=="__main__":unittest.main()

