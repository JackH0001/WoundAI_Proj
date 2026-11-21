"""Actual requests preparation, signed journals and AES; synthetic KMS only."""
import base64
import copy
import io
import json
import socket
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit, parse_qs
from unittest.mock import patch
import requests
from requests.adapters import BaseAdapter
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from lite_attest_state import SQLiteStateStore, StateUnavailable
from lite_key_lifecycle_dispatch import PinnedKeyLifecycle, KeyLifecycleDispatch, LifecycleRejected
from lite_subscription_executor import key, canonical
from lite_store_locations import location
from lite_usage_ledger import digest
from test_lite_rotating_grant_keys import SharedFixture, V1, V2, V3
from test_lite_subscription_grace import rows
import test_lite_native_purchase as native


class LifecycleAdapter(BaseAdapter):
    def __init__(self,kms):
        self.kms=kms;self.calls=[];self.before=lambda action:None;self.after=lambda action:None
        self.metadata=lambda body:body;self.status=200;self.destroy_at=None;self.event=None
    def row(self):
        return self.metadata(dict(name=V1,state=self.kms.states[V1],algorithm='GOOGLE_SYMMETRIC_ENCRYPTION',
            protectionLevel='SOFTWARE',destroyTime=self.destroy_at,destroyEventTime=self.event))
    def send(self,request,**kw):
        u=urlsplit(request.url);assert u.hostname=='cloudkms.googleapis.com'
        assert request.method in ('GET','PATCH','POST') and kw['timeout']==(5,10) and kw['stream']
        name,_,action=u.path.removeprefix('/v1/').partition(':');assert name==V1
        action=action or ('disable' if request.method=='PATCH' else 'get')
        self.calls.append((action,request.method,parse_qs(u.query),request.body))
        self.before(action)
        if self.status!=200:return self.response(request,self.status,b'synthetic private error material')
        if action=='disable':
            assert parse_qs(u.query)=={'updateMask':['state']}
            assert json.loads(request.body)==dict(name=V1,state='DISABLED') and self.kms.states[V1]=='ENABLED'
            self.kms.states[V1]='DISABLED'
        elif action=='destroy':
            assert request.method=='POST' and json.loads(request.body)=={} and self.kms.states[V1]=='DISABLED'
            self.kms.states[V1]='DESTROY_SCHEDULED';self.destroy_at='2026-12-11T00:00:00Z'
        elif action=='restore':
            assert request.method=='POST' and json.loads(request.body)=={} and self.kms.states[V1]=='DESTROY_SCHEDULED'
            self.kms.states[V1]='DISABLED';self.destroy_at=None
        else:assert action=='get' and request.method=='GET' and request.body is None
        self.after(action)
        return self.response(request,200,json.dumps(self.row()).encode())
    @staticmethod
    def response(request,status,body):
        r=requests.Response();r.status_code=status;r.url=request.url;r.request=request;r.raw=io.BytesIO(body);return r
    def close(self):pass
    def finish_destruction(self):
        assert self.kms.states[V1]=='DESTROY_SCHEDULED'
        self.kms.destroy_now(V1);self.event='2026-12-11T00:00:00Z'


