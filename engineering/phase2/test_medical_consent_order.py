"""Synthetic storage/HTTP regressions for delayed and cross-device consent."""
import sys, tempfile, unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'Backend/Flask'))
from store import LocalStore
from medical_consent_order import ConsentOrder, ConsentOrderError, PROTOCOL, ROOT

class Ordering(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=LocalStore(self.tmp.name);self.legacy=[]
        self.order=ConsentOrder(self.store,lambda:list(self.legacy))
    def state(self):return self.order.state('WD-TEST','default')
    def apply(self,action,ident,base=None,order=None):
        return (order or self.order).apply('WD-TEST','default','default:doctor',action,ident,base,['a'*16])
    def test_delayed_restore_cannot_undo_new_withdrawal(self):
        self.apply('withdraw','withdraw-first-001');base=self.state()['revision']
        self.apply('withdraw','withdraw-newer-002')
        with self.assertRaisesRegex(ConsentOrderError,'revision_conflict'):
            self.apply('restore','restore-delayed-1',base)
        self.assertEqual(self.state()['status'],'withdrawn')
    def test_cross_instance_restore_race_with_withdrawal_is_safe(self):
        self.apply('withdraw','withdraw-first-001');base=self.state()['revision']
        second=ConsentOrder(LocalStore(self.tmp.name),lambda:[])
        with ThreadPoolExecutor(2) as pool:
            results=[pool.submit(self.apply,'restore','restore-racing-01',base,second),pool.submit(self.apply,'withdraw','withdraw-racing01')]
            for r in results:
                try:r.result()
                except ConsentOrderError:pass
        self.assertEqual(self.state()['status'],'withdrawn')
    def test_old_withdraw_retry_does_not_undo_later_explicit_restore(self):
        first=self.apply('withdraw','withdraw-first-001')
        result=self.apply('restore','restore-newer-001',first['revision'])
        with self.assertRaisesRegex(ConsentOrderError,'superseded'):
            self.apply('withdraw','withdraw-first-001')
        self.assertEqual(self.state()['revision'],result['revision'])
    def test_response_lost_replay_keeps_sequence_and_receipt(self):
        first=self.apply('withdraw','withdraw-first-001');replay=self.apply('withdraw','withdraw-first-001')
        self.assertEqual(first['revision'],replay['revision']);self.assertTrue(replay['replayed']);self.assertEqual(replay['sequence'],1)
    def test_concurrent_same_intent_has_one_durable_slot(self):
        with ThreadPoolExecutor(8) as pool:
            results=list(pool.map(lambda _: self.apply('withdraw','withdraw-first-001'),range(8)))
        self.assertEqual({r['sequence'] for r in results},{1})
        self.assertEqual(self.state()['sequence'],1)
    def test_intent_id_cannot_change_action_or_base(self):
        receipt=self.apply('withdraw','withdraw-first-001')
        with self.assertRaisesRegex(ConsentOrderError,'intent_reused'):
            self.apply('restore','withdraw-first-001',receipt['revision'])
    def test_legacy_history_and_media_are_preserved(self):
        self.legacy.append(dict(code='WD-TEST',action='withdraw',image_ids=['b'*16]))
        state=self.state();self.assertEqual(state['status'],'withdrawn')
        self.apply('restore','restore-newer-001',state['revision'])
        self.assertEqual(self.legacy[0]['action'],'withdraw')
        self.assertEqual(set(self.state()['image_ids']),{'a'*16,'b'*16})
    def test_late_legacy_writer_fails_closed(self):
        self.apply('withdraw','withdraw-first-001')
        self.legacy.append(dict(code='WD-TEST',action='restore'))
        with self.assertRaisesRegex(ConsentOrderError,'legacy_history_changed'):self.state()
    def test_other_org_cannot_release_state(self):
        self.apply('withdraw','withdraw-first-001')
        with self.assertRaisesRegex(ConsentOrderError,'scope_conflict'):
            self.order.state('WD-TEST','other')
    def test_unversioned_restore_rejected(self):
        with self.assertRaisesRegex(ConsentOrderError,'revision_required'):
            self.apply('restore','restore-newer-001')
        self.assertFalse(self.store.list_keys(ROOT))
    def test_write_failure_never_acknowledged(self):
        with patch.object(self.store,'put_json_immutable',side_effect=IOError('disk full')):
            with self.assertRaises(IOError):self.apply('withdraw','withdraw-first-001')
    def test_gcs_slots_use_protected_create_only_storage(self):
        from store import GcsStore
        from unittest.mock import Mock
        from google.api_core.exceptions import PreconditionFailed
        st=GcsStore.__new__(GcsStore);st.prefix='flywheel'
        st._bucket_name,st._audit_bucket_name='synthetic-main','synthetic-audit'
        st._bucket,st._audit_bucket=Mock(),Mock()
        st._audit_bucket._properties={'retentionPolicy':{'retentionPeriod':'220903200','isLocked':True}}
        blob=st._audit_bucket.blob.return_value
        blob.download_as_bytes.return_value=b'{"synthetic":true}'
        for key in (ROOT+'/synthetic.json',self.order._slot('WD-TEST',1)):
            with self.subTest(key=key):
                blob.upload_from_string.side_effect=None
                self.assertTrue(st.put_json_immutable(key,{'synthetic':True}))
                self.assertEqual(blob.upload_from_string.call_args.kwargs['if_generation_match'],0)
                self.assertIs(st._target(key)[0],st._audit_bucket)
                blob.upload_from_string.side_effect=PreconditionFailed('synthetic conflict')
                self.assertFalse(st.put_json_immutable(key,{'synthetic':True}))
                with self.assertRaises(PermissionError):st.delete(key)
    def test_missing_sequence_slot_fails_closed(self):
        self.apply('withdraw','withdraw-first-001');self.apply('withdraw','withdraw-newer-002')
        (Path(self.tmp.name)/self.order._slot('WD-TEST',1)).unlink()
        with self.assertRaisesRegex(ConsentOrderError,'history_gap'):self.state()
    def test_corrupt_root_action_does_not_grant_consent(self):
        import json
        self.apply('withdraw','withdraw-first-001')
        path=next((Path(self.tmp.name)/ROOT).glob('*.json'))
        root=json.loads(path.read_text());root['action']='unknown';path.write_text(json.dumps(root))
        with self.assertRaises(ConsentOrderError):self.state()
    def test_corrupt_slot_fails_closed(self):
        self.apply('withdraw','withdraw-first-001')
        path=Path(self.tmp.name)/self.order._slot('WD-TEST',1)
        path.write_text('{"protocol":"wrong"}')
        with self.assertRaisesRegex(ConsentOrderError,'history_invalid'):self.state()

import test_research_format_guards as fixtures
import api_flywheel as fw
class ConsentHTTP(unittest.TestCase):
    setUp=fixtures.TissueHTTP.setUp
    tearDown=fixtures.TissueHTTP.tearDown
    def post(self,action,**fields):
        return self.client.post('/api/v1/consent/'+action,headers=self.headers['dr1'],json=dict(code='WD-SYNTH',**fields))
    def test_late_restore_is_rejected_and_assets_unchanged(self):
        first=self.post('withdraw',intent_id='withdraw-first-001');self.assertEqual(first.status_code,200,first.json)
        newer=self.post('withdraw',intent_id='withdraw-newer-001');self.assertEqual(newer.status_code,200,newer.json)
        old=self.post('restore',protocol=PROTOCOL,intent_id='restore-old-0001',consent_revision=first.json['revision'])
        self.assertEqual(old.status_code,409,old.json);self.assertIn('WD-SYNTH',fw.withdrawn_keys()[0])
        restored=self.post('restore',protocol=PROTOCOL,intent_id='restore-new-0001',consent_revision=newer.json['revision'])
        self.assertEqual(restored.status_code,200,restored.json);self.assertNotIn('WD-SYNTH',fw.withdrawn_keys()[0])
    def test_legacy_withdraw_accepted_legacy_restore_denied(self):
        self.assertEqual(self.post('withdraw').status_code,200)
        self.assertEqual(self.post('restore').status_code,428)
        self.assertIn('WD-SYNTH',fw.withdrawn_keys()[0])
    def test_legacy_other_subject_withdrawal_is_not_released_by_shared_image(self):
        fw.append_jsonl(fw.WITHDRAWN,dict(code='WD-OTHER',action='withdraw',image_ids=['a'*16]))
        first=self.post('withdraw',image_id='a'*16).json
        restored=self.post('restore',protocol=PROTOCOL,intent_id='restore-new-0001',consent_revision=first['revision'])
        self.assertEqual(restored.status_code,200,restored.json)
        self.assertIn('a'*16,fw.withdrawn_keys()[1])

    def test_authenticated_state_receipt(self):
        self.assertEqual(self.client.get('/api/v1/consent/state?code=WD-SYNTH').status_code,401)
        response=self.client.get('/api/v1/consent/state?code=WD-SYNTH',headers=self.headers['dr1'])
        self.assertEqual(response.status_code,200,response.json);self.assertEqual(response.json['protocol'],PROTOCOL)
    def test_unsealed_subject_state_does_not_leak_other_org_images(self):
        fw.append_jsonl(fw.QUEUE,dict(code='WD-SYNTH',org='other',image_id='a'*16))
        fw.append_jsonl(fw.WITHDRAWN,dict(code='WD-SYNTH',action='withdraw',image_ids=['a'*16]))
        response=self.client.get('/api/v1/consent/state?code=WD-SYNTH',headers=self.headers['dr1'])
        self.assertEqual(response.status_code,409,response.json)
        self.assertNotIn('image_ids',response.json)
    def test_custom_legacy_file_cannot_bypass_live_ordered_ledger(self):
        self.assertEqual(self.post('withdraw').status_code,200)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'withdrawn.jsonl';path.write_text('')
            with self.assertRaisesRegex(ValueError,'cannot bypass ordered consent'):
                fw.withdrawn_keys(str(path))
    def test_custom_local_source_with_receipts_requires_full_store_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            local=LocalStore(tmp);order=ConsentOrder(local,lambda:[])
            order.apply('WD-OTHER','default','default:doctor','withdraw','withdraw-offline01',None,[])
            path=Path(tmp)/'withdrawn.jsonl';path.write_text('')
            with self.assertRaisesRegex(ValueError,'cannot bypass ordered consent'):
                fw.withdrawn_keys(str(path))
