"""Fault-injected contract tests; never contact Google or use credentials."""
from copy import deepcopy
import unittest

from . import iap_policy as p
from .iap_adapter import IapPolicyAdapter, TransportError


def baseline():
    return {"version": 3, "etag": "v1", "bindings": [
        {"role": p.ENTRY_ROLE, "members": ["user:Jack.Hou@gmail.com"]},
        {"role": p.ENTRY_ROLE, "members": ["user:conditional@example.com"],
         "condition": {"title": "temporary", "expression": "request.time < timestamp('2026-12-01T00:00:00Z')"}},
        {"role": "roles/iap.admin", "members": ["group:existing@example.com"]}],
        "auditConfigs": [{"service": "allServices", "auditLogConfigs": [{"logType": "ADMIN_READ"}]}]}


class MemoryAdapter:
    def __init__(self):
        self.policy = baseline()
        self.writes = []
        self.reads = 0
        self.write_error = False
        self.diverge = False

    def get_policy(self, resource):
        assert resource == p.RESOURCE
        self.reads += 1
        result = deepcopy(self.policy)
        if self.diverge and self.reads > 1:
            result["bindings"][2]["members"].append("user:concurrent@example.com")
        return result

    def set_policy(self, resource, policy):
        assert resource == p.RESOURCE
        self.writes.append(deepcopy(policy))
        self.policy = deepcopy(policy)
        self.policy["etag"] = "v2"
        if self.write_error:
            raise TimeoutError("sensitive transport detail must not appear")


