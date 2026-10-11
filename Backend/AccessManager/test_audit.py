"""Fault-injected audit transport and policy integration; no cloud credentials."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import unittest
from urllib.parse import unquote
from uuid import uuid4

from .audit import (AuditError, BUCKET, PREFIX, GcsAuditObjects,
                    ManagementAudit, digest)
from .iap_policy import (ChangeUnconfirmed, ENTRY_ROLE, RESOURCE,
                         apply_confirmed_change, snapshot_digest)
from .test_iap_policy import MemoryAdapter


class Response:
    def __init__(self, code=200, body=None, content=b""):
        self.status_code, self.body, self.content = code, body, content

    def json(self):
        return deepcopy(self.body)


class ObjectHttp:
    def __init__(self):
        self.objects = {}
        self.calls = []
        self.fail_post = None
        self.corrupt_media = False
        self.bad_metadata = False
        self.fail_outcome = False

    def metadata(self, key):
        generation, data = self.objects[key]
        return {"bucket": BUCKET, "name": key, "size": str(len(data)),
                "generation": generation}

    def post(self, url, **kwargs):
        self.calls.append(("post", url, deepcopy(kwargs)))
        key = kwargs["params"]["name"]
        if self.fail_post == "before" or (self.fail_outcome and key.endswith("outcome.json")):
            raise TimeoutError("SECRET must not escape")
        if key in self.objects:
            return Response(412)
        self.objects[key] = (str(len(self.objects) + 1), kwargs["data"])
        if self.fail_post == "after":
            raise TimeoutError("SECRET must not escape")
        metadata = self.metadata(key)
        if self.bad_metadata:
            metadata["bucket"] = "wrong"
        return Response(body=metadata)

    def get(self, url, **kwargs):
        self.calls.append(("get", url, deepcopy(kwargs)))
        key = unquote(url.split("/o/")[1])
        if key not in self.objects:
            return Response(404)
        params = kwargs.get("params", {})
        if params.get("alt") != "media":
            return Response(body=self.metadata(key))
        generation, data = self.objects[key]
        if params.get("generation") != generation or params.get("ifGenerationMatch") != generation:
            return Response(412)
        return Response(content=b"corrupted" if self.corrupt_media else data)


def intent_event():
    return {"operation_id": str(uuid4()), "actor": "owner@example.com|mmhps20261007:admin",
            "member": "user:tester@example.com", "resource": RESOURCE, "role": ENTRY_ROLE,
            "action": "grant", "before_sha256": "a" * 64, "proposed_sha256": "b" * 64,
            "phase": "intent", "changed": True}


def completion(intent):
    return dict(intent, phase="verified", readback_sha256="c" * 64,
                effective_access_verified=False, conditional_entry_for_target=False)


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.http = ObjectHttp()
        self.objects = GcsAuditObjects(self.http)
        self.audit = ManagementAudit(self.objects,
            clock=lambda: datetime(2026, 10, 11, 0, 0, tzinfo=timezone.utc))
        self.event = intent_event()

    def apply(self, adapter=None):
        adapter = adapter or MemoryAdapter()
        return apply_confirmed_change(adapter=adapter, audit=self.audit,
            actor=self.event["actor"], email="tester@example.com", action="grant",
            expected_snapshot=snapshot_digest(adapter.policy))

    def test_real_policy_flow_has_verified_durable_intent_and_linked_outcome(self):
        result = self.apply()
        root = PREFIX + result.operation_id + "/"
        gen, data = self.http.objects[root + "intent.json"]
        outcome = json.loads(self.http.objects[root + "outcome.json"][1])
        self.assertEqual(outcome["intent"], {"key": root + "intent.json", "generation": gen,
                                            "sha256": digest(data)})
        self.assertEqual(outcome["event"]["phase"], "verified")
        self.assertEqual(outcome["recorded_at"], "2026-10-11T00:00:00+00:00")
        self.assertFalse(outcome["event"]["effective_access_verified"])
        posts = [call for call in self.http.calls if call[0] == "post"]
        self.assertEqual(len(posts), 2)
        for _, url, args in posts:
            self.assertEqual(url, f"https://storage.googleapis.com/upload/storage/v1/b/{BUCKET}/o")
            self.assertEqual(args["params"]["ifGenerationMatch"], "0")
            self.assertEqual(args["timeout"], 20)
            self.assertFalse(args["allow_redirects"])
        media_reads = [call[2]["params"] for call in self.http.calls
                       if call[0] == "get" and "params" in call[2]]
        self.assertTrue(media_reads)
        for params in media_reads:
            self.assertEqual(params["generation"], params["ifGenerationMatch"])

    def test_intent_failure_prevents_iam_write(self):
        for fail in ("before", "after"):
            with self.subTest(fail=fail):
                self.http.fail_post = fail
                adapter = MemoryAdapter()
                with self.assertRaises(AuditError) as caught:
                    self.apply(adapter)
                self.assertNotIn("SECRET", str(caught.exception))
                self.assertEqual(adapter.writes, [])

    def test_intent_readback_corruption_prevents_iam_write(self):
        self.http.corrupt_media = True
        adapter = MemoryAdapter()
        with self.assertRaises(AuditError):
            self.apply(adapter)
        self.assertEqual(adapter.writes, [])
        self.assertEqual(sum(c[0] == "post" for c in self.http.calls), 1)

    def test_completion_failure_reports_unknown_without_iam_retry(self):
        self.http.fail_outcome = True
        adapter = MemoryAdapter()
        with self.assertRaises(ChangeUnconfirmed) as caught:
            self.apply(adapter)
        self.assertEqual(caught.exception.stage, "completion_audit")
        self.assertEqual(len(adapter.writes), 1)
        self.assertEqual(len(self.http.objects), 1)

    def test_iam_timeout_persists_unconfirmed_outcome(self):
        adapter = MemoryAdapter()
        adapter.write_error = True
        with self.assertRaises(ChangeUnconfirmed):
            self.apply(adapter)
        events = [json.loads(data)["event"] for _, data in self.http.objects.values()]
        self.assertEqual(events[-1]["phase"], "unconfirmed")
        self.assertEqual(events[-1]["stage"], "write")
        self.assertEqual(len(adapter.writes), 1)

    def test_noop_still_records_intent_and_verified(self):
        adapter = MemoryAdapter()
        from .iap_policy import propose
        adapter.policy = propose(adapter.policy, "tester@example.com", "grant")
        self.apply(adapter)
        self.assertFalse(adapter.writes)
        self.assertEqual(len(self.http.objects), 2)
        for _, data in self.http.objects.values():
            self.assertFalse(json.loads(data)["event"]["changed"])

    def test_duplicate_intent_or_terminal_cannot_overwrite(self):
        self.audit.append(self.event)
        snapshot = deepcopy(self.http.objects)
        with self.assertRaises(AuditError):
            self.audit.append(self.event)
        self.assertEqual(self.http.objects, snapshot)
        self.audit.append(completion(self.event))
        snapshot = deepcopy(self.http.objects)
        other = {k: v for k, v in self.event.items() if k != "changed"}
        other.update(phase="unconfirmed", stage="readback")
        with self.assertRaises(AuditError):
            self.audit.append(other)
        self.assertEqual(self.http.objects, snapshot)

    def test_terminal_requires_matching_intent(self):
        with self.assertRaisesRegex(AuditError, "intent_missing"):
            self.audit.append(completion(self.event))
        self.assertEqual(self.http.objects, {})
        self.audit.append(self.event)
        other = completion(self.event)
        other["member"] = "user:another@example.com"
        with self.assertRaisesRegex(AuditError, "intent_missing"):
            self.audit.append(other)
        self.assertEqual(len(self.http.objects), 1)

    def test_tampered_intent_envelope_rejected(self):
        self.audit.append(self.event)
        key = PREFIX + self.event["operation_id"] + "/intent.json"
        generation, data = self.http.objects[key]
        envelope = json.loads(data)
        envelope["schema"] = "wrong"
        self.http.objects[key] = (generation, json.dumps(envelope).encode())
        with self.assertRaisesRegex(AuditError, "intent_missing"):
            self.audit.append(completion(self.event))

    def test_exact_whitelist_rejects_credentials_scope_or_invalid_claims(self):
        for change in ({"password": "secret"}, {"token": "secret"},
                       {"actor": "unverified"}, {"actor": "owner@example.com|other:admin"},
                       {"resource": "other"}, {"role": "roles/owner"},
                       {"operation_id": "../../escape"}, {"changed": 1},
                       {"changed": False}, {"member": "allUsers"},
                       {"before_sha256": "not-a-hash"}):
            with self.subTest(change=change), self.assertRaises(AuditError):
                self.audit.append(dict(self.event, **change))
        self.assertEqual(self.http.calls, [])
        invalid = completion(self.event)
        invalid["effective_access_verified"] = True
        with self.assertRaises(AuditError):
            self.audit.append(invalid)

    def test_key_scope_size_and_generation_rejected_before_http(self):
        for key in ("clinical/intent.json", PREFIX + "../intent.json", PREFIX + "a" * 36 + "/secrets.json"):
            with self.assertRaises(AuditError):
                self.objects.create(key, b"{}")
        key = PREFIX + self.event["operation_id"] + "/intent.json"
        for data in (b"", b"x" * 16385, "not-bytes"):
            with self.assertRaises(AuditError):
                self.objects.create(key, data)
        for generation in (0, "0", "-1", "1&alt=media"):
            with self.assertRaises(AuditError):
                self.objects.read(key, generation=generation)
        self.assertEqual(self.http.calls, [])

    def test_wrong_bucket_receipt_not_accepted(self):
        self.http.bad_metadata = True
        with self.assertRaises(AuditError):
            self.audit.append(self.event)
        self.assertEqual(len(self.http.calls), 1)

    def test_naive_clock_prevents_write(self):
        self.audit.clock = lambda: datetime(2026, 10, 11)
        with self.assertRaisesRegex(AuditError, "clock"):
            self.audit.append(self.event)
        self.assertEqual(self.http.calls, [])


if __name__ == "__main__":
    unittest.main()
