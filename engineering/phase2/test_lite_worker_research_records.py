"""Read withdrawal evidence from the actual issuer stores, without a proof export."""
import base64
from copy import deepcopy
from unittest import TestCase
from unittest.mock import patch

import test_lite_disposition_startup as startup_tests
import test_lite_native_purchase as native_tests
import test_lite_research_issuer as issuer_tests
from lite_account_research_closure import AccountResearchClosure
from lite_attest_state import StateUnavailable
from lite_disposition_startup import research_associations
from lite_reconsent import Reconsent, ReconsentRejected
from lite_research_association import ResearchLinkPending, object_key
from lite_research_issuer import ACCEPT
from lite_store_locations import LimitedStateStore
from lite_stored_research_associations import StoredResearchAssociations


class RecordsTests(TestCase):
    @classmethod
    def setUpClass(cls):
        native_tests.legacy.LinksTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        native_tests.legacy.LinksTests.tearDownClass()

    def setUp(self):
        self.x = issuer_tests.IssuanceTests()
        self.x.setUp()
        self.addCleanup(self.x.doCleanups)
        self.x.begin()
        self.f = self.x.f
        self.scope = self.f.d.scope
        self.research = self.x.research.owner
        self.native = self.x.native.owner
        self.registration = self.f.d.bridge.registry.registration(self.native)["proof"]
        self.args = (self.scope, self.x.bucket.name, self.f.person, self.native, self.x.operation, self.registration)
        self.resolver = Reconsent(self.x.privacy)
        self.native_store = self.f.d.audit
        self.research_store = self.x.privacy.manifest.store
        self.links = self.reader()

    def reader(self):
        # GET-only views make an unintended write fail even inside a test.
        return StoredResearchAssociations(*self.x.pub, native_audit=LimitedStateStore(self.native_store, mode="read"),
                                          research_state=LimitedStateStore(self.research_store, mode="read"))

    def worker(self):
        return AccountResearchClosure(deletion=self.f.d, research=self.resolver, store=self.f.store("closure"),
            audit_store=self.f.store("closure-audit"), associations=self.reader())

    def save_offer_and_accept(self):
        ticket = self.x.ticket()
        reply = self.x.research.post(ACCEPT, ticket=ticket)
        self.assertEqual(reply.status_code, 200, reply.json)
        return ticket, reply.json

    def test_separate_http_confirmations_restart_worker_from_pending_and_preserve_media(self):
        w = self.worker()
        self.assertEqual(w.advance(self.f.person)["waiting_for"], "verified_research_link_required")
        ticket = self.x.ticket()
        self.assertEqual(w.advance(self.f.person)["waiting_for"], "verified_research_link_required")
        scope = self.resolver.resolve(self.research, "legacy")
        write = scope.begin_write(self.research)
        scope.media(self.research, write).put_blob("lite/"+self.research+"/"+"a"*16+".jpg", self.f.capture)
        scope.finish_write(write)
        media = deepcopy((self.x.bucket.live, self.x.bucket.history))
        receipt = self.x.research.post(ACCEPT, ticket=ticket).json
        result = self.worker().advance(self.f.person)
        self.assertEqual(result["research_admission"], "linked_installations_closed")
        self.assertEqual(self.worker().advance(self.f.person), result)
        self.assertEqual(self.links.resolve(*self.args), (self.research, receipt["receipt_sha256"]))
        with self.assertRaises(ReconsentRejected):
            self.resolver.resolve(self.research, "legacy").begin_write(self.research)
        unrelated = self.resolver.resolve(self.native, "legacy")
        unrelated.finish_write(unrelated.begin_write(self.native))
        self.assertEqual(media, (self.x.bucket.live, self.x.bucket.history))
        self.assertFalse(result["account_deleted"])
        self.assertEqual(self.f.calls, 0)

    def test_resolves_by_two_exact_keys_no_discovery_or_write(self):
        self.save_offer_and_accept()
        native_key = object_key("native", [self.scope, self.f.person, self.native, self.x.operation])
        research_key = object_key("research", [self.scope, self.x.bucket.name, self.research])
        with patch.object(self.links, "load_native", wraps=self.links.load_native) as n, \
             patch.object(self.links, "load_research", wraps=self.links.load_research) as r:
            self.links.resolve(*self.args)
        n.assert_called_once_with(native_key)
        r.assert_called_once_with(research_key)

    def test_missing_or_invalid_native_authority_prevents_research_lookup(self):
        ticket = self.x.ticket()
        bad = deepcopy(ticket["packet"])
        bad["pair"]["research"] = "e"*32
        with patch.object(self.links, "load_research", side_effect=AssertionError("untrusted research lookup")):
            with patch.object(self.links, "load_native", return_value=None), self.assertRaises(ResearchLinkPending):
                self.links.resolve(*self.args)
            for row in [(1, bad), (1, {**ticket["packet"], "native": []}), (1, {"pair": None})]:
                with self.subTest(row=list(row[1])), patch.object(self.links, "load_native", return_value=row):
                    with self.assertRaises(StateUnavailable) as err:
                        self.links.resolve(*self.args)
                    self.assertNotIsInstance(err.exception, ResearchLinkPending)
            for i in (1, 5):
                fields = list(self.args)
                fields[i] = "f"*64
                with self.assertRaises(StateUnavailable):
                    self.links.resolve(*fields)

    def test_conflicting_research_row_or_bad_signature_never_authorizes_retirement(self):
        ticket, _ = self.save_offer_and_accept()
        key = object_key("research", [self.scope, self.x.bucket.name, self.research])
        generation, row = self.research_store.load(key)
        for bad in [ticket["packet"], dict(row, research=row["native"]), dict(row, native=row["research"]),
                    dict(row, pair={**row["pair"], "native": "a"*32}), None]:
            w = self.worker()
            with patch.object(w.associations, "load_research", return_value=(generation, bad)), self.assertRaises(StateUnavailable) as err:
                w.advance(self.f.person)
            self.assertNotIsInstance(err.exception, ResearchLinkPending)
            self.assertIsNone(self.resolver.retirement(self.research))

    def test_read_failure_is_not_missing_and_can_resume_same_operation(self):
        self.save_offer_and_accept()
        w = self.worker()
        with patch.object(w.associations, "load_research", side_effect=StateUnavailable("lost read")):
            with self.assertRaisesRegex(StateUnavailable, "lost read"):
                w.advance(self.f.person)
        self.assertIsNone(self.resolver.retirement(self.research))
        self.assertEqual(self.worker().advance(self.f.person)["linked_grants"], 1)

    def test_same_backing_store_or_missing_store_is_refused(self):
        spec = dict(source="issuer-records/1", native_key=base64.b64encode(self.x.pub[0]).decode(),
                    research_key=base64.b64encode(self.x.pub[1]).decode())
        with self.assertRaises(ValueError):
            research_associations(spec)
        with self.assertRaises(ValueError):
            research_associations(spec, native_audit=self.native_store, research_state=self.native_store)


