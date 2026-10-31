"""Real consent/CAS protocol with generated versioned RGB-D; no live cloud."""
import base64
import copy
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch
import google_crc32c
from google.api_core.exceptions import BadRequest, NotFound, PreconditionFailed
from lite_attest_state import GCSStateStore, StateUnavailable
from lite_fenced_objects import ObjectRef, GenerationConflict
from lite_fenced_store import FencedPrivacy
from lite_reconsent import Reconsent
try:from lite_research_history import ResearchMediaHistory
except ModuleNotFoundError as exc:
    if exc.name!='lite_research_history':raise
    ResearchMediaHistory=None
from lite_account_research_closure import AccountResearchClosure
from lite_subscription_executor import key
from lite_usage_ledger import digest
from test_lite_research_media_disposition import MediaFixture, PageBucket
from test_lite_fenced_objects import Blob
from test_lite_subscription_grace import rows
import test_lite_native_purchase as native


class VersionBucket(PageBucket):
    def __init__(self):
        super().__init__();self._properties={'versioning':{'enabled':True}};self.soft={}
        self.version_lists=[];self.deletes=[];self.mutate=lambda x:x;self.delete_hook=lambda x:None
        self.lost_delete=False;self.refuse=False;self.soft_unavailable=False;self.force_more=False;self.page_cap=100
    def reload(self,**kw):pass
    def blob(self,name,generation=None):return VersionBlob(self,name,generation)
    def list_blobs(self,**kw):
        if not (kw.get('versions') or kw.get('soft_deleted')):return super().list_blobs(**kw)
        self.version_lists.append(kw)
        if kw.get('soft_deleted') and self.soft_unavailable:raise BadRequest('unavailable')
        source=self.soft if kw.get('soft_deleted') else self.history
        with self.lock:items=[self.blob(n,g).fill(row) for (n,g),row in sorted(source.items()) if n.startswith(kw['prefix'])]
        cap=min(kw['max_results'],self.page_cap);more=len(items)>cap or self.force_more
        return SimpleNamespace(pages=iter([self.mutate(items[:cap])]),next_page_token='more' if more else None)


class VersionBlob(Blob):
    def fill(self,row):
        self.generation,raw,self.metadata=copy.deepcopy(row);self.size=len(raw)
        self.crc32c=base64.b64encode(google_crc32c.Checksum(raw).digest()).decode();self._properties={}
        return self
    def reload(self,**kw):
        b=self.bucket
        with b.lock:
            if b.reload_error:raise b.reload_error
            row=b.live.get(self.name) if self.generation is None else b.history.get((self.name,self.generation))
            if row is None:raise NotFound('absent')
            if kw.get('if_generation_match',row[0])!=row[0]:raise PreconditionFailed('different')
            self.fill(row)
    def delete(self,**kw):
        b=self.bucket;b.deletes.append(dict(name=self.name,generation=self.generation,**kw));b.delete_hook(self)
        if b.refuse:raise TimeoutError('not committed')
        with b.lock:
            assert self.generation and kw['if_generation_match']==self.generation
            k=(self.name,self.generation)
            if k not in b.history:raise NotFound('absent')
            if int(b._properties.get('softDeletePolicy',{}).get('retentionDurationSeconds',0)):b.soft[k]=b.history[k]
            del b.history[k]
            if b.live.get(self.name,(None,))[0]==self.generation:del b.live[self.name]
        if b.lost_delete:raise TimeoutError('committed response lost')


