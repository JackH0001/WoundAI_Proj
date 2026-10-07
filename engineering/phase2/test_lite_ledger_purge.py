"""Live research row purge, preserving other owners and enforcing version/lock boundaries."""
import concurrent.futures
import json
import multiprocessing
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'Backend'/'Flask'))
from store import LocalStore, GcsStore
from lite_ledger_purge import purge_live_research_rows
from google.api_core.exceptions import NotFound, PreconditionFailed


def append_worker(root, kind, started, done):
    store=LocalStore(root);started.set()
    if kind=='line': store.append_line('lite_labels.jsonl',json.dumps(dict(anon_id='other',value='concurrent')))
    else: store.append_record_once('lite_labels.jsonl','0'*16,dict(anon_id='other',annotation_receipt_id='0'*16))
    done.set()


class LocalPurgeTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.store=LocalStore(tmp.name);self.path=Path(tmp.name)/'lite_labels.jsonl'
    def write(self, rows): self.path.write_bytes(b''.join(json.dumps(r).encode()+b'\n' for r in rows))
    def rows(self): return [json.loads(v) for v in self.store.read_lines('lite_labels.jsonl')]
    def test_only_target_research_rows_removed_other_bytes_and_minimal_marker_preserved(self):
        other=b' { "anon_id": "other", "private": "untouched" } \r\n'
        marker=dict(anon_id='target',action='withdrawal_requested',received_at='now')
        self.write([dict(anon_id='target',polygons=[1,2]),marker]);self.path.write_bytes(self.path.read_bytes()+other)
        self.assertEqual(purge_live_research_rows(self.store,'lite_labels.jsonl','target'),1)
        self.assertIn(other,self.path.read_bytes());self.assertIn(marker,self.rows())
        self.assertEqual(purge_live_research_rows(self.store,'lite_labels.jsonl','target'),0)
    def test_action_with_payload_cannot_bypass_cleanup(self):
        self.write([dict(anon_id='target',action='deleted',payload_json='sensitive')])
        self.assertEqual(purge_live_research_rows(self.store,'lite_labels.jsonl','target'),1);self.assertEqual(self.rows(),[])
    def test_malformed_or_unattributable_row_aborts_before_any_mutation(self):
        for invalid in [b'broken',b'{}',b'[]',b'{"anon_id":"other","anon_id":"target"}',b'{"anon_id":"target","value":NaN}']:
            original=b'{"anon_id":"target","payload":1}\n'+invalid+b'\n';self.path.write_bytes(original)
            with self.assertRaises((ValueError,TypeError)):purge_live_research_rows(self.store,'lite_labels.jsonl','target')
            self.assertEqual(self.path.read_bytes(),original)
    def test_failure_before_atomic_replace_preserves_original(self):
        self.write([dict(anon_id='target',payload='keep until commit')]);original=self.path.read_bytes()
        with patch('lite_ledger_purge.os.replace',side_effect=OSError('injected')):
            with self.assertRaises(OSError):purge_live_research_rows(self.store,'lite_labels.jsonl','target')
        self.assertEqual(self.path.read_bytes(),original)
        self.assertEqual(list(self.path.parent.glob('.lite-purge-*')),[])
    def test_only_two_exact_research_ledgers_are_accepted(self):
        for key in ['audit.jsonl','receipts/a','retrain_queue.jsonl','../lite_labels.jsonl',str(self.path),'lite_labels.jsonl/x']:
            with self.assertRaises(ValueError):purge_live_research_rows(self.store,key,'target')
    def test_missing_ledger_is_empty(self):
        self.assertEqual(purge_live_research_rows(self.store,'lite_labels.jsonl','target'),0)
    def test_other_process_append_waits_for_purge_and_is_not_lost(self):
        context=multiprocessing.get_context('spawn')
        for kind in ('line','once'):
            self.write([dict(anon_id='target',polygons=[1])])
            entered=threading.Event();resume=threading.Event();started=context.Event();done=context.Event()
            original=os.replace
            def paused(src,dst):
                entered.set()
                if not resume.wait(8):raise RuntimeError('test timeout')
                return original(src,dst)
            with patch('lite_ledger_purge.os.replace',side_effect=paused), concurrent.futures.ThreadPoolExecutor(1) as pool:
                future=pool.submit(purge_live_research_rows,self.store,'lite_labels.jsonl','target')
                process=context.Process(target=append_worker,args=(self.store.root,kind,started,done))
                try:
                    self.assertTrue(entered.wait(5));process.start();self.assertTrue(started.wait(5))
                    self.assertFalse(done.wait(.2),'writer bypassed purge lock')
                finally:
                    resume.set()
                    if process.pid is not None:process.join(8)
                self.assertEqual(future.result(5),1)
                self.assertFalse(process.is_alive());self.assertEqual(process.exitcode,0)
            self.assertEqual([r['anon_id'] for r in self.rows()],['other'])


