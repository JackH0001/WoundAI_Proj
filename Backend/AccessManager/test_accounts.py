import copy
import hashlib
import json
import unittest
from unittest.mock import Mock

from .accounts import AccountAdapter, AccountError, AccountUnconfirmed, ORG, URL, account_snapshot
from .web import create_app, VerifiedAdmin
from .test_iap_policy import MemoryAdapter, MemoryAudit


class MemoryAccountsHttp:
    """Synthetic loopback fixture, never a deployable account store."""
    def __init__(self):
        self.rows = [dict(identity=ORG+":admin", org=ORG, user="admin", role="admin", disabled=False,
                          display_name="合成管理者", role_zh="管理者", created_at="test", updated_at="test", revision="1"*64)]
        self.posts = []

    def get(self, url, **kwargs):
        assert url == URL
        return Mock(status_code=200, json=lambda: {"users": copy.deepcopy(self.rows), "account_versioning":"cas-v1"})

    def post(self, url, *, json, **kwargs):
        assert url == URL
        self.posts.append(copy.deepcopy(json))
        current = next((row for row in self.rows if row["user"] == json["user"]), None)
        if json.get("expected_revision") != (current["revision"] if current else "0"*64):
            return Mock(status_code=409, json=lambda:{"error":"stale_account_revision"})
        if current is None:
            current = dict(identity=ORG+":"+json["user"], user=json["user"], org=ORG)
            self.rows.append(current)
        current.update({k: json[k] for k in ("role", "display_name", "disabled")})
        current["role_zh"] = {"physician":"醫師", "nurse":"護理師", "assistant":"助理", "engineer":"工程師", "admin":"管理者"}[current["role"]]
        current["revision"] = hashlib.sha256(repr(current).encode()).hexdigest()
        result = {"status":"ok", "user":copy.deepcopy(current)}
        if json.get("generate_password"):
            result["generated_password"] = "SYNTHETIC-ONLY-NOT-A-REAL-PASSWORD"
        return Mock(status_code=200, json=lambda: result)


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.http = MemoryAccountsHttp(); self.adapter = AccountAdapter(self.http)
        self.actor = ORG+":admin"

    def change(self, **overrides):
        data = dict(action="create", user="dr-test", role="physician", display_name="合成醫師",
                    disabled=False, account_snapshot=account_snapshot(self.http.rows))
        data.update(overrides)
        return self.adapter.change(data, actor=self.actor)

    def test_create_readback_and_one_time_password(self):
        result = self.change()
        self.assertEqual(result["status"], "account_readback_verified")
        self.assertTrue(result["generated_password"])
        self.assertNotIn("generated_password", self.adapter.list_accounts()[1])

    def test_slow_successful_creation_receipt_not_cut_off_at_thirty_seconds(self):
        original=self.http.post
        calls=[]
        def delayed(*args,**kwargs):
            calls.append(kwargs["timeout"])
            if kwargs["timeout"] < 36:raise TimeoutError("synthetic 36-second response")
            return original(*args,**kwargs)
        self.http.post=delayed
        result=self.change()
        self.assertEqual(calls,[90])
        self.assertEqual(result["status"],"account_readback_verified")
        self.assertTrue(result["generated_password"])
        self.assertEqual(len(self.http.posts),1)

    def test_role_disable_enable_preserves_password(self):
        self.change()
        self.change(action="update", role="nurse", disabled=True)
        self.assertTrue(self.http.rows[1]["disabled"])
        self.assertNotIn("generate_password", self.http.posts[-1])
        result = self.change(action="update", role="nurse", disabled=False)
        self.assertNotIn("generated_password", result)

    def test_identical_state_no_write(self):
        self.change(); self.http.posts.clear()
        self.assertFalse(self.change(action="update")["changed"])
        self.assertEqual(self.http.posts, [])

    def test_stale_snapshot_no_write(self):
        with self.assertRaisesRegex(AccountError, "stale_account"):
            self.change(account_snapshot="old")
        self.assertFalse(self.http.posts)

    def test_existing_create_and_missing_update_refused(self):
        with self.assertRaises(AccountError): self.change(action="update")
        self.change()
        with self.assertRaises(AccountError): self.change()
        self.assertEqual(len(self.http.posts), 1)

    def test_bootstrap_and_self_protected(self):
        with self.assertRaises(AccountError): self.change(user="admin")
        self.actor = ORG+":dr-test"
        with self.assertRaises(AccountError): self.change()
        self.assertFalse(self.http.posts)

    def test_reject_bad_types_unknown_role_and_control_name(self):
        for change in ({"user":[]}, {"role":[]}, {"role":"lite"}, {"disabled":"false"},
                       {"display_name":"bad\nname"}, {"display_name":"x"*81}):
            with self.subTest(change=change), self.assertRaises(AccountError): self.change(**change)
        self.assertFalse(self.http.posts)

    def test_foreign_org_duplicate_or_malformed_rows_fail(self):
        for bad in (dict(self.http.rows[0], org="other"), dict(self.http.rows[0], disabled=None), self.http.rows[0]):
            self.http.rows.append(bad)
            with self.assertRaises(AccountError): self.adapter.list_accounts()
            self.http.rows.pop()

    def test_post_timeout_redacted_not_retried(self):
        self.http.post = Mock(side_effect=TimeoutError("private password"))
        with self.assertRaisesRegex(AccountUnconfirmed, "^account_change_unconfirmed$"): self.change()
        self.assertEqual(self.http.post.call_count, 1)

    def test_readback_mismatch_is_unconfirmed(self):
        receipt = dict(identity=ORG+":dr-test", org=ORG, user="dr-test", role="physician", display_name="合成醫師", disabled=False)
        self.http.post = Mock(return_value=Mock(status_code=200, json=lambda: {"status":"ok", "user":receipt, "generated_password":"not-real-password"}))
        with self.assertRaises(AccountUnconfirmed): self.change()

    def test_http_failures_never_claim_success(self):
        for status in (302, 401, 403, 409, 500):
            self.http.post = Mock(return_value=Mock(status_code=status, json=lambda: {"status":"ok"}))
            with self.assertRaises(AccountUnconfirmed): self.change()

    def test_wrong_account_receipt_refused_even_if_readback_matches(self):
        original = self.http.post
        def mismatch(*args, **kwargs):
            result = original(*args, **kwargs).json()
            result["user"]["identity"] = ORG+":someone-else"
            return Mock(status_code=200, json=lambda: result)
        self.http.post = mismatch
        with self.assertRaises(AccountUnconfirmed): self.change()
        self.assertEqual(self.http.rows[-1]["user"], "dr-test")

    def test_unavailable_read_does_not_write(self):
        self.http.get = Mock(side_effect=TimeoutError())
        with self.assertRaises(AccountError): self.change()
        self.assertFalse(self.http.posts)

    def test_legacy_backend_without_cas_capability_cannot_write(self):
        self.http.get = Mock(return_value=Mock(status_code=200,json=lambda:{"users":self.http.rows}))
        with self.assertRaises(AccountError):self.change()
        self.assertFalse(self.http.posts)

    def test_backend_atomic_conflict_requires_refresh(self):
        self.http.post = Mock(return_value=Mock(status_code=409,json=lambda:{"error":"stale_account_revision"}))
        with self.assertRaisesRegex(AccountError,"stale_account_refresh_required"):self.change()
        self.assertEqual(self.http.post.call_count,1)

    def test_http_route_requires_csrf_and_returns_no_store(self):
        principal = VerifiedAdmin("admin@example.test", "test", self.actor, "admin", ORG, "session")
        app = create_app(origin="https://manager.example.test", authenticate=lambda request:principal,
                         policy_adapter=MemoryAdapter(), audit=MemoryAudit(), list_accounts=lambda:self.http.rows,
                         admin_emails=[principal.google_email], account_adapter_for=lambda request,p:self.adapter)
        client=app.test_client(); response=client.get("/api/members")
        self.assertEqual(response.status_code,200)
        data=dict(action="create",user="dr-test",role="physician",display_name="synthetic",disabled=False,
                  account_snapshot=response.json["account_snapshot"])
        self.assertEqual(client.post("/api/accounts",json=data).status_code,403)
        result=client.post("/api/accounts",json=data,headers={"Origin":"https://manager.example.test","X-MMH-CSRF":response.json["csrf"]})
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.headers["Cache-Control"],"no-store")
        self.assertIn("generated_password",result.json)
        self.assertNotIn("generated_password",str(client.get("/api/members").json))


if __name__ == "__main__": unittest.main()
