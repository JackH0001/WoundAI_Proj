"""Composition tests use real components with synthetic KMS/Apple providers."""
import copy
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from lite_disposition_progress import DispositionProgress, CATALOG, PENDING_LANES
from lite_attest_state import StateUnavailable
from lite_account_identity import IdentityRejected
from lite_apple_oauth import REVOKE_URL, TOKEN_URL
from lite_subscription_executor import key
from lite_usage_ledger import digest
from test_lite_account_deletion import coordinator
from test_lite_subscription_grace import rows
from lite_disposition_progress_probe import ProgressFixture, run
import test_lite_native_purchase as native


class ProgressTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):native.legacy.LinksTests.setUpClass()
    def setUp(self):self.f=ProgressFixture(self);self.p=self.f.progress
    def restart(self):
        d=coordinator(self.f.f)
        p=DispositionProgress(deletion=d,results=self.f.results,cipher=self.f.f.cipher,
            store=self.f.store('progress'),audit_store=self.f.store('progress-audit'))
        d.attach_disposition(p);self.f.d=d;self.f.progress=p;self.p=p
        return p
    def test_intake_v2_reports_missing_lanes_without_cleanup(self):
        self.f.complete();before=self.f.preserved();r=self.f.close()
        self.assertEqual(r['v'],2);self.assertEqual(r['disposition']['revision'],0)
        self.assertEqual(r['disposition']['pending_lanes'],PENDING_LANES)
        self.assertEqual(r['disposition']['service_total'],1)
        self.assertEqual(r['disposition']['grant_key_state'],'not_started')
        self.assertEqual(self.f.f.g.calls,[TOKEN_URL]);self.assertEqual(before,self.f.preserved())
    def test_worker_preserves_local_source_quota_and_remaining_data(self):
        original=bytes(self.f.capture);self.f.complete();before=self.f.preserved();self.f.close()
        r=self.f.advance();s=r['disposition']
        self.assertEqual(s['service_state'],'current_sealed');self.assertEqual(s['service_sealed'],1)
        self.assertEqual(s['grant_key_state'],'destroy_scheduled');self.assertFalse(r['account_deleted'])
        self.assertEqual(r['apple_revocation'],'confirmed');self.assertEqual(before,self.f.preserved())
        self.assertEqual(original,self.f.capture);self.assertEqual(self.f.calls,1)
    def test_key_completion_survives_restart_without_duplicate_revoke(self):
        self.f.complete();self.f.close();scheduled=self.f.advance();self.restart()
        v=self.f.f.provider.version(self.f.f.cipher.scope,self.f.person);self.f.f.kms.destroy_now(v)
        final=self.f.advance()
        self.assertEqual(final['disposition']['grant_key_state'],'unrecoverable')
        self.assertGreater(final['disposition']['revision'],scheduled['disposition']['revision'])
        self.assertEqual(self.f.f.g.calls.count(REVOKE_URL),1);self.assertFalse(final['account_deleted'])
        self.assertEqual(final['data_disposition'],'pending');self.assertTrue(final['local_records_preserved'])
    def test_status_never_calls_kms_or_revokes(self):
        self.f.complete();self.f.close();self.f.advance()
        before=self.f.f.kms.calls.copy(),self.f.f.g.calls.copy(),rows(self.p.store),rows(self.p.audit)
        for _ in range(3):self.f.status()
        self.assertEqual(before,(self.f.f.kms.calls,self.f.f.g.calls,rows(self.p.store),rows(self.p.audit)))
    def test_same_progress_replay_adds_no_observation(self):
        self.f.close();first=self.f.advance();before=rows(self.p.store),rows(self.p.audit)
        self.assertEqual(self.f.advance(),first);self.assertEqual(before,(rows(self.p.store),rows(self.p.audit)))
    def test_active_account_cannot_advance(self):
        with self.assertRaises(IdentityRejected):self.f.advance()
        self.assertEqual(rows(self.p.audit),[]);self.assertEqual(self.f.f.g.calls,[TOKEN_URL])
    def test_apple_outage_still_seals_masks_and_never_destroys_key(self):
        self.f.complete();self.f.close();self.f.f.g.revoke_error=True;r=self.f.advance()
        self.assertEqual(r['disposition']['service_state'],'current_sealed')
        self.assertEqual(r['disposition']['grant_key_state'],'not_started')
        self.assertEqual(r['apple_revocation'],'pending')
        self.assertNotIn('destroy',[x[0] for x in self.f.f.kms.calls])
        self.f.f.g.revoke_error=False;self.assertEqual(self.f.advance()['disposition']['grant_key_state'],'destroy_scheduled')
    def test_result_store_outage_does_not_block_revocation(self):
        self.f.complete();self.f.close()
        with patch.object(self.f.results.ops.store,'load',side_effect=StateUnavailable('offline')):r=self.f.advance()
        self.assertEqual(r['apple_revocation'],'confirmed')
        self.assertIn('service_results',r['disposition']['blocked_lanes'])
        self.assertEqual(r['disposition']['service_state'],'not_started')
        self.assertEqual(self.f.advance()['disposition']['service_state'],'current_sealed')
    def test_kms_outage_resumes_original_operation_without_repeating_model(self):
        self.f.complete();self.f.close();self.f.advance()
        with patch.object(self.f.f.provider,'describe',side_effect=StateUnavailable('offline')):r=self.f.advance()
        self.assertEqual(r['disposition']['blocked_lanes'],['grant_key'])
        self.assertEqual(r['disposition']['grant_key_state'],'destroy_scheduled')
        r2=self.restart().advance(self.f.person);self.assertEqual(r2['operation'],r['operation'])
        self.assertEqual(r2['disposition']['blocked_lanes'],[]);self.assertEqual(self.f.calls,1)
    def test_intent_audit_failure_prevents_all_worker_side_effects(self):
        self.f.complete();self.f.close();before=rows(self.f.ops.store),self.f.f.g.calls.copy()
        with patch.object(self.p.audit,'compare_exchange',side_effect=StateUnavailable('audit down')):
            with self.assertRaises(StateUnavailable):self.f.advance()
        self.assertEqual(before,(rows(self.f.ops.store),self.f.f.g.calls))
    def test_observation_write_failure_never_claims_progress_and_recovers(self):
        self.f.complete();self.f.close();real=self.p.audit.compare_exchange
        def fail(k,r,v):
            if 'sequence' in v:raise StateUnavailable('receipt unavailable')
            return real(k,r,v)
        with patch.object(self.p.audit,'compare_exchange',side_effect=fail):
            with self.assertRaises(StateUnavailable):self.f.advance()
        self.assertEqual(self.f.status()['disposition']['revision'],0)
        self.assertEqual(self.restart().advance(self.f.person)['disposition']['service_sealed'],1)
        self.assertEqual(self.f.f.g.calls.count(REVOKE_URL),1)
    def test_lost_checkpoint_acknowledgment_reads_back(self):
        self.f.complete();self.f.close();real=self.p.store.compare_exchange
        def lost(*a):real(*a);raise StateUnavailable('lost reply')
        with patch.object(self.p.store,'compare_exchange',side_effect=lost):r=self.f.advance()
        self.assertEqual(r['disposition']['service_sealed'],1)
    def test_missing_observation_blocks_status_and_worker_without_repairing_audit(self):
        self.f.complete();self.f.close();self.f.advance();self.p.audit=self.f.store('missing-audit')
        with self.assertRaises(StateUnavailable):self.f.status()
        before=self.f.f.kms.calls.copy()
        with self.assertRaises(StateUnavailable):self.f.advance()
        self.assertEqual(self.f.f.kms.calls,before)
    def test_malformed_checkpoint_not_reset(self):
        self.f.close();self.f.advance();p=self.p._plan(self.f.person);k=self.p._key(p);rev,v=self.p.store.load(k)
        self.p.store.compare_exchange(k,rev,dict(v,sequence=True))
        with self.assertRaises(StateUnavailable):self.f.status()
    def test_incorrect_bound_component_rejected(self):
        with self.assertRaises(ValueError):self.f.d.attach_disposition(object())
        with self.assertRaises(ValueError):DispositionProgress(deletion=self.f.d,results=self.f.results,cipher=object(),
            store=self.f.store('other'),audit_store=self.f.store('other-audit'))
    def test_shared_or_path_alias_stores_rejected(self):
        for s in (self.f.ops.store,self.f.f.key_store,self.f.d.audit,self.f.store('progress-audit')):
            with self.assertRaises(ValueError):DispositionProgress(deletion=self.f.d,results=self.f.results,cipher=self.f.f.cipher,
                store=s,audit_store=self.f.store('progress-audit'))
    def test_other_device_or_operation_cannot_read_status(self):
        self.f.f.login('device-two');self.f.close()
        for device,op in [('device-two','d'*32),('device-one','e'*32)]:
            with self.assertRaises(IdentityRejected):self.f.d.status(device,op)
    def test_more_than_one_hundred_results_resume_same_frozen_plan(self):
        for i in range(125):self.f.f.lifecycle.services.reserve(self.f.person,f'{i+100:032x}')
        self.f.close();r=self.f.advance(limit=100)
        self.assertEqual(r['disposition']['service_sealed'],100)
        r=self.restart().advance(self.f.person,limit=100);self.assertEqual(r['disposition']['service_sealed'],125)
        self.assertEqual(r['disposition']['service_total'],125)
    def test_invalid_batch_does_not_change_state(self):
        self.f.close();before=rows(self.p.store),rows(self.p.audit)
        for limit in (0,101,True,1.2):
            with self.assertRaises(ValueError):self.f.advance(limit=limit)
        self.assertEqual(before,(rows(self.p.store),rows(self.p.audit)))
    def test_stale_lower_result_receipt_cannot_regress_checkpoint(self):
        for i in range(3):self.f.f.lifecycle.services.reserve(self.f.person,f'{i+100:032x}')
        self.f.close();self.f.advance(limit=1);p=self.p._plan(self.f.person)
        early=self.p._read(p)[1]['service'];self.f.advance(limit=100)
        self.p._record(p,early,None,[])
        self.assertEqual(self.f.status()['disposition']['service_sealed'],3)
    def test_other_account_key_and_records_unchanged(self):
        self.f.f.login('other','apple-human-2');other=self.f.f.person
        before=self.f.f.cipher._read(other),self.f.f.kms.states.copy()
        self.f.complete();self.f.close();self.f.advance()
        self.assertEqual(self.f.f.cipher._read(other),before[0])
        version=self.f.f.provider.version(self.f.f.cipher.scope,other)
        self.assertEqual(self.f.f.kms.states[version],before[1][version])
    def test_no_internal_person_key_resource_or_token_in_public_progress(self):
        self.f.close();r=self.f.advance();text=repr(r)
        for secret in (self.f.person,'cryptoKeyVersions','private-refresh-token','wrapped_dek'):
            self.assertNotIn(secret,text)
    def test_probe_uses_one_model_call_and_preserves_usage(self):
        r=run();self.assertTrue(r['usage_identity_unchanged']);self.assertEqual(r['model_calls'],1)
        self.assertTrue(r['status_performs_no_provider_calls']);self.assertFalse(r['account_deleted'])


    def test_original_intent_is_not_recreated_after_loss(self):
        self.f.close();self.f.advance();real=self.p.audit.load
        target=key('disposition-progress-intent',digest(self.p._plan(self.f.person)))
        before=rows(self.p.audit),self.f.f.kms.calls.copy()
        with patch.object(self.p.audit,'load',side_effect=lambda k:None if k==target else real(k)):
            with self.assertRaises(StateUnavailable):self.f.advance()
        self.assertEqual(before,(rows(self.p.audit),self.f.f.kms.calls))
    def test_unattached_worker_cannot_dispatch(self):
        self.f.close();self.f.d.disposition=None
        with self.assertRaises(ValueError):self.f.advance()
        self.assertEqual(self.f.f.g.calls,[TOKEN_URL])
    def test_concurrent_workers_resume_one_deletion_without_duplicate_revoke(self):
        self.f.complete();self.f.close()
        d=coordinator(self.f.f)
        other=DispositionProgress(deletion=d,results=self.f.results,cipher=self.f.f.cipher,
            store=self.f.store('progress'),audit_store=self.f.store('progress-audit'))
        d.attach_disposition(other)
        def work(p):
            try:return p.advance(self.f.person)
            except StateUnavailable:return None # bounded lease/CAS contention, never a new operation
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(work,[self.p,other]))
        r=self.f.advance();self.assertEqual(r['disposition']['service_sealed'],1)
        self.assertEqual(self.f.f.g.calls.count(REVOKE_URL),1);self.assertEqual(self.f.calls,1)
    def test_late_scheduled_key_observation_cannot_regress_destroyed(self):
        self.f.close();self.f.advance();p=self.p._plan(self.f.person)
        old=self.p._read(p)[1]['grant_key'];self.f.f.kms.destroy_now(old['version']);self.f.advance()
        self.p._record(p,None,old,[])
        self.assertEqual(self.f.status()['disposition']['grant_key_state'],'unrecoverable')
    def test_invalid_component_claims_never_reach_progress(self):
        self.f.complete();self.f.close();self.f.advance();p=self.p._plan(self.f.person)
        r=self.p._read(p)[1];before=rows(self.p.store),rows(self.p.audit)
        for changes in ({'account_deleted':True},{'quota_reset':True},{'total':True},{'sealed':2},
                        {'research_data_changed':True},{'historical_versions_erased':True}):
            with self.assertRaises(StateUnavailable):self.p._record(p,dict(r['service'],**changes),None,[])
        for changes in ({'grant_key_unrecoverable':True},{'account_deleted':True},{'kms_state':'ENABLED'},
                        {'person':'0'*64},{'physical_backup_deletion_confirmed':True}):
            with self.assertRaises(StateUnavailable):self.p._record(p,None,dict(r['grant_key'],**changes),[])
        self.assertEqual(before,(rows(self.p.store),rows(self.p.audit)))
    def test_unknown_blocked_lane_cannot_be_persisted(self):
        self.f.close();p=self.p._plan(self.f.person);before=rows(self.p.store),rows(self.p.audit)
        for blocked in (['done'],[{}],None):
            with self.assertRaises(StateUnavailable):self.p._record(p,None,None,blocked)
        self.assertEqual(before,(rows(self.p.store),rows(self.p.audit)))
    def test_no_opt_in_preserves_legacy_receipt_and_status(self):
        self.f.d.disposition=None;r=self.f.close()
        self.assertEqual(r['v'],1);self.assertNotIn('disposition',r)
        self.assertEqual(r['data_disposition'],'not_started')


class ProgressHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):native.legacy.LinksTests.setUpClass()
    def setUp(self):
        import test_lite_service_http as http
        from lite_attest_http import install_lite_attest
        self.f=ProgressFixture(self);self.h=http.ServiceHTTPTests()
        def install(app,**kw):
            kw['commerce']=self.f.ops
            return install_lite_attest(app,**kw,identity=self.f.f.b,deletion=self.f.d)
        with patch.object(http,'install_lite_attest',side_effect=install):self.h.setUp()
        self.addCleanup(self.h.doCleanups);self.f.f.login(self.h.owner)
    def post(self,kind='request',**extra):
        import json
        path='/api/v1/lite/account/deletion/'+kind
        b=dict(anon_id=self.h.owner,operation='d'*32)
        if kind=='request':b['confirmation']='delete_account'
        b.update(extra);raw=json.dumps(b).encode()
        return self.h.client.post(path,data=raw,content_type='application/json',
            headers=self.h.headers(path,raw,'application/json',purpose='withdraw'))
    def test_signed_status_reports_real_worker_progress_without_dispatching(self):
        self.f.complete();r=self.post();self.assertEqual(r.status_code,202,r.json)
        self.assertEqual(r.json['disposition']['revision'],0)
        self.f.advance();before=self.f.f.kms.calls.copy(),self.f.f.g.calls.copy()
        r=self.post('status');self.assertEqual(r.status_code,202,r.json)
        self.assertEqual(r.json['disposition']['service_sealed'],1)
        self.assertEqual(r.headers['Cache-Control'],'no-store')
        self.assertEqual(before,(self.f.f.kms.calls,self.f.f.g.calls));self.assertEqual(self.f.calls,1)
    def test_unsigned_progress_cannot_close_or_disclose_account(self):
        r=self.h.client.post('/api/v1/lite/account/deletion/status',json={})
        self.assertEqual(r.status_code,401)
        self.assertEqual(self.f.f.lifecycle._read(self.f.person)[1]['phase'],'active')
    def test_worker_not_exposed_as_http_action(self):
        rules={r.rule for r in self.h.app.url_map.iter_rules()}
        self.assertFalse(any('/disposition' in r or '/deletion/advance' in r for r in rules))
    def test_corrupt_audit_returns_retryable_error_not_false_completion(self):
        self.post();self.f.advance();self.f.progress.audit=self.f.store('lost-progress-audit')
        r=self.post('status');self.assertEqual(r.status_code,503,r.json)
        self.assertNotIn('account_deleted',r.json)


if __name__=='__main__':unittest.main()
