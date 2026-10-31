"""Real SQLite/CAS and signed HTTP; generated media and no live cloud calls."""
import copy
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import patch
from lite_account_research_closure import AccountResearchClosure
from lite_account_identity import IdentityRejected
from lite_attest_state import StateUnavailable
from lite_fenced_store import FencedPrivacy
from lite_privacy_state import OwnerWithdrawn
from lite_reconsent import Reconsent, ReconsentRejected, CONSENT, DISCLOSURE
from lite_subscription_executor import key
from lite_usage_ledger import digest
from lite_disposition_progress_probe import ProgressFixture
from test_lite_subscription_grace import rows
import test_lite_fenced_objects as objects
import test_lite_native_purchase as native
import test_lite_reconsent as signed_fixture


class ClosureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):native.legacy.LinksTests.setUpClass()
    def setUp(self):
        self.f=ProgressFixture(self);self.owner='a'*32;self.f.f.login(self.owner)
        self.bucket=objects.Bucket();self.r=Reconsent(FencedPrivacy(self.f.store('research'),self.bucket))
        self.jobs=self.f.store('research-closure');self.audit=self.f.store('research-closure-audit')
        self.w=self.worker()
    def worker(self):return AccountResearchClosure(deletion=self.f.d,research=self.r,store=self.jobs,audit_store=self.audit)
    def advance(self,**kw):return self.w.advance(self.f.person,**kw)
    def rejoin(self,new='b'*32,previous='legacy'):
        return self.r.resume(self.owner,dict(anon_id=self.owner,research_consent=True,
            previous_epoch=previous,new_epoch=new,consent_version=CONSENT,disclosure_version=DISCLOSURE))
    def put(self,epoch='legacy'):
        p=self.r.privacy.scoped(epoch);ticket=p.begin_write(self.owner)
        p.media(self.owner,ticket).put_blob('lite/'+self.owner+'/'+'f'*16+'.jpg',b'original synthetic RGB')
        p.finish_write(ticket)
    def test_requires_saved_closure_and_original_audit(self):
        with self.assertRaises(IdentityRejected):self.advance()
        self.f.close();self.f.f.lifecycle.audit=self.f.store('empty-source-audit')
        with self.assertRaises(StateUnavailable):self.advance()
        self.assertIsNone(self.r.retirement(self.owner))
    def test_legacy_retirement_preserves_media_and_closes_writes(self):
        self.put();self.f.close();media=copy.deepcopy((self.bucket.live,self.bucket.history))
        original=self.f.preserved();provider=copy.deepcopy((self.f.f.g.calls,self.f.f.kms.calls))
        result=self.advance()
        self.assertTrue(result['scan_finished']);self.assertEqual(result['linked_grants'],1)
        self.assertEqual(result['unresolved_grants'],1) # synthetic device-one is not a research ID
        self.assertFalse(result['account_deleted']);self.assertEqual(result['media_disposition'],'not_started')
        self.assertEqual(media,(self.bucket.live,self.bucket.history));self.assertEqual(original,self.f.preserved())
        self.assertEqual(provider,(self.f.f.g.calls,self.f.f.kms.calls))
        with self.assertRaises(ReconsentRejected):self.r.resolve(self.owner,'legacy')
        with self.assertRaises(OwnerWithdrawn):self.r.privacy.begin_write(self.owner)
        with self.assertRaises(ReconsentRejected):self.rejoin()
    def test_completed_reconsent_preserves_epoch_and_original_consent(self):
        self.r.privacy.manifest.withdraw(self.owner);self.rejoin();self.put('b'*32)
        old=self.r.load(self.owner);audit=self.r.store.load(self.r.key(self.owner,'b'*32))
        self.f.close();self.advance()
        self.assertEqual(self.r.load(self.owner)[1:],old[1:])
        self.assertEqual(self.r.store.load(self.r.key(self.owner,'b'*32)),audit)
        self.assertTrue(self.r.privacy.scoped('b'*32).is_withdrawn(self.owner))
        with self.assertRaises(ReconsentRejected):self.rejoin('c'*32,'b'*32)
    def test_restart_idle_status_and_replay_do_not_grow_audit(self):
        self.f.close();a=self.advance();before=rows(self.jobs),rows(self.audit),rows(self.r.store)
        self.w=self.worker();self.assertEqual(a,self.w.status(self.f.person));self.assertEqual(a,self.advance())
        self.assertEqual(before,(rows(self.jobs),rows(self.audit),rows(self.r.store)))
    def test_duplicate_grants_join_same_installation_retirement(self):
        self.f.f.login(self.owner);self.f.close();r=self.advance()
        self.assertEqual(r['linked_grants'],2);self.assertIsNotNone(self.r.retirement(self.owner))
    def test_other_account_and_unlinked_owner_untouched(self):
        self.f.f.login('c'*32,'other-person');self.put();self.f.close();self.advance()
        self.assertIsNone(self.r.retirement('c'*32));self.assertIsNone(self.r.retirement('d'*32))
    def test_missing_registration_reported_without_guessing(self):
        self.f.f.lifecycle.reserve_grant(self.f.person,'c'*32,'e'*32)
        self.f.close();r=self.advance();self.assertEqual(r['unresolved_grants'],2)
        self.assertIsNone(self.r.retirement('c'*32))
    def test_foreign_registration_stops_without_retirement(self):
        reg=self.f.f.b.registry;rev,v=reg.store.load(reg.key(self.owner))
        reg.store.compare_exchange(reg.key(self.owner),rev,dict(v,subject='apple-'+'f'*64))
        self.f.close()
        with self.assertRaises(StateUnavailable):self.advance()
        self.assertIsNone(self.r.retirement(self.owner))
    def test_missing_membership_proof_stops_before_retirement(self):
        self.f.close()
        with patch.object(self.f.f.lifecycle,'member',return_value=None):
            with self.assertRaises(StateUnavailable):self.advance()
        self.assertIsNone(self.r.retirement(self.owner))
    def test_audit_unavailable_prevents_research_mutation(self):
        self.f.close();before=rows(self.r.store)
        with patch.object(self.audit,'compare_exchange',side_effect=StateUnavailable('offline')):
            with self.assertRaises(StateUnavailable):self.advance()
        self.assertEqual(before,rows(self.r.store))
    def test_lost_retirement_reply_readback_succeeds(self):
        self.f.close();real=self.r.store.compare_exchange
        def lost(k,rev,value):
            result=real(k,rev,value)
            if value.get('v')==2:raise StateUnavailable('lost reply')
            return result
        with patch.object(self.r.store,'compare_exchange',side_effect=lost):self.advance()
        self.assertTrue(self.r.privacy.is_withdrawn(self.owner))
    def test_retirement_without_fence_reply_resumes_same_epoch(self):
        self.f.close();real=self.r.store.compare_exchange
        def fail(k,rev,value):
            if value.get('withdrawn') is True:raise StateUnavailable('fence unavailable')
            return real(k,rev,value)
        with patch.object(self.r.store,'compare_exchange',side_effect=fail):
            with self.assertRaises(StateUnavailable):self.advance()
        self.assertIsNotNone(self.r.retirement(self.owner))
        with self.assertRaises(ReconsentRejected):self.r.resolve(self.owner,'legacy')
        self.w=self.worker();self.advance();self.assertTrue(self.r.privacy.is_withdrawn(self.owner))
    def test_lost_job_reply_resolves_without_duplicate_research_retirement(self):
        self.f.close();real=self.jobs.compare_exchange
        def lost(*a):real(*a);raise StateUnavailable('lost')
        with patch.object(self.jobs,'compare_exchange',side_effect=lost):self.advance()
        self.assertEqual(self.w.status(self.f.person)['linked_grants'],1)
    def test_missing_checkpoint_audit_is_not_recreated(self):
        self.f.close();self.advance(limit=1);self.w.audit=self.f.store('absent-worker-audit')
        with self.assertRaises(StateUnavailable):self.advance()
        self.assertEqual(rows(self.w.audit),[])
    def test_changed_retirement_reference_rejected(self):
        self.f.close();self.advance()
        with self.assertRaises(StateUnavailable):self.r.retire(self.owner,closure_sha256='f'*64,association_sha256='e'*64)
    def test_tampered_retirement_evidence_fails_closed(self):
        self.f.close();self.advance();_,v=self.r.store.load(self.r.key(self.owner));ak=self.r.key(self.owner,'retired.'+v['retirement_sha256'])
        rev,a=self.r.store.load(ak);self.r.store.compare_exchange(ak,rev,dict(a,owner='f'*32))
        for call in (lambda:self.r.load(self.owner),lambda:self.r.resolve(self.owner,'legacy'),self.rejoin):
            with self.assertRaises(StateUnavailable):call()
    def test_already_admitted_plan_retained_for_future_media_cleanup(self):
        ticket=self.r.privacy.begin_write(self.owner)
        ref=objects.ObjectRef(self.owner,'image','f'*16);pin=self.r.privacy.objects.pin(ref)
        self.r.privacy.manifest.plan(ticket,pin)
        self.f.close();self.advance()
        # A write which already pinned before withdrawal may still land. This
        # coordinator retains the plan and does not claim media erasure/drain.
        self.r.privacy.objects.write(pin,b'already admitted synthetic write')
        self.assertTrue(self.r.privacy.manifest._load(self.owner)[1]['writers'])
        self.assertFalse(self.w.status(self.f.person)['account_deleted'])
    def test_more_than_one_batch_survives_restart(self):
        for n in range(11):self.f.f.login(f'{n+100:032x}')
        self.f.close();counts=[]
        while True:
            r=self.advance(limit=3);counts.append(r['examined_grants']);self.w=self.worker()
            if r['scan_finished']:break
        self.assertEqual(counts,[3,6,9,12,13]);self.assertEqual(r['linked_grants'],12)
    def test_two_workers_join_same_retirement_and_checkpoints(self):
        self.f.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:self.worker().advance(self.f.person),range(2)))
        self.assertTrue(all(x['scan_finished'] for x in results))
        self.assertEqual(self.w.status(self.f.person)['linked_grants'],1)
    def test_bounded_input_and_separate_scopes(self):
        self.f.close()
        for value in (0,6,True,'1'):
            with self.assertRaises(ValueError):self.advance(limit=value)
        for store in (self.r.store,self.f.d.store,self.f.f.b.store,self.audit):
            with self.assertRaises(ValueError):AccountResearchClosure(deletion=self.f.d,research=self.r,store=store,audit_store=self.audit)
    def test_reconsent_wins_first_retirement_includes_new_epoch(self):
        self.r.privacy.manifest.withdraw(self.owner);self.f.close();real=self.r.store.compare_exchange;seen=[]
        def race(k,rev,value):
            if value.get('v')==2 and not seen:
                seen.append(1);self.rejoin()
            return real(k,rev,value)
        with patch.object(self.r.store,'compare_exchange',side_effect=race):self.advance()
        self.assertEqual(self.r.load(self.owner)[1],'b'*32)
        self.assertTrue(self.r.privacy.scoped('b'*32).is_withdrawn(self.owner))
    def test_retirement_wins_prevents_inflight_reconsent_publish(self):
        self.r.privacy.manifest.withdraw(self.owner);self.f.close();real=self.r.store.compare_exchange;seen=[]
        def race(k,rev,value):
            if k==self.r.key(self.owner) and value.get('v')==1 and not seen:
                seen.append(1);self.advance()
            return real(k,rev,value)
        with patch.object(self.r.store,'compare_exchange',side_effect=race):
            with self.assertRaises(StateUnavailable):self.rejoin()
        self.assertEqual(self.r.load(self.owner)[1],'legacy')
        with self.assertRaises(ReconsentRejected):self.rejoin()
    def test_status_is_read_only_and_never_starts_closure(self):
        self.f.close();before=rows(self.r.store)
        with self.assertRaises(StateUnavailable):self.w.status(self.f.person)
        self.assertEqual(before,rows(self.r.store))
    def test_account_inventory_binds_retirement_proof_and_detects_later_drift(self):
        from lite_account_data_inventory import AccountDataInventory
        self.f.close();self.advance()
        i=AccountDataInventory(deletion=self.f.d,research=self.r,audit_store=self.f.store('inventory-audit'))
        report=i.inspect(self.f.person);h=self.r.store.load(self.r.key(self.owner))[1]['retirement_sha256']
        k=self.r.key(self.owner,'retired.'+h)
        self.assertTrue(any(x['key']==k.hex() for x in i.references(report['report_sha256'])))
        rev,value=self.r.store.load(k);self.r.store.compare_exchange(k,rev,value)
        with self.assertRaises(StateUnavailable):i.verify_unchanged(report['report_sha256'])
    def test_research_store_cannot_alias_account_store(self):
        r=Reconsent(FencedPrivacy(self.f.f.b.store,self.bucket))
        with self.assertRaises(ValueError):AccountResearchClosure(deletion=self.f.d,research=r,store=self.jobs,audit_store=self.audit)
    def test_retirement_audit_unknown_outcome_does_not_publish_without_readback(self):
        self.f.close();real=self.r.store.compare_exchange
        def fail(k,rev,value):
            if value.get('action')=='account_deletion':raise StateUnavailable('audit not committed')
            return real(k,rev,value)
        with patch.object(self.r.store,'compare_exchange',side_effect=fail):
            with self.assertRaises(StateUnavailable):self.advance()
        self.assertIsNone(self.r.retirement(self.owner));self.assertFalse(self.r.privacy.is_withdrawn(self.owner))
        self.advance();self.assertIsNotNone(self.r.retirement(self.owner))
    def test_lost_retirement_audit_reply_is_verified_before_publication(self):
        self.f.close();real=self.r.store.compare_exchange
        def lost(k,rev,value):
            result=real(k,rev,value)
            if value.get('action')=='account_deletion':raise StateUnavailable('audit committed reply lost')
            return result
        with patch.object(self.r.store,'compare_exchange',side_effect=lost):self.advance()
        self.assertIsNotNone(self.r.retirement(self.owner))
    def test_corrupt_checkpoint_is_not_reset_or_reported_complete(self):
        self.f.close();self.advance(limit=1);p=self.w._plan(self.f.person);k=self.w._key(p);rev,j=self.jobs.load(k)
        self.jobs.compare_exchange(k,rev,dict(j,remaining=True));before=rows(self.r.store)
        with self.assertRaises(StateUnavailable):self.advance()
        self.assertEqual(before,rows(self.r.store))
    def test_terminal_reference_validation_before_writes(self):
        before=rows(self.r.store)
        for h in (None,True,'x'*64,'a'*63):
            with self.assertRaises(ValueError):self.r.retire(self.owner,closure_sha256=h,association_sha256='a'*64)
        self.assertEqual(before,rows(self.r.store))
    def test_replay_reconsent_racing_retirement_cannot_report_rejoined(self):
        self.r.privacy.manifest.withdraw(self.owner);self.rejoin();self.f.close()
        original=self.r.privacy.scoped
        # Avoid altering the worker's own readback instance by injecting once.
        p=original('b'*32);method=p.is_withdrawn
        def race(owner):
            result=method(owner)
            with patch.object(self.r.privacy,'scoped',side_effect=original):self.advance()
            return result
        p.is_withdrawn=race
        with patch.object(self.r.privacy,'scoped',return_value=p):
            with self.assertRaises(ReconsentRejected):self.rejoin()

    def test_zero_grant_account_does_not_claim_erasure(self):
        p='e'*64;self.f.f.lifecycle.initialize_new(p,existing_account=False);self.f.f.lifecycle.close(p,'empty','a'*32)
        r=self.w.advance(p);self.assertTrue(r['scan_finished']);self.assertEqual(r['total_grants'],0)
        self.assertFalse(r['account_deleted']);self.assertFalse(r['historical_epochs_verified'])


