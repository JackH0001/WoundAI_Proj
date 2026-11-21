import copy
import json
import socket
import unittest
from unittest.mock import patch
from lite_fresh_inventory import MetadataInventory, InventoryUnavailable
from lite_usage_ledger import digest


class InventoryTests(unittest.TestCase):
    def setUp(self):
        p=patch.object(socket.socket,'connect',side_effect=AssertionError('no network'));p.start();self.addCleanup(p.stop)
        self.sources=[dict(id=r,role=r,project_number='123456789',location=dict(bucket='synthetic-bucket',prefix='native/test/'+r,match='directory'))
                      for r in ('accounts','links','quota')]
        self.calls=[];self.now=0
        self.meta=dict(name='synthetic-bucket',projectNumber='123456789',metageneration='1',
                       versioning={'enabled':True},softDeletePolicy={'retentionDurationSeconds':'604800'})
        self.reply=lambda p,q:(200,{})
    def get(self,path,query):
        self.calls.append((path,query))
        if path.endswith('/o'):return self.reply(path,query)
        return 200,copy.deepcopy(self.meta)
    def scan(self,**options):
        return MetadataInventory(get=self.get,clock=lambda:self.now,**options).inspect(self.sources,expected_sources=digest(self.sources))
    def item(self,query,**changes):
        row=dict(name=query['prefix']+'private-person.json',generation='17',size='123')
        if 'softDeleted' in query:row['softDeleteTime']='2026-11-04T00:00:00Z'
        row.update(changes);return row
    def test_complete_empty_metadata_never_grants_bootstrap_or_writer_fence(self):
        r=self.scan();self.assertTrue(r['complete']);self.assertEqual(r['requests'],12)
        self.assertTrue(all(x['observed_empty'] is True for x in r['sources']))
        for k in ('authorizes_bootstrap','writer_fence_verified','historical_never_used_verified'):self.assertFalse(r[k])
    def test_versions_and_soft_delete_separate_queries_without_payload(self):
        self.scan()
        for p,q in self.calls:
            self.assertNotIn('alt',q)
            if p.endswith('/o'):
                self.assertEqual(sum(k in q for k in ('versions','softDeleted')),1)
                self.assertEqual(q['maxResults'],'100');self.assertNotIn('delimiter',q)
    def test_retained_versions_and_soft_deletes_count_but_names_not_exported(self):
        self.reply=lambda p,q:(200,dict(items=[self.item(q,timeDeleted='2026-11-03T00:00:00Z')]))
        r=self.scan();self.assertTrue(r['complete'])
        for s in r['sources']:
            self.assertFalse(s['observed_empty']);self.assertEqual(s['versions']['count'],1)
            self.assertEqual(s['soft_deleted']['bytes'],123);self.assertEqual(s['versions']['noncurrent'],1)
        self.assertNotIn('private-person',json.dumps(r))
    def test_empty_page_with_token_does_not_terminate_inventory(self):
        self.reply=lambda p,q:(200,dict(items=[self.item(q)]) if 'pageToken' in q else dict(nextPageToken='next'))
        r=self.scan();self.assertTrue(r['complete']);self.assertEqual(r['requests'],18)
        self.assertTrue(all(s['versions']['count']==1 for s in r['sources']))
    def test_wrong_pin_rejected_before_get(self):
        with self.assertRaises(ValueError):MetadataInventory(get=self.get).inspect(self.sources,expected_sources='0'*64)
        self.assertEqual(self.calls,[])
    def test_incomplete_classes_or_duplicate_id_rejected_before_get(self):
        for fields in ({'role':'accounts'},{'id':'accounts'}):
            old=copy.deepcopy(self.sources);self.sources[1].update(fields)
            if 'role' in fields:self.sources[2].update(fields)
            with self.assertRaises(ValueError):self.scan()
            self.sources=old
        self.assertEqual(self.calls,[])
    def test_prefix_alias_or_sibling_escape_rejected(self):
        self.sources[1]['location']['prefix']='native/test/accounts/nested'
        with self.assertRaises(ValueError):self.scan()
        self.assertEqual(self.calls,[])
    def test_flat_legacy_ledger_prefix_is_explicit_and_not_treated_as_directory(self):
        self.sources[2]['location'].update(prefix='research/lite_rate_',match='filename_prefix')
        self.scan();requests=[q for p,q in self.calls if p.endswith('/o')]
        self.assertEqual([q['prefix'] for q in requests[-2:]],['research/lite_rate_']*2)
    def test_broad_unqualified_filename_prefix_refused(self):
        self.sources[2]['location'].update(prefix='research/lite',match='filename_prefix')
        with self.assertRaises(ValueError):self.scan()
        self.assertEqual(self.calls,[])
    def test_403_unknown_never_zero(self):
        self.reply=lambda p,q:(403,{})
        r=self.scan();self.assertFalse(r['complete'])
        self.assertTrue(all(s['observed_empty'] is None and s['versions']['count'] is None for s in r['sources']))
    def test_soft_delete_400_with_disabled_policy_stays_unknown(self):
        self.meta['softDeletePolicy']['retentionDurationSeconds']='0'
        self.reply=lambda p,q:(400,{}) if 'softDeleted' in q else (200,{})
        r=self.scan();self.assertFalse(r['complete'])
        self.assertEqual(r['sources'][0]['versions']['count'],0)
        self.assertIsNone(r['sources'][0]['soft_deleted']['count']);self.assertIsNone(r['sources'][0]['observed_empty'])
    def test_missing_bucket_does_not_prove_unused_history(self):
        with patch.object(self,'get',return_value=(404,{})):r=self.scan()
        self.assertFalse(r['complete']);self.assertTrue(all(s['status']=='bucket_absent' for s in r['sources']))
        self.assertTrue(all(s['observed_empty'] is None for s in r['sources']))
    def test_page_bound_is_incomplete(self):
        self.reply=lambda p,q:(200,dict(nextPageToken=q.get('pageToken','')+'x'))
        r=self.scan(max_pages=2);self.assertFalse(r['complete'])
        self.assertTrue(all(s['reason']=='inventory page bound' for s in r['sources']))
    def test_repeated_page_token_fails(self):
        self.reply=lambda p,q:(200,dict(nextPageToken='x'))
        r=self.scan();self.assertFalse(r['complete']);self.assertEqual(r['requests'],9)
    def test_duplicate_generation_across_pages_fails(self):
        self.reply=lambda p,q:(200,dict(items=[self.item(q)],nextPageToken=q.get('pageToken','')+'x'))
        r=self.scan();self.assertFalse(r['complete'])
        self.assertIn('duplicate',r['sources'][0]['reason'])
    def test_item_limit_is_incomplete(self):
        self.reply=lambda p,q:(200,dict(items=[self.item(q,generation='1'),self.item(q,generation='2')]))
        r=self.scan(max_items=1);self.assertFalse(r['complete']);self.assertIn('item bound',r['sources'][0]['reason'])
    def test_foreign_prefix_never_accepted(self):
        self.reply=lambda p,q:(200,dict(items=[self.item(q,name=q['prefix'].rstrip('/')+'-other/private.json')]))
        r=self.scan();self.assertFalse(r['complete']);self.assertIn('outside',r['sources'][0]['reason'])
    def test_bad_metadata_number_rejected(self):
        for value in (False,None,-1,'-1','1.1','01'):
            self.reply=lambda p,q:(200,dict(items=[self.item(q,size=value)]))
            with self.subTest(value=value):self.assertFalse(self.scan()['complete'])
    def test_soft_deleted_items_need_expected_metadata(self):
        self.reply=lambda p,q:(200,dict(items=[self.item(q,softDeleteTime=None)])) if 'softDeleted' in q else (200,{})
        self.assertFalse(self.scan()['complete'])
    def test_policy_drift_after_listing_preserves_unknown(self):
        def reply(p,q):self.meta['metageneration']='2';return 200,{}
        self.reply=reply;r=self.scan();self.assertFalse(r['sources'][0]['complete'])
        self.assertIn('policy changed',r['sources'][0]['reason'])
    def test_wrong_project_does_not_list_objects(self):
        self.meta['projectNumber']='9';r=self.scan()
        self.assertFalse(r['complete']);self.assertTrue(all(not p.endswith('/o') for p,q in self.calls))
    def test_network_error_sanitized_not_leaked(self):
        def reply(p,q):raise RuntimeError('Bearer should-never-be-logged')
        self.reply=reply;r=self.scan()
        self.assertFalse(r['complete']);self.assertNotIn('Bearer',json.dumps(r))
    def test_request_and_wall_clock_bounds(self):
        r=self.scan(max_requests=2);self.assertEqual(r['requests'],2);self.assertFalse(r['complete'])
        def reply(p,q):self.now+=130;return 200,{}
        self.reply=reply;r=self.scan();self.assertFalse(r['complete']);self.assertEqual(r['requests'],2)
    def test_hierarchical_namespace_requires_different_inventory(self):
        self.meta['hierarchicalNamespace']={'enabled':True};self.assertFalse(self.scan()['complete'])
        self.assertTrue(all(not p.endswith('/o') for p,q in self.calls))
    def test_collector_is_single_use(self):
        c=MetadataInventory(get=self.get);c.inspect(self.sources,expected_sources=digest(self.sources))
        calls=len(self.calls)
        with self.assertRaises(ValueError):c.inspect(self.sources,expected_sources=digest(self.sources))
        self.assertEqual(len(self.calls),calls)


if __name__=='__main__':unittest.main()
