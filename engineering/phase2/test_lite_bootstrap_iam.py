"""Offline IAM contract/rollback tests, not Google CEL or effective-IAM proof."""
import copy
import unittest
from unittest.mock import patch
from lite_subscription_executor import canonical
from lite_usage_ledger import digest
import lite_bootstrap_iam as iam
from lite_fresh_bootstrap_factory import READ, CREATE
import test_lite_fresh_bootstrap_runner as fixtures


class Fixture:
    @classmethod
    def setUpClass(cls): fixtures.RunnerTests.setUpClass()
    def setUp(self):
        self.h=fixtures.RunnerTests();self.h.setUp();self.addCleanup(self.h.doCleanups)
    def propose(self,**changes):
        h=self.h
        args=dict(raw=canonical(h.runner),native_raw=canonical(h.config),
            expected_config=digest(h.runner),expected_native=digest(h.config),
            expected_source=h.runner['deployment']['source'],expected_image=h.runner['deployment']['image'],
            allowed_buckets=sorted(h.runner['buckets']),clock=h.f.h.clock)
        args.update(changes);return iam.propose(**args)

class ProposalTests(Fixture, unittest.TestCase):
    def test_twenty_lanes_use_runtime_modes_and_exact_principal(self):
        p=self.propose();self.assertEqual(len(p['object_lanes']),20)
        for r in p['object_lanes']:
            mode='read' if r['lane'] in READ else 'create' if r['lane'] in CREATE or r['lane']=='budget-audit' else 'cas'
            self.assertEqual(r['mode'],mode)
        self.assertEqual(p['principal'],'serviceAccount:'+self.h.runner['deployment']['service_account'])
        self.assertFalse(p['applied'] or p['effective_iam_verified'] or p['storage_list_granted'])
    def test_no_list_secret_kms_iam_or_model_authority(self):
        p=self.propose();perms={q for r in p['roles'].values() for q in r['includedPermissions']}
        self.assertEqual(perms,{'storage.objects.get','storage.objects.create','storage.objects.delete','storage.buckets.get'})
    def test_create_only_audit_and_readonly_subject_data(self):
        for r in self.propose()['object_lanes']:
            if r['lane'] in READ:self.assertEqual(iam.MODES[r['mode']],('storage.objects.get',))
            if r['lane'] in CREATE or r['lane']=='budget-audit':self.assertNotIn('storage.objects.delete',iam.MODES[r['mode']])
    def test_exact_prefix_boundary_excludes_shadow_and_other_buckets(self):
        p=self.propose()
        for r in p['object_lanes']:
            binding=next(b for b in p['bucket_bindings'][r['bucket']] if b['condition']['title'].endswith('_'+r['lane'].replace('-','_')))
            self.assertIn('/objects/'+r['prefix']+"/')",binding['condition']['expression'])
            self.assertNotIn('/objects/'+r['prefix']+"')",binding['condition']['expression'])
    def test_metadata_granted_separately_at_exact_bucket(self):
        p=self.propose()
        for b,bindings in p['bucket_bindings'].items():
            meta=[v for v in bindings if v['role']==p['roles']['metadata']['name']]
            self.assertEqual(len(meta),1);self.assertIn("resource.name == 'projects/_/buckets/"+b+"'",meta[0]['condition']['expression'])
    def test_every_binding_has_same_bounded_start_and_end(self):
        p=self.propose();self.assertLessEqual(p['expires_at']-p['not_before'],3600)
        for bindings in p['bucket_bindings'].values():
            for b in bindings:
                expr=b['condition']['expression']
                self.assertIn("request.time >= timestamp('"+iam.timestamp(p['not_before'])+"')",expr)
                self.assertIn("request.time < timestamp('"+iam.timestamp(p['expires_at'])+"')",expr)
    def test_expired_or_overlong_budget_cannot_issue_live_proposal(self):
        self.h.runner['provider_budget']['expires_at']=self.h.f.h.clock()
        with self.assertRaises(ValueError):self.propose()
        self.h.runner['provider_budget']['expires_at']=self.h.f.h.clock()+3601
        with self.assertRaises(ValueError):self.propose()
    def test_bucket_omission_extra_and_duplicate_refused(self):
        names=sorted(self.h.runner['buckets'])
        for wrong in ([],names[:-1],names+['mmh-production'],names+[names[0]]):
            with self.subTest(wrong=wrong),self.assertRaises(ValueError):self.propose(allowed_buckets=wrong)
    def test_tampered_pins_refused(self):
        for field,value in [('expected_config','0'*64),('expected_native','0'*64),('expected_source','0'*40),('expected_image','0'*64)]:
            with self.subTest(field=field),self.assertRaises(ValueError):self.propose(**{field:value})
    def test_native_runtime_cannot_become_bootstrap_principal(self):
        self.h.runner['deployment']['service_account']=self.h.config['deployment']['service_account']
        with self.assertRaises(ValueError):self.propose()
    def test_cannot_add_credential_or_research_lane(self):
        self.h.runner['locations']['grants']=self.h.config['stores']['grants']
        with self.assertRaises(ValueError):self.propose()
    def test_budget_cannot_alias_customer_data(self):
        self.h.runner['budget_stores']['control']=self.h.config['stores']['grants']
        with self.assertRaises(ValueError):self.propose()
    def test_no_provider_connect_or_credential_acquisition(self):
        with patch('lite_fresh_bootstrap_runner.GCSProviders.connect',side_effect=AssertionError('provider forbidden')):
            self.assertEqual(self.propose()['provider_calls'],0)