class SignedRetirementTests(unittest.TestCase):
    def setUp(self):
        self.t=signed_fixture.ReconsentTests();self.t.setUp();self.addCleanup(self.t.doCleanups)
    def retire(self):return self.t.renew.retire(self.t.owner,closure_sha256='c'*64,association_sha256='d'*64)
    def test_signed_upload_and_rejoin_denied_without_running_model(self):
        self.t.clear();self.retire();before=copy.deepcopy(self.t.f.bucket.live)
        self.assertEqual(self.t.rejoin().status_code,409)
        self.assertEqual(self.t.f.upload().status_code,409)
        self.assertEqual(before,self.t.f.bucket.live)
    def test_signed_withdrawal_still_available_after_retirement(self):
        self.assertEqual(self.t.f.upload().status_code,200);self.retire()
        self.assertEqual(self.t.f.delete().status_code,200)
        self.assertIsNotNone(self.t.renew.retirement(self.t.owner))
    def test_retired_renewed_epoch_remains_deletable(self):
        self.t.clear();self.assertEqual(self.t.rejoin().status_code,200)
        self.assertEqual(self.t.new_upload().status_code,200);self.retire()
        path='/api/v1/lite/data/'+self.t.owner+'/'+self.t.new
        headers=self.t.h.headers(path,b'','',method='DELETE')
        self.assertEqual(self.t.h.app.test_client().delete(path,headers=headers).status_code,200)
        self.assertEqual(self.t.new_upload().status_code,409)
    def test_no_public_retirement_route(self):
        r=self.t.h.app.test_client().post('/api/v1/lite/research/retire',data=b'{}',content_type='application/json')
        self.assertEqual(r.status_code,404)
    def test_ordinary_withdrawal_still_permits_explicit_new_consent(self):
        self.t.clear();self.assertEqual(self.t.rejoin().status_code,200)
        self.assertEqual(self.t.new_upload().status_code,200)
    def test_restart_loads_retirement_and_preserves_original_receipt(self):
        self.t.clear();self.t.rejoin();old=self.t.renew.load(self.t.owner)[1:];self.retire()
        self.t.f.restart();r=Reconsent(self.t.f.privacy)
        self.assertEqual(r.load(self.t.owner)[1:],old)
        with self.assertRaises(ReconsentRejected):r.resume(self.t.owner,self.t.body())
