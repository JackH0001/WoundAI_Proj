from dataclasses import replace
import unittest

from .test_iap_policy import MemoryAdapter, MemoryAudit
from .web import VerifiedAdmin, create_app

ORIGIN = "https://management.example.test"


class WebTests(unittest.TestCase):
    def setUp(self):
        self.adapter = MemoryAdapter()
        self.audit = MemoryAudit()
        self.principal = VerifiedAdmin("jack.hou@gmail.com", "verified-google-sub", "mmhps20261007:admin", "admin", "mmhps20261007", "session-1")
        self.accounts = [{"org": "mmhps20261007", "identity": "mmhps20261007:dr01", "role": "physician", "disabled": False, "pw": "must-not-be-returned"}]
        self.app = create_app(origin=ORIGIN, authenticate=lambda req: self.principal,
            policy_adapter=self.adapter, audit=self.audit, list_accounts=lambda: self.accounts,
            admin_emails=["jack.hou@gmail.com"], csrf_secret=b"test-only-key-do-not-use-in-live!!")
        self.client = self.app.test_client()

    def load(self): return self.client.get("/api/members").get_json()

    def post(self, data=None, headers=None):
        state = self.load()
        return self.client.post("/api/entry", json=data or {"email": "new@example.com", "action": "grant", "policy_snapshot": state["policy_snapshot"]},
            headers=headers or {"Origin": ORIGIN, "X-MMH-CSRF": state["csrf"]})

    def test_unconfigured_api_is_closed_and_static_page_has_no_data(self):
        c = create_app(origin=ORIGIN).test_client()
        self.assertEqual(c.get("/api/members").status_code, 503)
        self.assertEqual(c.post("/api/entry").status_code, 503)
        with c.get("/") as response:
            self.assertNotIn(b"jack.hou", response.data)

    def test_unverified_identity_cannot_read(self):
        self.principal = None
        self.assertEqual(self.client.get("/api/members").status_code, 401)

    def test_disallowed_google_or_app_role_org_session_cannot_read(self):
        original = self.principal
        for edit in [{"google_email": "other@example.com"}, {"role": "physician"}, {"org": "foreign"},
                     {"app_identity": "other:admin"}, {"google_subject": ""}, {"session_id": ""}]:
            self.principal = replace(original, **edit)
            with self.subTest(edit=edit): self.assertEqual(self.client.get("/api/members").status_code, 403)

    def test_client_claims_do_not_grant_admin(self):
        self.principal = None
        response = self.client.post("/api/entry", json={"role": "admin"}, headers={"X-Goog-Authenticated-User-Email": "accounts.google.com:jack.hou@gmail.com"})
        self.assertEqual(response.status_code, 401)
        self.assertFalse(self.adapter.writes)

    def test_member_response_filters_credentials_and_does_not_infer_mapping(self):
        response = self.client.get("/api/members")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"must-not-be-returned", response.data)
        state = response.get_json()
        self.assertFalse(state["effective_access_verified"])
        self.assertNotIn("app_identity", state["entries"][0])
        self.assertEqual(state["accounts"][0]["identity"], "mmhps20261007:dr01")

    def test_foreign_accounts_fail_whole_read(self):
        self.accounts.append({"org": "other", "identity": "other:admin"})
        self.assertEqual(self.client.get("/api/members").status_code, 503)

    def test_origin_mismatch_or_missing_rejected(self):
        state = self.load()
        for origin in ("https://evil.example", "null", ""):
            response = self.post(headers={"Origin": origin, "X-MMH-CSRF": state["csrf"]})
            self.assertEqual(response.status_code, 403)
        self.assertFalse(self.adapter.writes)

    def test_csrf_missing_or_invalid_rejected(self):
        response = self.post(headers={"Origin": ORIGIN, "X-MMH-CSRF": "wrong"})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.adapter.writes)

    def test_non_ascii_csrf_rejected_without_write(self):
        response = self.post(headers={"Origin": ORIGIN, "X-MMH-CSRF": "非合法值"})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.adapter.writes)

    def test_malformed_verified_adapter_output_is_denied(self):
        original = self.principal
        for edit in ({"google_email": None}, {"app_identity": 12}, {"session_id": "a\0b"}):
            self.principal = replace(original, **edit)
            self.assertEqual(self.client.get("/api/members").status_code, 403)

    def test_csrf_rotates_with_login_session(self):
        previous = self.load()["csrf"]
        self.principal = replace(self.principal, session_id="session-2")
        response = self.post(headers={"Origin": ORIGIN, "X-MMH-CSRF": previous})
        self.assertEqual(response.status_code, 403)

    def test_non_json_request_rejected(self):
        response = self.client.post("/api/entry", data="action=grant", headers={"Origin": ORIGIN})
        self.assertEqual(response.status_code, 415)

    def test_arbitrary_resource_policy_role_input_rejected(self):
        state = self.load()
        for field in ("resource", "policy", "role", "actor"):
            data = {"email": "new@example.com", "action": "grant", "policy_snapshot": state["policy_snapshot"], field: "forged"}
            self.assertEqual(self.post(data=data).status_code, 400)
        self.assertFalse(self.adapter.writes)

    def test_grant_result_is_readback_not_full_account_activation(self):
        response = self.post()
        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assertEqual(result["status"], "direct_policy_verified")
        self.assertFalse(result["effective_access_verified"])
        self.assertFalse(result["existing_sessions_revoked"])
        self.assertEqual(len(self.adapter.writes), 1)

    def test_stale_snapshot_returns_conflict_without_write(self):
        state = self.load()
        self.adapter.policy["etag"] = "new"
        response = self.post(data={"email": "new@example.com", "action": "grant", "policy_snapshot": state["policy_snapshot"]})
        self.assertEqual(response.status_code, 409)
        self.assertFalse(self.adapter.writes)

    def test_ambiguous_write_returns_operation_reference_not_retry(self):
        self.adapter.write_error = True
        response = self.post()
        self.assertEqual(response.status_code, 502)
        self.assertFalse(response.get_json()["retry_safe"])
        self.assertTrue(response.get_json()["operation_id"])
        self.assertNotIn(b"sensitive", response.data)

    def test_audit_unavailable_prevents_iam_write(self):
        self.audit.fail = "intent"
        self.assertEqual(self.post().status_code, 503)
        self.assertFalse(self.adapter.writes)

    def test_security_headers_no_cache_no_embedding(self):
        for route in ("/", "/api/members", "/static/members.js"):
            with self.client.get(route) as response:
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
                self.assertNotIn("Access-Control-Allow-Origin", response.headers)

    def test_real_non_https_or_path_origin_rejected(self):
        for origin in ("http://example.com", "https://example.com/path", "https://a:b@example.com", "ftp://localhost"):
            with self.subTest(origin=origin), self.assertRaises(ValueError): create_app(origin=origin)


if __name__ == "__main__": unittest.main()
