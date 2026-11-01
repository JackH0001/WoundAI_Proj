"""Real signatures and SQLite CAS; synthetic Apple and GCS transport only."""
import base64
import copy
import unittest
from unittest.mock import patch
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from lite_attest_state import SQLiteStateStore, GCSStateStore, StateUnavailable
from lite_usage_ledger import digest, UsageConflict
from lite_subscription_executor import (SignedEvidence, StoredObservation, SubscriptionMigrationExecutor,
    EvidenceUnavailable, EvidenceContradiction, make_manifest, canonical, key)
import test_lite_subscription_migration as migration
from test_lite_subscription_grace import rows
from test_lite_attest_state import FakeBucket

SOURCE='8d4df18a0246cda0959e3fd0f3adfec90821e7a6'
IMAGE='e'*64


class Fixture:
    def __init__(self, host, gcs=False):
        self.base=migration.MigrationTests();self.base.setUp();host.addCleanup(self.base.doCleanups)
        b=self.base;self.a=b.a;self.links=b.links;self.accounts=b.accounts
        self.accounts.provision('free-device','free-subject','20000000-0000-4000-8000-000000000001',None,'a'*64)
        self.approver=Ed25519PrivateKey.generate();self.observer=Ed25519PrivateKey.generate()
        self.evidence=SignedEvidence({'operator':self.approver.public_key().public_bytes_raw()},
            {'observer':self.observer.public_key().public_bytes_raw()},clock=lambda:self.a.now)
        self.buckets=[]
        def store(name):
            if gcs:
                bucket=FakeBucket();self.buckets.append(bucket)
                return GCSStateStore(bucket,prefix='synthetic-'+name)
            return SQLiteStateStore(str(b.f.root/('executor-'+name+'.sqlite')))
        self.control,self.audit,self.observations=store('control'),store('audit'),store('observations')
        self.manifest=make_manifest(self.links,self.accounts,['first-device','free-device'],
            namespace='synthetic-batch',source=SOURCE,image=IMAGE,inventory='b'*64,fence='f'*64,operation='c'*64)
        self.observation=StoredObservation(self.observations,self.evidence,self.manifest['namespace'])
        self.executor=SubscriptionMigrationExecutor(manifest=self.manifest,links=self.links,registry=self.accounts,
            state_store=self.control,audit_store=self.audit,observation=self.observation,evidence=self.evidence,
            expected_source=SOURCE,expected_image=IMAGE)
        self.links=self.executor.links  # private batch control; runtime reader is unchanged
        self.publish();self.n=0

    def sign(self,p,role='approval'):
        private,name=(self.approver,'operator') if role=='approval' else (self.observer,'observer')
        return dict(key_id=name,payload=copy.deepcopy(p),signature=base64.b64encode(private.sign(canonical(p))).decode())

    def approval(self,action='start',**changes):
        self.n+=1
        p=dict(kind='lite-migration-approval/1',manifest=digest(self.manifest),action=action,
            expected_state=self.executor.state_digest(),issued_at=self.a.now,expires_at=self.a.now+900,nonce=f'{self.n:064x}')
        p.update(changes);return self.sign(p)

    def publish(self,**changes):
        m=self.manifest
        p=dict(kind='lite-migration-observation/1',namespace=m['namespace'],manifest=digest(m),
            source=m['source'],image=m['image'],inventory=m['inventory'],fence=m['fence'],legacy_disabled=True,
            legacy_inflight=0,migration_inflight=0,stable_reader=True,issued_at=self.a.now,expires_at=self.a.now+30)
        p.update(changes);k=key('observation',m['namespace']);old=self.observations.load(k)
        self.observations.compare_exchange(k,old[0] if old else 0,self.sign(p,'observation'))

    def active(self):return self.executor.advance(self.approval())

    def account(self,device='first-device'):return self.accounts.resolve(device)

    def fail_second(self):
        real=self.links.execute
        def interrupted(plan,*args,**kwargs):
            if plan==self.manifest['entries'][1]['plan']:raise StateUnavailable('synthetic second-account outage')
            return real(plan,*args,**kwargs)
        with patch.object(self.links,'execute',side_effect=interrupted):
            with self.base.assertRaises(StateUnavailable):self.active()


class ExecutorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):migration.MigrationTests.setUpClass()

    def setUp(self):self.f=Fixture(self);self.e=self.f.executor

    def test_valid_batch_activates_only_after_all_exact_completions(self):
        f=self.f;seen=[];real=f.links.execute
        def inspect(plan,*args,**kwargs):
            seen.append(self.e.inspect()['phase']);return real(plan,*args,**kwargs)
        protected=[rows(s) for s in (f.accounts.store,f.base.f.ledger.store)]
        old=dict((r[0],r) for r in rows(f.base.f.audit))
        with patch.object(f.links,'execute',side_effect=inspect):r=f.active()
        self.assertEqual((r['phase'],r['next']),('active',2));self.assertEqual(seen,['running','running'])
        self.assertEqual(protected,[rows(s) for s in (f.accounts.store,f.base.f.ledger.store)])
        self.assertTrue(old.items()<=dict((r[0],r) for r in rows(f.base.f.audit)).items())
        self.assertEqual(f.accounts.grant(f.account(),f.a.now).limit,10)
        self.assertEqual(f.accounts.grant(f.account('free-device'),f.a.now).limit,2)

    def test_manifest_preparation_does_not_write_any_store(self):
        f=self.f;before=[rows(s) for s in (f.base.f.store,f.base.f.audit,f.accounts.store,f.control,f.audit)]
        make_manifest(f.accounts.subscription_links,f.accounts,['first-device'],namespace='another',source=SOURCE,image=IMAGE,
            inventory='b'*64,fence='f'*64,operation='1'*64)
        self.assertEqual(before,[rows(s) for s in (f.base.f.store,f.base.f.audit,f.accounts.store,f.control,f.audit)])

    def test_duplicate_account_devices_and_oversized_inventory_rejected(self):
        f=self.f
        for devices in ([],['first-device','second-device'],['first-device']*101):
            with self.subTest(devices=len(devices)),self.assertRaises((ValueError,UsageConflict)):
                make_manifest(f.accounts.subscription_links,f.accounts,devices,namespace='another',source=SOURCE,image=IMAGE,
                    inventory='b'*64,fence='f'*64,operation='1'*64)

    def test_unsigned_or_tampered_approval_cannot_write(self):
        a=self.f.approval();a['payload']['nonce']='0'*64
        for envelope in ({},a,None):
            with self.subTest(envelope=envelope),self.assertRaises(EvidenceUnavailable):self.e.advance(envelope)
        self.assertEqual(rows(self.f.control),[]);self.assertEqual(rows(self.f.audit),[])

    def test_observer_key_cannot_approve_and_roles_cannot_share_key(self):
        f=self.f;p=f.approval()['payload']
        with self.assertRaises(EvidenceUnavailable):self.e.advance(f.sign(p,'observation'))
        raw=f.approver.public_key().public_bytes_raw()
        with self.assertRaises(ValueError):SignedEvidence({'a':raw},{'b':raw})

    def test_expired_future_or_long_lived_approval_rejected(self):
        n=self.f.a.now
        for change in ({'expires_at':n},{'issued_at':n+1},{'expires_at':n+901}):
            with self.subTest(change=change),self.assertRaises(EvidenceUnavailable):self.e.advance(self.f.approval(**change))
        self.assertEqual(rows(self.f.control),[])

    def test_approval_wrong_manifest_action_or_snapshot_rejected(self):
        for change in ({'manifest':'0'*64},{'action':'resume'},{'expected_state':'0'*64}):
            with self.subTest(change=change),self.assertRaises(EvidenceContradiction):self.e.advance(self.f.approval(**change))
        self.assertEqual(rows(self.f.control),[])

    def test_fresh_signed_but_wrong_scope_or_unsafe_observation_rejected(self):
        for change in ({'source':'0'*40},{'image':'0'*64},{'inventory':'0'*64},{'fence':'0'*64},
                       {'legacy_disabled':False},{'legacy_inflight':1},{'migration_inflight':1},{'stable_reader':False}):
            self.f.publish(**change)
            with self.subTest(change=change),self.assertRaises(EvidenceContradiction):self.e.advance(self.f.approval())
        self.assertEqual(rows(self.f.control),[])

    def test_malformed_or_expired_observation_rejected_before_write(self):
        for change in ({'legacy_disabled':1},{'legacy_inflight':False},{'expires_at':self.f.a.now}):
            self.f.publish(**change)
            with self.subTest(change=change),self.assertRaises(EvidenceUnavailable):self.e.advance(self.f.approval())
        self.assertEqual(rows(self.f.control),[])

    def test_partial_failure_pauses_and_preserves_completed_first_account(self):
        f=self.f;f.fail_second();self.assertEqual(self.e.inspect()['phase'],'paused')
        self.assertEqual(self.e.inspect()['next'],1)
        self.assertFalse(self.e.admission_allowed(f.account()))
        with self.assertRaises(StateUnavailable):f.accounts.grant(f.account(),f.a.now)
        self.assertIsNotNone(f.links._read(f.base.f.subject,f.base.f.token))

    def test_resume_requires_new_signed_state_and_drain(self):
        f=self.f;old=f.approval();f.fail_second()
        with self.assertRaises(EvidenceContradiction):self.e.advance(old,resume=True)
        f.publish(migration_inflight=1)
        with self.assertRaises(EvidenceContradiction):self.e.advance(f.approval('resume'),resume=True)
        f.publish()
        with patch.object(f.a.client,'get_all_subscription_statuses',side_effect=TimeoutError()) as apple:
            r=self.e.advance(f.approval('resume'),resume=True)
        self.assertEqual(apple.call_count,0);self.assertEqual(r['phase'],'active')

    def test_lost_account_completion_response_resumes_same_intent_without_apple(self):
        f=self.f;real=f.links.execute
        def lost(plan,*args,**kwargs):
            real(plan,*args,**kwargs);raise StateUnavailable('committed response lost')
        with patch.object(f.links,'execute',side_effect=lost),self.assertRaises(StateUnavailable):f.active()
        self.assertEqual(self.e.inspect()['next'],0)
        original=rows(f.base.f.store)
        with patch.object(f.a.client,'get_all_subscription_statuses',side_effect=TimeoutError()) as apple:
            self.e.advance(f.approval('resume'),resume=True)
        self.assertEqual(apple.call_count,0)
        self.assertTrue(dict((r[0],r) for r in original).items()<=dict((r[0],r) for r in rows(f.base.f.store)).items())

    def test_lost_control_response_is_contained_and_explicitly_resumable(self):
        f=self.f;real=f.control.compare_exchange;lost=[False]
        def write(k,rev,p):
            result=real(k,rev,p)
            if p.get('phase')=='running' and not lost[0]:lost[0]=True;raise StateUnavailable('lost control response')
            return result
        with patch.object(f.control,'compare_exchange',side_effect=write),self.assertRaises(StateUnavailable):f.active()
        self.assertEqual(self.e.inspect()['phase'],'paused')
        self.assertEqual(self.e.advance(f.approval('resume'),resume=True)['phase'],'active')

    def test_observation_outage_while_active_only_reports_and_never_rolls_back(self):
        f=self.f;f.active();before=[rows(f.control),rows(f.audit)]
        with patch.object(f.observations,'load',side_effect=TimeoutError()):
            self.assertEqual(self.e.inspect(),dict(phase='active',next=2,observation='unavailable'))
            self.assertFalse(self.e.admission_allowed(f.account()))
            with self.assertRaises(EvidenceUnavailable):self.e.advance()
        self.assertEqual(before,[rows(f.control),rows(f.audit)])

    def test_contradiction_distinguished_from_unavailable_without_mutation(self):
        f=self.f;f.active();before=rows(f.control);f.publish(legacy_disabled=False)
        self.assertEqual(self.e.inspect()['observation'],'contradictory')
        with self.assertRaises(EvidenceContradiction):self.e.advance()
        self.assertEqual(before,rows(f.control))

    def test_explicit_pause_does_not_require_observer_and_keeps_reader(self):
        f=self.f;f.active();account=f.account();before=rows(f.base.f.store)
        with patch.object(f.observations,'load',side_effect=TimeoutError()):self.e.pause(f.approval('pause'))
        self.assertEqual(f.account(),account);self.assertEqual(before,rows(f.base.f.store))
        with self.assertRaises(StateUnavailable):f.accounts.grant(account,f.a.now)

    def test_active_repeat_does_not_repeat_writes_or_query_apple(self):
        f=self.f;first=f.active();before=[rows(s) for s in (f.control,f.audit,f.base.f.store,f.base.f.audit)]
        with patch.object(f.a.client,'get_all_subscription_statuses',side_effect=TimeoutError()) as apple:
            self.assertEqual(self.e.advance(),first)
        self.assertEqual(apple.call_count,0)
        self.assertEqual(before,[rows(s) for s in (f.control,f.audit,f.base.f.store,f.base.f.audit)])

    def test_outside_manifest_account_never_uses_new_admission(self):
        f=self.f;f.active()
        self.assertFalse(self.e.admission_allowed(dict(subject='outside')))
        self.assertFalse(self.e.admission_allowed({}))

    def test_stale_observer_closes_admission_without_reverting_state(self):
        f=self.f;f.active();f.a.now+=30
        self.assertFalse(self.e.admission_allowed(f.account()));self.assertEqual(self.e.inspect()['phase'],'active')
        f.publish();self.assertTrue(self.e.admission_allowed(f.account()))

    def test_changed_registration_stops_batch_and_does_not_overwrite(self):
        f=self.f;real=f.accounts.registration
        def changed(device):
            value=copy.deepcopy(real(device));value['proof']='0'*64;return value
        before=rows(f.base.f.store)
        with patch.object(f.accounts,'registration',side_effect=changed),self.assertRaises(Exception):f.active()
        self.assertEqual(self.e.inspect()['phase'],'paused');self.assertEqual(before,rows(f.base.f.store))

    def test_fence_lost_during_progress_blocks_activation(self):
        f=self.f;real=f.links.execute
        def execute(*args,**kwargs):
            result=real(*args,**kwargs);f.publish(legacy_disabled=False);return result
        with patch.object(f.links,'execute',side_effect=execute),self.assertRaises(EvidenceContradiction):f.active()
        self.assertEqual(self.e.inspect()['phase'],'paused')

    def test_approval_expiring_mid_batch_stops_and_preserves_receipt(self):
        f=self.f;real=f.links.execute
        def execute(*args,**kwargs):
            result=real(*args,**kwargs);f.a.now+=901;f.publish();return result
        with patch.object(f.links,'execute',side_effect=execute),self.assertRaises(EvidenceUnavailable):f.active()
        self.assertEqual(self.e.inspect()['phase'],'paused')

    def test_audit_outage_cannot_activate_and_never_deletes_old_audit(self):
        f=self.f;before=rows(f.base.f.audit)
        with patch.object(f.audit,'compare_exchange',side_effect=StateUnavailable('audit denied')):
            with self.assertRaises(StateUnavailable):f.active()
        self.assertEqual(rows(f.control),[]);self.assertEqual(before,rows(f.base.f.audit))

    def test_unconfirmed_active_audit_blocks_runtime_admission(self):
        f=self.f;f.active()
        with patch.object(f.audit,'load',return_value=None):self.assertFalse(self.e.admission_allowed(f.account()))

    def test_control_read_outage_blocks_runtime_admission(self):
        f=self.f;f.active()
        with patch.object(f.control,'load',side_effect=StateUnavailable('offline')):
            self.assertFalse(self.e.admission_allowed(f.account()))

    def test_corrupt_control_is_not_replaced(self):
        f=self.f;f.control.compare_exchange(self.e.control_key,0,dict(v=1,phase='active'))
        before=rows(f.control)
        with self.assertRaises(StateUnavailable):self.e.advance(f.sign({}))
        self.assertEqual(before,rows(f.control));self.assertFalse(self.e.admission_allowed({}))

    def test_core_authority_hash_alone_cannot_execute(self):
        f=self.f;p=f.manifest['entries'][0]['plan']
        with self.assertRaises(StateUnavailable):f.links.execute(p,self.e.registry,'first-device',
            authorization_sha256=self.e._account_authorization(p))
        self.assertIsNone(f.links._read(f.base.f.subject,f.base.f.token))

    def test_pause_after_active_then_resume_keeps_pointers_and_ledger(self):
        f=self.f;f.active();self.e.pause(f.approval('pause'))
        before=[rows(f.base.f.store),rows(f.base.f.ledger.store)]
        self.assertEqual(self.e.advance(f.approval('resume'),resume=True)['phase'],'active')
        self.assertEqual(before,[rows(f.base.f.store),rows(f.base.f.ledger.store)])

    def test_pause_racing_account_completion_prevents_activation(self):
        f=self.f;real=f.links.execute
        def execute(*args,**kwargs):
            result=real(*args,**kwargs);self.e.pause(f.approval('pause'));return result
        with patch.object(f.links,'execute',side_effect=execute),self.assertRaises(StateUnavailable):f.active()
        self.assertEqual(self.e.inspect()['phase'],'paused');self.assertFalse(self.e.admission_allowed(f.account()))

    def test_running_after_crash_requires_explicit_pause_not_blind_resume(self):
        f=self.f;a=f.approval();self.e._move(None,'running',0,digest(a))
        with self.assertRaises(UsageConflict):self.e.advance(f.approval('resume'),resume=True)
        self.e.pause(f.approval('pause'));f.publish()
        self.assertEqual(self.e.advance(f.approval('resume'),resume=True)['phase'],'active')

    def test_constructor_rejects_source_mismatch_before_binding(self):
        f=self.f
        with self.assertRaises(UsageConflict):SubscriptionMigrationExecutor(manifest=f.manifest,links=f.links,
            registry=f.accounts,state_store=f.control,audit_store=f.audit,observation=f.observation,evidence=f.evidence,
            expected_source='0'*40,expected_image=IMAGE)

    def test_transport_uses_generation_cas_without_list_or_delete(self):
        f=Fixture(self,gcs=True);f.active();f.executor.pause(f.approval('pause'))
        f.executor.advance(f.approval('resume'),resume=True)
        calls=[c for b in f.buckets for c in b.calls]
        self.assertTrue(calls);self.assertTrue(all(c[0] in ('reload','download','upload') for c in calls))
        self.assertTrue(all('if_generation_match' in c[2] for c in calls if c[0]=='upload'))

    def test_expired_uncommitted_intent_pauses_without_silently_rotating_plan(self):
        f=self.f;real=f.links.store.compare_exchange
        def fail_pointer(k,rev,p):
            if k==f.links.key(f.base.f.subject):raise StateUnavailable('before pointer')
            return real(k,rev,p)
        with patch.object(f.links.store,'compare_exchange',side_effect=fail_pointer),self.assertRaises(StateUnavailable):f.active()
        retained=rows(f.base.f.audit);f.a.now+=61;f.publish()
        with self.assertRaises(Exception):self.e.advance(f.approval('resume'),resume=True)
        self.assertEqual(retained,rows(f.base.f.audit));self.assertEqual(self.e.inspect()['phase'],'paused')
        self.assertEqual(self.e.inspect()['next'],0)

    def test_stale_start_cas_loser_does_not_pause_winning_command(self):
        f=self.f;real=f.control.compare_exchange;winner='d'*64
        def other(k,rev,p):
            if rev==0:
                alternate=copy.deepcopy(p);alternate['attempt']=winner;real(k,rev,alternate);return False
            return real(k,rev,p)
        with patch.object(f.control,'compare_exchange',side_effect=other),self.assertRaises(StateUnavailable):f.active()
        self.assertEqual(self.e._read()[1]['attempt'],winner)
        self.assertEqual(self.e.inspect()['phase'],'running')

    def test_fake_gcs_lost_write_response_reconciles_without_delete(self):
        f=Fixture(self,gcs=True);f.buckets[0].lose_response=True
        with self.assertRaises(StateUnavailable):f.active()
        f.buckets[0].lose_response=False
        # The adapter reports ambiguity; inspect retained CAS state before a new command.
        current=f.executor.inspect()['phase']
        if current!='paused':f.executor.pause(f.approval('pause'))
        self.assertEqual(f.executor.advance(f.approval('resume'),resume=True)['phase'],'active')
        self.assertFalse(any(c[0]=='delete' for b in f.buckets for c in b.calls))


if __name__=='__main__':unittest.main()
