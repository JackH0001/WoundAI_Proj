"""P1-P4 synthetic regressions. No cloud access or real research media."""
import json,sys,tempfile,unittest
from pathlib import Path,PureWindowsPath
from unittest.mock import patch,Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'Backend/Flask'))
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools'))
from store import LocalStore,GcsStore
from medical_consent_order import ConsentOrder,ConsentOrderError,ROOT,INDEX_MARKER,IMAGE_INDEX,PROTOCOL,subject
from medical_media_cleanup import cleanup_subject,inventory,media_key,require_delete_policy,_delete
from medical_consent_maintenance import backfill
import api_flywheel as fw
import test_research_format_guards as fixtures

POLICY={'id':'synthetic-approved-30d','retention_seconds':30*86400}

class Disposal(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.st=LocalStore(self.tmp.name);self.clock=100
        self.order=ConsentOrder(self.st,lambda:[],now=lambda:self.clock)
        self.code='WD-DISPOSAL';self.iid='a'*16
        self.files=['images/'+self.iid+'.jpg','staging/'+self.iid+'.jpg',
                    'quarantine/'+self.iid+'.jpg','tissue_masks/'+self.iid+'/hash.png',
                    'wound_masks/'+self.iid+'/hash.png','depth_maps/'+self.iid+'/hash.f32',
                    'depth_maps/'+self.iid+'.f32','depth_maps/'+self.iid+'/hash.meta.json']
        for key in self.files:self.st.put_blob(key,b'synthetic-only')
        self.st.put_blob('images/'+'b'*16+'.jpg',b'other subject')
        self.st.put_json_immutable('receipts/synthetic-retained.json',{'audit':'retain'})
        self.first=self.event('withdraw','withdraw-first-001')
    def event(self,action,intent,base=None):
        return self.order.apply(self.code,'default','default:doctor',action,intent,base,[self.iid])
    def cleanup(self,**kwargs):
        return cleanup_subject(self.order,self.code,'default',POLICY,now=100+30*86400,**kwargs)
    def test_default_is_dry_run(self):
        result=self.cleanup();self.assertEqual(result['status'],'dry_run');self.assertEqual(len(result['media'][0]['objects']),8)
        self.assertTrue(all(self.st.exists(k) for k in self.files));self.assertEqual(self.order.state(self.code,'default')['sequence'],1)
    def test_windows_inventory_uses_portable_keys_without_false_absence(self):
        expected=inventory(self.st,self.iid)
        def windows_base(path):
            base=Path(path);wrapped=Mock();wrapped.exists.return_value=base.exists()
            entries=[]
            for p in base.rglob('*'):
                entry=Mock()
                entry.relative_to.return_value=PureWindowsPath(p.relative_to(self.st.root).as_posix())
                entry.is_file.return_value=p.is_file();entry.is_symlink.return_value=p.is_symlink()
                if p.is_file():entry.read_bytes.return_value=p.read_bytes()
                entries.append(entry)
            wrapped.rglob.return_value=entries
            return wrapped
        with patch('pathlib.Path',side_effect=windows_base):
            observed=inventory(self.st,self.iid)
        self.assertEqual(len(observed),8)
        self.assertEqual(observed,expected)
        self.assertTrue(all('\\' not in r['key'] for r in observed))
        self.assertTrue(all(self.st.exists(k) for k in self.files))
    def test_not_due_keeps_bytes(self):
        result=cleanup_subject(self.order,self.code,'default',POLICY,apply=True,now=101)
        self.assertEqual(result['status'],'not_due');self.assertTrue(self.st.exists(self.files[0]))
    def test_all_media_removed_metadata_and_other_subject_preserved(self):
        result=self.cleanup(apply=True);self.assertEqual(result['status'],'media_versions_absent_at_check')
        self.assertFalse(any(self.st.exists(k) for k in self.files));self.assertTrue(self.st.exists('images/'+'b'*16+'.jpg'))
        self.assertTrue(self.st.exists('receipts/synthetic-retained.json'))
        for key in result['receipts']:self.assertEqual(self.st.get_json(key)['status'],'media_versions_absent_at_check')
    def test_restore_wins_before_claim_no_media_deleted(self):
        original=self.order.apply
        def racing(*args,**kwargs):
            if args[3]=='purge':self.event('restore','restore-first-001',self.first['revision'])
            return original(*args,**kwargs)
        with patch.object(self.order,'apply',side_effect=racing):
            with self.assertRaisesRegex(ConsentOrderError,'revision_conflict'):self.cleanup(apply=True)
        self.assertTrue(all(self.st.exists(k) for k in self.files))
    def test_purge_wins_restore_does_not_readmit_old_media(self):
        import medical_media_cleanup as cleanup
        original=cleanup._delete;restored=[]
        def racing(st,item):
            if not restored:
                restored.append(self.event('restore','restore-after-001',self.order.state(self.code,'default')['revision']))
            return original(st,item)
        with patch.object(cleanup,'_delete',side_effect=racing):self.cleanup(apply=True)
        self.assertEqual(restored[0]['status'],'restored');self.assertEqual(restored[0]['purged_image_ids'],[self.iid])
        with patch.object(fw,'_store',return_value=self.st),patch.object(fw,'read_jsonl',return_value=[]):
            self.assertTrue(fw.is_consent_blocked(self.iid))
    def test_interrupted_delete_resumes_without_releasing_or_losing_audit(self):
        original=self.st.delete;count=[]
        def fail(key):
            count.append(key)
            if len(count)==2:raise IOError('synthetic interruption')
            return original(key)
        with patch.object(self.st,'delete',side_effect=fail):
            with self.assertRaises(IOError):self.cleanup(apply=True)
        self.assertEqual(self.order.state(self.code,'default')['purged_image_ids'],[self.iid])
        result=self.cleanup(apply=True);self.assertEqual(result['status'],'media_versions_absent_at_check')
        self.assertEqual(self.order.state(self.code,'default')['sequence'],2)
    def test_late_inflight_orphan_is_found_on_next_reconcile(self):
        self.cleanup(apply=True);self.st.put_blob(self.files[0],b'late synthetic writer')
        self.cleanup(apply=True);self.assertFalse(self.st.exists(self.files[0]))
        self.assertEqual(self.order.state(self.code,'default')['sequence'],2)
    def test_missing_timestamp_never_guesses_age(self):
        path=Path(self.tmp.name)/self.order._slot(self.code,1)
        row=json.loads(path.read_text());del row['committed_at'];path.write_text(json.dumps(row))
        self.assertEqual(self.cleanup(apply=True)['status'],'blocked_missing_timestamp');self.assertTrue(self.st.exists(self.files[0]))
    def test_policy_is_explicit_and_paths_cannot_escape(self):
        for value in (None,{},dict(id='x',retention_seconds=-1),dict(id='x',retention_seconds=True)):
            with self.subTest(policy=value),self.assertRaises(ValueError):cleanup_subject(self.order,self.code,'default',value,apply=True)
        for key in ('receipts/'+self.iid+'.json','users.jsonl/'+self.iid+'.json','images/../'+self.iid+'.jpg','images/'+self.iid+'b.jpg'):
            self.assertFalse(media_key(key,self.iid))
    def test_cloud_versions_and_generation_pinning(self):
        st=GcsStore.__new__(GcsStore);st.prefix='flywheel';st._bucket_name='media';st._audit_bucket_name='audit'
        st._bucket,st._audit_bucket,st._client=Mock(),Mock(),Mock()
        name='flywheel/images/'+self.iid+'.jpg'
        a=Mock();a.name=name;a.generation=4;a.size=11
        b=Mock();b.name=name;b.generation=3;b.size=10
        st._client.list_blobs.side_effect=lambda bucket,**kw:[a,b] if kw['prefix'].startswith('flywheel/images/') else []
        selected=inventory(st,self.iid);self.assertEqual(len(selected),2)
        self.assertTrue(all(c.kwargs['versions'] is True for c in st._client.list_blobs.call_args_list))
        for item in selected:_delete(st,item)
        self.assertEqual([c.kwargs['if_generation_match'] for c in st._bucket.blob.return_value.delete.call_args_list],[3,4])
        self.assertTrue(all(c.kwargs['generation'] in (3,4) for c in st._bucket.blob.call_args_list))
    def test_cloud_hold_rejected_soft_delete_reported_separately(self):
        st=GcsStore.__new__(GcsStore);st._bucket=Mock()
        for metadata in ({'retentionPolicy':{'retentionPeriod':'2'}},{'defaultEventBasedHold':True}):
            st._bucket._properties=metadata
            with self.assertRaises(PermissionError):require_delete_policy(st)
        st._bucket._properties={'softDeletePolicy':{'retentionDurationSeconds':'604800'}}
        self.assertEqual(require_delete_policy(st),604800)

class SoftDeleteOutcome(unittest.TestCase):
    setUp=Disposal.setUp
    event=Disposal.event
    cleanup=Disposal.cleanup
    # Use a fake inventory adapter to model recoverability without any cloud.
    def test_soft_deleted_copy_is_not_a_completed_destruction(self):
        import medical_media_cleanup as cleanup
        original=cleanup.inventory
        def selected(store,iid,**kwargs):
            if kwargs.get('soft_deleted'): return [dict(key='images/'+iid+'.jpg',generation=1)]
            return original(store,iid,**kwargs)
        with patch.object(cleanup,'require_delete_policy',return_value=604800),patch.object(cleanup,'inventory',side_effect=selected):
            result=self.cleanup(apply=True)
        self.assertEqual(result['status'],'recoverable_media_pending')
        self.assertEqual(self.st.get_json(result['receipts'][0])['status'],'recoverable_media_pending')
        self.assertEqual(self.cleanup(apply=True)['status'],'media_versions_absent_at_check')

class ScopedReads(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.st=LocalStore(self.tmp.name);self.order=ConsentOrder(self.st,lambda:[]);self.queue=[]
        for name,value in (('_store',self.st),):
            p=patch.object(fw,name,return_value=value);p.start();self.addCleanup(p.stop)
        p=patch.object(fw,'read_jsonl',side_effect=lambda path,*a,**k:self.queue if path==fw.QUEUE else []);p.start();self.addCleanup(p.stop)
    def add(self,n):
        code='WD-'+str(n);iid=('%016x'%n)
        r=self.order.apply(code,'default','default:doctor','withdraw','withdraw-%016d'%n,None,[iid])
        self.queue.append(dict(code=code,image_id=iid,org='default'))
        return code,iid,r
    def reads(self,iid):
        with patch.object(self.st,'get_json',wraps=self.st.get_json) as get,patch.object(self.st,'list_keys',wraps=self.st.list_keys) as listing,patch.object(self.st,'read_lines_fresh',wraps=self.st.read_lines_fresh) as legacy:
            self.assertTrue(fw.is_consent_blocked(iid))
            names=[c.args[0] for c in listing.call_args_list]
            self.assertNotIn(ROOT,names)
            return (get.call_count,listing.call_count,legacy.call_count)
    def test_200_subjects_do_not_increase_single_image_cloud_reads(self):
        _,iid,_=self.add(1);small=self.reads(iid)
        for n in range(2,201):self.add(n)
        large=self.reads(iid)
        self.assertEqual(small,large);self.assertLess(sum(large),20)
        print('SCOPED_READ_BUDGET subjects=1,200 get_json/list/fresh=',small,large)
    def test_corrupt_subject_does_not_block_other_subject(self):
        bad,badid,_=self.add(1);good,goodid,r=self.add(2)
        self.order.apply(good,'default','default:doctor','restore','restore-healthy-01',r['revision'],[goodid])
        (Path(self.tmp.name)/self.order._slot(bad,1)).write_text('{bad')
        self.assertTrue(fw.is_consent_blocked(badid));self.assertFalse(fw.is_consent_blocked(goodid))
        codes,imgs=fw.withdrawn_keys();self.assertIn(bad,codes);self.assertNotIn(good,codes);self.assertNotIn(goodid,imgs)
    def test_corrupt_root_still_excludes_subject_in_bulk_and_image_gate(self):
        code,iid,_=self.add(1);self.add(2)
        (Path(self.tmp.name)/ROOT/(subject(code)+'.json')).write_text('{bad')
        self.assertTrue(fw.is_consent_blocked(iid));self.assertIn(code,fw.withdrawn_keys()[0])
    def test_missing_indexed_root_is_not_treated_as_new_consent(self):
        code,iid,_=self.add(1)
        (Path(self.tmp.name)/ROOT/(subject(code)+'.json')).unlink()
        (Path(self.tmp.name)/self.order._slot(code,1)).unlink()
        self.assertTrue(fw.is_consent_blocked(iid))
        self.assertIn(iid,fw.withdrawn_keys()[1])
    def test_index_is_committed_before_state_even_if_event_write_fails(self):
        original=self.st.put_json_immutable
        def fail(key,value):
            if 'medical_consent_events/' in key:raise IOError('event failed')
            return original(key,value)
        with patch.object(self.st,'put_json_immutable',side_effect=fail),self.assertRaises(IOError):self.add(1)
        self.assertEqual(len(self.order.image_subjects('0000000000000001')),1)
        self.assertTrue(fw.is_consent_blocked('0000000000000001'))
    def test_existing_candidate_requires_explicit_verified_index_backfill(self):
        code,iid,_=self.add(1);Path(self.st._p(INDEX_MARKER)).unlink()
        self.assertTrue(fw.is_consent_blocked(iid))
        with self.assertRaisesRegex(ValueError,'writers paused'):backfill(self.order,apply=True)
        plan=backfill(self.order)
        self.assertEqual(backfill(self.order,apply=True,writers_paused=True,plan=plan,
                                 expected_sha256=plan['sha256'])['status'],'completed')
        references=self.order.image_subjects(iid)
        self.assertEqual({r['code'] for r in references},{code})
        self.assertEqual({r['consent_root_required'] for r in references},{True,False})

class HTTP(unittest.TestCase):
    setUp=fixtures.TissueHTTP.setUp
    tearDown=fixtures.TissueHTTP.tearDown
    def test_malformed_legacy_queue_id_cannot_block_withdrawal(self):
        for value in ('../never-touch',{'unexpected':'shape'},42,float('nan')):
            fw.append_jsonl(fw.QUEUE,dict(code='WD-SYNTH',org='test',image_id=value))
        resp=self.client.post('/api/v1/consent/withdraw',headers=self.headers['dr1'],json=dict(code='WD-SYNTH',intent_id='withdraw-badid-001'))
        self.assertEqual(resp.status_code,200,resp.json);self.assertEqual(resp.json['status'],'withdrawn')
        self.assertEqual(resp.json['invalid_queue_image_count'],4)
        self.assertIn('WD-SYNTH',fw.withdrawn_keys()[0])
        events=fw._consent_order().history('WD-SYNTH','test')[1]
        self.assertEqual(len(events[-1]['rejected_image_digests']),4)
        self.assertNotIn('../never-touch',json.dumps(events));self.assertEqual(resp.json['media_cleanup'],'pending_policy_worker')
    def test_user_supplied_bad_image_id_still_rejected(self):
        resp=self.client.post('/api/v1/consent/withdraw',headers=self.headers['dr1'],json=dict(code='WD-SYNTH',image_id='../never-touch'))
        self.assertEqual(resp.status_code,400)
    def test_stats_survives_one_corrupt_subject(self):
        self.client.post('/api/v1/consent/withdraw',headers=self.headers['dr1'],json=dict(code='WD-SYNTH'))
        path=Path(self.tmp.name)/fw._consent_order()._slot('WD-SYNTH',1);path.write_text('{bad')
        response=self.client.get('/api/v1/flywheel/stats',headers=self.headers['admin'])
        self.assertEqual(response.status_code,200,response.json)
    def test_bad_legacy_withdrawal_id_seals_without_unsafe_paths(self):
        fw.append_jsonl(fw.WITHDRAWN,dict(code='WD-SYNTH',image_ids=['not-a-path','a'*16],action='withdraw'))
        resp=self.client.post('/api/v1/consent/withdraw',headers=self.headers['dr1'],json=dict(code='WD-SYNTH'))
        self.assertEqual(resp.status_code,200,resp.json);self.assertIn('a'*16,resp.json['image_ids'])

import test_p0_4_staging as staging_fixtures
from medical_consent_order import record_media_owner, MEDIA_OWNERS
from consent_staging import ConsentError

class StagingOwnership(unittest.TestCase):
    setUp=staging_fixtures.StagingTests.setUp
    stage=staging_fixtures.StagingTests.stage
    token=staging_fixtures.StagingTests.token
    prepare=staging_fixtures.StagingTests.prepare
    def order(self):return ConsentOrder(self.st,lambda:[],now=lambda:self.clock)
    def withdraw(self):
        return self.order().apply(self.code,self.org,self.actor,'withdraw','withdraw-stage-001',None,[])
    def test_unannotated_staging_is_included_in_withdrawal_and_cleanup(self):
        self.stage();self.assertEqual(self.queue,[])
        receipt=self.withdraw();self.assertIn(self.iid,receipt['image_ids'])
        result=cleanup_subject(self.order(),self.code,self.org,POLICY,apply=True,now=self.clock+30*86400)
        self.assertEqual(result['status'],'media_versions_absent_at_check')
        self.assertFalse(self.st.exists('staging/'+self.iid+'.jpg'))
    def test_owner_write_failure_prevents_media_storage(self):
        original=self.st.put_json_immutable
        def fail(key,value):
            if key.startswith(MEDIA_OWNERS+'/'):raise IOError('owner receipt failure')
            return original(key,value)
        with patch.object(self.st,'put_json_immutable',side_effect=fail),self.assertRaises(ConsentError):self.stage()
        self.assertFalse(self.st.exists('staging/'+self.iid+'.jpg'))
    def test_promoted_retry_owner_failure_is_unknown_not_analysis_only(self):
        self.stage();self.prepare()
        self.assertTrue(self.st.exists('images/'+self.iid+'.jpg'))
        with patch('medical_consent_order.record_media_owner',side_effect=IOError('synthetic outage')):
            with self.assertRaises(ConsentError) as caught:self.stage()
        self.assertEqual(caught.exception.status,503)
        self.assertTrue(self.st.exists('images/'+self.iid+'.jpg'))
    def test_invalid_care_never_registers_owner(self):
        with self.assertRaises(ConsentError):self.svc.stage(self.image,'bad-token',self.actor,'physician',self.org)
        self.assertFalse(self.order().owned_images(self.code,self.org)[0])
    def test_withdraw_during_owner_registration_blocks_first_media_write(self):
        def registered(st,code,org,iid):
            record_media_owner(st,code,org,iid);self.withdraw();self.withdrawn.add(code)
        with patch('medical_consent_order.record_media_owner',side_effect=registered),self.assertRaises(ConsentError):self.stage()
        self.assertFalse(self.st.exists('staging/'+self.iid+'.jpg'))
        self.assertIn(self.iid,self.order().state(self.code,self.org)['image_ids'])
    def test_cleanup_includes_owner_registered_after_withdrawal_snapshot(self):
        self.withdraw()
        # Model a suspended pre-withdrawal staging writer. No annotation exists.
        record_media_owner(self.st,self.code,self.org,self.iid)
        self.st.put_blob('staging/'+self.iid+'.jpg',self.image.data)
        result=cleanup_subject(self.order(),self.code,self.org,POLICY,apply=True,now=self.clock+30*86400)
        self.assertEqual(result['status'],'media_versions_absent_at_check')
        self.assertFalse(self.st.exists('staging/'+self.iid+'.jpg'))
        self.assertIn(self.iid,self.order().state(self.code,self.org)['purged_image_ids'])

if __name__=='__main__':unittest.main(verbosity=2)