class WorkerConfigTests(TestCase):
    @classmethod
    def setUpClass(cls):
        native_tests.legacy.LinksTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        native_tests.legacy.LinksTests.tearDownClass()

    def test_actual_factory_reads_late_proof_from_pinned_namespaces_without_restart(self):
        fixture = startup_tests.Fixture(self)
        cfg = fixture.c["research_associations"]
        proof, = cfg.pop("proofs")
        cfg["source"] = "issuer-records/1"
        snap = fixture.snap()
        executor = fixture.build()
        self.assertEqual(fixture.snap(), snap)
        closure = executor.w.r.w.c
        self.assertIsInstance(closure.associations, StoredResearchAssociations)
        self.assertEqual(closure.advance(fixture.person)["waiting_for"], "verified_research_link_required")
        pair = proof["pair"]
        native = object_key("native", [pair["scope"], pair["person"], pair["native"], pair["operation"]])
        research = object_key("research", [pair["scope"], pair["bucket"], pair["research"]])
        self.assertTrue(fixture.stores["deletion_audit"].compare_exchange(native, 0, {k:proof[k] for k in ("pair","native")}))
        self.assertEqual(closure.advance(fixture.person)["waiting_for"], "verified_research_link_required")
        self.assertTrue(fixture.stores["research"].compare_exchange(research, 0, proof))
        result = closure.advance(fixture.person)
        self.assertTrue(result["scan_finished"], result)
        self.assertEqual(result["linked_grants"], 1, result)
        # The independent legacy grant has no valid installation ID.
        self.assertEqual(result["unresolved_grants"], 1, result)
        self.assertEqual(result["research_admission"], "pending")
        self.assertTrue(closure.r.privacy.is_withdrawn(pair["research"]))

    def test_mixed_or_invalid_modes_rejected_before_any_provider_call(self):
        fixture = startup_tests.Fixture(self)
        base = fixture.c["research_associations"]
        for spec in [dict(base, source="issuer-records/1"), dict(base, source="autodiscover"),
                     {**{k:base[k] for k in ("native_key","research_key")}, "source":"future"},
                     dict(native_key=base["native_key"],research_key=base["native_key"],source="issuer-records/1"),
                     dict(native_key=base["native_key"],research_key=fixture.c["signers"]["drain"]["observer"],source="issuer-records/1")]:
            fixture.c["research_associations"] = spec
            with self.subTest(fields=spec.keys()), self.assertRaises(ValueError):
                fixture.build()
            self.assertEqual(fixture.providers.calls, [])