class MemoryAudit:
    def __init__(self):
        self.events = []
        self.fail = None

    def append(self, event):
        if event["phase"] == self.fail:
            raise OSError("audit unavailable")
        self.events.append(deepcopy(event))


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.adapter = MemoryAdapter()
        self.audit = MemoryAudit()

    def apply(self, **changes):
        args = dict(adapter=self.adapter, audit=self.audit, actor="verified-admin",
                    email="new@example.com", action="grant",
                    expected_snapshot=p.snapshot_digest(self.adapter.policy))
        args.update(changes)
        return p.apply_confirmed_change(**args)

    def test_grant_preserves_other_roles_conditions_and_audit(self):
        original = baseline()
        after = p.propose(original, "new@example.com", "grant")
        self.assertEqual(after["bindings"][1:], original["bindings"][1:])
        self.assertEqual(after["auditConfigs"], original["auditConfigs"])
        self.assertEqual(after["etag"], original["etag"])
        self.assertEqual(original, baseline())

    def test_remove_only_target_not_other_members(self):
        before = p.propose(baseline(), "new@example.com", "grant")
        self.assertEqual(p.propose(before, "new@example.com", "revoke"), baseline())

    def test_last_empty_binding_removed_not_other_bindings(self):
        before = baseline()
        before["bindings"][0]["members"] = ["user:new@example.com"]
        self.assertEqual(p.propose(before, "new@example.com", "revoke")["bindings"], before["bindings"][1:])

    def test_recovery_administrator_cannot_be_revoked(self):
        with self.assertRaisesRegex(p.PolicyError, "protected"):
            self.apply(email="Jack.Hou@gmail.com", action="revoke")
        self.assertFalse(self.adapter.writes)
        self.assertFalse(self.audit.events)

    def test_reject_public_group_service_account_or_malformed_input(self):
        for email in ["allUsers", "allAuthenticatedUsers", "group:x@example.com",
                      "serviceAccount:x@example.com", "runtime@test.iam.gserviceaccount.com",
                      "x@example.com\nother", "x@localhost", {}, None]:
            with self.subTest(email=email), self.assertRaises(p.PolicyError):
                self.apply(email=email)
        self.assertFalse(self.adapter.writes)

    def test_email_normalization_does_not_add_duplicate(self):
        self.assertEqual(p.propose(baseline(), " Jack.Hou@gmail.com ", "grant"), baseline())

    def test_bad_action_rejected(self):
        with self.assertRaises(p.PolicyError):
            self.apply(action="owner")
        self.assertFalse(self.adapter.writes)

    def test_missing_or_bad_etag_rejected(self):
        for value in [None, "", 1, " "]:
            policy = baseline(); policy["etag"] = value
            with self.subTest(value=value), self.assertRaises(p.PolicyError):
                p.propose(policy, "new@example.com", "grant")

    def test_invalid_policy_shapes_rejected(self):
        for policy in [None, {"etag": "v1", "version": True},
                       {"etag": "v1", "bindings": {}},
                       {"etag": "v1", "bindings": [{"role": p.ENTRY_ROLE, "members": "user:a@b.com"}]},
                       {"etag": "v1", "bindings": [{"role": p.ENTRY_ROLE, "members": [1]}]}]:
            with self.subTest(policy=policy), self.assertRaises(p.PolicyError):
                p.propose(policy, "new@example.com", "grant")

    def test_condition_with_version_one_rejected(self):
        policy = baseline(); policy["version"] = 1
        with self.assertRaises(p.PolicyError):
            p.snapshot_digest(policy)

    def test_duplicate_members_and_entry_bindings_rejected(self):
        for kind in ("member", "binding"):
            policy = baseline()
            if kind == "member": policy["bindings"][0]["members"].append("user:jack.hou@gmail.com")
            else: policy["bindings"].append(deepcopy(policy["bindings"][0]))
            with self.subTest(kind=kind), self.assertRaises(p.PolicyError):
                p.snapshot_digest(policy)

    def test_snapshot_includes_etag_and_unrelated_policy(self):
        old = p.snapshot_digest(self.adapter.policy)
        self.adapter.policy["etag"] = "someone-else"
        with self.assertRaisesRegex(p.PolicyError, "stale"):
            self.apply(expected_snapshot=old)
        self.assertFalse(self.adapter.writes)
        self.assertFalse(self.audit.events)

    def test_snapshot_rejects_unrelated_change_even_if_etag_same(self):
        old = p.snapshot_digest(self.adapter.policy)
        self.adapter.policy["bindings"][2]["members"].append("user:other@example.com")
        with self.assertRaisesRegex(p.PolicyError, "stale"):
            self.apply(expected_snapshot=old)

    def test_audit_intent_failure_prevents_write(self):
        self.audit.fail = "intent"
        with self.assertRaises(OSError): self.apply()
        self.assertFalse(self.adapter.writes)

    def test_success_has_verified_readback_but_not_effective_access(self):
        result = self.apply()
        self.assertTrue(result.changed)
        self.assertTrue(result.direct_binding_verified)
        self.assertFalse(result.effective_access_verified)
        self.assertFalse(result.existing_sessions_revoked)
        self.assertEqual(self.adapter.reads, 2)
        self.assertEqual([e["phase"] for e in self.audit.events], ["intent", "verified"])
        self.assertEqual(self.adapter.writes[0]["etag"], "v1")

    def test_noop_does_not_write_but_reads_and_audits(self):
        result = self.apply(email="jack.hou@gmail.com")
        self.assertFalse(result.changed)
        self.assertFalse(self.adapter.writes)
        self.assertEqual(self.adapter.reads, 2)
        self.assertEqual(self.audit.events[-1]["phase"], "verified")

    def test_conditional_access_never_misreported_as_revoked(self):
        result = self.apply(email="conditional@example.com", action="revoke")
        self.assertFalse(result.changed)
        self.assertTrue(result.conditional_entry_for_target)
        self.assertFalse(result.effective_access_verified)

    def test_timeout_after_mutation_is_not_retried_or_rolled_back(self):
        self.adapter.write_error = True
        with self.assertRaises(p.ChangeUnconfirmed) as caught: self.apply()
        self.assertEqual(caught.exception.stage, "write")
        self.assertNotIn("sensitive", str(caught.exception))
        self.assertEqual(len(self.adapter.writes), 1)
        self.assertIn("user:new@example.com", self.adapter.policy["bindings"][0]["members"])

    def test_readback_concurrent_change_never_claims_success(self):
        self.adapter.diverge = True
        with self.assertRaises(p.ChangeUnconfirmed) as caught: self.apply()
        self.assertEqual(caught.exception.stage, "readback")
        self.assertEqual(len(self.adapter.writes), 1)
        self.assertEqual(self.audit.events[-1]["phase"], "unconfirmed")

    def test_completion_audit_failure_is_not_silent_success(self):
        self.audit.fail = "verified"
        with self.assertRaises(p.ChangeUnconfirmed) as caught: self.apply()
        self.assertEqual(caught.exception.stage, "completion_audit")
        self.assertEqual(len(self.adapter.writes), 1)

    def test_etag_conflict_keeps_concurrent_change_and_stops(self):
        def concurrent_write(resource, requested):
            self.adapter.policy["etag"] = "v3-other-admin"
            self.adapter.policy["bindings"][2]["members"].append("user:other@example.com")
            if requested["etag"] != self.adapter.policy["etag"]:
                raise RuntimeError("HTTP 409")
            self.fail("a stale write must not be accepted")
        self.adapter.set_policy = concurrent_write
        with self.assertRaises(p.ChangeUnconfirmed): self.apply()
        self.assertEqual(self.adapter.policy["etag"], "v3-other-admin")
        self.assertNotIn("user:new@example.com", self.adapter.policy["bindings"][0]["members"])
        self.assertIn("user:other@example.com", self.adapter.policy["bindings"][2]["members"])

    def test_reordered_readback_with_canonical_email_accepted(self):
        original = self.adapter.get_policy
        def get_reordered(resource):
            value = original(resource)
            value["bindings"].reverse()
            for binding in value["bindings"]:
                binding["members"] = [m.lower() for m in reversed(binding["members"])]
            return value
        self.adapter.get_policy = get_reordered
        self.assertTrue(self.apply().direct_binding_verified)

    def test_missing_readback_etag_never_counts_as_saved(self):
        original = self.adapter.get_policy
        def get_incomplete(resource):
            value = original(resource)
            if self.adapter.reads > 1: value.pop("etag")
            return value
        self.adapter.get_policy = get_incomplete
        with self.assertRaises(p.ChangeUnconfirmed) as caught: self.apply()
        self.assertEqual(caught.exception.stage, "readback")

    def test_invalid_actor_or_snapshot_stops_before_write(self):
        for edit in [{"actor": ""}, {"actor": None}, {"expected_snapshot": ""}, {"expected_snapshot": 1}]:
            with self.subTest(edit=edit), self.assertRaises(p.PolicyError): self.apply(**edit)
        self.assertFalse(self.adapter.writes)


