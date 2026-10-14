"""Offline isolation and persistence checks for the separate review service."""
import json, os, sys, unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'Backend/Flask'))
import store as s
import test_mmh_unlocked_audit as audit_fixture
import test_research_format_guards as http_fixture
from review_service_profile import EXPECTED, BUCKETS, MODE, review_environment, validate_review_scope
from lite_service_profile import service_profile

class ProfileScope(unittest.TestCase):
    def test_exact_environment_and_existing_mmh(self):
        self.assertTrue(review_environment(EXPECTED));self.assertEqual(service_profile(EXPECTED),'medical')
        self.assertFalse(review_environment(audit_fixture.ENV))
        self.assertEqual(service_profile(audit_fixture.ENV),'medical')
        with patch.dict(os.environ,audit_fixture.ENV,clear=True):
            s.MmhUnlockedValidationStore.validate_scope(BUCKETS[0].replace('mmh-review','mmhps20261007'),'flywheel',audit_fixture.ENV['WOUNDAI_AUDIT_BUCKET'],audit_fixture.ENV['WOUNDAI_SECURITY_BUCKET'])
    def test_each_missing_wrong_field_and_legacy_seed_rejected(self):
        for key in EXPECTED:
            for value in (None,'wrong','',True):
                env=dict(EXPECTED)
                if value is None:env.pop(key)
                else:env[key]=value
                with self.subTest(key=key,value=value),self.assertRaises(PermissionError):service_profile(env)
        with self.assertRaises(PermissionError):service_profile(dict(EXPECTED,WOUNDAI_DEMO_SEED_USER='demo01'))
    def test_bucket_argument_cannot_escape_fixed_environment(self):
        for arguments in [(BUCKETS[2],'flywheel',BUCKETS[0],BUCKETS[1]),
                          (BUCKETS[0],'other',BUCKETS[2],BUCKETS[1]),
                          (audit_fixture.ENV['WOUNDAI_GCS_BUCKET'],'flywheel',BUCKETS[2],BUCKETS[1])]:
            with self.assertRaises(PermissionError):validate_review_scope(EXPECTED,*arguments)
    def test_mmh_cannot_borrow_review_mode_and_reverse(self):
        for mode,cls,env in [(MODE,s.SyntheticReviewValidationStore,audit_fixture.ENV),
                             ('mmh-unlocked-validation',s.MmhUnlockedValidationStore,EXPECTED)]:
            with patch.dict(os.environ,dict(env,WOUNDAI_AUDIT_MODE=mode),clear=True),self.assertRaises(PermissionError):
                cls.validate_scope(env['WOUNDAI_GCS_BUCKET'],'flywheel',env['WOUNDAI_AUDIT_BUCKET'],env['WOUNDAI_SECURITY_BUCKET'])
    def test_factory_selection_and_cached_wrong_store(self):
        with patch.dict(os.environ,EXPECTED,clear=True),patch.object(s,'_refuse_cloud_in_test_process'):
            s.reset_store(object())
            try:
                with self.assertRaises(RuntimeError):s.get_store()
                s.reset_store()
                with patch.object(s.SyntheticReviewValidationStore,'__init__',return_value=None):
                    self.assertIsInstance(s.get_store(),s.SyntheticReviewValidationStore)
            finally:s.reset_store()
    def test_invalid_profile_refused_before_cloud_client_construction(self):
        for key in EXPECTED:
            env=dict(EXPECTED);env[key]='other'
            with self.subTest(key=key),patch.dict(os.environ,env,clear=True), \
                 patch.object(s.GcsStore,'__init__',return_value=None) as construct:
                s.reset_store()
                try:
                    with self.assertRaises((PermissionError,RuntimeError)):s.get_store()
                    construct.assert_not_called()
                finally:s.reset_store()

    def test_independent_profile_signals_cannot_select_legacy_store(self):
        for key in ('WOUNDAI_DEPLOYMENT_ENVIRONMENT','WOUNDAI_AUDIT_MODE',
                    'WOUNDAI_INSTITUTION_ORG','K_SERVICE','WOUNDAI_GCS_BUCKET',
                    'WOUNDAI_SECURITY_BUCKET','WOUNDAI_AUDIT_BUCKET'):
            with self.subTest(key=key),patch.dict(os.environ,{key:EXPECTED[key]},clear=True):
                with self.assertRaises(PermissionError):service_profile(os.environ)

    def test_cross_org_tokens_remain_rejected(self):
        import institution_context as ctx
        self.assertEqual(ctx.configured_org(EXPECTED),'mmh-review-demo')
        row={'org':'mmh-review-demo','user':'review01','role':'physician','disabled':False}
        good=dict(row,sub='mmh-review-demo:review01')
        with patch.object(ctx,'BOUND_ORG','mmh-review-demo'):
            self.assertTrue(ctx.token_matches_institution(good,lambda *args:row))
            self.assertFalse(ctx.token_matches_institution(dict(good,org='mmhps20261007'),lambda *args:row))
            self.assertFalse(ctx.token_matches_institution(dict(good,sub='mmhps20261007:review01'),lambda *args:row))
    def test_local_exporters_refuse_demo_before_reading_records(self):
        import export_flywheel_dataset as wound, export_tissue_dataset as tissue
        with patch.dict(os.environ,EXPECTED,clear=True),patch.object(wound.fw,'effective_queue') as read:
            self.assertEqual(wound.main(),1);self.assertEqual(tissue.main(),1);read.assert_not_called()

