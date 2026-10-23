"""Offline tests of the exact smoke runner; no ADC or real cloud mutation."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools'))
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'Backend/Flask'))
from medical_consent_smoke_scope import *
from medical_consent_gcs_smoke import (Budget, ScopedClient, SyntheticSmokeStore,
    preflight, scenarios, main, source_snapshot, evidence_inventory)
from medical_fake_gcs import Client
from store import GcsStore, get_store, ImmutableConflict


def run_manifest(): return manifest('a'*40,{'tools/example.py':'b'*64},'smoke-'+'a'*24,100)
def configure(raw):
    for name in BUCKETS:
        b=raw.bucket(name)
        b._properties=dict(name=name,projectNumber=PROJECT_NUMBER,location=REGION,
            iamConfiguration=dict(publicAccessPrevention='enforced',uniformBucketLevelAccess=dict(enabled=True)),
            versioning=dict(enabled=True),softDeletePolicy=dict(retentionDurationSeconds='604800'))
        b.bindings=[dict(role='projects/'+PROJECT+'/roles/consentSmoke'+('Media' if name==MEDIA else 'Audit'),members=['serviceAccount:'+EXECUTOR])]
        b.permissions=BASE_PERMISSIONS|({'storage.objects.delete'} if name==MEDIA else set())
    return raw


class ScopeTests(unittest.TestCase):
    def test_manifest_fixed_scope(self):
        r=run_manifest(); self.assertEqual(validate_manifest(r,sha(r),101),r)
        self.assertFalse(r['audit_is_worm']); self.assertTrue(r['no_policy_changes'])
    def test_tamper_even_rehashed_fails_scope(self):
        for key,val in [('media_bucket','real-media'),('audit_bucket',MEDIA),('executor','user@gmail.com'),
                        ('prefix','flywheel'),('soft_delete_seconds',0),('synthetic_only',False),
                        ('expires_at',99999999),('limits',dict(LIMITS,max_writes=100000))]:
            r=run_manifest();r[key]=val
            with self.subTest(key=key),self.assertRaises((ValueError,PermissionError)):validate_manifest(r,sha(r),101)
    def test_wrong_digest_expiry_future_or_dirty_pins_rejected(self):
        r=run_manifest()
        with self.assertRaises(ValueError):validate_manifest(r,'f'*64,101)
        for now in (99,86500):
            with self.assertRaises(ValueError):validate_manifest(r,sha(r),now)
        r['source_files']={'../source':'b'*64}
        with self.assertRaises(ValueError):validate_manifest(r,sha(r),101)
    def test_app_or_job_environment_refused(self):
        for name in ('K_SERVICE','CLOUD_RUN_JOB','WOUNDAI_AUDIT_MODE','WOUNDAI_GCS_BUCKET','WOUNDAI_AUDIT_BUCKET','WOUNDAI_SECURITY_BUCKET'):
            with self.subTest(name=name),self.assertRaises(PermissionError):validate_environment({name:''})
        validate_environment({})
    def test_unlocked_exception_not_registered_in_application(self):
        with patch.dict('os.environ',{'WOUNDAI_AUDIT_MODE':'isolated-synthetic-smoke-only'}),self.assertRaises(RuntimeError):get_store()
    def test_formal_store_still_rejects_disposable_audit_policy(self):
        raw=configure(Client());st=SyntheticSmokeStore(ScopedClient(raw,Budget()))
        with self.assertRaises(PermissionError):GcsStore.require_audit_write_policy(st)
    def test_runtime_cloud_guard_before_credential_discovery(self):
        with tempfile.TemporaryDirectory() as d:
            r=run_manifest();r['created_at']=int(__import__('time').time());r['expires_at']=r['created_at']+86400
            p=Path(d)/'manifest.json';p.write_text(json.dumps(r))
            with patch('medical_consent_gcs_smoke.source_snapshot',return_value=(r['source_commit'],r['source_files'])),patch('medical_consent_gcs_smoke.validate_environment'),patch('medical_consent_gcs_smoke._refuse_cloud_in_test_process',side_effect=RuntimeError('no ADC')) as guard:
                with self.assertRaisesRegex(RuntimeError,'no ADC'):main(['--execute','--manifest',str(p),'--expected-sha256',sha(r),'--evidence-dir',str(Path(d)/'evidence')])
                guard.assert_called_once();self.assertFalse((Path(d)/'evidence').exists())


class SDKTests(unittest.TestCase):
    def setUp(self):
        self.raw=configure(Client());self.budget=Budget()
        self.clients=[ScopedClient(self.raw,self.budget),ScopedClient(self.raw,self.budget)]
        self.stores=[SyntheticSmokeStore(c) for c in self.clients]
    def test_empty_policy_preflight_is_read_only(self):
        evidence=preflight(self.clients);self.assertEqual(len(evidence),4)
        self.assertFalse(self.raw.rows);self.assertFalse(self.raw.uploads);self.assertFalse(self.raw.deletes)
    def test_metadata_drift_blocks_before_fixture_write(self):
        for key,val in [('projectNumber','wrong'),('location','US'),('retentionPolicy',{'retentionPeriod':'1'}),
                        ('defaultEventBasedHold',True),('lifecycle',{'rule':[{}]}),
                        ('versioning',{'enabled':False}),('softDeletePolicy',{'retentionDurationSeconds':'0'})]:
            with self.subTest(key=key):
                raw=configure(Client());raw.bucket(MEDIA)._properties[key]=val
                with self.assertRaises(PermissionError):preflight([ScopedClient(raw,Budget())])
                self.assertFalse(raw.uploads)
    def test_public_or_extra_bindings_and_inherited_delete_refused(self):
        for changes in ('public','extra','audit-delete','admin'):
            raw=configure(Client());b=raw.bucket(AUDIT)
            if changes=='public':b.bindings[0]['members']=['allUsers']
            elif changes=='extra':b.bindings.append(dict(role='roles/storage.admin',members=['user:x@example.test']))
            elif changes=='audit-delete':b.permissions|={'storage.objects.delete'}
            else:b.permissions|={'storage.buckets.update'}
            with self.subTest(changes=changes),self.assertRaises(PermissionError):preflight([ScopedClient(raw,Budget())])
    def test_noncurrent_and_soft_objects_block_reuse(self):
        for state in ('live','noncurrent','soft'):
            raw=configure(Client());raw.rows[MEDIA,'unrelated',1]=dict(data=b'synthetic',state=state,hold=False)
            with self.subTest(state=state),self.assertRaises(PermissionError):preflight([ScopedClient(raw,Budget())])
            self.assertFalse(raw.uploads)
    def test_wrong_namespace_traversal_and_unpinned_writes_refused(self):
        c=self.clients[0]
        for name in ('flywheel/images/a',PREFIX+'/images/../audit',PREFIX+'/users.jsonl/a',PREFIX+'/images/%2f'):
            with self.subTest(name=name),self.assertRaises(PermissionError):c.bucket(MEDIA).blob(name)
        with self.assertRaises(PermissionError):c.bucket('production')
        with self.assertRaises(PermissionError):c.bucket(MEDIA).blob(PREFIX+'/images/a').upload_from_string(b'x')
        self.assertFalse(self.raw.uploads)
    def test_delete_requires_observed_generation_and_never_audit(self):
        c=self.clients[0];st=self.stores[0];key='images/'+'a'*16+'.jpg'
        st.put_blob_immutable(key,b'synthetic')
        g=next(iter(self.raw.rows))[2]
        with self.assertRaises(PermissionError):c.bucket(MEDIA).blob(PREFIX+'/'+key,generation=g).delete(if_generation_match=g)
        rows=list(c.list_blobs(MEDIA,prefix=PREFIX+'/images/',versions=True))
        c.bucket(MEDIA).blob(rows[0].name,generation=g).delete(if_generation_match=g)
        self.assertEqual(len(self.raw.deletes),1)
        with self.assertRaises(PermissionError):c.bucket(AUDIT).blob(PREFIX+'/receipts/x',generation=g).delete(if_generation_match=g)
    def test_policy_rechecked_before_each_write(self):
        preflight(self.clients)
        self.raw.bucket(AUDIT).permissions|={'storage.objects.delete'}
        with self.assertRaises(PermissionError):self.stores[0].put_json_immutable('receipts/test/a',{})
        self.assertFalse(self.raw.uploads)
    def test_budget_time_writes_bytes_and_calls(self):
        for field,value in [('calls',LIMITS['max_sdk_calls']),('writes',LIMITS['max_writes']),('bytes',LIMITS['max_upload_bytes'])]:
            b=Budget();setattr(b,field,value)
            with self.subTest(field=field),self.assertRaises(RuntimeError):b.take(write=True,size=1)
        now=[0];b=Budget(lambda:now[0]);now[0]=1200
        with self.assertRaises(RuntimeError):b.take()
    def test_lazy_page_error_does_not_claim_success(self):
        class Lazy:
            @property
            def pages(self):
                yield []
                raise PermissionError('synthetic next-page denied')
        with patch.object(self.raw,'list_blobs',return_value=Lazy()),self.assertRaises(PermissionError):
            list(self.clients[0].list_blobs(MEDIA,prefix=PREFIX+'/images/'))
    def test_exact_runner_scenarios_offline(self):
        preflight(self.clients);results=[]
        scenarios(self.stores,run_manifest(),results.append)
        self.assertTrue(all(r.get('passed',True) for r in results))
        self.assertEqual(results[-1]['status'],'pending')
        self.assertTrue(all(b==MEDIA for (b,_,_),pin in self.raw.deletes))
        self.assertTrue(all(pin==0 for b,n,pin in self.raw.uploads if b==AUDIT))
        self.assertFalse(any(k[0]==AUDIT and r['state']!='live' for k,r in self.raw.rows.items()))
        self.assertLess(self.budget.writes,1000)
        writes,deletes=list(self.raw.uploads),list(self.raw.deletes)
        rows=evidence_inventory(self.clients[1])
        self.assertEqual(self.raw.uploads,writes);self.assertEqual(self.raw.deletes,deletes)
        expected_soft={k for k,v in self.raw.rows.items() if v['state']=='soft'}
        self.assertEqual({(r['bucket'],r['name'],r['generation']) for r in rows if r['soft_deleted']},expected_soft)
        self.assertGreaterEqual(len(expected_soft),2)
        for r in rows:
            self.assertEqual('sha256' in r,not r['soft_deleted'])
            if not r['soft_deleted']:
                data=self.raw.rows[r['bucket'],r['name'],r['generation']]['data']
                self.assertEqual(r['sha256'],__import__('hashlib').sha256(data).hexdigest())
    def test_failed_run_is_not_automatically_reused(self):
        self.stores[0].put_json_immutable('receipts/smoke_run/claim.json',run_manifest())
        with self.assertRaises(PermissionError):preflight(self.clients)
    def test_different_run_losing_empty_bucket_race_cannot_claim(self):
        preflight(self.clients)
        a=run_manifest();b=dict(a,run_id='smoke-'+'b'*24)
        self.stores[0].put_json_immutable('receipts/smoke_run/claim.json',a)
        with self.assertRaises(ImmutableConflict):self.stores[1].put_json_immutable('receipts/smoke_run/claim.json',b)
        self.assertEqual(self.stores[0].get_json('receipts/smoke_run/claim.json'),a)
    def test_staging_binding_cannot_be_written_or_deleted(self):
        name=PREFIX+'/staging_meta/bind/'+'a'*16
        self.budget.observed.add((MEDIA,name,1))
        blob=self.clients[0].bucket(MEDIA).blob(name,generation=1)
        with self.assertRaises(PermissionError):blob.upload_from_string(b'bad',if_generation_match=0)
        with self.assertRaises(PermissionError):blob.delete(if_generation_match=1)
        self.assertFalse(self.raw.uploads);self.assertFalse(self.raw.deletes)
    def test_delete_counts_toward_shared_mutation_limit(self):
        key=PREFIX+'/images/'+'a'*16+'.jpg'
        self.clients[0].bucket(MEDIA).blob(key).upload_from_string(b'synthetic',if_generation_match=0)
        listed=list(self.clients[1].list_blobs(MEDIA,prefix=PREFIX+'/images/',versions=True))[0]
        blob=self.clients[1].bucket(MEDIA).blob(key,generation=listed.generation)
        before=self.budget.writes
        blob.delete(if_generation_match=blob.generation)
        self.assertEqual(self.budget.writes,before+1)
        self.assertTrue(self.budget.summary()['mutation_count_includes_deletes'])
    def test_exhausted_mutation_limit_preserves_pinned_media(self):
        key=PREFIX+'/images/'+'b'*16+'.jpg'
        self.clients[0].bucket(MEDIA).blob(key).upload_from_string(b'synthetic',if_generation_match=0)
        listed=list(self.clients[1].list_blobs(MEDIA,prefix=PREFIX+'/images/',versions=True))[0]
        blob=self.clients[1].bucket(MEDIA).blob(key,generation=listed.generation)
        self.budget.writes=LIMITS['max_writes']
        with self.assertRaises(RuntimeError):blob.delete(if_generation_match=blob.generation)
        self.assertFalse(self.raw.deletes)
        self.assertEqual(self.raw.rows[MEDIA,key,blob.generation]['state'],'live')
    def test_expired_run_stops_before_next_sdk_operation(self):
        budget=Budget(expires_at=1)
        with self.assertRaises(RuntimeError):budget.take(write=True,size=1)
        self.assertEqual(budget.calls,0)
    def test_unbounded_list_request_rejected_before_sdk(self):
        with self.assertRaises(PermissionError):list(self.clients[0].list_blobs(MEDIA,prefix=PREFIX+'/images/',max_results=100000))
        self.assertFalse(self.raw.lists)


class JobTests(unittest.TestCase):
    def fixture(self):
        return dict(job='woundai-medical-consent-cleanup-candidate',worker='woundai-consent-cleanup@'+PROJECT+'.iam.gserviceaccount.com',
            scheduler='woundai-consent-scheduler@'+PROJECT+'.iam.gserviceaccount.com',
            image='asia-east1-docker.pkg.dev/'+PROJECT+'/repo/worker@sha256:'+'a'*64,
            audit_mode='locked-seven-year',media_bucket='woundai-medical-consent-media',
            audit_bucket='woundai-medical-consent-audit',schedule_paused=True,apply=False,
            tasks=1,parallelism=1,max_retries=0,timeout_seconds=600,policy_sha256='b'*64)
    def test_only_structural_review_never_deployment_authorization(self):
        r=validate_job_candidate(self.fixture());self.assertFalse(r['execution_authorized']);self.assertFalse(r['locked_epoch_verified'])
    def test_service_exception_mutable_image_or_unpaused_schedule_refused(self):
        for key,val in [('audit_mode','mmh-unlocked-validation'),('audit_mode','synthetic-review-validation'),
                        ('image','image:latest'),('schedule_paused',False),('apply',True),
                        ('worker','woundai-backend@'+PROJECT+'.iam.gserviceaccount.com'),('tasks',True),
                        ('audit_bucket',MEDIA),('timeout_seconds',601)]:
            s=self.fixture();s[key]=val
            with self.subTest(key=key,val=val),self.assertRaises(PermissionError):validate_job_candidate(s)


if __name__=='__main__':unittest.main(verbosity=2)
