"""Signed private execution over actual CAS/closure components; synthetic only."""
import base64,copy,unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from lite_attest_state import StateUnavailable
from lite_disposition_executor import DispositionExecutor,CommandRejected,ExecutionUncertain,PATH
from lite_disposition_worker_probe import WorkerFixture
from lite_subscription_executor import canonical,key
from lite_usage_ledger import digest
from test_lite_subscription_grace import rows
import test_lite_native_purchase as native


def public(k):return k.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)
def signed(k,p,name):return dict(key_id=name,payload=p,signature=base64.b64encode(k.sign(canonical(p))).decode())


class ExecutorFixture:
    def __init__(self,host,*,max_commands=20,steps=12):
        self.f=WorkerFixture(host);self.f.ready();self.person=self.f.person;self.now=[1792886500]
        self.operator=Ed25519PrivateKey.generate();self.observer=Ed25519PrivateKey.generate();self.seq=0
        self.store=self.f.f.store('executor-state');self.audit=self.f.f.store('executor-audit')
        self.m=dict(v=1,namespace='synthetic-executor',source='a'*40,image='b'*64,operation='c'*64,
            accounts=[dict(person=self.person,plan_sha256=digest(self.f.w._plan(self.person)))],
            not_before=self.now[0],expires_at=self.now[0]+900,max_commands=max_commands,steps=steps,limit=2,seconds=30)
        self.e=self.build()
    def build(self,**changes):
        kw=dict(enabled=True,manifest=self.m,expected_manifest=digest(self.m),expected_source='a'*40,
            expected_image='b'*64,worker=self.f.worker(),store=self.store,audit_store=self.audit,
            operator_keys={'operator':public(self.operator)},drain_keys={'observer':public(self.observer)},clock=lambda:self.now[0])
        kw.update(changes);return DispositionExecutor(**kw)
    def command(self,action='run',*,replaces=None,**changes):
        self.seq+=1
        p=dict(kind='lite-disposition-command/1',manifest=digest(self.m),command=f'{self.seq:032x}',
            expected_state=self.e._state(self.e._read()),issued_at=self.now[0],expires_at=self.now[0]+120,
            action=action,person=self.person,replaces=replaces)
        p.update(changes);return signed(self.operator,p,'operator')
    def drain(self,previous,**changes):
        p=dict(kind='lite-disposition-drain/1',manifest=digest(self.m),command=previous,
            expected_state=self.e._state(self.e._read()),issued_at=self.now[0],expires_at=self.now[0]+30,drained=True)
        p.update(changes);return signed(self.observer,p,'observer')
    def finish(self):
        outputs=[]
        for _ in range(10):
            out=self.e.execute(self.command());outputs.append(out)
            k=self.f.w.status(self.person)['observations']['grant_key']
            if k and k['kms_state']=='DESTROY_SCHEDULED':self.f.f.f.kms.destroy_now(k['version'])
            self.e=self.build()
            if all(s=='observed' for s in out['worker']['states'].values()):return outputs
        raise AssertionError('finite composition did not complete')


class ExecutorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):native.legacy.LinksTests.setUpClass()
    def setUp(self):self.h=ExecutorFixture(self);self.e=self.h.e
    def test_constructor_and_signed_inspection_are_read_only(self):
        before=(rows(self.h.store),rows(self.h.audit),self.h.f.f.preserved())
        self.h.build();out=self.e.execute(self.h.command('inspect'))
        self.assertEqual(out['used_dispatch_budget'],0);self.assertFalse(out['account_deleted'])
        self.assertEqual(before,(rows(self.h.store),rows(self.h.audit),self.h.f.f.preserved()))
    def test_all_real_components_via_signed_commands_preserve_capture_quota(self):
        before=self.h.f.f.preserved();capture=bytes(self.h.f.f.capture);out=self.h.finish()
        self.assertEqual(len(out),4);self.assertEqual(self.h.f.f.calls,1)
        self.assertEqual(self.h.f.f.preserved(),before);self.assertEqual(self.h.f.f.capture,capture)
        self.assertEqual(len(self.h.f.h.bucket.history),len(self.h.f.h.bucket.live))
        self.assertEqual(len(self.h.f.service_bucket.versions),len(self.h.f.service_bucket.live))
        self.assertFalse(out[-1]['account_deleted'])
    def test_same_completed_command_replays_without_dispatch_or_budget_debit(self):
        c=self.h.command();first=self.e.execute(c);before=(rows(self.h.store),rows(self.h.audit))
        self.e=self.h.build()
        with patch.object(self.e.w,'run',side_effect=AssertionError('duplicate execution')):
            self.assertEqual(self.e.execute(c),first)
        self.assertEqual(before,(rows(self.h.store),rows(self.h.audit)))
    def test_changed_command_id_binding_is_rejected(self):
        c=self.h.command();self.e.execute(c)
        changed=self.h.command(command=c['payload']['command'])
        with self.assertRaises(CommandRejected):self.e.execute(changed)
    def test_new_command_must_observe_latest_state(self):
        a=self.h.command();b=self.h.command();self.e.execute(a)
        with self.assertRaises(CommandRejected):self.e.execute(b)
    def test_finite_budget_survives_restart_and_no_refund_for_short_run(self):
        h=ExecutorFixture(self,max_commands=1);h.e.w.set_paused(h.person,paused=True,expected_revision=0)
        out=h.e.execute(h.command());self.assertEqual(out['dispatches'],0)
        h.e=h.build();self.assertEqual(h.e.inspect(h.person)['used_dispatch_budget'],12)
        with self.assertRaises(CommandRejected):h.e.execute(h.command())
    def test_expired_manifest_denies_execution_but_allows_fresh_signed_inspection(self):
        self.h.now[0]=self.h.m['expires_at']
        with self.assertRaises(CommandRejected):self.e.execute(self.h.command())
        self.assertEqual(self.e.execute(self.h.command('inspect'))['used_dispatch_budget'],0)
    def test_signature_expiry_or_wrong_key_rejects_before_writes(self):
        for changes in ({'expires_at':self.h.now[0]},{'issued_at':self.h.now[0]+1},
                        {'expires_at':self.h.now[0]+301},{'kind':'lite-migration-approval/1'}):
            with self.assertRaises(CommandRejected):self.e.execute(self.h.command(**changes))
        bad=self.h.command();bad['signature']=base64.b64encode(b'x'*64).decode()
        with self.assertRaises(CommandRejected):self.e.execute(bad)
        self.assertEqual(rows(self.h.store),[]);self.assertEqual(rows(self.h.audit),[])
    def test_foreign_account_and_plan_drift_rejected(self):
        with self.assertRaises(CommandRejected):self.e.execute(self.h.command(person='f'*64))
        with patch.object(self.e.w,'_plan',return_value={'changed':True}):
            with self.assertRaises(CommandRejected):self.e.execute(self.h.command())
        self.assertEqual(rows(self.h.store),[])
    def test_independent_signers_explicit_enablement_and_storage(self):
        for kw in ({'enabled':False},{'expected_source':'d'*40},{'expected_image':'d'*64},
                   {'expected_manifest':'d'*64},{'drain_keys':{'observer':public(self.h.operator)}},
                   {'store':self.h.f.store},{'audit_store':self.h.store}):
            with self.assertRaises(ValueError):self.h.build(**kw)
    def test_manifest_cannot_change_to_reset_existing_budget(self):
        self.e.execute(self.h.command());m=dict(self.h.m,operation='d'*64)
        with self.assertRaises(ValueError):self.h.build(manifest=m,expected_manifest=digest(m))
    def test_missing_state_never_resets_bound_budget(self):
        self.e.execute(self.h.command());real=self.h.store.load;k=self.e._root_key()
        with patch.object(self.h.store,'load',side_effect=lambda n:None if n==k else real(n)):
            with self.assertRaises(StateUnavailable):self.e.inspect(self.h.person)
    def test_missing_checkpoint_audit_stops_without_dispatch(self):
        self.e.execute(self.h.command());self.e.audit=self.h.f.f.store('missing-executor-audit')
        with self.assertRaises(StateUnavailable):self.e.inspect(self.h.person)
    def test_claim_response_lost_does_not_guess_owner_or_dispatch(self):
        c=self.h.command();real=self.h.store.compare_exchange
        def lost(k,g,v):
            result=real(k,g,v)
            if k==self.e._root_key():raise StateUnavailable('lost claim response')
            return result
        with patch.object(self.h.store,'compare_exchange',side_effect=lost),patch.object(self.e.w,'run') as run:
            with self.assertRaises(ExecutionUncertain):self.e.execute(c)
        run.assert_not_called()
        self.assertEqual(self.e.inspect(self.h.person)['commands'][c['payload']['command']],'running')
    def test_duplicate_inflight_command_cannot_dispatch_twice(self):
        entered=Event();release=Event();c=self.h.command();real=self.e.w.run
        def waiting(*a,**kw):
            entered.set();self.assertTrue(release.wait(10));return real(*a,**kw)
        with ThreadPoolExecutor(max_workers=2) as pool,patch.object(self.e.w,'run',side_effect=waiting) as run:
            a=pool.submit(self.e.execute,c);self.assertTrue(entered.wait(10))
            with self.assertRaises(ExecutionUncertain):self.h.build().execute(c)
            release.set();a.result(timeout=10)
        self.assertEqual(run.call_count,1)
    def test_cas_loser_cannot_dispatch_even_if_winner_claim_is_identical(self):
        c=self.h.command();real=self.h.store.compare_exchange
        def loser(k,g,v):
            result=real(k,g,v)
            return False if k==self.e._root_key() else result
        with patch.object(self.h.store,'compare_exchange',side_effect=loser),patch.object(self.e.w,'run') as run:
            with self.assertRaises(ExecutionUncertain):self.e.execute(c)
        run.assert_not_called()
    def interrupt(self):
        c=self.h.command()
        with patch.object(self.e.w,'run',side_effect=RuntimeError('injected process failure')):
            with self.assertRaises(ExecutionUncertain):self.e.execute(c)
        return c['payload']['command']
    def test_unknown_execution_requires_new_command_and_independent_drain(self):
        old=self.interrupt();before=self.e.inspect(self.h.person)
        with self.assertRaises(CommandRejected):self.e.execute(self.h.command())
        c=self.h.command('resume',replaces=old)
        with self.assertRaises(CommandRejected):self.e.execute(c)
        result=self.e.execute(c,drain=self.h.drain(old))
        self.assertEqual(result['reserved_dispatches'],12)
        status=self.e.inspect(self.h.person)
        self.assertEqual(status['commands'][old],'superseded');self.assertEqual(status['used_dispatch_budget'],24)
        self.assertEqual(before['used_dispatch_budget'],12)
    def test_drain_wrong_state_old_command_or_non_drained_is_rejected(self):
        old=self.interrupt()
        for changes in ({'expected_state':'f'*64},{'command':'f'*32},{'drained':False},
                        {'issued_at':self.h.now[0]-100,'expires_at':self.h.now[0]-1}):
            with self.assertRaises(CommandRejected):self.e.execute(self.h.command('resume',replaces=old),
                drain=self.h.drain(old,**changes))
        self.assertEqual(self.e.inspect(self.h.person)['used_dispatch_budget'],12)
    def test_original_drain_evidence_required_after_restart(self):
        old=self.interrupt();c=self.h.command('resume',replaces=old)
        self.e.execute(c,drain=self.h.drain(old));real=self.h.audit.load;k=key('disposition-execution-drain',digest(c))
        with patch.object(self.h.audit,'load',side_effect=lambda n:None if n==k else real(n)):
            with self.assertRaises(StateUnavailable):self.h.build().inspect(self.h.person)
    def test_completed_checkpoint_response_loss_replays_without_new_run(self):
        c=self.h.command();real=self.h.store.compare_exchange
        def lost(k,g,v):
            result=real(k,g,v)
            if k==self.e._root_key() and v['commands'][c['payload']['command']]['phase']=='complete':
                raise StateUnavailable('lost completion response')
            return result
        with patch.object(self.h.store,'compare_exchange',side_effect=lost):first=self.e.execute(c)
        self.assertEqual(self.h.build().execute(c),first)
    def test_receipt_write_failure_requires_drain_then_continues_saved_worker(self):
        c=self.h.command();real=self.h.audit.compare_exchange
        def fail(k,g,v):
            if v.get('reserved_dispatches'):raise StateUnavailable('receipt unavailable')
            return real(k,g,v)
        with patch.object(self.h.audit,'compare_exchange',side_effect=fail):
            with self.assertRaises(ExecutionUncertain):self.e.execute(c)
        old=c['payload']['command'];self.e.execute(self.h.command('resume',replaces=old),drain=self.h.drain(old))
        self.assertEqual(self.h.f.f.calls,1);self.assertEqual(self.e.inspect(self.h.person)['used_dispatch_budget'],24)
    def test_cancel_window_between_claim_and_work_stops_next_dispatch(self):
        c=self.h.command();real=self.e._account;calls=[0]
        def expire(person):
            real(person);calls[0]+=1
            if calls[0]==2:self.h.now[0]=c['payload']['expires_at']
        with patch.object(self.e,'_account',side_effect=expire),patch.object(self.e.w,'_invoke') as invoked:
            with self.assertRaises(ExecutionUncertain):self.e.execute(c)
        invoked.assert_not_called()
    def test_pause_during_execution_prevents_next_provider_dispatch(self):
        c=self.h.command();self.e.w.set_paused(self.h.person,paused=True,expected_revision=0)
        result=self.e.execute(c);self.assertEqual(result['stop_reason'],'paused');self.assertEqual(result['dispatches'],0)
    def test_private_http_exact_route_signed_only_and_no_internal_resource_leak(self):
        app=self.e.create_app();client=app.test_client()
        for path in ('/','/api/v1/lite/service/result','/api/v1/lite/account/deletion/request','/api/lite'):
            self.assertEqual(client.post(path,json={}).status_code,404)
        self.assertEqual(client.get(PATH).status_code,404)
        self.assertEqual(client.post(PATH,json={'command':{},'drain':None}).status_code,409)
        response=client.post(PATH,json={'command':self.h.command(),'drain':None})
        self.assertEqual(response.status_code,200);self.assertEqual(response.headers['Cache-Control'],'no-store')
        for secret in (self.h.person,'cryptoKeyVersions','private-refresh-token','wrapped_dek','observations'):
            self.assertNotIn(secret,response.text)
    def test_private_http_duplicate_json_oversized_and_unsigned_requests(self):
        client=self.e.create_app().test_client()
        self.assertEqual(client.post(PATH,data='{"command":{},"command":{},"drain":null}',content_type='application/json').status_code,409)
        self.assertEqual(client.post(PATH,json={'command':{},'drain':None,'override':True}).status_code,409)
        self.assertEqual(client.post(PATH,data='x'*33000,content_type='application/json').status_code,413)
        self.assertEqual(rows(self.h.store),[])
    def test_private_http_unknown_execution_never_asks_automatic_retry(self):
        c=self.h.command();client=self.e.create_app().test_client()
        with patch.object(self.e.w,'run',side_effect=StateUnavailable('private-secret')):
            r=client.post(PATH,json={'command':c,'drain':None})
        self.assertEqual(r.status_code,503);self.assertFalse(r.json['retry_automatically'])
        self.assertNotIn('private-secret',r.text)

    def test_manifest_bounds_and_duplicate_accounts_reject_without_writes(self):
        for changes in ({'max_commands':101},{'steps':101},{'max_commands':100,'steps':100},
                        {'limit':True},{'seconds':61},{'expires_at':self.h.now[0]+3601},
                        {'accounts':self.h.m['accounts']*2},{'v':True}):
            m=dict(self.h.m,**changes)
            with self.assertRaises(ValueError):self.h.build(manifest=m,expected_manifest=digest(m))
        self.assertEqual(rows(self.h.store),[])
    def test_receipt_corruption_cannot_be_used_as_successful_replay(self):
        c=self.h.command();first=self.e.execute(c);k=key('disposition-execution-receipt',digest(first))
        rev,body=self.h.audit.load(k);self.h.audit.compare_exchange(k,rev,dict(body,account_deleted=True))
        with self.assertRaises(StateUnavailable):self.e.execute(c)
    def test_original_operator_command_proof_cannot_be_removed(self):
        c=self.h.command();self.e.execute(c);real=self.h.audit.load
        k=key('disposition-command',digest([self.e.identity,c['payload']['command']]))
        with patch.object(self.h.audit,'load',side_effect=lambda n:None if n==k else real(n)):
            with self.assertRaises(StateUnavailable):self.e.inspect(self.h.person)
    def test_historical_signatures_remain_readable_after_expiry(self):
        self.e.execute(self.h.command());self.h.now[0]+=7200
        result=self.e.execute(self.h.command('inspect'))
        self.assertEqual(result['used_dispatch_budget'],12)
    def test_drain_expiry_while_claiming_stops_before_provider(self):
        old=self.interrupt();c=self.h.command('resume',replaces=old);drain=self.h.drain(old)
        real=self.e._save
        def delayed(*a,**kw):
            r=real(*a,**kw)
            if kw.get('claim'):self.h.now[0]+=31
            return r
        with patch.object(self.e,'_save',side_effect=delayed),patch.object(self.e.w,'run') as run:
            with self.assertRaises(ExecutionUncertain):self.e.execute(c,drain=drain)
        run.assert_not_called()


if __name__=='__main__':unittest.main()
