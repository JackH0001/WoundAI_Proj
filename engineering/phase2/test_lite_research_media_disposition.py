"""Generated RGB-D/labels, actual CAS journals and SDK paging; no live cloud."""
import copy
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch
from google.auth.credentials import AnonymousCredentials
from google.cloud.storage import Client
from lite_account_research_closure import AccountResearchClosure
from lite_research_media_disposition import ResearchMediaDisposition
from lite_attest_state import StateUnavailable
from lite_fenced_objects import ObjectRef, FencedObjects, GenerationConflict, KINDS
from lite_fenced_store import FencedPrivacy
from lite_reconsent import Reconsent, CONSENT, DISCLOSURE, ReconsentRejected
from lite_subscription_executor import key
from lite_usage_ledger import digest
from lite_disposition_progress_probe import ProgressFixture
from test_lite_subscription_grace import rows
import test_lite_fenced_objects as objects
import test_lite_native_purchase as native


class PageBucket(objects.Bucket):
    def __init__(self):super().__init__();self.pages_called=[];self.list_error=None;self.override=None
    def list_blobs(self,*,prefix,page_token=None,page_size=None,max_results=None,timeout=10,retry=None):
        if page_size is None:return super().list_blobs(prefix=prefix,timeout=timeout,retry=retry,max_results=max_results)
        self.pages_called.append(dict(prefix=prefix,token=page_token,limit=max_results,page_size=page_size))
        if self.list_error:raise self.list_error
        if self.override:return self.override(prefix,page_token,page_size)
        with self.lock:names=sorted(n for n in self.live if n.startswith(prefix))
        offset=int(page_token or 0);page=names[offset:offset+max_results];end=offset+len(page)
        return SimpleNamespace(pages=iter([[SimpleNamespace(name=n) for n in page]]),
            next_page_token=str(end) if end<len(names) else None)


class MediaFixture:
    def __init__(self,host):
        self.f=ProgressFixture(host);self.owner='a'*32;self.f.f.login(self.owner)
        self.bucket=PageBucket();self.r=Reconsent(FencedPrivacy(self.f.store('research-media-source'),self.bucket))
        self.closure=AccountResearchClosure(deletion=self.f.d,research=self.r,
            store=self.f.store('research-closure'),audit_store=self.f.store('research-closure-audit'))
        self.jobs,self.audit=self.f.store('media-jobs'),self.f.store('media-audit');self.w=self.worker()
    def worker(self):return ResearchMediaDisposition(closure=self.closure,store=self.jobs,audit_store=self.audit)
    def seed(self,count=7,ep='legacy',owner=None):
        owner=owner or self.owner;p=self.r.privacy.scoped(ep)
        for n in range(count):
            ticket=p.begin_write(owner);ref=ObjectRef(owner,tuple(sorted(KINDS))[n%7],f'{n+1:016x}')
            p.manifest.write_registered(ticket,p.objects,ref,b'private-generated-payload-'+str(n).encode())
            p.finish_write(ticket)
    def rejoin(self,previous,new):
        self.r.resume(self.owner,dict(anon_id=self.owner,research_consent=True,previous_epoch=previous,new_epoch=new,
            consent_version=CONSENT,disclosure_version=DISCLOSURE))
    def advance(self,**kw):return self.w.advance(self.f.person,apply=True,**kw)
    def finish(self,limit=3,max_calls=1000):
        for i in range(max_calls):
            r=self.advance(limit=limit);self.w=self.worker()
            if r['scan_finished']:return r,i+1
        raise AssertionError('bounded worker did not complete fixture')
    def owner_job(self):
        p=dict(v=1,config=self.w.config,closure=self.closure._plan(self.f.person))
        b=self.w._bound(p,self.owner)
        return p,b,self.w._owner(b)


class MediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):native.legacy.LinksTests.setUpClass()
    def setUp(self):self.h=MediaFixture(self)
    def test_before_after_probe_preserves_history_and_bounds_calls(self):
        from lite_research_media_probe import run
        result=run()
        self.assertTrue(result['implementation_present'])
        self.assertEqual(result['original_current_objects'],73)
        self.assertEqual(result['max_seals_per_call'],4)
        self.assertEqual(result['current_payloads_remaining'],0)
        self.assertFalse(result['account_deleted'])
    def close(self):
        self.h.f.close()
        while not self.h.closure.advance(self.h.f.person)['scan_finished']:pass
    def test_explicit_application_required(self):
        self.h.seed();self.close();before=copy.deepcopy(self.h.bucket.live)
        with self.assertRaises(ValueError):self.h.w.advance(self.h.f.person)
        self.assertEqual(before,self.h.bucket.live)
    def test_single_page_per_call_and_all_current_kinds_sealed(self):
        self.h.seed(73);original=copy.deepcopy(self.h.bucket.history);self.close()
        for _ in range(100):
            prior=len(self.h.bucket.pages_called);uploads=sum(x[0]=='upload' for x in self.h.bucket.calls)
            r=self.h.advance(limit=4)
            self.assertLessEqual(len(self.h.bucket.pages_called)-prior,1)
            self.assertLessEqual(sum(x[0]=='upload' for x in self.h.bucket.calls)-uploads,4)
            if r['scan_finished']:break
        self.assertTrue(r['scan_finished']);self.assertEqual(r['linked_grants'],1)
        self.assertEqual(r['unresolved_grants'],1);self.assertFalse(r['account_deleted'])
        self.assertTrue(all(v[1]==b'' and v[2]['state']=='sealed' for v in self.h.bucket.live.values()))
        self.assertTrue(all(self.h.bucket.history[k]==v for k,v in original.items()))
        self.assertFalse(r['historical_versions_erased']);self.assertFalse(r['physical_backups_erased'])
    def test_restart_replays_receipt_without_extra_writes(self):
        self.h.seed();self.close();r,_=self.h.finish();before=rows(self.h.jobs),rows(self.h.audit),copy.deepcopy(self.h.bucket.history)
        self.h.w=self.h.worker();self.assertEqual(r,self.h.w.status(self.h.f.person));self.assertEqual(r,self.h.advance())
        self.assertEqual(before,(rows(self.h.jobs),rows(self.h.audit),self.h.bucket.history))
    def test_other_owner_epoch_account_and_quotas_untouched(self):
        self.h.seed(owner='e'*32);self.h.seed();self.h.f.f.login('d'*32,'another-identity');self.close()
        other={k:v for k,v in self.h.bucket.live.items() if '/'+('e'*32)+'/' in k}
        original=self.h.f.preserved();provider=copy.deepcopy((self.h.f.f.g.calls,self.h.f.f.kms.calls))
        self.h.finish();self.assertEqual(original,self.h.f.preserved())
        self.assertEqual(provider,(self.h.f.f.g.calls,self.h.f.f.kms.calls))
        self.assertEqual(other,{k:v for k,v in self.h.bucket.live.items() if k in other})
        self.assertIsNone(self.h.r.retirement('d'*32));self.assertEqual(self.h.f.calls,0)
    def test_all_verified_installations_retire_before_first_media_page(self):
        self.h.seed(65)
        owners=[f'{n+100:032x}' for n in range(12)]
        for owner in owners:self.h.f.f.login(owner);self.h.seed(1,owner=owner)
        self.h.f.close();initial=copy.deepcopy(self.h.bucket.live)
        for _ in range(3):
            self.h.advance(limit=2)
            self.assertEqual(initial,self.h.bucket.live)
            self.assertEqual(self.h.bucket.pages_called,[])
        for owner in owners+[self.h.owner]:
            self.assertIsNotNone(self.h.r.retirement(owner))
            with self.assertRaises(ReconsentRejected):self.h.r.resolve(owner,'legacy')
        r,_=self.h.finish(limit=2);self.assertEqual(r['linked_grants'],13)
    def test_walks_more_than_sixteen_epochs_without_all_epoch_loop(self):
        old='legacy'
        for n in range(18):
            self.h.seed(2,ep=old)
            self.h.r.privacy.scoped(old).manifest.withdraw(self.h.owner)
            self.h.r.privacy.scoped(old).complete_withdrawal(self.h.owner)
            new=f'{n+100:032x}';self.h.rejoin(old,new);old=new
        self.h.seed(5,ep=old);consents={self.h.r.key(self.h.owner,f'{n+100:032x}'):self.h.r.store.load(self.h.r.key(self.h.owner,f'{n+100:032x}')) for n in range(18)}
        self.close();r,calls=self.h.finish(limit=2)
        _,_,job=self.h.owner_job();self.assertEqual(job[1]['epochs'],19);self.assertEqual(job[1]['objects'],41)
        self.assertGreater(calls,19);self.assertTrue(r['scan_finished'])
        for k,v in consents.items():self.assertEqual(v,self.h.r.store.load(k))
    def test_empty_page_continuation_is_not_completion(self):
        self.h.seed(3);self.close();original=self.h.bucket.list_blobs
        def empty(prefix,token,limit):
            if token is None:return SimpleNamespace(pages=iter([[]]),next_page_token='empty-first')
            self.h.bucket.override=None
            return original(prefix=prefix,page_token=None,max_results=limit,page_size=limit)
        self.h.bucket.override=empty;r,_=self.h.finish(limit=5)
        self.assertTrue(r['scan_finished']);self.assertEqual(len(self.h.bucket.pages_called),3)
        self.assertTrue(all(v[1]==b'' for v in self.h.bucket.live.values()))
    def test_short_page_with_continuation_is_followed(self):
        self.h.seed(4);self.close()
        def short(prefix,token,limit):
            names=sorted(n for n in self.h.bucket.live if n.startswith(prefix));offset=int(token or 0)
            return SimpleNamespace(pages=iter([[SimpleNamespace(name=names[offset])]]),next_page_token=str(offset+1) if offset<3 else None)
        self.h.bucket.override=short;self.h.finish(limit=20)
        self.assertEqual(len(self.h.bucket.pages_called),4)
    def test_provider_failure_does_not_advance_or_claim_completion(self):
        self.h.seed();self.close();self.h.advance();self.h.advance()
        before=rows(self.h.jobs);self.h.bucket.list_error=TimeoutError('unavailable')
        with self.assertRaises(StateUnavailable):self.h.advance()
        self.assertEqual(before,rows(self.h.jobs));self.assertFalse(self.h.w.status(self.h.f.person)['scan_finished'])
        self.h.bucket.list_error=None;self.h.finish()
    def test_two_token_cycle_is_rejected_durably_after_restart(self):
        self.close();self.h.advance();self.h.advance()
        def cycle(prefix,token,limit):
            return SimpleNamespace(pages=iter([[]]),next_page_token='b' if token=='a' else 'a')
        self.h.bucket.override=cycle
        self.h.advance();self.h.w=self.h.worker();self.h.advance();self.h.advance()
        with self.assertRaises(StateUnavailable):self.h.advance()
        self.assertFalse(self.h.w.status(self.h.f.person)['scan_finished'])
    def test_seal_outcome_audit_loss_recovers_same_version(self):
        self.h.seed(2);self.close();self.h.advance();self.h.advance();original=self.h.audit.compare_exchange
        def fail(k,g,v):
            if v.get('current_payload_absent') is True:raise StateUnavailable('outcome unavailable')
            return original(k,g,v)
        with patch.object(self.h.audit,'compare_exchange',side_effect=fail):
            with self.assertRaises(StateUnavailable):self.h.advance()
        self.h.finish();self.assertEqual(len(self.h.bucket.history),4)
    def test_failed_original_seal_intent_never_changes_media(self):
        self.h.seed(2);self.close();self.h.advance();self.h.advance();before=copy.deepcopy(self.h.bucket.live)
        original=self.h.audit.compare_exchange
        def fail(k,g,v):
            if 'prior' in v:raise StateUnavailable('intent unavailable')
            return original(k,g,v)
        with patch.object(self.h.audit,'compare_exchange',side_effect=fail):
            with self.assertRaises(StateUnavailable):self.h.advance()
        self.assertEqual(before,self.h.bucket.live)
    def test_seal_lost_response_recovers_without_duplicate_version(self):
        self.h.seed(3);self.close();self.h.advance();self.h.advance();self.h.bucket.lose_response=True
        with self.assertRaises(StateUnavailable):self.h.advance(limit=3)
        self.h.bucket.lose_response=False;self.h.finish(limit=3)
        self.assertEqual(len(self.h.bucket.history),6)
    def test_checkpoint_reply_loss_readback_recovers(self):
        self.h.seed(3);self.close();real=self.h.jobs.compare_exchange
        def lost(k,g,v):
            result=real(k,g,v)
            if v.get('objects',0)>0:raise StateUnavailable('committed response lost')
            return result
        with patch.object(self.h.jobs,'compare_exchange',side_effect=lost):r,_=self.h.finish()
        self.assertTrue(r['scan_finished']);self.assertEqual(len(self.h.bucket.history),6)
    def test_checkpoint_failure_after_seal_replays_original_page(self):
        self.h.seed(3);self.close();self.h.advance();self.h.advance();real=self.h.jobs.compare_exchange
        def fail(k,g,v):
            if v.get('objects',0)>0:raise StateUnavailable('not committed')
            return real(k,g,v)
        with patch.object(self.h.jobs,'compare_exchange',side_effect=fail):
            with self.assertRaises(StateUnavailable):self.h.advance(limit=3)
        self.h.finish(limit=3);self.assertEqual(len(self.h.bucket.history),6)
    def test_pending_plans_preserved_in_audit_before_sealing(self):
        p=self.h.r.privacy;tickets=[];pins=[]
        for group in range(3):
            t=p.begin_write(self.h.owner);tickets.append(t)
            for i in range(9):
                pin=p.objects.pin(ObjectRef(self.h.owner,'rgbd',f'{group*9+i+1:016x}'))
                p.manifest.plan(t,pin);pins.append(pin)
        self.close();self.h.advance(limit=4) # frozen pending inventory saved, no payload writes
        self.assertEqual(self.h.bucket.live,{})
        original=copy.deepcopy(p.manifest._load(self.h.owner)[1])
        self.h.advance(limit=4)
        self.assertEqual(len(self.h.bucket.live),4)
        with self.assertRaises(GenerationConflict):p.objects.write(pins[0],b'late')
        self.h.finish(limit=4);_,b,j=self.h.owner_job()
        self.assertEqual(self.h.w._plans(b,j[1]),original)
        self.assertEqual(len(self.h.bucket.live),27)
        for pin in pins:
            with self.assertRaises(GenerationConflict):p.objects.write(pin,b'late after completion')
    def test_admitted_writer_wins_first_then_is_sealed(self):
        p=self.h.r.privacy;t=p.begin_write(self.h.owner);pin=p.objects.pin(ObjectRef(self.h.owner,'image','e'*16));p.manifest.plan(t,pin)
        self.close();done=[]
        def race(blob,data,kw):
            if data==b'' and not done:done.append(1);p.objects.write(pin,b'late-but-before-seal')
        self.h.bucket.before_upload=race;self.h.finish()
        self.assertEqual(self.h.bucket.live[p.objects.name(pin.ref)][1],b'')
        self.assertTrue(any(v[1]==b'late-but-before-seal' for v in self.h.bucket.history.values()))
    def test_empty_pending_writer_removed_without_claiming_it_died(self):
        self.h.r.privacy.begin_write(self.h.owner);self.close();r,_=self.h.finish()
        self.assertEqual(self.h.r.privacy.manifest._load(self.h.owner)[1]['writers'],{})
        self.assertFalse(r['writers_drained'])
    def test_status_never_starts_worker_or_changes_research(self):
        self.h.seed();self.close();before=rows(self.h.r.store),copy.deepcopy(self.h.bucket.live)
        with self.assertRaises(StateUnavailable):self.h.w.status(self.h.f.person)
        self.assertEqual(before,(rows(self.h.r.store),self.h.bucket.live))
    def test_public_deletion_status_does_not_dispatch_attached_worker(self):
        self.h.f.progress.attach_research(self.h.w);self.h.seed();self.close();before=copy.deepcopy(self.h.bucket.live)
        self.h.f.status();self.assertEqual(before,self.h.bucket.live)
        for _ in range(30):
            result=self.h.f.advance(limit=3)
            if self.h.w.status(self.h.f.person)['scan_finished']:break
        self.assertTrue(all(v[1]==b'' for v in self.h.bucket.live.values()))
        self.assertFalse(result['account_deleted'])
        self.assertIn('research_association_disposition',result['disposition']['pending_lanes'])
    def test_default_progress_does_not_touch_research(self):
        self.h.seed();self.close();before=copy.deepcopy(self.h.bucket.live)
        self.h.f.advance();self.assertEqual(before,self.h.bucket.live)
    def test_wrong_progress_binding_rejected(self):
        with self.assertRaises(ValueError):self.h.f.progress.attach_research(object())
        other=MediaFixture(self)
        with self.assertRaises(ValueError):self.h.f.progress.attach_research(other.w)
    def test_corrupt_owner_checkpoint_never_resets(self):
        self.close();self.h.advance();_,b,row=self.h.owner_job()
        self.h.jobs.compare_exchange(key('research-media-owner',digest(b)),row[0],dict(row[1],objects=True))
        before=copy.deepcopy(self.h.bucket.live)
        with self.assertRaises(StateUnavailable):self.h.advance()
        self.assertEqual(before,self.h.bucket.live)
    def test_missing_original_audit_stops_before_more_media(self):
        self.h.seed();self.close();self.h.advance();self.h.w.audit=self.h.f.store('missing-media-audit')
        before=copy.deepcopy(self.h.bucket.live)
        with self.assertRaises(StateUnavailable):self.h.advance()
        self.assertEqual(before,self.h.bucket.live);self.assertEqual(rows(self.h.w.audit),[])
    def test_changed_frozen_write_inventory_stops(self):
        self.close();self.h.advance();_,b,j=self.h.owner_job()
        k=key('research-media-frozen-plans',j[1]['plans_sha256']);rev,v=self.h.audit.load(k)
        self.h.audit.compare_exchange(k,rev,dict(v,generation=v['generation']+1))
        with self.assertRaises(StateUnavailable):self.h.advance()
    def test_missing_prior_epoch_does_not_report_complete(self):
        self.h.r.privacy.manifest.withdraw(self.h.owner);self.h.rejoin('legacy','b'*32)
        self.h.r.privacy.scoped('b'*32).manifest.withdraw(self.h.owner);self.h.rejoin('b'*32,'c'*32)
        self.h.seed(ep='c'*32);self.close();real=self.h.r.store.load;oldkey=self.h.r.key(self.h.owner,'b'*32)
        with patch.object(self.h.r.store,'load',side_effect=lambda k:None if k==oldkey else real(k)):
            with self.assertRaises(StateUnavailable):self.h.finish()
        self.assertFalse(self.h.w.status(self.h.f.person)['scan_finished']);self.h.finish()
    def test_missing_object_seal_is_not_treated_as_success(self):
        self.h.seed(1);self.close();self.h.finish();_,b,j=self.h.owner_job()
        # A completed receipt is a historical observation; explicit integrity
        # replay of an object must still reject a physically deleted marker.
        ref=ObjectRef(self.h.owner,tuple(sorted(KINDS))[0],f'{1:016x}');self.h.bucket.live.clear()
        with self.assertRaises(StateUnavailable):self.h.w._seal(b,'legacy',ref)
    def test_no_payload_in_audit_and_original_capture_retained(self):
        self.h.seed();self.close();capture=self.h.f.capture;self.h.finish()
        self.assertNotIn('private-generated-payload',repr(rows(self.h.audit)))
        self.assertEqual(self.h.f.capture,capture)
    def test_duplicate_grants_do_not_reseal_payload(self):
        self.h.seed(5);self.h.f.f.login(self.h.owner);self.close();r,_=self.h.finish()
        self.assertEqual(r['linked_grants'],2);self.assertEqual(len(self.h.bucket.history),10)
    def test_two_workers_join_receipts_and_finish(self):
        self.h.seed(15);self.close()
        def run(_):
            for i in range(80):
                try:
                    r=self.h.worker().advance(self.h.f.person,limit=3,apply=True)
                    if r['scan_finished']:return
                except StateUnavailable:pass
            raise AssertionError('worker never completed')
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(run,range(2)))
        self.assertTrue(self.h.w.status(self.h.f.person)['scan_finished'])
        self.assertEqual(len(self.h.bucket.history),30)
    def test_zero_grant_account_does_not_claim_whole_account_deleted(self):
        p='e'*64;self.h.f.f.lifecycle.initialize_new(p,existing_account=False);self.h.f.f.lifecycle.close(p,'empty','a'*32)
        r=self.h.w.advance(p,apply=True);self.assertEqual(r['total_grants'],0);self.assertTrue(r['scan_finished'])
        self.assertFalse(r['account_deleted'])
    def test_bound_limits_and_store_isolation(self):
        for n in (0,21,True,None):
            with self.assertRaises(ValueError):self.h.advance(limit=n)
        for s in (self.h.r.store,self.h.closure.store,self.h.closure.audit,self.h.f.d.store,self.h.audit):
            with self.assertRaises(ValueError):ResearchMediaDisposition(closure=self.h.closure,store=s,audit_store=self.h.audit)