class DeltaTests(Fixture, unittest.TestCase):
    def setUp(self):
        super().setUp();self.p=self.propose();self.bucket=sorted(self.p['bucket_bindings'])[0]
        self.old=dict(version=3,etag='old-etag',bindings=[dict(role='roles/storage.objectViewer',members=['user:reader@example.test'])],
            auditConfigs=[dict(service='allServices',auditLogConfigs=[dict(logType='ADMIN_READ')])])
    def add(self,policy=None,**kw):
        args=dict(bucket=self.bucket,expected_proposal=digest(self.p),expected_etag='old-etag');args.update(kw)
        return iam.add_delta(self.old if policy is None else policy,self.p,**args)
    def undo(self,current,receipt,**kw):
        args=dict(bucket=self.bucket,expected_receipt=digest(receipt),expected_etag=current['etag']);args.update(kw)
        return iam.remove_delta(current,receipt,**args)
    def test_add_preserves_existing_fields_and_etag(self):
        out,receipt=self.add();self.assertEqual(out['etag'],self.old['etag'])
        self.assertEqual(out['auditConfigs'],self.old['auditConfigs'])
        self.assertEqual(out['bindings'][0],self.old['bindings'][0]);self.assertFalse(receipt['applied'])
    def test_prior_exact_bindings_are_not_owned_or_removed(self):
        existing,_=self.add();out,receipt=self.add(existing)
        self.assertEqual(receipt['added'],[]);self.assertEqual(out,existing)
        undone,status=self.undo(out,receipt);self.assertEqual(undone,out);self.assertEqual(status['removed'],0)
    def test_response_lost_then_cleanup_only_exact_additions(self):
        applied,receipt=self.add();applied['etag']='provider-etag'
        later=dict(role='roles/storage.legacyBucketReader',members=['user:later@example.test'])
        applied['bindings'].append(later)
        out,status=self.undo(applied,receipt)
        self.assertEqual(out['bindings'],self.old['bindings']+[later])
        self.assertEqual(out['auditConfigs'],self.old['auditConfigs']);self.assertEqual(out['etag'],'provider-etag')
        self.assertEqual(status['removed'],len(receipt['added']));self.assertFalse(status['effective_access_removed'])
    def test_cleanup_repeat_is_noop(self):
        applied,receipt=self.add();out,_=self.undo(applied,receipt)
        again,status=self.undo(out,receipt);self.assertEqual(again,out);self.assertEqual(status['removed'],0)
    def test_changed_owned_binding_never_gets_deleted_by_guess(self):
        applied,receipt=self.add();applied['bindings'][-1]['members'].append('user:other@example.test')
        with self.assertRaises(ValueError):self.undo(applied,receipt)
    def test_renamed_or_broadened_grant_stops_instead_of_claiming_removed(self):
        for change in ('rename','condition'):
            applied,receipt=self.add()
            if change=='rename':applied['bindings'][-1]['condition']['title']='changed-title'
            else:del applied['bindings'][-1]['condition']
            with self.subTest(change=change),self.assertRaises(ValueError):self.undo(applied,receipt)
    def test_receipt_does_not_share_mutable_objects_with_policy(self):
        applied,receipt=self.add();saved=copy.deepcopy(receipt)
        applied['bindings'][-1]['members'].append('user:other@example.test')
        self.assertEqual(receipt,saved)
    def test_unconditional_prior_grant_is_not_treated_as_restricted(self):
        old=copy.deepcopy(self.old);old['bindings'].append(dict(role='roles/storage.admin',members=[self.p['principal'].upper()]))
        with self.assertRaises(ValueError):self.add(old)
    def test_public_bucket_needs_independent_review(self):
        for member in ('allUsers','allAuthenticatedUsers'):
            old=copy.deepcopy(self.old);old['bindings'].append(dict(role='roles/storage.objectViewer',members=[member]))
            with self.assertRaises(ValueError):self.add(old)
    def test_stale_empty_or_changed_etag_never_yields_update(self):
        for etag in ('new-etag','',None):
            with self.assertRaises(ValueError):self.add(expected_etag=etag)
        applied,receipt=self.add()
        with self.assertRaises(ValueError):self.undo(applied,receipt,expected_etag='new-etag')
    def test_wrong_proposal_bucket_or_receipt_pin_refused(self):
        with self.assertRaises(ValueError):self.add(expected_proposal='0'*64)
        with self.assertRaises(ValueError):self.add(bucket='mmh-production')
        applied,receipt=self.add()
        with self.assertRaises(ValueError):self.undo(applied,receipt,expected_receipt='0'*64)
        with self.assertRaises(ValueError):self.undo(applied,receipt,bucket='mmh-production')
    def test_policy_v1_upgrades_to_three_without_dropping_bindings(self):
        old=copy.deepcopy(self.old);old['version']=1;out,_=self.add(old)
        self.assertEqual(out['version'],3);self.assertEqual(out['bindings'][0],old['bindings'][0])
    def test_missing_roles_are_only_create_candidates(self):
        obs={r['name']:dict(status=404,body=None) for r in self.p['roles'].values()}
        v=iam.check_roles(obs,self.p,expected_proposal=digest(self.p))
        self.assertEqual(len(v['create_only']),4);self.assertEqual(v['update_existing'],[])
    def test_same_named_broader_role_is_refused(self):
        obs={r['name']:dict(status=200,body=copy.deepcopy(r)) for r in self.p['roles'].values()}
        self.assertEqual(iam.check_roles(obs,self.p,expected_proposal=digest(self.p))['create_only'],[])
        next(iter(obs.values()))['body']['includedPermissions'].append('storage.objects.list')
        with self.assertRaises(ValueError):iam.check_roles(obs,self.p,expected_proposal=digest(self.p))
    def test_unknown_role_readback_is_never_missing(self):
        obs={r['name']:dict(status=404,body=None) for r in self.p['roles'].values()}
        for status in (403,500,None):
            next(iter(obs.values()))['status']=status
            with self.assertRaises(ValueError):iam.check_roles(obs,self.p,expected_proposal=digest(self.p))
        with self.assertRaises(ValueError):iam.check_roles({},self.p,expected_proposal=digest(self.p))


if __name__=='__main__':unittest.main()