class FakeBlob:
    def __init__(self,bucket,name,generation=None): self.bucket=bucket;self.name=name;self.generation=generation
    def download_as_bytes(self,*,if_generation_match,checksum):
        if checksum!='crc32c' or if_generation_match!=self.generation:raise AssertionError('unpinned read')
        current=self.bucket.values.get(self.name)
        if current is None:raise NotFound('missing')
        if current[0]!=self.generation:raise PreconditionFailed('changed')
        return current[1]
    def delete(self,*,if_generation_match):
        self.bucket.calls.append((self.name,if_generation_match))
        if self.bucket.before_delete:self.bucket.before_delete(self.name)
        current=self.bucket.values.get(self.name)
        if current is None:raise NotFound('gone')
        if current[0]!=if_generation_match:raise PreconditionFailed('changed')
        if not self.bucket.ignore_delete:del self.bucket.values[self.name]


class FakeBucket:
    def __init__(self):self.values={};self.calls=[];self.before_delete=None;self.ignore_delete=False
    def blob(self,name,generation=None):return FakeBlob(self,name,generation)
    def list_blobs(self,bucket,prefix):
        assert bucket=='research'
        return [FakeBlob(self,k,g) for k,(g,_) in sorted(self.values.items()) if k.startswith(prefix)]


class GCSPurgeTests(unittest.TestCase):
    def setUp(self):
        self.bucket=FakeBucket();self.store=object.__new__(GcsStore)
        self.store._bucket=self.bucket;self.store._bucket_name='research';self.store._client=self.bucket
        self.store.prefix='';self.store._line_cache={('research','lite_labels.jsonl/'):dict(stale=True)}
        self.key='lite_labels.jsonl';self.name=self.key+'/receipt_'+'1'*16+'.jsonl'
    def put(self,name,owner='target',generation=1,**extra):
        self.bucket.values[name]=(generation,json.dumps(dict(anon_id=owner,**extra)).encode()+b'\n')
    def test_target_generation_deleted_other_owner_untouched_cache_cleared(self):
        self.put(self.name,payload=1);other=self.key+'/receipt_'+'2'*16+'.jsonl';self.put(other,'other',payload=2)
        original=self.bucket.values[other]
        self.assertEqual(purge_live_research_rows(self.store,self.key,'target'),1)
        self.assertEqual(self.bucket.calls,[(self.name,1)]);self.assertEqual(self.bucket.values[other],original)
        self.assertEqual(self.store._line_cache,{})
    def test_replacement_between_inventory_and_delete_is_not_deleted(self):
        self.put(self.name,payload=1)
        self.bucket.before_delete=lambda name:self.put(name,'other',generation=2,payload=2)
        with self.assertRaises(PreconditionFailed):purge_live_research_rows(self.store,self.key,'target')
        self.assertEqual(json.loads(self.bucket.values[self.name][1])['anon_id'],'other')
        self.assertEqual(self.store._line_cache,{})
    def test_lying_delete_is_caught_by_fresh_readback(self):
        self.put(self.name,payload=1);self.bucket.ignore_delete=True
        with self.assertRaises(OSError):purge_live_research_rows(self.store,self.key,'target')
    def test_mixed_owner_object_is_never_deleted(self):
        self.put(self.name,payload=1);self.bucket.values[self.name]=(1,self.bucket.values[self.name][1]+b'{"anon_id":"other"}\n')
        with self.assertRaises(ValueError):purge_live_research_rows(self.store,self.key,'target')
        self.assertEqual(self.bucket.calls,[])
    def test_missing_generation_or_unknown_object_name_aborts_before_delete(self):
        for name,generation in [(self.name,None),(self.key+'/unknown.jsonl',1)]:
            self.bucket.values={};self.put(name,generation=generation,payload=1)
            with self.assertRaises(ValueError):purge_live_research_rows(self.store,self.key,'target')
            self.assertEqual(self.bucket.calls,[])
    def test_minimal_revocation_marker_remains(self):
        self.put(self.name,action='withdrawal_requested',received_at='now')
        self.assertEqual(purge_live_research_rows(self.store,self.key,'target'),0);self.assertIn(self.name,self.bucket.values)
    def test_concurrent_same_cleanup_not_found_requires_fresh_empty_inventory(self):
        self.put(self.name,payload=1)
        self.bucket.before_delete=lambda name:self.bucket.values.pop(name,None)
        self.assertEqual(purge_live_research_rows(self.store,self.key,'target'),0)
    def test_inventory_read_failure_does_not_mutate_other_objects(self):
        self.put(self.name,payload=1)
        with patch.object(FakeBlob,'download_as_bytes',side_effect=OSError('unavailable')):
            with self.assertRaises(OSError):purge_live_research_rows(self.store,self.key,'target')
        self.assertEqual(self.bucket.calls,[])


if __name__=='__main__':unittest.main()
