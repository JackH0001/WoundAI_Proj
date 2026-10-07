"""Persistence tests use synthetic bytes and explicit temporary LocalStore only."""
import concurrent.futures
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import test_lite_raw_depth_contract as contract_fixture
from lite_raw_depth_store import save_capture,unpack,capture_key,erase_installation_objects
from store import LocalStore,ImmutableConflict

class RawDepthStoreTests(unittest.TestCase):
    def setUp(self):
        self.fixture=contract_fixture.RawDepthTests();self.fixture.setUp()
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=LocalStore(self.temp.name)
    def save(self,metadata=None):
        f=self.fixture
        return save_capture(self.store,'test-install','test-capture',json.dumps(f.m if metadata is None else metadata).encode(),f.raw,f.jpeg)
    def test_single_object_roundtrip_and_identical_retry(self):
        first=self.save();self.assertEqual(self.save(),first)
        files=[p for p in Path(self.temp.name).rglob('*') if p.is_file()];self.assertEqual(len(files),1)
        packet=unpack(files[0].read_bytes());self.assertEqual(packet['raw_depth'],self.fixture.raw)
        self.assertEqual(packet['jpeg'],self.fixture.jpeg)
    def test_concurrent_identical_uploads_share_receipt(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:receipts=list(executor.map(lambda _:self.save(),range(12)))
        self.assertTrue(all(r==receipts[0] for r in receipts))
    def test_conflicting_capture_cannot_overwrite(self):
        first=self.save()
        with self.assertRaises(ImmutableConflict):self.save(dict(self.fixture.m,filtered=True))
        self.assertEqual(self.save(),first)
    def test_readback_failure_never_returns_success_and_retry_recovers(self):
        with patch.object(self.store,'get_blob',return_value=None):
            with self.assertRaises(IOError):self.save()
        self.assertEqual(self.save()['status'],'stored')
    def test_storage_failure_never_returns_receipt(self):
        with patch.object(self.store,'put_blob_immutable',side_effect=OSError('injected')):
            with self.assertRaises(OSError):self.save()
    def test_gcs_fake_transport_uses_generation_zero_and_same_receipt(self):
        from google.api_core.exceptions import PreconditionFailed
        from test_audit_chain_concurrency import _FakeGcs,_FakeBlob,make_gcs_store
        fake=_FakeGcs(PreconditionFailed);gcs=make_gcs_store(fake)
        with patch.object(self,'store',gcs), patch.object(_FakeBlob,'exists',lambda b:b.name in b.store.objects,create=True):
            first=self.save();self.assertEqual(self.save(),first)
            with self.assertRaises(ImmutableConflict):self.save(dict(self.fixture.m,filtered=True))
        writes=[call for call in fake.calls if call[0]=='upload']
        self.assertTrue(writes)
        self.assertTrue(all(call[2]==0 for call in writes))
        self.assertEqual(len(fake.objects),1)

    def test_framing_truncation_and_path_escape_rejected(self):
        self.save();bundle=self.store.get_blob(capture_key('test-install','test-capture'))
        for data in (bundle[:-1],bundle+b'x',b'BAD!'+bundle[4:]):
            with self.assertRaises(ValueError):unpack(data)
        with self.assertRaises(ValueError):capture_key('../escape','test')

    def test_erasure_scoped_and_retryable(self):
        self.save()
        f=self.fixture
        save_capture(self.store,'test-install-other','test-capture',json.dumps(f.m).encode(),f.raw,f.jpeg)
        self.assertEqual(erase_installation_objects(self.store,'test-install'),1)
        self.assertEqual(erase_installation_objects(self.store,'test-install'),0)
        self.assertIsNotNone(self.store.get_blob(capture_key('test-install-other','test-capture')))

    def test_erasure_rejects_untrusted_listing_before_any_delete(self):
        good=capture_key('test-install','test-capture')
        for bad in ('lite_raw/other/x.rgbd','lite_raw/test-install/../x.rgbd',
                    'lite_raw/test-install/nested/x.rgbd','lite_raw/test-install/x.json',None):
            with self.subTest(key=bad), patch.object(self.store,'list_keys',return_value=[good,bad]), patch.object(self.store,'delete') as delete:
                with self.assertRaises(ValueError):erase_installation_objects(self.store,'test-install')
                delete.assert_not_called()

    def test_erasure_failure_preserves_retry(self):
        self.save()
        with patch.object(self.store,'delete',side_effect=OSError('injected')):
            with self.assertRaises(OSError):erase_installation_objects(self.store,'test-install')
        self.assertEqual(erase_installation_objects(self.store,'test-install'),1)

    def test_erasure_does_not_trust_delete_return_value(self):
        self.save()
        with patch.object(self.store,'delete',return_value=True):
            with self.assertRaises(IOError):erase_installation_objects(self.store,'test-install')

    def test_erasure_detects_a_write_between_snapshots(self):
        with patch.object(self.store,'list_keys',side_effect=[[],[capture_key('test-install','late')]]):
            with self.assertRaises(IOError):erase_installation_objects(self.store,'test-install')

    def test_erasure_listing_or_readback_failure_is_not_success(self):
        with patch.object(self.store,'list_keys',side_effect=OSError('unavailable')):
            with self.assertRaises(OSError):erase_installation_objects(self.store,'test-install')
        self.save()
        with patch.object(self.store,'exists',side_effect=OSError('unavailable')):
            with self.assertRaises(OSError):erase_installation_objects(self.store,'test-install')
        self.assertEqual(erase_installation_objects(self.store,'test-install'),0)

if __name__=='__main__':unittest.main()
