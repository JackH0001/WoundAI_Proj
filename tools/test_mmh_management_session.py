"""Read-only MMH session endpoint, real JWT and temporary account store."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"Backend"/"Flask"))
from flask import Flask
from flask_jwt_extended import JWTManager, create_access_token
import auth_users
import api_users
import institution_context
from store import LocalStore

ORG = "mmhps20261007"


class ManagementSessionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.store = LocalStore(tmp.name)
        for target, value in (("institution_context.BOUND_ORG", ORG),
                              ("auth_users.BOUND_ORG", ORG)):
            p = patch(target, value); p.start(); self.addCleanup(p.stop)
        p = patch.object(auth_users, "_store", return_value=self.store)
        p.start(); self.addCleanup(p.stop)
        auth_users.upsert_user(ORG, "admin", "admin", "synthetic-test-password-only")
        self.app = Flask(__name__)
        self.app.config["JWT_SECRET_KEY"] = "synthetic-test-key-" + "x"*40
        JWTManager(self.app)  # Deliberately omit the main app live-check hook.
        self.app.register_blueprint(api_users.users_bp)
        self.client = self.app.test_client()

    def get(self, **overrides):
        claims = dict(org=ORG, user="admin", role="admin"); claims.update(overrides)
        with self.app.app_context():
            token = create_access_token(identity=ORG+":admin", additional_claims=claims)
        return self.client.get("/api/v1/users/session", headers={"Authorization":"Bearer "+token})

    def test_live_admin_minimal_response(self):
        result = self.get()
        self.assertEqual(result.status_code, 200)
        self.assertEqual(set(result.json), {"identity","role","org","session_id","expires_at"})
        self.assertEqual(result.headers["Cache-Control"], "no-store")

    def test_disabled_admin_refused(self):
        auth_users.upsert_user(ORG, "admin", "admin", disabled=True)
        self.assertEqual(self.get().status_code, 401)

    def test_old_role_and_current_nonadmin_refused(self):
        auth_users.upsert_user(ORG, "admin", "physician")
        self.assertEqual(self.get().status_code, 401)
        self.assertEqual(self.get(role="physician").status_code, 403)

    def test_foreign_org_and_otc_refused(self):
        self.assertEqual(self.get(org="other").status_code, 401)
        self.assertEqual(self.get(typ="otc").status_code, 401)

    def test_store_unavailable_refused(self):
        with patch.object(auth_users, "get_user", side_effect=OSError("private error")):
            result = self.get()
        self.assertEqual(result.status_code, 401)
        self.assertNotIn("private error", result.get_data(as_text=True))

    def test_legacy_service_not_enabled(self):
        with patch.object(institution_context, "BOUND_ORG", None):
            self.assertEqual(self.get().status_code, 503)

    def test_unsigned_request_refused(self):
        self.assertEqual(self.client.get("/api/v1/users/session").status_code, 401)


if __name__ == "__main__":
    unittest.main()
