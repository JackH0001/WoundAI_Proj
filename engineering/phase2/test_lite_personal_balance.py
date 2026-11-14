"""Signed personal balance, including no-work, exhausted, stale, and privacy cases."""
from unittest import TestCase
from unittest.mock import patch
import test_lite_native_backend as fixtures
from test_lite_signed_client import SignedServiceClient
from test_lite_subscription_grace import rows
from lite_usage_ledger import free_grant, DailyGrant
from lite_attest_state import StateUnavailable


class BalanceHTTPTests(SignedServiceClient, TestCase):
    initialize = fixtures.NativeBackendTests.initialize
    post = fixtures.NativeBackendTests.post
    login = fixtures.NativeBackendTests.login

    @classmethod
    def setUpClass(cls): fixtures.NativeBackendTests.setUpClass()
    @classmethod
    def tearDownClass(cls): fixtures.NativeBackendTests.tearDownClass()
    def setUp(self): self.initialize()

    def read(self, **fields):
        account = self.login_result["account_sha256"] if hasattr(self, "login_result") else "a"*64
        return self.post("service/balance", **({"account_sha256":account} | fields))

    def protected_rows(self):
        return [rows(v) for v in (self.h.release.ledger.store,self.backend.operations.store,self.h.privacy.store)]

    def test_query_new_account_returns_two_without_creating_work_or_ledger_row(self):
        self.login()
        before = self.protected_rows()
        first = self.read()
        self.assertEqual(first.status_code,200,first.json)
        self.assertEqual([first.json[k] for k in ("limit","used","pending","remaining")], [2,0,0,2])
        self.assertEqual(self.read().json, first.json)
        self.assertEqual(first.headers["Cache-Control"],"no-store")
        self.assertEqual(self.protected_rows(),before)
        self.assertEqual(self.h.model_calls,0)

    def test_reserved_then_completed_work_is_visible_and_replay_never_deducts(self):
        self.login()
        self.assertEqual(self.json_request("prepare").status_code,200)
        before=self.protected_rows()
        data=self.read().json
        self.assertEqual((data["used"],data["pending"],data["remaining"]), (0,1,1))
        self.assertEqual(self.protected_rows(),before)
        self.assertEqual(self.run_request().status_code,200)
        before=self.protected_rows()
        data=self.read().json
        self.assertEqual((data["used"],data["pending"],data["remaining"]), (1,0,1))
        self.assertEqual(self.read().json,data)
        self.assertEqual(self.protected_rows(),before)
        self.assertEqual(self.h.model_calls,1)

    def test_unbound_unsigned_or_other_account_cannot_read_usage(self):
        self.assertEqual(self.read().status_code,401)
        self.login()
        with patch.object(self.backend.operations.accounts, "grant",side_effect=AssertionError("no entitlement lookup for foreign account")):
            self.assertEqual(self.read(account_sha256="b"*64).status_code,409)
        self.assertEqual(self.client.post("/api/v1/lite/service/balance",json={}).status_code,401)
        self.assertEqual(self.read(operation="c"*32).status_code,409)

    def test_plus_is_daily_total_and_downgrade_preserves_used(self):
        self.login();self.complete()
        def plus(account,now):
            return DailyGrant(account["subject"],10,"b"*64,now,now+30)
        before=self.protected_rows()
        with patch.object(self.backend.operations.accounts,"grant",side_effect=plus) as grant:
            paid=self.read()
        self.assertEqual(paid.status_code,200,paid.json)
        self.assertEqual((paid.json["limit"],paid.json["remaining"]), (10,9))
        grant.assert_called_once()
        back=self.read().json
        self.assertEqual((back["limit"],back["remaining"]),(2,1))
        self.assertEqual(self.protected_rows(),before)

    def test_expired_reservation_preview_changes_no_stored_history(self):
        self.login()
        r=self.json_request("prepare");self.assertEqual(r.status_code,200,r.json)
        ledger=self.h.release.ledger
        account=self.backend.operations.accounts.registration(self.owner)
        day=self.h.clock()//86400
        _,saved=ledger._load(account["subject"],day)
        deadline=saved["policy"]["reservation_seconds"]
        before=self.protected_rows()
        with patch.object(ledger,"clock",return_value=self.h.clock()+deadline+1):
            now=ledger.now()
            result=ledger.current_balance(account["subject"],free_grant(account["subject"],now))
        self.assertEqual((result["pending"],result["remaining"]),(0,2))
        self.assertEqual(self.protected_rows(),before)

    def test_missing_untrusted_baseline_or_clock_crossing_midnight_fails_closed(self):
        from lite_usage_ledger import UsageLedger
        self.login()
        subject=self.backend.operations.accounts.registration(self.owner)["subject"]
        now=self.h.clock()
        empty=UsageLedger(self.h.stores["operations"],namespace="balance-test",clock=lambda:now)
        with self.assertRaises(StateUnavailable):
            empty.current_balance(subject,free_grant(subject,now))
        midnight=(now//86400+1)*86400
        ledger=self.h.release.ledger
        clock = iter([midnight-1])
        with patch.object(ledger,"now",side_effect=lambda:next(clock,midnight+1)), self.assertRaises(StateUnavailable):
            ledger.current_balance(subject,free_grant(subject,midnight-1))

    def test_verified_purchase_and_independent_research_withdrawal(self):
        self.h.privacy.withdraw(self.owner)
        previous=rows(self.h.privacy.store)
        self.login(); self.complete(1); self.complete(2)
        self.assertEqual(self.read().json["remaining"],0)
        context=self.post("purchase/context",operation="c"*32)
        self.assertEqual(context.status_code,200,context.json)
        apple=self.h.release.n.a
        apple.payload["appAccountToken"]=context.json["app_account_token"]
        reply=self.post("purchase/confirm",operation="c"*32,signed_transaction=apple.sign(apple.payload))
        self.assertEqual(reply.status_code,200,reply.json)
        before=self.protected_rows(); balance=self.read()
        self.assertEqual(balance.status_code,200,balance.json)
        self.assertEqual((balance.json["limit"],balance.json["used"],balance.json["remaining"]),(10,2,8))
        self.assertEqual(self.protected_rows(),before)
        self.assertEqual(previous,rows(self.h.privacy.store))
        self.assertEqual(self.h.model_calls,2)

    def test_attempt_limit_can_block_work_with_successes_remaining(self):
        from lite_usage_ledger import UsageLedger
        self.login()
        account=self.backend.operations.accounts.registration(self.owner)["subject"]
        now=self.h.clock()
        legacy=UsageLedger(self.h.stores["operations"],namespace="previous-usage",clock=lambda:now)
        legacy.initialize_day(account,now//86400,baseline_successes=1,baseline_attempts=30,manifest_sha256="a"*64)
        before=rows(legacy.store)
        receipt=legacy.current_balance(account,free_grant(account,now))
        self.assertEqual((receipt["remaining"],receipt["attempt_limit_reached"]),(1,True))
        self.assertEqual(rows(legacy.store),before)

    def test_deletion_during_entitlement_check_never_returns_balance(self):
        self.login()
        registry=self.backend.operations.accounts
        original=registry.grant
        def closing(account,now):
            grant=original(account,now)
            self.backend.deletion.begin(self.owner,"c"*32,withdraw_research=True)
            return grant
        with patch.object(registry,"grant",side_effect=closing):
            reply=self.read()
        self.assertNotEqual(reply.status_code,200)
        self.assertNotIn("remaining",reply.json)
        self.assertEqual(self.h.model_calls,0)

    def test_closed_account_cannot_refresh_saved_balance(self):
        self.login()
        self.assertEqual(self.read().status_code,200)
        self.backend.deletion.begin(self.owner,"c"*32,withdraw_research=True)
        self.assertNotEqual(self.read().status_code,200)
        self.assertEqual(self.h.model_calls,0)
