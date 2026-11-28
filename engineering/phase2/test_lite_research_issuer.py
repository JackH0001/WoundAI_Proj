"""Two real signed synthetic installations and independent origins. No live services."""
import base64
import copy
import hashlib
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from flask import Flask

import test_lite_attest_enrollment as enrollment_tests
import test_lite_fenced_objects as object_tests
import test_lite_native_purchase as native_tests
from test_lite_grant_key_custody import KeyFixture
from test_lite_signed_client import SignedHTTPClient
from lite_account_research_closure import AccountResearchClosure
from lite_attest_budget import RequestBudget
from lite_attest_enrollment import RegistrationService
from lite_attest_http import InternalOwner, install_lite_attest
from lite_account_deletion import LINKED_WITHDRAWAL
from lite_attest_state import StateUnavailable
from lite_disposition_progress_probe import ProgressFixture
from lite_fenced_store import FencedPrivacy
from lite_privacy_state import PrivacyState
from lite_reconsent import Reconsent, ReconsentRejected
from lite_research_association import ResearchAssociations
from lite_research_issuer import ACCEPT, CONFIRM, OFFER, TTL, LinkSigner, object_key, settings
from lite_service_profile import install_lite_perimeter


class Phone(SignedHTTPClient):
    def __init__(self, host, audience):
        self.audience = audience
        self.assertEqual = host.assertEqual
        self.e = enrollment_tests.EnrollmentTests()
        self.e.engine = lambda store: RegistrationService(
            store, policy=self.e.r.policy, audience=audience, clock=lambda: self.e.now)
        self.e.setUp()
        host.addCleanup(self.e.doCleanups)
        self.owner = self.e.register().installation
        self.key = base64.b64encode(self.e.key_id).decode()
        self.counter = 0

    def post(self, path, **fields):
        raw = json.dumps({"anon_id": self.owner, "confirmation": CONFIRM, **fields}).encode()
        headers = self.headers(path, raw, "application/json", purpose="withdraw")
        return self.client.post(path, data=raw, headers=headers, content_type="application/json")


class IssuanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        native_tests.legacy.LinksTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        native_tests.legacy.LinksTests.tearDownClass()

    def setUp(self):
        self.native = Phone(self, "native-link-fixture")
        self.research = Phone(self, "research-link-fixture")
        self.assertNotEqual(self.native.owner, self.research.owner)
        self.operation = "d" * 32
        login = KeyFixture.login
        with patch.object(KeyFixture, "login", lambda f: login(f, self.native.owner)):
            self.f = ProgressFixture(self)
        self.bucket = object_tests.Bucket()
        self.privacy = FencedPrivacy(self.f.store("research"), self.bucket)
        self.seeds = [Ed25519PrivateKey.generate() for _ in range(2)]
        self.pub = [k.public_key().public_bytes_raw() for k in self.seeds]
        self.now = 1500
        self.install_services()

    def install_services(self, *, native_owner=None, research_owner=None):
        for idx, (role, phone) in enumerate((("native", self.native), ("research", self.research))):
            signer = LinkSigner(role=role, scope=self.f.d.scope, bucket=self.bucket.name,
                                native_key=self.pub[0], research_key=self.pub[1],
                                private_key=self.seeds[idx].private_bytes_raw(), clock=lambda: self.now)
            app = Flask(role, static_folder=None)
            if role == 'research':
                install_lite_perimeter(app)
            budget = RequestBudget(self.f.store(role + "-budget"), minute_limit=500, day_limit=5000)
            extras = (dict(commerce=self.f.ops, identity=self.f.f.b, deletion=self.f.d,
                           privacy=PrivacyState(self.f.store("native-privacy"))) if role == "native"
                      else dict(privacy=self.privacy))
            internal_owner = native_owner if role == "native" else research_owner
            install_lite_attest(app, service=phone.e.service, budget=budget, link_signer=signer,
                                internal_owner=internal_owner, **extras)
            phone.client = app.test_client()

    def begin(self):
        self.f.d.begin(self.native.owner, self.operation, withdraw_research=True)

    def test_proof_requires_signature_and_does_not_enable_capture(self):
        self.assertEqual(self.research.client.post(ACCEPT, json={}).status_code, 401)
        for path in (OFFER, '/api/v1/lite/research/capture', '/api/v1/lite/research/capture/status'):
            self.assertEqual(self.research.client.post(path, json={}).status_code, 404)

    def offer(self, **changes):
        fields = dict(operation=self.operation, research=self.research.owner)
        return self.native.post(OFFER, **(fields | changes))

    def ticket(self):
        reply = self.offer()
        self.assertEqual(reply.status_code, 200, reply.json)
        return reply.json

    def test_original_requester_can_finish_link_after_both_services_restrict_new_work(self):
        self.install_services(native_owner=InternalOwner("native", "1"*64, "2"*64),
                              research_owner=InternalOwner("research", "3"*64))
        accepted = self.native.post("/api/v1/lite/account/deletion/request", operation=self.operation,
                                    confirmation=LINKED_WITHDRAWAL)
        self.assertEqual(accepted.status_code, 202, accepted.json)
        self.assertEqual(accepted.json["research_withdrawal"], "requested")
        ticket = self.ticket()
        reply = self.research.post(ACCEPT, ticket=ticket)
        self.assertEqual(reply.status_code, 200, reply.json)
        self.assertFalse(reply.json["cleanup_confirmed"])
        self.assertEqual(self.research.post(ACCEPT, ticket=ticket).json, reply.json)
        for phone, path in ((self.native, "/api/v1/lite/service/prepare"),
                            (self.research, "/api/v1/lite/research/rejoin")):
            with self.subTest(path=path):
                raw = json.dumps({"anon_id": phone.owner, "operation": self.operation}).encode()
                blocked = phone.post_signed(path, raw)
                self.assertEqual(blocked.status_code, 403, blocked.json)
                self.assertEqual(blocked.json["error"], "lite_internal_owner_required")

    def test_research_service_accepts_outstanding_offer_after_pause(self):
        self.begin()
        ticket = self.ticket()
        self.install_services(research_owner=InternalOwner("research", "3"*64))
        key = object_key("research", [self.f.d.scope, self.bucket.name, self.research.owner])
        self.assertIsNone(self.privacy.manifest.store.load(key))
        first = self.research.post(ACCEPT, ticket=ticket)
        self.assertEqual(first.status_code, 200, first.json)
        proof = self.privacy.manifest.store.load(key)
        self.assertIsNotNone(proof)
        self.now += TTL + 1
        self.assertEqual(self.research.post(ACCEPT, ticket=ticket).json, first.json)
        self.assertEqual(self.privacy.manifest.store.load(key), proof)

    def test_restricted_service_requires_original_confirmed_deletion_for_offer(self):
        self.install_services(native_owner=InternalOwner("native", "1"*64, "2"*64))
        self.f.d.begin(self.native.owner, self.operation, withdraw_research=False)
        reply = self.offer()
        self.assertEqual(reply.status_code, 409, reply.json)
        self.assertEqual(reply.json["error"], "lite_research_link_conflict")
        self.assertIsNone(self.f.d.audit.load(
            object_key("native", [self.f.d.scope, self.f.person, self.native.owner, self.operation])))

    def test_authenticated_proof_reaches_worker_for_correct_owner_only(self):
        self.begin()
        resolver = Reconsent(self.privacy)
        scope = resolver.resolve(self.research.owner, "legacy")
        write = scope.begin_write(self.research.owner)
        scope.media(self.research.owner, write).put_blob("lite/"+self.research.owner+"/"+"f"*16+".jpg", self.f.capture)
        scope.finish_write(write)
        objects = copy.deepcopy((self.bucket.live, self.bucket.history))
        reply = self.research.post(ACCEPT, ticket=self.ticket())
        self.assertEqual(reply.status_code, 200, reply.json)
        self.assertFalse(reply.json["cleanup_confirmed"])
        key = object_key("research", [self.f.d.scope, self.bucket.name, self.research.owner])
        _, proof = self.privacy.manifest.store.load(key)
        verified = ResearchAssociations(*self.pub, [proof])
        owner, receipt = verified.resolve(self.f.d.scope,self.bucket.name,self.f.person,self.native.owner,
                                          self.operation,proof["pair"]["registration"])
        self.assertEqual((owner,receipt),(self.research.owner,reply.json["receipt_sha256"]))
        worker = AccountResearchClosure(deletion=self.f.d, research=resolver,
            store=self.f.store("closure"), audit_store=self.f.store("closure-audit"), associations=verified)
        self.assertEqual(worker.advance(self.f.person)["research_admission"], "linked_installations_closed")
        with self.assertRaises(ReconsentRejected):
            resolver.resolve(self.research.owner, "legacy").begin_write(self.research.owner)
        other = resolver.resolve(self.native.owner, "legacy")
        other.finish_write(other.begin_write(self.native.owner))
        self.assertEqual(objects, (self.bucket.live, self.bucket.history))
        self.assertEqual(self.f.calls, 0)

    def test_expired_offer_without_receipt_requires_new_offer(self):
        self.begin()
        ticket = self.ticket()
        self.now += TTL
        self.assertEqual(self.research.post(ACCEPT,ticket=ticket).status_code,410)
        fresh = self.ticket()
        self.assertEqual(fresh["packet"],ticket["packet"])
        self.assertEqual(self.research.post(ACCEPT,ticket=fresh).status_code,200)

    def test_lost_success_replayed_after_expiry_without_second_write(self):
        self.begin()
        ticket = self.ticket()
        first = self.research.post(ACCEPT,ticket=ticket)
        self.assertEqual(first.status_code,200,first.json)
        key = object_key("research",[self.f.d.scope,self.bucket.name,self.research.owner])
        before = self.privacy.manifest.store.load(key)
        self.now += TTL + 1
        again = self.research.post(ACCEPT,ticket=ticket)
        self.assertEqual(again.json, first.json)
        self.assertEqual(before, self.privacy.manifest.store.load(key))

    def test_native_cannot_change_target_after_offer(self):
        self.begin()
        self.ticket()
        self.assertEqual(self.offer(research="b"*32).status_code,409)
        self.ticket()

    def test_confirmation_and_accepted_operation_required(self):
        self.assertNotEqual(self.offer().status_code,200)
        self.f.d.begin(self.native.owner,self.operation,withdraw_research=False)
        self.assertNotEqual(self.offer().status_code,200)

    def test_wrong_origin_owner_confirmation_or_modified_ticket_fails_closed(self):
        self.begin()
        ticket = self.ticket()
        self.assertEqual(self.native.post(ACCEPT,ticket=ticket).status_code,404)
        self.assertEqual(self.research.post(OFFER,operation=self.operation,research=self.research.owner).status_code,404)
        self.assertEqual(self.research.post(ACCEPT,ticket=ticket,anon_id=self.native.owner).status_code,403)
        self.assertEqual(self.research.post(ACCEPT,ticket=ticket,confirmation="yes").status_code,409)
        for path, value in [(("packet","pair","research"),"f"*32),(("packet","pair","operation"),"e"*32),
                            (("packet","native"),"A"*88),(("signature",),"A"*88)]:
            bad = copy.deepcopy(ticket)
            cursor = bad
            for k in path[:-1]:
                cursor = cursor[k]
            cursor[path[-1]] = value
            self.assertEqual(self.research.post(ACCEPT,ticket=bad).status_code,409)
        self.assertEqual(self.research.client.post(ACCEPT,json={"ticket":ticket}).status_code,401)
        self.assertEqual(self.privacy.manifest.store.load(object_key("research",[
            self.f.d.scope,self.bucket.name,self.research.owner])), None)


    def test_unknown_storage_response_read_back_without_second_proof(self):
        self.begin()
        ticket = self.ticket()
        store = self.privacy.manifest.store
        original = store.compare_exchange
        def lost(*args):
            original(*args)
            raise StateUnavailable("simulated lost ack")
        with patch.object(store, "compare_exchange", side_effect=lost) as write:
            first = self.research.post(ACCEPT, ticket=ticket)
        self.assertEqual(first.status_code, 200, first.json)
        write.assert_called_once()
        with patch.object(store, "compare_exchange", side_effect=AssertionError("duplicate proof")):
            self.assertEqual(self.research.post(ACCEPT, ticket=ticket).json, first.json)

    def test_racing_offers_commit_to_one_target(self):
        from concurrent.futures import ThreadPoolExecutor
        signer = LinkSigner(role="native",scope=self.f.d.scope,bucket=self.bucket.name,
            native_key=self.pub[0],research_key=self.pub[1],private_key=self.seeds[0].private_bytes_raw())
        self.begin()
        def submit(owner):
            try:
                return signer.offer(self.f.d,self.native.owner,self.operation,owner)["packet"]["pair"]["research"]
            except ValueError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(submit,[self.research.owner,"a"*32]))
        self.assertEqual(len([item for item in outcomes if item]),1)

    def test_general_purpose_challenge_cannot_sign_privacy_route(self):
        self.begin()
        body = dict(anon_id=self.native.owner,confirmation=CONFIRM,operation=self.operation,research=self.research.owner)
        raw = json.dumps(body).encode()
        headers = self.native.headers(OFFER,raw,"application/json",purpose="assert")
        response = self.native.client.post(OFFER,data=raw,headers=headers,content_type="application/json")
        self.assertEqual(response.status_code,401)

    def test_config_requires_exact_hash_distinct_authorities_and_regular_file(self):
        self.assertIsNone(settings({}, "research"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "signer.json"
            def env(cfg):
                raw = json.dumps(cfg).encode()
                path.write_bytes(raw)
                return {"LITE_RESEARCH_LINK_FILE":str(path),"LITE_RESEARCH_LINK_SHA256":hashlib.sha256(raw).hexdigest()}
            as64 = lambda data:base64.b64encode(data).decode()
            cfg = dict(schema="lite-research-issuer/1",role="research",scope=self.f.d.scope,bucket=self.bucket.name,
                       native=as64(self.pub[0]),research=as64(self.pub[1]),seed=as64(self.seeds[1].private_bytes_raw()))
            valid = env(cfg)
            self.assertEqual(settings(valid,"research").role,"research")
            link = path.with_name("link")
            link.symlink_to(path)
            for invalid in [dict(valid,LITE_RESEARCH_LINK_FILE=str(link)),dict(valid,LITE_RESEARCH_LINK_SHA256="0"*64),
                            {"LITE_RESEARCH_LINK_FILE":str(path)}]:
                with self.assertRaises(ValueError):
                    settings(invalid,"research")
            with self.assertRaises(ValueError):
                settings(env(dict(cfg,native=cfg["research"])),"research")
            with self.assertRaises(ValueError):
                settings(env(dict(cfg,seed=as64(self.seeds[0].private_bytes_raw()))),"research")