class ReviewAudit(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,EXPECTED,clear=True);self.env.start();self.addCleanup(self.env.stop)
        self.fake=audit_fixture.Fake(audit_fixture.PreconditionFailed)
        self.st=audit_fixture.make_gcs_store(self.fake);self.st.__class__=s.SyntheticReviewValidationStore
        self.st._bucket_name,self.st._security_bucket_name,self.st._audit_bucket_name=BUCKETS
        self.st._bucket=self.fake.bucket(BUCKETS[0]);self.st._security_bucket=self.fake.bucket(BUCKETS[1]);self.st._audit_bucket=self.fake.bucket(BUCKETS[2])
        self.st._audit_bucket._properties={}
    @staticmethod
    def record(seq,prev):
        r=audit_fixture.record(seq,prev);r.update(actor='mmh-review-demo:admin',org='mmh-review-demo');r['hash']=s.audit_hash(r);return r
    def append(self):return self.st.append_next_chained('audit.jsonl',self.record)
    def seed(self,count):
        previous='GENESIS'
        for seq in range(count):
            r=self.record(seq,previous);self.fake.put_object('flywheel/audit.jsonl/%020d.jsonl'%seq,json.dumps(r).encode());previous=r['hash']
    def test_truthful_unlocked_disclosure_and_mode(self):
        self.assertEqual(self.st.audit_write_mode,MODE)
        text=self.st.describe(retention=self.st.retention_info())
        self.assertIn('Synthetic review',text);self.assertIn('not WORM',text);self.assertNotIn('[MMH validation',text)
    def test_cannot_reuse_cached_mmh_instance(self):
        old,_=audit_fixture.fixture();s.reset_store(old)
        try:
            with patch.object(s,'_refuse_cloud_in_test_process'),self.assertRaises(RuntimeError):s.get_store()
        finally:s.reset_store()

# Reuse actual adverse audit behaviors on the new scoped store, without importing
# the old TestCase into this module's discovery or weakening its original tests.
for name in ('test_unlocked_append_and_receipt_remain_conditional','test_every_append_reads_all_historical_bytes',
             'test_parallel_reads_bounded_ordered_and_complete','test_parallel_network_failure_never_appends_and_latches',
             'test_parallel_middle_broken_link_never_appends','test_formal_gate_still_rejects_unlocked_subclass',
             'test_unreadable_or_unexpected_retention_never_downgrades','test_replaced_generation_latches_failure_across_retries',
             'test_scope_change_after_initialization_blocks_receipts_and_chain','test_bad_middle_bytes_even_without_generation_change_rejected',
             'test_restart_fully_validates_chain','test_generation_changes_during_download_rejected',
             'test_generation_changes_after_download_rejected','test_runtime_cannot_delete_move_or_generic_append_audit'):
    setattr(ReviewAudit,name,getattr(audit_fixture.UnlockedAuditTests,name))

