"""Synthetic identities, real SQLite CAS; no live Apple, GCS or personal data."""
import threading
import unittest
import copy
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from lite_account_identity import AccountIdentityBridge, IdentityRejected
from lite_account_lifecycle import AccountLifecycle, AccountClosing
from lite_apple_grants import AppleGrantAuthority
from lite_apple_oauth import TOKEN_URL, REVOKE_URL, GrantUncertain
from lite_attest_state import SQLiteStateStore, StateUnavailable
from lite_subscription_executor import key
from lite_usage_ledger import digest
from test_lite_apple_grants import GrantFixture
from test_lite_account_sessions import RenewalFixture
from test_lite_subscription_grace import rows
import test_lite_native_purchase as native


class Fixture:
    def __init__(self, host, service_catalog=False):
        self.g = GrantFixture(host); self.f = self.g.f; old = self.f.b
        self.lifecycle = AccountLifecycle(store=SQLiteStateStore(self.f.h.f.root/'lifecycle.sqlite'),
            audit_store=SQLiteStateStore(self.f.h.f.root/'lifecycle-audit.sqlite'),binding=self.g.a.binding,clock=self.f.now,service_catalog=service_catalog)
        # Start an explicitly new synthetic vault. Production cannot simply
        # turn this on over an old vault without an inventory migration.
        self.g.store=SQLiteStateStore(self.f.h.f.root/'indexed-grant-vault.sqlite')
        self.g.a = self.authority()
        self.old_grants = copy.copy(self.g.a);self.old_grants.lifecycle=None
        self.f.b = AccountIdentityBridge(runtime=old.runtime,verifier=old.verifier,store=old.store,audit_store=old.audit,
            namespace=old.namespace,identity_key=old.secret,grants=self.g.a,lifecycle=self.lifecycle)
        self.b = self.f.b

    def authority(self):
        g=self.g
        return AppleGrantAuthority(provider=g.provider,verifier=g.f.verifier,store=g.store,audit_store=g.audit,
            cipher=g.cipher,namespace=g.f.b.namespace,identity_key=g.f.b.secret,lifecycle=self.lifecycle)

    def login(self, device='device-one', subject='apple-human-1'):
        self.g.prepare(device)
        self.g.signed=self.f.token(self.g.c,sub=subject);self.g.token=self.g.signed
        self.g.confirm()
        self.person=self.b._read(device)[1]['account']['identity_sha256']
        return self.g.c.copy()

    def close(self, device='device-one', operation='d'*32):
        return self.b.begin_account_closure(device,operation)


class LifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): native.legacy.LinksTests.setUpClass()
    def setUp(self): self.f=Fixture(self)

    def test_inventory_exists_before_apple_exchange(self):
        f=self.f
        def inspect(url):
            c=f.g.c
            # Signed identity is not copied into the index; membership can be
            # derived from the actual grant claim written before dispatch.
            ctx=f.g.a._row(f.g.a._id(c['installation'],c['operation']))[1]['context']
            self.assertIsNotNone(f.lifecycle.member(ctx['identity_sha256'],ctx['grant_id']))
            self.assertEqual(f.lifecycle.require_active(ctx['identity_sha256'])[1]['count'],1)
        f.g.hook=inspect;f.login();self.assertEqual(f.g.calls,[TOKEN_URL])

    def test_same_person_two_devices_and_prior_logins_all_indexed(self):
        f=self.f;first=f.login();second=f.login('device-two');third=f.login();f.close()
        page=f.lifecycle.page(f.person)
        self.assertEqual(page['total'],3)
        self.assertEqual({n['operation'] for n in page['items']},{first['operation'],second['operation'],third['operation']})

    def test_saved_confirm_does_not_append_or_reexchange(self):
        f=self.f;f.login();before=rows(f.lifecycle.store),rows(f.lifecycle.audit)
        f.g.confirm();self.assertEqual(before,(rows(f.lifecycle.store),rows(f.lifecycle.audit)))
        self.assertEqual(f.g.calls,[TOKEN_URL])

    def test_close_blocks_all_devices_new_login_and_renewal_primitives(self):
        f=self.f;f.login();f.login('device-two');r=f.close()
        for device in ('device-one','device-two'):
            with self.assertRaises(AccountClosing):f.b.require_session(device)
            with self.assertRaises(AccountClosing):f.b.challenge(device,'e'*32)
        self.assertFalse(r['account_deleted']);self.assertEqual(r['data_disposition'],'not_started')
        self.assertEqual(r['apple_revocation'],'not_completed')

    def test_close_blocks_same_person_on_new_unbound_device_before_exchange(self):
        f=self.f;f.login();f.close();f.g.prepare('device-new')
        with self.assertRaises(AccountClosing):f.g.confirm()
        self.assertEqual(f.g.calls,[TOKEN_URL])

    def test_close_one_account_does_not_block_other_person(self):
        f=self.f;f.login();first=f.person;f.login('other-person','apple-human-2');f.close()
        self.assertEqual(f.b.status('other-person')['state'],'signed_in')
        self.assertEqual(f.lifecycle._read(first)[1]['phase'],'closing')

    def test_close_retry_after_session_is_blocked_returns_same_intent(self):
        f=self.f;f.login();r=f.close();before=rows(f.lifecycle.store),rows(f.lifecycle.audit)
        self.assertEqual(f.close(),r);self.assertEqual(before,(rows(f.lifecycle.store),rows(f.lifecycle.audit)))

    def test_different_operation_cannot_rewrite_closure(self):
        f=self.f;f.login();f.close();before=rows(f.lifecycle.store)
        with self.assertRaises(IdentityRejected):f.close(operation='c'*32)
        self.assertEqual(before,rows(f.lifecycle.store))

    def test_other_device_cannot_read_requesters_deletion_receipt(self):
        f=self.f;f.login();f.login('device-two');f.close()
        with self.assertRaises(IdentityRejected):f.close(device='device-two')

    def test_inventory_uses_bounded_pages_for_more_than_100_grants(self):
        f=self.f;f.login()
        with patch.object(f.lifecycle.store,'load',wraps=f.lifecycle.store.load) as load:
            for i in range(125):f.lifecycle.reserve_grant(f.person,'synthetic-extra',f'{100+i:032x}')
            # New enrollment is constant reads, not O(number of prior grants).
            self.assertLess(load.call_count,125*9)
        f.close();items=[];cursor=None
        while True:
            p=f.lifecycle.page(f.person,cursor=cursor,limit=50);items+=p['items'];cursor=p['next_cursor']
            if cursor is None:break
        self.assertEqual(len(items),126);self.assertEqual(len({x['grant_id'] for x in items}),126)
        self.assertEqual([x['position'] for x in items],list(range(126,0,-1)))

    def test_page_requires_closed_snapshot_and_bounded_limit(self):
        f=self.f;f.login()
        with self.assertRaises(IdentityRejected):f.lifecycle.page(f.person)
        f.close()
        for limit in (0,101,True,'1'):
            with self.assertRaises(ValueError):f.lifecycle.page(f.person,limit=limit)

    def test_cursor_cannot_read_another_person(self):
        f=self.f;f.login();first=f.person;f.close();f.login('other','apple-human-2');f.close('other')
        with self.assertRaises(StateUnavailable):f.lifecycle.page(first,cursor=f.lifecycle._read(f.person)[1]['head'])

    def test_closure_commit_response_loss_recovers_same_operation(self):
        f=self.f;f.login();real=f.lifecycle.store.compare_exchange
        def lose(k,rev,v):
            r=real(k,rev,v)
            if v.get('phase')=='closing':raise StateUnavailable('response lost')
            return r
        with patch.object(f.lifecycle.store,'compare_exchange',side_effect=lose):r=f.close()
        self.assertEqual(f.close(),r)

    def test_closure_before_commit_failure_does_not_claim_done(self):
        f=self.f;f.login();real=f.lifecycle.store.compare_exchange
        def fail(k,rev,v):
            if v.get('phase')=='closing':raise StateUnavailable('write failed')
            return real(k,rev,v)
        with patch.object(f.lifecycle.store,'compare_exchange',side_effect=fail),self.assertRaises(StateUnavailable):f.close()
        self.assertEqual(f.b.status('device-one')['state'],'signed_in')
        self.assertEqual(f.close()['state'],'account_closing')

    def test_closure_audit_failure_keeps_account_closed_and_retries_receipt(self):
        f=self.f;f.login();real=f.lifecycle.audit.compare_exchange
        def fail(k,rev,v):
            if v.get('state')=='account_closing':raise StateUnavailable('audit unavailable')
            return real(k,rev,v)
        with patch.object(f.lifecycle.audit,'compare_exchange',side_effect=fail),self.assertRaises(StateUnavailable):f.close()
        with self.assertRaises(AccountClosing):f.b.require_session('device-one')
        self.assertEqual(f.close()['state'],'account_closing')

    def test_inventory_commit_response_loss_does_not_duplicate_member(self):
        f=self.f;f.login();real=f.lifecycle.store.compare_exchange
        def lose(k,rev,v):
            r=real(k,rev,v)
            if v.get('phase')=='active' and v.get('count')==2:raise StateUnavailable('lost')
            return r
        with patch.object(f.lifecycle.store,'compare_exchange',side_effect=lose):f.login('device-two')
        self.assertEqual(f.lifecycle._read(f.person)[1]['count'],2)

    def test_inventory_receipt_failure_never_dispatches_apple(self):
        f=self.f;f.login();real=f.lifecycle.audit.compare_exchange
        def fail(k,rev,v):
            if set(v)=={'v','node'}:raise StateUnavailable('membership audit unavailable')
            return real(k,rev,v)
        with patch.object(f.lifecycle.audit,'compare_exchange',side_effect=fail),self.assertRaises(StateUnavailable):f.login('device-two')
        self.assertEqual(f.g.calls,[TOKEN_URL]);f.g.confirm()
        self.assertEqual(len(f.g.calls),2);self.assertEqual(f.lifecycle._read(f.person)[1]['count'],2)

    def test_two_workers_same_grant_only_one_inventory_member(self):
        f=self.f;f.login()
        with ThreadPoolExecutor(2) as pool:
            result=list(pool.map(lambda _:f.lifecycle.reserve_grant(f.person,'extra','c'*32),range(2)))
        self.assertEqual(result[0],result[1]);self.assertEqual(f.lifecycle._read(f.person)[1]['count'],2)

    def test_parallel_different_grants_do_not_lose_members(self):
        f=self.f;f.login()
        with ThreadPoolExecutor(2) as pool:
            list(pool.map(lambda n:f.lifecycle.reserve_grant(f.person,'extra',f'{n:032x}'),[100,101]))
        f.close();self.assertEqual(len(f.lifecycle.page(f.person)['items']),3)

    def test_close_wins_during_grant_root_commit_no_external_dispatch(self):
        f=self.f;f.login();real=f.lifecycle.store.compare_exchange;fired=[]
        def race(k,rev,v):
            if v.get('count')==2 and v.get('phase')=='active' and not fired:
                fired.append(1);f.close()
            return real(k,rev,v)
        with patch.object(f.lifecycle.store,'compare_exchange',side_effect=race),self.assertRaises(AccountClosing):f.login('device-two')
        self.assertEqual(f.g.calls,[TOKEN_URL]);self.assertEqual(f.lifecycle._read(f.person)[1]['count'],1)

    def test_exchange_inflight_is_indexed_and_late_tokens_retained_for_revoke(self):
        f=self.f;f.login();start=threading.Event();finish=threading.Event()
        def wait(url):
            if url==TOKEN_URL:start.set();self.assertTrue(finish.wait(5))
        f.g.hook=wait
        with ThreadPoolExecutor(1) as pool:
            pending=pool.submit(f.login,'device-two');self.assertTrue(start.wait(5))
            try:
                r=f.close();self.assertEqual(r['grant_count'],2)
                c=f.g.c.copy()
                with self.assertRaises(GrantUncertain):f.g.a.revoke(c['installation'],c['operation'])
            finally:finish.set()
            with self.assertRaises(IdentityRejected):pending.result()
        f.g.hook=lambda _:None
        self.assertEqual(f.g.a.revoke(c['installation'],c['operation'])['state'],'apple_token_revoked')
        self.assertEqual(f.g.calls.count(TOKEN_URL),2)

    def test_closed_grants_can_still_be_revoked_by_trusted_coordinator(self):
        f=self.f;c=f.login();f.close()
        self.assertEqual(f.g.a.revoke(c['installation'],c['operation'])['state'],'apple_token_revoked')
        self.assertFalse(f.close()['account_deleted'])

    def test_no_research_quota_or_registry_mutations_from_closure(self):
        f=self.f;f.login();before=rows(f.f.h.accounts.store),rows(f.f.h.f.ledger.store)
        f.close();self.assertEqual(before,(rows(f.f.h.accounts.store),rows(f.f.h.f.ledger.store)))

    def test_active_authorization_reads_do_not_scan_full_inventory(self):
        f=self.f;f.login()
        for i in range(15):f.lifecycle.reserve_grant(f.person,'extra',f'{100+i:032x}')
        with patch.object(f.lifecycle.store,'load',wraps=f.lifecycle.store.load) as load:f.b.require_session('device-one')
        self.assertLessEqual(load.call_count,4)

    def test_service_registry_blocks_old_results_and_quota_after_closure(self):
        f=self.f;f.login();a=f.f.h.accounts.resolve('device-one');f.close()
        with self.assertRaises(AccountClosing):f.f.h.accounts.resolve('device-one')
        with self.assertRaises(AccountClosing):f.f.h.accounts.grant(a,f.f.now())
        self.assertEqual(f.f.h.accounts.registration('device-one'),a)  # Progress lookup still possible.

    def test_old_registry_instance_cannot_bypass_durable_closure_component(self):
        from lite_service_operations import ServiceAccounts
        f=self.f;f.login();old=ServiceAccounts(f.f.h.accounts.store)
        with self.assertRaises(StateUnavailable):old.resolve('device-one')

    def test_native_purchase_cannot_create_new_context_after_closure(self):
        from lite_native_purchase import NativePurchaseBridge
        f=self.f;f.login();p=NativePurchaseBridge(runtime=f.b.runtime,
            store=SQLiteStateStore(f.f.h.f.root/'purchase.sqlite'),audit_store=SQLiteStateStore(f.f.h.f.root/'purchase-audit.sqlite'))
        f.close()
        with self.assertRaises(AccountClosing):p.context('device-one','a'*32)
        self.assertEqual(rows(p.store),[])

    def test_old_grant_authority_instance_fails_closed_after_enable(self):
        f=self.f;f.g.prepare()
        with self.assertRaises(StateUnavailable):f.g.capture(f.old_grants)
        self.assertEqual(f.g.calls,[])

    def test_constructor_cannot_disable_lifecycle(self):
        f=self.f;b=f.b
        with self.assertRaises(StateUnavailable):f.g.clone()
        with self.assertRaises(StateUnavailable):AccountIdentityBridge(runtime=b.runtime,verifier=b.verifier,
            store=b.store,audit_store=b.audit,namespace=b.namespace,identity_key=b.secret,grants=f.g.a)

    def test_runtime_cannot_silently_remove_lifecycle(self):
        f=self.f;f.login();f.b.lifecycle=None
        with self.assertRaises(StateUnavailable):f.b.require_session('device-one')

    def test_existing_identity_without_inventory_not_treated_as_fresh(self):
        f=self.f;person='a'*64
        with self.assertRaises(StateUnavailable):f.lifecycle.initialize_new(person,existing_account=True)
        self.assertIsNone(f.lifecycle._read(person))

    def test_legacy_vault_even_without_account_cannot_enable_empty_inventory(self):
        g=GrantFixture(self);g.prepare();g.capture()
        lifecycle=AccountLifecycle(store=SQLiteStateStore(g.f.h.f.root/'new-life.sqlite'),
            audit_store=SQLiteStateStore(g.f.h.f.root/'new-life-audit.sqlite'),binding=g.a.binding,clock=g.f.now)
        with self.assertRaises(StateUnavailable):AppleGrantAuthority(provider=g.provider,verifier=g.f.verifier,
            store=g.store,audit_store=g.audit,cipher=g.cipher,namespace=g.f.b.namespace,
            identity_key=g.f.b.secret,lifecycle=lifecycle)

    def test_existing_unindexed_grant_cannot_be_automatically_backfilled(self):
        f=self.f;f.login();person=f.person;c=f.g.c.copy();grant=f.g.a._row(f.g.a._id(c['installation'],c['operation']))
        other=AccountLifecycle(store=SQLiteStateStore(f.f.h.f.root/'empty-inventory.sqlite'),
            audit_store=SQLiteStateStore(f.f.h.f.root/'empty-inventory-audit.sqlite'),binding=f.lifecycle.binding,clock=f.f.now)
        other.initialize_new(person,existing_account=False);f.g.a.lifecycle=other
        with self.assertRaises(StateUnavailable):f.g.capture()
        self.assertEqual(grant,f.g.a._row(f.g.a._id(c['installation'],c['operation'])))

    def test_corrupt_one_root_blocks_only_that_account(self):
        f=self.f;f.login();person=f.person;f.login('other','apple-human-2')
        rev,c=f.lifecycle._read(person);f.lifecycle.store.compare_exchange(f.lifecycle._key(person),rev,dict(c,count=True))
        with self.assertRaises(StateUnavailable):f.b.require_session('device-one')
        self.assertEqual(f.b.status('other')['state'],'signed_in')

    def test_corrupt_inventory_node_never_claims_empty_inventory(self):
        f=self.f;f.login();f.close();node_id=f.lifecycle._read(f.person)[1]['head'];k=key('account-grant-node',node_id)
        rev,n=f.lifecycle.store.load(k);f.lifecycle.store.compare_exchange(k,rev,dict(n,operation='b'*32))
        with self.assertRaises(StateUnavailable):f.lifecycle.page(f.person)

    def test_missing_root_is_unavailable_not_new_account_reset(self):
        with self.assertRaises(StateUnavailable):self.f.lifecycle.require_active('a'*64)

    def test_closing_cannot_be_reinitialized_or_reopened(self):
        f=self.f;f.login();f.close()
        with self.assertRaises(AccountClosing):f.lifecycle.initialize_new(f.person,existing_account=False)

    def test_no_raw_apple_identity_or_tokens_in_inventory(self):
        f=self.f;f.login();f.close();raw=repr(rows(f.lifecycle.store)+rows(f.lifecycle.audit))
        for s in ['apple-human-1',f.g.signed,f.g.code,'private-refresh-token']:self.assertNotIn(s,raw)

    def test_default_bridge_has_no_inventory_or_deletion_route(self):
        g=GrantFixture(self)
        self.assertIsNone(g.a.lifecycle);self.assertIsNone(g.f.b.lifecycle)


class RefreshRevocationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):native.legacy.LinksTests.setUpClass()

    def test_refresh_inflight_defers_revoke_and_revokes_late_credential(self):
        f=RenewalFixture(self);f.advance(86401);start=threading.Event();finish=threading.Event()
        original=f.g.provider.refresh
        def refresh(token):
            start.set();self.assertTrue(finish.wait(5));v=original(token);v['refresh_token']='rotated-late-token';return v
        with patch.object(f.g.provider,'refresh',side_effect=refresh),ThreadPoolExecutor(1) as pool:
            pending=pool.submit(f.renew);self.assertTrue(start.wait(5))
            try:
                with self.assertRaises(GrantUncertain):f.g.revoke()
                self.assertNotIn(REVOKE_URL,f.g.calls)
            finally:finish.set()
            with self.assertRaises(IdentityRejected):pending.result()
        with patch.object(f.g.provider,'revoke',wraps=f.g.provider.revoke) as call:
            self.assertEqual(f.g.revoke()['state'],'apple_token_revoked');call.assert_called_once_with('rotated-late-token')

    def test_unknown_refresh_cannot_report_revocation_complete(self):
        f=RenewalFixture(self);f.advance(86401)
        with patch.object(f.g.provider,'refresh',side_effect=GrantUncertain('unknown')):
            with self.assertRaises(GrantUncertain):f.renew()
        with self.assertRaises(GrantUncertain):f.g.revoke()
        self.assertNotEqual(f.g.a._row(f.g.a._id(f.c['installation'],f.c['operation']))[1]['phase'],'revoked')

    def test_received_wrong_identity_token_remains_encrypted_and_cannot_fake_complete(self):
        from lite_apple_oauth import GrantRejected
        f=RenewalFixture(self);f.advance(86401);f.g.token=f.f.token(f.c,sub='wrong-person')
        with self.assertRaises(GrantRejected):f.renew()
        identity=f.g.a._id(f.c['installation'],f.c['operation'])
        saved=f.g.store.load(key('apple-grant-refresh-output',digest([identity,f.f.now()])))[1]
        tokens=f.g.cipher.open(saved['envelope'],saved['context'])
        self.assertEqual(tokens['id_token'],f.g.token)
        self.assertNotIn('private-refresh-token',repr(rows(f.g.store)+rows(f.g.audit)))
        with self.assertRaises(GrantUncertain):f.g.revoke()

    def test_refresh_output_commit_response_loss_is_read_back_without_second_apple_call(self):
        f=RenewalFixture(self);f.advance(86401);real=f.g.store.compare_exchange
        def lost(k,rev,v):
            result=real(k,rev,v)
            if 'grant_context' in v.get('context',{}):raise StateUnavailable('lost')
            return result
        with patch.object(f.g.store,'compare_exchange',side_effect=lost):f.renew()
        self.assertEqual(f.g.calls.count(TOKEN_URL),2)

    def test_refresh_output_write_failure_never_claims_revoke_complete(self):
        f=RenewalFixture(self);f.advance(86401);real=f.g.store.compare_exchange
        def fail(k,rev,v):
            if 'grant_context' in v.get('context',{}):raise StateUnavailable('disk failure')
            return real(k,rev,v)
        with patch.object(f.g.store,'compare_exchange',side_effect=fail),self.assertRaises(GrantUncertain):f.renew()
        with self.assertRaises(GrantUncertain):f.g.revoke()
        self.assertEqual(f.g.calls.count(TOKEN_URL),2)


class ClosureHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):native.legacy.LinksTests.setUpClass()
    def setUp(self):
        from test_lite_apple_grants import GrantHTTPTests
        self.base=GrantHTTPTests();self.base.setUp();self.addCleanup(self.base.doCleanups)
        self.h=self.base.h;self.g=self.base.g;b=self.base.bridge;g=self.g
        root=self.h.f.h.f.root
        self.lifecycle=AccountLifecycle(store=SQLiteStateStore(root/'http-lifecycle.sqlite'),
            audit_store=SQLiteStateStore(root/'http-lifecycle-audit.sqlite'),binding=g.a.binding,clock=self.h.f.now,service_catalog=getattr(self,"service_catalog",False))
        g.store=SQLiteStateStore(root/'http-indexed-vault.sqlite')
        g.a=AppleGrantAuthority(provider=g.provider,verifier=g.f.verifier,store=g.store,audit_store=g.audit,
            cipher=g.cipher,namespace=b.namespace,identity_key=b.secret,lifecycle=self.lifecycle)
        from lite_apple_account_events import AppleAccountEvents
        events=AppleAccountEvents(verifier=b.verifier,store=SQLiteStateStore(root/'http-events.sqlite'),
            audit_store=SQLiteStateStore(root/'http-events-audit.sqlite'),namespace=b.namespace,identity_key=b.secret)
        b.__init__(runtime=b.runtime,verifier=b.verifier,store=b.store,audit_store=b.audit,namespace=b.namespace,
            identity_key=b.secret,grants=g.a,lifecycle=self.lifecycle,events=events,renewal=True)
        self.b=b;self.h.f.b=b
        from lite_account_identity import PREFIX
        c=self.h.post(PREFIX+'challenge',dict(operation='1'*32)).json;g.token=self.h.f.token(c)
        r=self.h.post(PREFIX+'confirm',dict(operation=c['operation'],state=c['state'],identity_token=g.token,
            authorization_code='synthetic-closure-code'))
        self.assertEqual(r.status_code,200,r.json)
        self.account=self.h.h.ops.accounts.resolve(self.h.h.owner)
        self.h.h.ledger.initialize_day(self.account['subject'],self.h.h.e.now//86400,
            baseline_successes=0,baseline_attempts=0,manifest_sha256='a'*64)
        self.h.f.h.publish()

    def close(self):return self.b.begin_account_closure(self.h.h.owner,'d'*32)

    def test_existing_service_result_and_quota_preserved_but_not_accessible_after_close(self):
        h=self.h;h.h.complete(77)
        before=rows(h.h.ops.store),rows(h.h.ledger.store),rows(h.h.privacy.store)
        r=self.close();self.assertFalse(r['account_deleted'])
        for response in (h.h.json_request('status',77),h.h.run_request(77),h.h.json_request('prepare',78)):
            self.assertEqual(response.status_code,401,response.json)
        self.assertEqual(h.h.model_calls,1);self.assertEqual(before,(rows(h.h.ops.store),rows(h.h.ledger.store),rows(h.h.privacy.store)))

    def test_closure_during_inference_blocks_late_result_and_no_second_model(self):
        h=self.h;self.assertEqual(h.h.json_request('prepare',79).status_code,200)
        real=h.h.ops.inference.infer
        def infer(rgb):
            result=real(rgb);self.close();return result
        with patch.object(h.h.ops.inference,'infer',side_effect=infer):response=h.h.run_request(79)
        self.assertNotEqual(response.status_code,200)
        self.assertIsNone(h.h.ops.load(self.account['subject'],f'{79:032x}')[1]['result'])
        self.assertEqual(h.h.run_request(79).status_code,401);self.assertEqual(h.h.model_calls,1)

    def test_closed_family_cannot_renew(self):
        from lite_account_identity import PREFIX
        self.close();self.h.f.h.a.now+=901
        r=self.h.post(PREFIX+'renew',dict(operation='1'*32,renewal_operation='a'*32,expected_generation=0))
        self.assertEqual(r.status_code,401);self.assertEqual(self.g.calls,[TOKEN_URL])

    def test_closure_does_not_disable_separate_research_withdrawal_route(self):
        h=self.h;self.close();path='/api/v1/lite/data/'+h.h.owner
        r=h.h.client.delete(path,headers=h.h.headers(path,b'','',method='DELETE',purpose='withdraw'))
        self.assertEqual(r.status_code,200,r.json)

if __name__=='__main__':unittest.main()
