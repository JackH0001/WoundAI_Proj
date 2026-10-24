"""Offline continuation regressions. No ADC, network, IAM or real GCS writes."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools'))
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'Backend/Flask'))
import medical_consent_gcs_continue as c
from medical_consent_gcs_smoke import preflight
from medical_consent_smoke_scope import *
from medical_fake_gcs import Client
from medical_gcs_sdk_fixture import Transport
from test_medical_gcs_smoke import configure


def run_manifest():return c.continuation_manifest('a'*40,{'x.py':'b'*64},'smoke-'+'b'*24,int(time.time()))
def seeded():
    raw=configure(Client())
    old=json.loads((Path(__file__).parent/'fixtures/consent_smoke_preserved_manifest_7df6037.json').read_text())
    for bucket,name,data in [(AUDIT,c.PRESERVED[0][1],canonical(old)),(MEDIA,c.PRESERVED[1][1],b'synthetic RGB fixture')]:
        expected=next(p for p in c.PRESERVED if p[0]==bucket)
        assert len(data)==expected[2] and hashlib.sha256(data).hexdigest()==expected[3]
        raw.bucket(bucket).blob(name).upload_from_string(data,if_generation_match=0)
    raw.uploads.clear();return raw
def clients(raw,budget=None):
    budget=budget or c.Budget()
    return [c.ScopedClient(raw,budget,prefix=CONTINUATION_PREFIX) for _ in range(2)]


class ManifestTests(unittest.TestCase):
    def test_valid_and_original_scope_stays_separate(self):
        r=run_manifest();self.assertEqual(c.validate_continuation(r,sha(r)),r)
        with self.assertRaises(ValueError):validate_manifest(r,sha(r))
        with self.assertRaises(PermissionError):preflight(clients(seeded()))
        with self.assertRaises(PermissionError):preflight([c.ScopedClient(seeded(),c.Budget())])
    def test_rehashed_tampering_still_rejected(self):
        for key,value in [('prefix',PREFIX),('previous_source','a'*40),('preserved_objects',[]),
                          ('previous_run_id','smoke-'+'f'*24),('limits',{}),('audit_is_worm',True)]:
            r=run_manifest();r[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):c.validate_continuation(r,sha(r))
        r=run_manifest();r['run_id']=r['previous_run_id']
        with self.assertRaises(ValueError):c.validate_continuation(r,sha(r))
    def test_expiry_digest_and_prefix_capability(self):
        r=run_manifest()
        with self.assertRaises(ValueError):c.validate_continuation(r,'0'*64)
        with self.assertRaises(ValueError):c.validate_continuation(r,sha(r),r['expires_at'])
        with self.assertRaises(PermissionError):c.ScopedClient(seeded(),c.Budget(),prefix=CONTINUATION_PREFIX+'-other')
    def test_runtime_cloud_guard_precedes_credentials(self):
        with tempfile.TemporaryDirectory() as d:
            r=run_manifest();p=Path(d)/'manifest.json';p.write_text(json.dumps(r))
            with patch.object(c,'source_snapshot',return_value=(r['source_commit'],r['source_files'])),patch.object(c,'validate_environment'),patch.object(c,'_refuse_cloud_in_test_process',side_effect=RuntimeError('offline guard')):
                with self.assertRaisesRegex(RuntimeError,'offline guard'):c.main(['--execute','--manifest',str(p),'--expected-sha256',sha(r),'--evidence-dir',str(Path(d)/'run')])
                self.assertFalse((Path(d)/'run').exists())


class PreservationTests(unittest.TestCase):
    def setUp(self):self.raw=seeded();self.clients=clients(self.raw)
    def test_two_exact_objects_are_read_only_and_generation_pinned(self):
        result=c.preservation_preflight(self.clients)
        self.assertEqual(len(result['baseline']),2);self.assertTrue(result['new_namespace_empty'])
        self.assertFalse(self.raw.uploads);self.assertFalse(self.raw.deletes)
    def test_missing_extra_versions_and_foreign_objects_block_before_write(self):
        for case in ('missing','noncurrent','soft','foreign','new-prefix'):
            raw=seeded();key=next(iter(raw.rows))
            if case=='missing':del raw.rows[key]
            elif case in ('noncurrent','soft'):
                raw.rows[key[0],key[1],99]=dict(data=raw.rows[key]['data'],state=case,hold=False)
            else:
                name=CONTINUATION_PREFIX+'/images/x.jpg' if case=='new-prefix' else 'unrelated'
                raw.rows[MEDIA,name,99]=dict(data=b'x',state='live',hold=False)
            with self.subTest(case=case),self.assertRaises(PermissionError):c.preservation_preflight(clients(raw))
            self.assertFalse(raw.uploads);self.assertFalse(raw.deletes)
    def test_wrong_size_and_equal_length_wrong_hash_stop(self):
        for data in (b'x',b'x'*21):
            raw=seeded();key=next(k for k in raw.rows if k[0]==MEDIA);raw.rows[key]['data']=data
            with self.subTest(size=len(data)),self.assertRaises(PermissionError):c.preservation_preflight(clients(raw))
            self.assertFalse(raw.uploads)
    def test_generation_replacement_after_baseline_is_rejected_even_same_bytes(self):
        baseline=c.preservation_preflight(self.clients)['baseline'];key=next(iter(self.raw.rows))
        row=self.raw.rows.pop(key);self.raw.rows[key[0],key[1],99]=row
        with self.assertRaises(PermissionError):c.preserved_readback(self.clients[0],baseline=baseline,allow_new=True)
    def test_change_between_clients_is_rejected(self):
        calls=0;original=c.preserved_readback
        def read(client,**kw):
            nonlocal calls
            calls+=1
            if calls==2:
                key=next(iter(self.raw.rows));row=self.raw.rows.pop(key);self.raw.rows[key[0],key[1],99]=row
            return original(client,**kw)
        with patch.object(c,'preserved_readback',side_effect=read),self.assertRaises(PermissionError):c.preservation_preflight(self.clients)
    def test_old_namespace_never_gets_writable_capability(self):
        client=self.clients[0]
        for bucket,name,_,_ in c.PRESERVED:
            with self.subTest(name=name),self.assertRaises(PermissionError):client.bucket(bucket).blob(name)
        for name in (CONTINUATION_PREFIX+'x/images/a',CONTINUATION_PREFIX+'/images/../a',CONTINUATION_PREFIX+'/images/%2f'):
            with self.assertRaises(PermissionError):client.bucket(MEDIA).blob(name)
        self.assertFalse(self.raw.uploads);self.assertFalse(self.raw.deletes)
    def test_unknown_metadata_never_downloaded(self):
        self.raw.rows[MEDIA,'patient-unrelated',999]=dict(data=b'not to read',state='live',hold=False)
        with patch.object(self.raw.bucket(MEDIA),'blob',side_effect=AssertionError('must reject inventory first')):
            # Known media may be encountered first; sort unknown before known.
            self.raw.rows[MEDIA,'000-unknown',1000]=self.raw.rows.pop((MEDIA,'patient-unrelated',999))
            with self.assertRaises(PermissionError):c.preserved_readback(self.clients[0])
    def test_non_live_only_and_permission_policy_drift_rejected(self):
        key=next(iter(self.raw.rows));self.raw.rows[key]['state']='noncurrent'
        from google.api_core.exceptions import NotFound
        with self.assertRaises(NotFound):c.preserved_readback(self.clients[0])
        self.raw=seeded();self.raw.bucket(AUDIT).permissions|={'storage.objects.delete'}
        with self.assertRaises(PermissionError):c.preserved_readback(clients(self.raw)[0])
    def test_result_failure_keeps_partial_evidence_and_checks_old_bytes(self):
        old=copy.deepcopy(self.raw.rows)
        def fail(stores,value,record):
            stores[0].put_blob_immutable('images/'+'a'*16+'.jpg',b'new synthetic')
            raise ValueError('secret must not enter result')
        with tempfile.TemporaryDirectory() as d,patch.object(c,'scenarios',side_effect=fail):
            r=c.run_verified(self.clients,run_manifest(),Path(d),self.clients[0].budget)
            self.assertEqual(r['status'],'blocked');self.assertTrue(r['preserved_after_verified'])
            self.assertNotIn('secret',json.dumps(r));self.assertEqual(r['error']['phase'],'scenarios')
        for k,v in old.items():self.assertEqual(self.raw.rows[k],v)
        with self.assertRaises(PermissionError):c.preservation_preflight(self.clients)
    def test_post_readback_failure_cannot_claim_success(self):
        original=c.preserved_readback
        def read(client,**kw):
            if kw.get('allow_new'):raise PermissionError('post read blocked')
            return original(client,**kw)
        with tempfile.TemporaryDirectory() as d,patch.object(c,'scenarios'),patch.object(c,'preserved_readback',side_effect=read):
            r=c.run_verified(self.clients,run_manifest(),Path(d),self.clients[0].budget)
            self.assertEqual(r['status'],'blocked');self.assertFalse(r['preserved_after_verified'])
            self.assertEqual(r['preservation_error']['phase'],'preserved_after')
    def test_scenario_prefix_mismatch_refused_before_claim(self):
        from medical_consent_gcs_smoke import scenarios,SyntheticSmokeStore
        r=run_manifest();r['prefix']=PREFIX
        with self.assertRaises(PermissionError):scenarios([SyntheticSmokeStore(x) for x in self.clients],r,lambda row:None)
        self.assertFalse(self.raw.uploads)
    def test_budget_exhaustion_reports_unverified_preservation(self):
        def exhaust(stores,value,record):self.clients[0].budget.calls=LIMITS['max_sdk_calls']
        with tempfile.TemporaryDirectory() as d,patch.object(c,'scenarios',side_effect=exhaust):
            r=c.run_verified(self.clients,run_manifest(),Path(d),self.clients[0].budget)
            self.assertEqual(r['status'],'blocked');self.assertFalse(r['preserved_after_verified'])
            self.assertEqual(r['preservation_error']['type'],'RuntimeError')
        self.assertFalse(self.raw.uploads)


class RealSDKTests(unittest.TestCase):
    def setUp(self):
        self.raw=seeded();self.http=Transport(self.raw);self.budget=c.Budget()
        self.clients=[c.ScopedClient(self.http.client(PROJECT),self.budget,prefix=CONTINUATION_PREFIX) for _ in range(2)]
    def test_full_real_sdk_continuation_and_readback_no_network(self):
        old=copy.deepcopy(self.raw.rows)
        with tempfile.TemporaryDirectory() as d:
            r=c.run_verified(self.clients,run_manifest(),Path(d),self.budget)
            self.assertIsNone(r['error'],r);self.assertIsNone(r['preservation_error'],r)
            self.assertEqual(r['status'],'bounded-scenarios-passed-expiry-pending')
            self.assertTrue(r['preserved_after_verified']);self.assertGreater(len(r['outcomes']),5)
        for k,v in old.items():self.assertEqual(self.raw.rows[k],v)
        self.assertTrue(all(name.startswith(CONTINUATION_PREFIX+'/') for _,name,_ in self.raw.uploads))
        self.assertTrue(all(key[1].startswith(CONTINUATION_PREFIX+'/') and key[0]==MEDIA for key,_ in self.raw.deletes))
        self.assertTrue(any('pageToken=' in url for _,url in self.http.calls))
        with self.assertRaises(PermissionError):c.preservation_preflight(self.clients)
    def test_real_sdk_generation_change_between_list_and_metadata(self):
        def before(method,url,kw):
            if '/o/' in url and method=='GET':
                self.http.before=None
                key=next(k for k in self.raw.rows if k[0] in url)
                self.raw.bucket(key[0]).blob(key[1]).upload_from_string(self.raw.rows[key]['data'])
        self.http.before=before
        from google.api_core.exceptions import PreconditionFailed
        with self.assertRaises(PreconditionFailed):c.preservation_preflight(self.clients)
        self.assertFalse(self.raw.deletes)
    def test_real_sdk_second_page_failure_is_not_empty(self):
        # Duplicate metadata forces page two; fail there before any body read.
        for i in range(2):self.raw.rows[AUDIT,'z-extra-'+str(i),100+i]=dict(data=b'x',state='live',hold=False)
        def before(method,url,kw):
            if 'pageToken=' in url:raise PermissionError('synthetic second page denied')
        self.http.before=before
        with self.assertRaisesRegex(PermissionError,'second page denied'):
            list(c.root_versions(self.clients[0],AUDIT))
        self.assertTrue(any('pageToken=' in url for _,url in self.http.calls))
        self.assertFalse(self.raw.uploads);self.assertFalse(self.raw.deletes)
    def test_real_sdk_missing_or_bad_preserved_body_is_rejected(self):
        key=next(k for k in self.raw.rows if k[0]==MEDIA);self.raw.rows[key]['data']=b'x'*21
        with self.assertRaises(PermissionError):c.preservation_preflight(self.clients)
        self.assertFalse(self.raw.uploads)


if __name__=='__main__':unittest.main()