class HistoryFixture:
    def __init__(self,host):
        self.m=MediaFixture(host);m=self.m;self.bucket=VersionBucket();m.bucket=self.bucket
        m.r=Reconsent(FencedPrivacy(m.f.store('history-research-source'),self.bucket))
        m.closure=AccountResearchClosure(deletion=m.f.d,research=m.r,
            store=m.f.store('history-closure'),audit_store=m.f.store('history-closure-audit'))
        m.w=m.worker();self.jobs=m.f.store('history-jobs');self.audit=m.f.store('history-audit')
        self.policy=dict(v=1,id='synthetic-v1',bucket=self.bucket.name,prefixes=['lite_fenced/v1/','lite_epochs/v1/'])
        self.h=self.worker();self.ref=ObjectRef(m.owner,'image','1'*16)
    def worker(self):
        return None if ResearchMediaHistory is None else ResearchMediaHistory(media=self.m.w,store=self.jobs,audit_store=self.audit,policy=self.policy)
    def seed(self,count=3,current='legacy'):
        p=self.m.r.privacy.scoped(current)
        for i in range(count):
            t=p.begin_write(self.m.owner);p.manifest.write_registered(t,p.objects,self.ref,b'generated-'+str(i).encode());p.finish_write(t)
    def close(self):
        self.m.f.close();self.m.finish(limit=3)
        p=dict(v=1,config=self.m.w.config,closure=self.m.closure._plan(self.m.f.person))
        g=p['closure']['head']
        while g:
            n=self.m.f.d.lifecycle._node(self.m.f.person,g)
            if n['installation']==self.m.owner:self.grant=g;break
            g=n['previous']
        self.op=dict(grant=self.grant,epoch='legacy',kind=self.ref.kind,identifier=self.ref.identifier)
        self.name=self.m.r.privacy.objects.name(self.ref);self.seal=copy.deepcopy(self.bucket.live.get(self.name))
    def advance(self,**kw):return self.h.advance(self.m.f.person,self.op,**kw)
    def finish(self,versions=2,max_calls=500):
        for i in range(max_calls):
            r=self.h.advance_account(self.m.f.person,versions=versions,apply=True);self.h=self.worker()
            if r['scan_finished']:return r,i+1
        raise AssertionError('account history did not finish')


class ResearchHistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):native.legacy.LinksTests.setUpClass()
    def setUp(self):self.f=HistoryFixture(self);self.b=self.f.bucket
    def ready(self,count=3):self.f.seed(count);self.f.close()
    def test_dry_run_preserves_all_payloads_and_audit(self):
        self.ready();before=copy.deepcopy(self.b.history);a=rows(self.f.audit);j=rows(self.f.jobs)
        r=self.f.advance();s=self.f.h.advance_account(self.f.m.f.person)
        self.assertFalse(s['apply']);self.assertEqual(r['status'],'active_history_pending')
        self.assertEqual(before,self.b.history);self.assertEqual(a,rows(self.f.audit));self.assertEqual(j,rows(self.f.jobs))
    def test_exact_versions_removed_fence_capture_identity_and_consent_preserved(self):
        self.ready(8);f=self.f.m;before=rows(f.r.store),rows(f.audit),rows(f.closure.audit),f.f.preserved()
        r,calls=self.f.finish(versions=2)
        self.assertTrue(r['scan_finished']);self.assertGreater(calls,4);self.assertEqual(r['objects_checked'],1)
        self.assertEqual(len(self.b.history),1);self.assertEqual(self.b.live[self.f.name],self.f.seal)
        self.assertEqual(before,(rows(f.r.store),rows(f.audit),rows(f.closure.audit),f.f.preserved()))
        self.assertFalse(r['account_deleted']);self.assertFalse(r['physical_backups_erased']);self.assertEqual(r['unresolved_grants'],1)
    def test_more_than_fifty_versions_no_unbounded_call(self):
        self.ready(65)
        for _ in range(100):
            n=len(self.b.deletes);r=self.f.h.advance_account(self.f.m.f.person,versions=4,apply=True)
            self.assertLessEqual(len(self.b.deletes)-n,4)
            if r['scan_finished']:break
        self.assertTrue(r['scan_finished']);self.assertEqual(len(self.b.history),1)
        self.assertTrue(all(c['max_results']<=5 and c['page_size']<=5 for c in self.b.version_lists))
    def test_all_kinds_and_multiple_consent_epochs(self):
        m=self.f.m;m.seed(7);m.r.privacy.manifest.withdraw(m.owner);m.r.privacy.complete_withdrawal(m.owner)
        m.rejoin('legacy','1'*32);m.seed(7,ep='1'*32);m.r.privacy.scoped('1'*32).manifest.withdraw(m.owner)
        m.r.privacy.scoped('1'*32).complete_withdrawal(m.owner)
        m.rejoin('1'*32,'2'*32);m.seed(7,ep='2'*32);self.f.close()
        current=copy.deepcopy(self.b.live);r,_=self.f.finish()
        self.assertEqual(r['objects_checked'],21);self.assertEqual(current,self.b.live)
        self.assertEqual(len(self.b.history),21)
    def test_current_media_must_complete_first(self):
        self.f.seed();self.f.m.f.close()
        with self.assertRaises(StateUnavailable):self.f.h.advance_account(self.f.m.f.person,apply=True)
        self.assertEqual(self.b.deletes,[])
    def test_original_seal_evidence_missing_no_reconstruction(self):
        self.ready();self.f.h.w.audit=self.f.m.f.store('missing-evidence');before=copy.deepcopy(self.b.history)
        with self.assertRaises(StateUnavailable):self.f.advance(apply=True)
        self.assertEqual(before,self.b.history);self.assertEqual(self.b.deletes,[])
    def test_nonmember_wrong_epoch_or_object_does_not_authorize(self):
        self.ready();original=dict(self.f.op)
        for k,v in [('grant','f'*64),('epoch','f'*32),('identifier','d'*32),('kind','invalid')]:
            self.f.op=dict(original,**{k:v})
            with self.assertRaises((StateUnavailable,ValueError)):self.f.advance(apply=True)
        self.assertEqual(self.b.deletes,[])
    def test_unrelated_owner_or_prefix_preserved(self):
        self.f.m.seed(3,owner='c'*32);self.ready();other={k:v for k,v in self.b.history.items() if '/'+('c'*32)+'/' in k[0]}
        self.f.finish();self.assertTrue(all(self.b.history[k]==v for k,v in other.items()))
    def test_policy_and_scope_aliases_rejected(self):
        for p in (dict(self.f.policy,bucket='other'),dict(self.f.policy,prefixes=['']),dict(self.f.policy,v=True)):
            with self.assertRaises(ValueError):ResearchMediaHistory(media=self.f.m.w,store=self.f.jobs,audit_store=self.f.audit,policy=p)
        with self.assertRaises(ValueError):ResearchMediaHistory(media=self.f.m.w,store=self.f.m.jobs,audit_store=self.f.audit,policy=self.f.policy)
    def test_zero_byte_fence_and_large_media_are_valid(self):
        p=self.f.m.r.privacy;t=p.begin_write(self.f.m.owner)
        p.manifest.write_registered(t,p.objects,self.f.ref,b'x'*20000);p.finish_write(t)
        self.f.close();self.assertEqual(self.f.advance(apply=True)['status'],'listed_history_absent_at_check')
        self.assertEqual(self.b.live[self.f.name][1],b'')
    def test_lost_delete_reply_readback_same_generation(self):
        self.ready();self.b.lost_delete=True;self.f.finish()
        for c in self.b.deletes:
            self.assertEqual(c['generation'],c['if_generation_match']);self.assertLess(c['generation'],self.f.seal[0]);self.assertIsNone(c['retry'])
    def test_not_committed_delete_resumes_original_batch(self):
        self.ready();self.b.refuse=True
        with self.assertRaises(StateUnavailable):self.f.advance(apply=True)
        self.b.refuse=False;self.f.h=self.f.worker();self.f.finish();self.assertEqual(len(self.b.history),1)
    def test_lost_outcome_audit_repairs_deleted_version(self):
        self.ready();real=self.f.audit.compare_exchange
        def fail(k,g,v):
            if v.get('active_generation_absent'):raise StateUnavailable('lost outcome')
            return real(k,g,v)
        with patch.object(self.f.audit,'compare_exchange',side_effect=fail):
            with self.assertRaises(StateUnavailable):self.f.advance(apply=True)
        self.f.h=self.f.worker();self.f.finish();self.assertEqual(len(self.b.history),1)
    def test_lost_scan_checkpoint_reply_resumes(self):
        self.ready();real=self.f.jobs.compare_exchange
        def lost(k,g,v):real(k,g,v);raise StateUnavailable('lost reply')
        with patch.object(self.f.jobs,'compare_exchange',side_effect=lost):r,_=self.f.finish()
        self.assertTrue(r['scan_finished']);self.assertEqual(len(self.b.history),1)
    def test_two_workers_join_same_scan_and_version_batch(self):
        self.ready(12)
        def run(_):
            for i in range(100):
                try:r=self.f.worker().advance_account(self.f.m.f.person,versions=2,apply=True)
                except StateUnavailable:continue
                if r['scan_finished']:return r
            raise AssertionError('workers did not settle')
        with ThreadPoolExecutor(max_workers=2) as p:r=list(p.map(run,range(2)))
        self.assertTrue(all(v['scan_finished'] for v in r));self.assertEqual(len(self.b.history),1)
    def test_soft_history_stays_pending_then_explicit_rescan(self):
        self.ready();self.b._properties['softDeletePolicy']={'retentionDurationSeconds':'604800'}
        r,_=self.f.finish();self.assertEqual(r['soft_pending_slots'],1);self.assertFalse(r['account_deleted'])
        self.b.soft.clear() # independent synthetic provider expiry
        self.assertEqual(self.f.h.advance_account(self.f.m.f.person,apply=True)['soft_pending_slots'],1)
        self.f.h.advance_account(self.f.m.f.person,apply=True,rescan=True)
        r,_=self.f.finish();self.assertEqual(r['round'],2);self.assertEqual(r['soft_pending_slots'],0)
    def test_soft_inventory_unsupported_not_absence(self):
        self.ready();self.b.soft_unavailable=True;r,_=self.f.finish()
        self.assertEqual(r['soft_unverified_slots'],1);self.assertFalse(r['physical_backups_erased'])
    def test_retention_hold_or_bad_metadata_stops(self):
        self.ready()
        for prop in ({'retentionPolicy':{'isLocked':True}},{'defaultEventBasedHold':True}):
            self.b._properties=prop
            with self.assertRaises(StateUnavailable):self.f.advance(apply=True)
        self.b._properties={}
        def mutate(items):
            if items:items[0]._properties['temporaryHold']=True
            return items
        self.b.mutate=mutate
        with self.assertRaises(StateUnavailable):self.f.advance(apply=True)
        self.assertEqual(self.b.deletes,[])
    def test_empty_version_page_with_continuation_not_complete(self):
        self.ready();self.b.force_more=True;self.b.mutate=lambda _:[]
        self.assertFalse(self.f.advance(apply=True)['active_history_absent_at_check']);self.assertEqual(self.b.deletes,[])
    def test_current_fence_omitted_or_changed_fails(self):
        self.ready();self.b.mutate=lambda _:[]
        with self.assertRaises(StateUnavailable):self.f.advance(apply=True)
        self.b.mutate=lambda x:x;g,raw,md=self.b.live[self.f.name];self.b.live[self.f.name]=(g+1,raw,md)
        with self.assertRaises(StateUnavailable):self.f.advance(apply=True)
        self.assertEqual(self.b.deletes,[])
    def test_old_writer_pins_remain_rejected_after_history_removed(self):
        self.f.seed();p=self.f.m.r.privacy;pin=p.objects.pin(self.f.ref);self.f.close();self.f.finish()
        with self.assertRaises(GenerationConflict):p.objects.write(pin,b'late original write')
        self.assertEqual(self.b.live[self.f.name],self.f.seal)
    def test_no_audit_growth_when_idle_and_rescan_not_implicit(self):
        self.ready();self.f.finish();a=rows(self.f.audit);j=rows(self.f.jobs)
        self.f.h.advance_account(self.f.m.f.person,apply=True)
        self.assertEqual(a,rows(self.f.audit));self.assertEqual(j,rows(self.f.jobs))
    def test_corrupt_checkpoint_stops_without_reset(self):
        self.ready();self.f.h.advance_account(self.f.m.f.person,apply=True)
        p=self.f.h._plan(self.f.m.f.person);k=key('research-history-scan',digest(p));g,j=self.f.jobs.load(k)
        self.f.jobs.compare_exchange(k,g,dict(j,step=True));before=rows(self.f.jobs)
        with self.assertRaises(StateUnavailable):self.f.finish()
        self.assertEqual(before,rows(self.f.jobs));self.assertEqual(self.b.deletes,[])
    def test_original_scan_evidence_missing_stops(self):
        self.ready();self.f.h.advance_account(self.f.m.f.person,apply=True);self.f.h.audit=self.f.m.f.store('lost-history-audit')
        with self.assertRaises(StateUnavailable):self.f.h.advance_account(self.f.m.f.person,apply=True)
        self.assertEqual(self.b.deletes,[])
    def test_scan_empty_name_page_continuation_and_cycle(self):
        self.ready();self.b.override=lambda *args:SimpleNamespace(pages=iter([[]]),next_page_token='repeated')
        with self.assertRaises(StateUnavailable):self.f.finish()
        self.assertEqual(self.b.deletes,[])
    def test_mid_scan_cannot_rescan_or_use_invalid_limits(self):
        self.ready();self.f.h.advance_account(self.f.m.f.person,apply=True)
        with self.assertRaises(StateUnavailable):self.f.h.advance_account(self.f.m.f.person,apply=True,rescan=True)
        for v in (True,0,51):
            with self.assertRaises(ValueError):self.f.h.advance_account(self.f.m.f.person,versions=v,apply=True)

    def test_missing_only_object_intent_is_not_recreated(self):
        self.ready();real=self.f.h.w.audit.load;prior=rows(self.f.h.w.audit)
        p=self.f.h._plan(self.f.m.f.person);_,b,_=self.f.h._scope(p,self.f.grant,'legacy')
        identity=dict(v=1,bound_sha256=digest(b),epoch='legacy',kind=self.f.ref.kind,identifier=self.f.ref.identifier)
        missing_key=key('research-media-object-intent',digest(identity));self.assertIsNotNone(real(missing_key))
        def missing(k):return None if k==missing_key else real(k)
        with patch.object(self.f.h.w.audit,'load',side_effect=missing):
            with self.assertRaises(StateUnavailable):self.f.advance(apply=True)
        self.assertEqual(self.b.deletes,[]);self.assertEqual(prior,rows(self.f.h.w.audit))
    def test_missing_epoch_visit_cannot_guess_from_prefix(self):
        self.ready();real=self.f.h.w.audit.load
        p=self.f.h._plan(self.f.m.f.person);_,b,_=self.f.h._scope(p,self.f.grant,'legacy')
        missing_key=key('research-media-epoch',digest([b,'legacy']));self.assertIsNotNone(real(missing_key))
        with patch.object(self.f.h.w.audit,'load',side_effect=lambda k:None if k==missing_key else real(k)):
            with self.assertRaises(StateUnavailable):self.f.advance(apply=True)
        self.assertEqual(self.b.deletes,[])
    def test_missing_retirement_proof_cannot_delete(self):
        self.ready();real=self.f.h.w.r.store.load
        r=self.f.h.w.r;h=r._state(self.f.m.owner)[1]['retirement_sha256']
        missing_key=r.key(self.f.m.owner,'retired.'+h);self.assertIsNotNone(real(missing_key))
        with patch.object(r.store,'load',side_effect=lambda k:None if k==missing_key else real(k)):
            with self.assertRaises(StateUnavailable):self.f.advance(apply=True)
        self.assertEqual(self.b.deletes,[])
    def test_unresolved_grant_never_lists_or_deletes(self):
        self.ready();p=self.f.h._plan(self.f.m.f.person);g=p['media']['closure']['head']
        while g:
            n,b=self.f.h._member(p,g)
            if b is None:break
            g=n['previous']
        self.assertIsNotNone(g);self.f.op=dict(self.f.op,grant=g)
        with self.assertRaises(StateUnavailable):self.f.advance(apply=True)
        self.assertEqual(self.b.version_lists,[]);self.assertEqual(self.b.deletes,[])
    def test_selected_slot_must_be_saved_before_delete(self):
        self.ready();real=self.f.jobs.compare_exchange
        def fail(k,g,v):
            if v.get('phase')=='history':raise StateUnavailable('slot not saved')
            return real(k,g,v)
        with patch.object(self.f.jobs,'compare_exchange',side_effect=fail):
            with self.assertRaises(StateUnavailable):self.f.finish()
        self.assertEqual(self.b.deletes,[]);r,_=self.f.finish();self.assertTrue(r['scan_finished'])
    def test_fence_drift_during_delete_cannot_report_success(self):
        self.ready()
        def change(blob):
            self.b.delete_hook=lambda _:None
            g,raw,md=self.b.live[self.f.name];self.b.live[self.f.name]=(g+1,raw,md)
        self.b.delete_hook=change
        with self.assertRaises(StateUnavailable):self.f.advance(apply=True)
        self.assertEqual(len(self.b.deletes),1)
    def test_short_version_pages_stay_bounded(self):
        self.ready(7);self.b.page_cap=1;r,_=self.f.finish(versions=1)
        self.assertTrue(r['scan_finished']);self.assertEqual(len(self.b.history),1)
    def test_invalid_crc_oversized_or_foreign_generation_prevents_delete(self):
        self.ready()
        for attr,value in [('crc32c','bad'),('size',24*1024*1024+1),('generation',True),('name','foreign')]:
            def mutate(items):
                if items:setattr(items[0],attr,value)
                return items
            self.b.mutate=mutate
            with self.assertRaises(StateUnavailable):self.f.advance(apply=True)
        self.assertEqual(self.b.deletes,[])
    def test_sdk_accepts_zero_byte_seal_and_media_above_state_limit(self):
        from test_lite_service_history import SDKContractTests
        from urllib.parse import parse_qs,urlsplit
        import os
        sdk=SDKContractTests()
        with patch.dict(os.environ,{'DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA':'true'}):
            h=object.__new__(ResearchMediaHistory)
            h.bucket=sdk.bucket([(200,{'items':[dict(name='p/object',generation='1',size='20000',crc32c='AAAAAA=='),
                                               dict(name='p/object',generation='2',size='0',crc32c='AAAAAA==')]})])
            r=h._list(dict(name='p/object',seal_generation=2),2)
        self.assertEqual([v['bytes'] for v in r['items']],[20000,0]);self.assertFalse(r['more'])
        q=parse_qs(urlsplit(sdk.wire.calls[0]['url']).query)
        self.assertEqual(q['versions'],['True']);self.assertEqual(q['maxResults'],['3']);self.assertEqual(len(sdk.wire.calls),1)
    def test_jobs_cannot_share_media_parent_or_child_prefix(self):
        for prefix in ('lite_fenced/v1','lite_fenced/v1/'+self.f.m.owner,'lite_epochs'):
            state=GCSStateStore(self.b,prefix=prefix)
            with self.assertRaises(ValueError):ResearchMediaHistory(media=self.f.m.w,store=state,audit_store=self.f.audit,policy=self.f.policy)
        self.assertEqual(self.b.history,{})
    def test_more_than_ten_installations_scanned_without_global_delete(self):
        m=self.f.m
        owners=[f'{i+100:032x}' for i in range(12)]
        for owner in owners:m.f.f.login(owner);m.seed(1,owner=owner)
        self.f.seed(1);self.f.close();seals=copy.deepcopy(self.b.live);r,_=self.f.finish(max_calls=500)
        self.assertEqual(r['linked_grants'],13);self.assertEqual(r['objects_checked'],13)
        self.assertEqual(seals,self.b.live);self.assertEqual(len(self.b.history),13)
    def test_before_after_probe_reports_active_and_soft_separately(self):
        from lite_research_history_probe import run
        for soft in (False,True):
            r=run(soft=soft)
            self.assertEqual(r['old_active_versions_before'],63);self.assertEqual(r['old_active_versions_after'],0)
            self.assertLessEqual(r['max_version_deletes_per_call'],2)
            self.assertEqual(r['soft_versions_observed'],63 if soft else 0)
            self.assertFalse(r['account_deleted']);self.assertEqual(r['live_gcs_apple_kms_calls'],0)


if __name__=='__main__':unittest.main()
