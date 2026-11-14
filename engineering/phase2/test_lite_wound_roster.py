"""Synthetic account tracking: parallel installations, plan changes, retries and closure."""
from concurrent.futures import ThreadPoolExecutor
from unittest import TestCase
from unittest.mock import patch

import test_lite_native_backend as fixtures
from test_lite_signed_client import SignedServiceClient
from test_lite_subscription_grace import rows
from lite_usage_ledger import DailyGrant
from lite_attest_state import StateUnavailable


class WoundRosterTests(SignedServiceClient, TestCase):
    initialize = fixtures.NativeBackendTests.initialize
    post = fixtures.NativeBackendTests.post
    login = fixtures.NativeBackendTests.login

    @classmethod
    def setUpClass(cls): fixtures.NativeBackendTests.setUpClass()

    def setUp(self):
        self.initialize()
        self.login()
        self.account = self.login_result["account_sha256"]
        self.ops = self.backend.operations

    def request(self, action, n=None, rev=None, **extra):
        fields = {} if n is None else {"wound_id": f"{n:032x}", "revision": rev}
        return self.post("service/wounds", account_sha256=self.account, action=action, **fields, **extra)

    def plus(self):
        return patch.object(self.ops.accounts, "grant", side_effect=lambda a, now:
                           DailyGrant(a["subject"], 10, "b"*64, now, now+30))

    def test_free_slot_replay_finish_and_swap_preserve_service_and_research_rows(self):
        led = self.h.release.ledger.store
        before = rows(led), rows(self.h.privacy.store)
        self.assertEqual(self.request("status").json["revision"], 0)
        first = self.request("open", 1, 0)
        self.assertEqual(first.status_code, 200, first.json)
        self.assertEqual(self.request("open", 1, 0).json, first.json)
        self.assertEqual(self.request("open", 2, 1).json["decision"], "limit")
        self.assertEqual(self.request("finish", 1, 1).json["active"], [])
        swapped = self.request("open", 2, 2)
        self.assertEqual(swapped.json["active"], [f"{2:032x}"])
        self.assertEqual(self.request("open", 1, 0).json["decision"], "changed")
        self.assertEqual(before, (rows(led), rows(self.h.privacy.store)))
        self.assertEqual(self.h.model_calls, 0)

    def test_five_active_downgrade_and_restore(self):
        with self.plus():
            for n in range(1,6):
                self.assertEqual(self.request("open", n, n-1).status_code, 200)
            self.assertEqual(self.request("open", 6, 5).json["decision"], "limit")
        self.assertEqual(self.request("status").json["limit"], 1)
        self.assertEqual(len(self.request("status").json["active"]), 5)
        self.assertEqual(self.request("open", 2, 5).json["decision"], "limit")
        self.assertEqual(self.request("open", 1, 5).status_code, 200)
        with self.plus():
            self.assertEqual(self.request("open", 2, 5).status_code, 200)
            self.assertEqual(self.request("finish", 3, 5).json["revision"], 6)
            self.assertEqual(self.request("open", 6, 6).json["revision"], 7)
            self.assertEqual(self.request("finish", 6, 5).json["decision"], "changed")

    def test_signed_purchase_restore_changes_one_slot_to_five_without_resetting_usage(self):
        self.complete(1)
        self.assertEqual(self.request("open", 1, 0).json["limit"], 1)
        context = self.post("purchase/context", operation="c"*32)
        self.assertEqual(context.status_code, 200, context.json)
        apple = self.h.release.n.a
        apple.payload["appAccountToken"] = context.json["app_account_token"]
        receipt = apple.sign(apple.payload)
        confirmed = self.post("purchase/confirm", operation="c"*32, signed_transaction=receipt)
        self.assertEqual(confirmed.status_code, 200, confirmed.json)
        for n in range(2,6):
            self.assertEqual(self.request("open", n, n-1).status_code, 200)
        self.assertEqual(self.post("purchase/confirm", operation="c"*32, signed_transaction=receipt).json, confirmed.json)
        self.assertEqual(len(self.request("status").json["active"]), 5)
        balance = self.post("service/balance", account_sha256=self.account).json
        self.assertEqual((balance["limit"], balance["used"], self.h.model_calls), (10,1,1))

    def test_two_installations_compete_for_last_slot_on_same_subject(self):
        # Authorization/attestation is covered by the signed tests. Resolve two distinct
        # authenticated installations to one account and race the actual storage CAS.
        bound = self.ops.accounts.resolve(self.owner)
        with self.plus(), patch.object(self.ops.accounts, "resolve", return_value=bound):
            for n in range(1,5):
                self.ops.wound_roster(self.owner,self.account,"open",f"{n:032x}",n-1)
            def reserve(n):
                return self.ops.wound_roster(f"install-{n}",self.account,"open",f"{n:032x}",4)
            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(reserve,[5,6]))
            self.assertCountEqual([r["decision"] for r in outcomes],["confirmed","changed"])
            final = self.ops.wound_roster("device-b",self.account,"status")
            self.assertEqual((len(final["active"]),final["revision"]),(5,5))

    def test_lost_write_response_recovers_same_slot_once(self):
        life = self.ops.accounts.lifecycle
        exchange = life.store.compare_exchange
        def lose(*args):
            won = exchange(*args)
            if won: raise StateUnavailable("lost response")
            return won
        with patch.object(life.store,"compare_exchange",side_effect=lose):
            self.assertEqual(self.request("open",1,0).json["revision"],1)
        self.assertEqual(self.request("status").json["active"],[f"{1:032x}"])

    def test_foreign_account_unsigned_client_bad_body_cannot_change_slots(self):
        self.assertEqual(self.post("service/wounds",account_sha256="0"*64,action="status").status_code,409)
        self.assertEqual(self.client.post("/api/v1/lite/service/wounds",json={}).status_code,401)
        self.assertEqual(self.request("open",1,0,location="heel").status_code,409)
        self.assertEqual(self.request("open",1,False).status_code,409)
        self.assertEqual(self.request("status").json["active"],[])

    def test_account_closure_fences_late_opens(self):
        self.request("open",1,0)
        self.backend.deletion.begin(self.owner,'d'*32)
        self.assertNotEqual(self.request("open",2,1).status_code,200)
        _, root = self.ops.accounts.lifecycle._read(self.person)
        self.assertEqual(root["phase"],"closing")
        self.assertNotIn("wounds",root)