class PageTests(unittest.TestCase):
    def setUp(self):self.b=PageBucket();self.o=FencedObjects(self.b);self.owner='a'*32
    def response(self,names,token=None):return SimpleNamespace(pages=iter([[SimpleNamespace(name=n) for n in names]]),next_page_token=token)
    def test_real_sdk_sends_bounded_query_and_preserves_continuation(self):
        c=Client(project='synthetic-project',credentials=AnonymousCredentials());b=c.bucket('synthetic-lite-fences');o=FencedObjects(b)
        name=ObjectRef(self.owner,'image','f'*16).name
        with patch.object(c._connection,'api_request',return_value={'items':[{'name':name}],'nextPageToken':'more'}) as http:
            refs,t,last=o.page_refs(self.owner,token='first',limit=2)
        q=http.call_args.kwargs['query_params']
        self.assertEqual(q['maxResults'],2);self.assertEqual(q['pageToken'],'first');self.assertTrue(q['prefix'].endswith(self.owner+'/'))
        self.assertEqual(t,'more');self.assertEqual(last,name);self.assertEqual(len(refs),1)
    def test_rejects_wrong_owner_and_epoch(self):
        for name in (ObjectRef('b'*32,'image','f'*16).name,'lite_epochs/v1/'+('b'*32)+'/'+ObjectRef(self.owner,'image','f'*16).name):
            self.b.override=lambda *args:self.response([name])
            with self.assertRaises(StateUnavailable):self.o.page_refs(self.owner)
    def test_rejects_unknown_kind_or_malformed_identifier(self):
        for name in ('lite_fenced/v1/'+self.owner+'/other/f','lite_fenced/v1/'+self.owner+'/image/../f'):
            self.b.override=lambda *args:self.response([name])
            with self.assertRaises(StateUnavailable):self.o.page_refs(self.owner)
    def test_rejects_duplicate_unordered_or_repeated_boundary(self):
        a=ObjectRef(self.owner,'image','a'*16).name;b=ObjectRef(self.owner,'image','b'*16).name
        for names,after in (([a,a],None),([b,a],None),([a],a)):
            self.b.override=lambda *args:self.response(names)
            with self.assertRaises(StateUnavailable):self.o.page_refs(self.owner,after=after)
    def test_rejects_unbounded_provider_page_and_self_loop_token(self):
        names=[ObjectRef(self.owner,'image',f'{n:016x}').name for n in range(3)]
        self.b.override=lambda *args:self.response(names)
        with self.assertRaises(StateUnavailable):self.o.page_refs(self.owner,limit=2)
        self.b.override=lambda *args:self.response([],token='same')
        with self.assertRaises(StateUnavailable):self.o.page_refs(self.owner,token='same')
    def test_no_fallback_to_unbounded_iterator(self):
        with patch.object(self.b,'list_blobs',return_value=[]):
            with self.assertRaises(StateUnavailable):self.o.page_refs(self.owner)
    def test_epoch_prefix_is_bound_to_owner(self):
        self.o=FencedObjects(self.b,epoch='b'*32);self.o.page_refs(self.owner)
        self.assertEqual(self.b.pages_called[0]['prefix'],'lite_epochs/v1/'+('b'*32)+'/lite_fenced/v1/'+self.owner+'/')


if __name__=='__main__':unittest.main()