class Response:
    status_code = 200
    def json(self): return baseline()


class Session:
    def __init__(self): self.calls = []; self.response = Response()
    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class AdapterTests(unittest.TestCase):
    def setUp(self): self.session = Session(); self.adapter = IapPolicyAdapter(self.session)

    def test_reads_version_three_and_fixed_resource_without_redirect(self):
        self.adapter.get_policy(p.RESOURCE)
        url, kwargs = self.session.calls[0]
        self.assertEqual(url, f"https://iap.googleapis.com/v1/{p.RESOURCE}:getIamPolicy")
        self.assertEqual(kwargs["json"], {"options": {"requestedPolicyVersion": 3}})
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(kwargs["timeout"], 20)

    def test_cannot_select_another_resource(self):
        with self.assertRaises(p.PolicyError): self.adapter.get_policy(p.RESOURCE + "-other")
        self.assertFalse(self.session.calls)

    def test_write_keeps_etag_and_condition_version(self):
        policy = baseline(); self.adapter.set_policy(p.RESOURCE, policy)
        body = self.session.calls[0][1]["json"]
        self.assertEqual(body["policy"], policy)
        self.assertEqual(body["updateMask"], "bindings,etag")

    def test_http_errors_and_redirects_fail_closed(self):
        for status in (302, 401, 403, 409, 429, 500):
            self.session.response.status_code = status
            with self.subTest(status=status), self.assertRaises(TransportError):
                self.adapter.get_policy(p.RESOURCE)

    def test_no_etag_cannot_be_written(self):
        with self.assertRaises(p.PolicyError): self.adapter.set_policy(p.RESOURCE, {"bindings": []})
        self.assertFalse(self.session.calls)

    def test_invalid_response_rejected(self):
        self.session.response.json = lambda: {"error": "not a policy"}
        with self.assertRaises(TransportError): self.adapter.get_policy(p.RESOURCE)

    def test_transport_failure_is_redacted_and_not_retried(self):
        def fail(url, **kwargs):
            self.session.calls.append((url, kwargs))
            raise OSError("credential must never be returned")
        self.session.post = fail
        with self.assertRaises(TransportError) as caught: self.adapter.get_policy(p.RESOURCE)
        self.assertEqual(str(caught.exception), "iap_transport_unconfirmed")
        self.assertEqual(len(self.session.calls), 1)


if __name__ == "__main__": unittest.main()