class ReviewHTTP(unittest.TestCase):
    setUp=http_fixture.TissueHTTP.setUp
    tearDown=http_fixture.TissueHTTP.tearDown
    annotation=http_fixture.TissueHTTP.annotation
    rows=http_fixture.TissueHTTP.rows
    def test_server_marks_review_but_preserves_existing_source_and_receipt(self):
        with patch.dict(os.environ,EXPECTED,clear=True):rid=self.annotation()
        row=self.rows()[0];self.assertEqual(row['deployment_environment'],'synthetic-review')
        self.assertEqual(row['source'],'sample');self.assertEqual(row['annotation_receipt_id'],rid)
    def test_manifest_research_export_is_blocked_in_review_only(self):
        self.annotation()
        with patch.dict(os.environ,EXPECTED,clear=True):
            response=self.client.get('/api/v1/dataset/manifest?source=sample',headers=self.headers['admin'])
        self.assertEqual(response.status_code,403);self.assertEqual(response.json['error'],'review_dataset_export_disabled')
        response=self.client.get('/api/v1/dataset/manifest?source=sample',headers=self.headers['admin'])
        self.assertEqual(response.status_code,200)

class ReviewDataFlow(unittest.TestCase):
    """Actual annotation/depth endpoints, isolated synthetic disk store and JWT gate.

    Storage restart/audit behavior is separately covered by ReviewAudit; this
    fixture does not stand in for an eventual deployed GCS acceptance test.
    """
    def setUp(self):
        import institution_context as ctx
        from flask_jwt_extended import create_access_token
        http_fixture.TissueHTTP.setUp(self)
        self.addCleanup(http_fixture.TissueHTTP.tearDown,self)
        env=patch.dict(os.environ,EXPECTED,clear=True);env.start();self.addCleanup(env.stop)
        binding=patch.object(ctx,'BOUND_ORG','mmh-review-demo');binding.start();self.addCleanup(binding.stop)
        app=self.client.application
        accounts={u:{'org':'mmh-review-demo','user':u,'role':r,'disabled':False}
                  for u,r in [('dr1','physician'),('dr2','physician'),('admin','admin')]}
        app.extensions['flask-jwt-extended'].token_verification_loader(
            lambda header,claims:ctx.token_matches_institution(claims,lambda org,user:accounts.get(user)))
        self.headers={}
        with app.app_context():
            for user,row in accounts.items():
                token=create_access_token(identity='mmh-review-demo:'+user,additional_claims=row)
                self.headers[user]={'Authorization':'Bearer '+token}
            foreign=create_access_token(identity='mmhps20261007:dr1',additional_claims={
                'org':'mmhps20261007','user':'dr1','role':'physician'})
            self.foreign={'Authorization':'Bearer '+foreign}

    def test_foreign_mmh_token_rejected_without_saving(self):
        response=self.client.post('/api/v1/annotation',headers=self.foreign,json=self.payload())
        self.assertEqual(response.status_code,400)
        self.assertFalse(self.rows())

    def test_review_metadata_and_depth_receipt_bind_same_revision(self):
        body=self.payload();response=self.post(body);self.assertEqual(response.status_code,200,response.json)
        rid=response.json['annotation_receipt_id'];depth=self.depth(rid)
        self.assertEqual(depth.status_code,200,depth.json)
        row=self.rows()[0];bound=self.depths()[0]
        self.assertEqual(row['org'],'mmh-review-demo')
        self.assertEqual(row['deployment_environment'],'synthetic-review')
        self.assertEqual(row['annotation_receipt_id'],bound['annotation_receipt_id'])
        self.assertEqual(row['image_id'],bound['image_id'])
        self.assertEqual(row['wound_mask_sha256'],body['wound_mask_sha256'])

import test_native_wound_mask as native_fixture
for name in ('rows','depth','depths','payload','post','getmask',
             'test_roundtrip_hole_island_unknown_and_receipt',
             'test_same_payload_dedup_and_changed_hole_membership_new_revision',
             'test_invalid_masks_reject_before_asset_or_intent',
             'test_withdrawal_and_actor_guards_remain'):
    setattr(ReviewDataFlow,name,getattr(native_fixture.NativeMasks,name))

if __name__=='__main__':unittest.main()
