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


class MultipleDeviceTests(TestCase):
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
        self.others = [f"{i:032x}" for i in range(10, 15)]
        for device in self.others:
            self.x.f.f.login(device)
        self.x.begin()
        self.resolver = Reconsent(self.x.privacy)
        self.links = StoredResearchAssociations(*self.x.pub, native_audit=self.x.f.d.audit,
                                               research_state=self.x.privacy.manifest.store)

    def worker(self):
        return AccountResearchClosure(deletion=self.x.f.d, research=self.resolver,
            store=self.x.f.store("multi-closure"), audit_store=self.x.f.store("multi-audit"), associations=self.links)

    def confirm(self):
        reply = self.x.research.post(ACCEPT, ticket=self.x.ticket())
        self.assertEqual(reply.status_code, 200, reply.json)

    def test_other_unconfirmed_devices_do_not_keep_verified_requester_writable(self):
        self.confirm()
        for _ in range(2*(len(self.others)+1)):
            w = self.worker()
            with patch.object(w, "_node", wraps=w._node) as attempts:
                result = w.advance(self.x.f.person, limit=1)
            self.assertLessEqual(attempts.call_count, 1)
            self.assertEqual(result["research_admission"], "pending")
            if self.resolver.retirement(self.x.research.owner):
                break
        else:
            self.fail("a verified withdrawal remained writable")
        self.assertEqual(result["examined_grants"], 0)
        self.assertTrue(self.resolver.privacy.is_withdrawn(self.x.research.owner))
        self.assertTrue(all(self.resolver.retirement(owner) is None for owner in self.others))

    def test_repeated_scans_detect_proof_arriving_after_previous_full_pass(self):
        for _ in range(5):
            self.worker().advance(self.x.f.person, limit=4)
        self.assertIsNone(self.resolver.retirement(self.x.research.owner))
        self.confirm()
        for _ in range(5):
            self.worker().advance(self.x.f.person, limit=4)
        self.assertIsNotNone(self.resolver.retirement(self.x.research.owner))
        self.assertFalse(self.worker().status(self.x.f.person)["scan_finished"])

    def test_late_evidence_for_all_devices_counts_each_grant_exactly_once(self):
        self.confirm()
        for _ in range(3):
            self.worker().advance(self.x.f.person, limit=4)
        # Other devices' independent issuer signatures are generated only for
        # this private-worker fixture; the phone test covers the actual requester.
        for i, device in enumerate(self.others):
            target = f"{i+100:032x}"
            pair = dict(action="withdraw-linked-research/1", scope=self.x.f.d.scope, bucket=self.x.bucket.name,
                        person=self.x.f.person, native=device, research=target, operation=self.x.operation,
                        registration=self.x.f.d.bridge.registry.registration(device)["proof"])
            proof = dict(pair=pair)
            for role, seed in zip(("native","research"),self.x.seeds):
                proof[role] = base64.b64encode(seed.sign(self.links.message(role,pair))).decode()
            native_key = object_key("native", [pair[k] for k in ("scope","person","native","operation")])
            research_key = object_key("research", [pair[k] for k in ("scope","bucket","research")])
            self.assertTrue(self.x.f.d.audit.compare_exchange(native_key,0,{k:proof[k] for k in ("pair","native")}))
            self.assertTrue(self.x.privacy.manifest.store.compare_exchange(research_key,0,proof))
        for _ in range(12):
            result = self.worker().advance(self.x.f.person, limit=1)
            if result["scan_finished"]:
                break
        self.assertEqual(result["linked_grants"], len(self.others)+1, result)
        self.assertEqual(result["research_admission"], "linked_installations_closed")
        self.assertEqual(self.worker().advance(self.x.f.person), result)

    def test_repeated_unresolved_cycles_do_not_append_unbounded_audit_records(self):
        from test_lite_subscription_grace import rows
        def passes():
            for _ in range(8):
                self.worker().advance(self.x.f.person, limit=5)
        passes()
        first = rows(self.worker().audit)
        passes()
        self.assertEqual(rows(self.worker().audit), first)
        self.assertTrue(all(self.resolver.retirement(owner) is None for owner in self.others))

    def test_corrupt_survey_state_stops_without_touching_unproven_account(self):
        from lite_subscription_executor import key
        from lite_usage_ledger import digest
        w = self.worker()
        w.advance(self.x.f.person, limit=2)
        plan = w._plan(self.x.f.person)
        identity = key("account-research-survey", digest(plan))
        revision, state = w.store.load(identity)
        self.assertTrue(w.store.compare_exchange(identity, revision, dict(state, cursor="0"*64)))
        with self.assertRaises(StateUnavailable):
            w.advance(self.x.f.person, limit=1)
        self.assertIsNone(self.resolver.retirement(self.x.research.owner))

    def test_replay_after_survey_checkpoint_ack_loss_does_not_skip_work(self):
        from lite_subscription_executor import key
        from lite_usage_ledger import digest
        w = self.worker()
        identity = key("account-research-survey", digest(w._plan(self.x.f.person)))
        write = w.store.compare_exchange
        def lose_response(item, revision, value):
            done = write(item, revision, value)
            if item == identity:
                raise StateUnavailable("synthetic lost acknowledgement")
            return done
        self.confirm()
        with patch.object(w.store, "compare_exchange", side_effect=lose_response):
            for _ in range(6):
                result = w.advance(self.x.f.person, limit=3)
        self.assertIsNotNone(self.resolver.retirement(self.x.research.owner))
        self.assertFalse(result["account_deleted"])


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