class DispatchFixture:
    def __init__(self,host):
        self.f=SharedFixture(host);self.f.login();self.a=self.f.person
        self.old=copy.deepcopy(self.f.cipher._read(self.a)[1]);self.a_snapshot=self.f.snapshot()
        self.f.login('device-two','apple-human-2');self.b=self.f.person;self.b_snapshot=self.f.snapshot()
        self.f.close_revoke();self.f.cipher.destroy_after_revocation(self.f.d,self.a)
        source=digest(self.f.cipher._read(self.b)[1]);self.f.rotate_to(V2);self.f.cipher.rewrap(self.b,expected_record_sha256=source)
        self.session=requests.Session();self.session.trust_env=False;host.addCleanup(self.session.close)
        self.wire=LifecycleAdapter(self.f.kms);self.session.mount('https://',self.wire)
        self.provider=PinnedKeyLifecycle(session=self.session,version=V1,protected_version=V2)
        root=self.f.f.h.f.root
        self.store=SQLiteStateStore(root/'lifecycle-journal.sqlite');self.audit=SQLiteStateStore(root/'lifecycle-audit.sqlite')
        self.op=Ed25519PrivateKey.generate();self.dr=Ed25519PrivateKey.generate();self.now=1000;self.counter=0
        self.plan('disable')
    def plan(self,action):
        self.m=dict(v=1,source_commit='a'*40,version=V1,protected_version=V2,action=action,
            expected_metadata_sha256=digest(self.provider.describe()),not_before=self.now,expires_at=self.now+900,review_sha256='b'*64,
            state_location_sha256=digest(location(self.store)),audit_location_sha256=digest(location(self.audit)))
        self.e=self.restart();return self.e
    def restart(self,**kw):
        args=dict(enabled=True,provider=self.provider,manifest=self.m,expected_manifest=digest(self.m),expected_source='a'*40,
            store=self.store,audit_store=self.audit,operator_key=self.op.public_key().public_bytes_raw(),
            drain_key=self.dr.public_key().public_bytes_raw(),clock=lambda:self.now)
        args.update(kw);return KeyLifecycleDispatch(**args)
    @staticmethod
    def sign(p,sk):return dict(payload=p,signature=base64.b64encode(sk.sign(canonical(p))).decode())
    def command(self):
        self.counter+=1
        return self.sign(dict(kind='key-lifecycle-command/1',version=V1,manifest=digest(self.m),command=f'{self.counter:064x}',
            expected_state=self.e.inspect()['state_sha256'],issued_at=self.now,expires_at=self.now+300),self.op)
    def drain(self,request):
        row=self.e._read(allow_missing_for_reconcile=True)
        return self.sign(dict(kind='key-lifecycle-drain/1',version=V1,manifest=digest(self.m),command=digest(request),
            expected_state=self.e._state(row),issued_at=self.now,expires_at=self.now+60,prior_dispatch_drained=True),self.dr)
    def mutations(self):return [x for x in self.wire.calls if x[0]!='get']
    def perform(self,action):
        self.plan(action);c=self.command();return c,self.e.execute(c)


class DispatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):native.legacy.LinksTests.setUpClass()
    def setUp(self):
        p=patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden'));p.start();self.addCleanup(p.stop)
        self.x=DispatchFixture(self);self.f=self.x.f
    def test_disable_keeps_surviving_account_and_all_envelopes(self):
        before=rows(self.f.g.store),rows(self.f.key_store),rows(self.f.key_audit),rows(self.f.f.h.f.ledger.store)
        r=self.x.e.execute(self.x.command());self.assertEqual(r['provider_state'],'DISABLED')
        self.assertEqual(self.f.cipher.open(*self.x.b_snapshot)['refresh_token'],'private-refresh-token')
        self.assertEqual(before,(rows(self.f.g.store),rows(self.f.key_store),rows(self.f.key_audit),rows(self.f.f.h.f.ledger.store)))
        self.assertEqual(self.f.kms.states[V2],'ENABLED');self.assertEqual(len(self.x.mutations()),1)
        self.assertIs(r['account_deleted'],False);self.assertIs(r['complete_inventory_verified'],False)
    def test_schedule_restore_and_reconcile_destroyed_remain_separate(self):
        self.x.perform('disable');_,s=self.x.perform('schedule')
        self.assertEqual(s['provider_state'],'DESTROY_SCHEDULED');self.assertFalse(s['account_deleted'])
        _,r=self.x.perform('restore');self.assertEqual(r['provider_state'],'DISABLED')
        self.assertIsNone(self.x.wire.destroy_at);self.assertEqual(self.f.kms.states[V2],'ENABLED')
        c,_=self.x.perform('schedule');self.x.wire.finish_destruction();self.x.now+=86400*31
        observed=self.x.e.reconcile(self.x.drain(c))
        self.assertEqual(observed['provider_state'],'DESTROYED');self.assertFalse(observed['physical_backups_erased'])
        self.assertEqual(self.f.cipher.open(*self.x.b_snapshot)['refresh_token'],'private-refresh-token')
    def test_default_off_and_equal_signers_refused(self):
        for kw in (dict(enabled=False),dict(drain_key=self.x.op.public_key().public_bytes_raw()),dict(audit_store=self.x.store)):
            with self.assertRaises(ValueError):self.x.restart(**kw)
    def test_wrong_manifest_source_and_protected_version_refused(self):
        for kw in (dict(expected_source='c'*40),dict(expected_manifest='c'*64)):
            with self.assertRaises(ValueError):self.x.restart(**kw)
        m=dict(self.x.m,version=V2)
        with self.assertRaises(ValueError):self.x.restart(manifest=m,expected_manifest=digest(m))
        for version,target in ((V2,V1),(V2,V2),(V1,V3.replace('/shared-lab/','/other/'))):
            with self.assertRaises(ValueError):PinnedKeyLifecycle(session=self.x.session,version=version,protected_version=target)
    def test_same_plan_cannot_use_another_journal_to_bypass_prior_dispatch(self):
        other=SQLiteStateStore(self.f.f.h.f.root/'other-journal.sqlite')
        with self.assertRaises(ValueError):self.x.restart(store=other)
        with self.assertRaises(ValueError):self.x.restart(audit_store=other)
    def test_invalid_action_or_unbounded_window_refused(self):
        for change in (dict(action='enable'),dict(expires_at=1901),dict(not_before=True),dict(v=True)):
            m=dict(self.x.m,**change)
            with self.assertRaises(ValueError):self.x.restart(manifest=m,expected_manifest=digest(m))
    def test_bad_signature_and_expired_command_no_provider_request(self):
        c=self.x.command();c['signature']=self.x.sign(c['payload'],self.x.dr)['signature'];before=self.x.wire.calls.copy()
        with self.assertRaises(LifecycleRejected):self.x.e.execute(c)
        c=self.x.command();self.x.now+=301
        with self.assertRaises(LifecycleRejected):self.x.e.execute(c)
        self.assertEqual(self.x.wire.calls,before)
    def test_expired_plan_even_with_fresh_command_is_rejected(self):
        self.x.now+=901
        with self.assertRaises(LifecycleRejected):self.x.e.execute(self.x.command())
        self.assertEqual(self.x.mutations(),[])
    def test_observed_metadata_must_match_review(self):
        self.f.kms.states[V1]='DISABLED'
        with self.assertRaises(LifecycleRejected):self.x.e.execute(self.x.command())
        self.assertEqual(self.x.mutations(),[])
    def test_schedule_from_enabled_never_dispatches(self):
        self.x.plan('schedule')
        with self.assertRaises(LifecycleRejected):self.x.e.execute(self.x.command())
        self.assertEqual(self.x.mutations(),[])
    def test_replay_no_get_no_mutation_after_completion_and_later_action(self):
        c,r=self.x.perform('disable');before=self.x.wire.calls.copy()
        self.assertEqual(self.x.e.execute(c),r);self.assertEqual(self.x.wire.calls,before)
        first=self.x.e;self.x.perform('schedule');before=self.x.wire.calls.copy()
        self.assertEqual(first.execute(c),r);self.assertEqual(self.x.wire.calls,before)
    def test_forged_finished_result_cannot_invent_deletion(self):
        c,_=self.x.perform('disable');load=self.x.audit.load
        def fake(k):
            r=load(k)
            if k==key('key-lifecycle-finished',digest(c)):return r[0],dict(r[1],account_deleted=True)
            return r
        with patch.object(self.x.audit,'load',side_effect=fake),self.assertRaises(StateUnavailable):self.x.e.execute(c)
    def test_committed_but_lost_claim_reply_never_calls_kms(self):
        c=self.x.command();cas=self.x.store.compare_exchange
        def lost(k,g,v):cas(k,g,v);raise StateUnavailable('lost synthetic claim acknowledgement')
        with patch.object(self.x.store,'compare_exchange',side_effect=lost),self.assertRaises(StateUnavailable):self.x.e.execute(c)
        self.assertEqual(self.x.mutations(),[]);self.assertEqual(self.x.e.inspect()['phase'],'pending')
        r=self.x.e.reconcile(self.x.drain(c));self.assertEqual(r['provider_state'],'ENABLED');self.assertEqual(self.x.mutations(),[])
    def test_uncommitted_claim_can_reconcile_only_with_original_audit_and_drain(self):
        c=self.x.command()
        with patch.object(self.x.store,'compare_exchange',side_effect=StateUnavailable('synthetic write failure')),self.assertRaises(StateUnavailable):self.x.e.execute(c)
        with self.assertRaises(StateUnavailable):self.x.e.inspect()
        self.assertEqual(self.x.mutations(),[])
        r=self.x.e.reconcile(self.x.drain(c));self.assertEqual(r['provider_state'],'ENABLED')
        self.assertEqual(self.x.e.execute(c),r);self.assertEqual(self.x.mutations(),[])
    def test_timeout_after_provider_changed_state_is_not_auto_retried(self):
        c=self.x.command()
        self.x.wire.after=lambda action:(_ for _ in ()).throw(TimeoutError('synthetic sensitive text')) if action=='disable' else None
        with self.assertRaises(StateUnavailable):self.x.e.execute(c)
        self.assertEqual(self.f.kms.states[V1],'DISABLED');self.assertEqual(self.x.e.inspect()['phase'],'uncertain')
        before=self.x.wire.calls.copy()
        with self.assertRaises(StateUnavailable):self.x.e.execute(c)
        self.assertEqual(self.x.wire.calls,before)
        self.x.wire.after=lambda action:None
        r=self.x.e.reconcile(self.x.drain(c));self.assertEqual(r['provider_state'],'DISABLED');self.assertEqual(len(self.x.mutations()),1)
        self.assertEqual(self.x.e.execute(c),r);self.assertEqual(len(self.x.mutations()),1)
    def test_timeout_before_provider_changed_state_also_requires_fresh_drain(self):
        c=self.x.command()
        self.x.wire.before=lambda action:(_ for _ in ()).throw(TimeoutError()) if action=='disable' else None
        with self.assertRaises(StateUnavailable):self.x.e.execute(c)
        self.assertEqual(self.f.kms.states[V1],'ENABLED')
        self.x.wire.before=lambda action:None
        self.x.e.reconcile(self.x.drain(c));self.x.perform('disable')
        self.assertEqual(len(self.x.mutations()),2)  # First one never reached simulated provider.
    def test_other_plan_cannot_supersede_uncertain_dispatch(self):
        c=self.x.command();self.x.wire.status=503
        # Fail after claim, not preflight.
        self.x.wire.status=200
        self.x.wire.after=lambda a:(_ for _ in ()).throw(TimeoutError()) if a=='disable' else None
        with self.assertRaises(StateUnavailable):self.x.e.execute(c)
        self.x.wire.after=lambda a:None;self.x.plan('schedule');before=self.x.wire.calls.copy()
        with self.assertRaises(StateUnavailable):self.x.e.execute(self.x.command())
        self.assertEqual(self.x.wire.calls,before)
    def test_wrong_operator_cannot_attest_drain(self):
        c,_=self.x.perform('disable');d=self.x.drain(c);d=self.x.sign(d['payload'],self.x.op)
        with self.assertRaises(LifecycleRejected):self.x.e.reconcile(d)
    def test_expired_or_wrong_checkpoint_drain_rejected(self):
        c,_=self.x.perform('disable');d=self.x.drain(c);self.x.now+=61
        with self.assertRaises(LifecycleRejected):self.x.e.reconcile(d)
        d=self.x.drain(c);d['payload']['expected_state']='f'*64;d=self.x.sign(d['payload'],self.x.dr)
        with self.assertRaises(LifecycleRejected):self.x.e.reconcile(d)
    def test_reconciliation_provider_failure_keeps_original_pending_record(self):
        c,_=self.x.perform('disable');before=rows(self.x.store)
        self.x.wire.status=503
        with self.assertRaises(StateUnavailable):self.x.e.reconcile(self.x.drain(c))
        self.assertEqual(rows(self.x.store),before)
    def test_result_audit_failure_is_recoverable_without_repeating_mutation(self):
        c=self.x.command();cas=self.x.audit.compare_exchange
        def fail(k,g,v):
            if k==key('key-lifecycle-finished',digest(c)):raise StateUnavailable('synthetic receipt failure')
            return cas(k,g,v)
        with patch.object(self.x.audit,'compare_exchange',side_effect=fail),self.assertRaises(StateUnavailable):self.x.e.execute(c)
        self.assertEqual(self.x.e.inspect()['phase'],'observed')
        self.x.e.reconcile(self.x.drain(c));self.x.e.execute(c);self.assertEqual(len(self.x.mutations()),1)
    def test_original_audit_corruption_blocks_before_provider(self):
        self.x.perform('disable');before=self.x.wire.calls.copy()
        with patch.object(self.x.audit,'load',return_value=None),self.assertRaises(StateUnavailable):self.x.e.inspect()
        self.assertEqual(before,self.x.wire.calls)
    def test_original_receipt_has_not_been_relabelled_as_current_after_restore(self):
        self.x.perform('disable');c,s=self.x.perform('schedule');old=self.x.e
        self.x.perform('restore');self.assertEqual(old.execute(c),s)
        self.assertEqual(self.x.e.inspect()['provider_state'],'DISABLED');self.assertEqual(s['provider_state'],'DESTROY_SCHEDULED')
    def test_two_dispatchers_only_one_mutation(self):
        one=self.x.e;two=self.x.restart();a=self.x.command();b=self.x.command();barrier=threading.Barrier(2)
        describe=self.x.provider.describe;lock=threading.Lock();count=[0]
        def wait():
            r=describe()
            with lock:count[0]+=1;n=count[0]
            if n<=2:barrier.wait(timeout=10)
            return r
        def run(e,c):
            try:return e.execute(c)['phase']
            except StateUnavailable:return 'denied'
        with patch.object(self.x.provider,'describe',side_effect=wait),ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(run,one,a),pool.submit(run,two,b)];results=[x.result(15) for x in futures]
        self.assertEqual(sorted(results),['denied','observed']);self.assertEqual(len(self.x.mutations()),1)
    def test_lost_status_write_readback_does_not_repeat_rpc(self):
        c=self.x.command();cas=self.x.store.compare_exchange
        def lost(k,g,v):
            result=cas(k,g,v)
            if v['phase']=='observed':raise StateUnavailable('synthetic status acknowledgement lost')
            return result
        with patch.object(self.x.store,'compare_exchange',side_effect=lost):r=self.x.e.execute(c)
        self.assertEqual(r['phase'],'observed');self.assertEqual(len(self.x.mutations()),1)
    def test_cancel_destruction_does_not_enable_key(self):
        self.x.perform('disable');self.x.perform('schedule');self.x.perform('restore')
        self.assertEqual(self.f.kms.states[V1],'DISABLED')
        self.assertFalse(any(b and b'ENABLED' in b for _,_,_,b in self.x.mutations()))
    def test_provider_metadata_imported_or_wrong_resource_rejected(self):
        for field,value in [('name',V2),('algorithm','RSA_SIGN_PSS_2048_SHA256'),('protectionLevel','HSM'),('importJob','foreign'),('reimportEligible',True)]:
            with self.subTest(field=field):
                self.x.wire.metadata=lambda r:dict(r,**{field:value})
                with self.assertRaises(StateUnavailable):self.x.provider.describe()
        self.assertEqual(self.x.mutations(),[])
    def test_provider_replies_do_not_leak_error_body(self):
        self.x.wire.status=403
        with self.assertRaises(StateUnavailable) as e:self.x.provider.describe()
        self.assertNotIn('private',str(e.exception));self.assertEqual(self.x.mutations(),[])
    def test_provider_get_and_mutation_each_have_one_application_attempt(self):
        self.x.wire.status=429;before=len(self.x.wire.calls)
        with self.assertRaises(StateUnavailable):self.x.provider.describe()
        self.assertEqual(len(self.x.wire.calls),before+1)
    def test_unknown_mutation_action_does_not_send(self):
        before=self.x.wire.calls.copy()
        for a in ('enable','create','rotate','import','encrypt','decrypt','destroy'):
            with self.assertRaises(LifecycleRejected):self.x.provider.dispatch(a)
        self.assertEqual(self.x.wire.calls,before)
    def test_missing_destroy_time_is_not_a_confirmed_schedule(self):
        self.f.kms.states[V1]='DESTROY_SCHEDULED'
        with self.assertRaises(StateUnavailable):self.x.provider.describe()
    def test_bad_timestamp_does_not_enter_audit(self):
        self.x.wire.metadata=lambda r:dict(r,destroyTime={'untrusted':'material'})
        with self.assertRaises(StateUnavailable):self.x.provider.describe()
    def test_audit_failure_before_claim_does_not_mutate_provider(self):
        c=self.x.command()
        with patch.object(self.x.audit,'compare_exchange',side_effect=StateUnavailable('audit unavailable')),self.assertRaises(StateUnavailable):self.x.e.execute(c)
        self.assertEqual(self.x.mutations(),[])
