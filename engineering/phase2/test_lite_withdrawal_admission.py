"""Real Flask/LocalStore revocation tests; not distributed transaction coverage."""
import unittest
from unittest.mock import patch
import test_lite_quota as fixture

class WithdrawalAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.LiteQuotaTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.aid = 'test-installation'

    def withdraw(self):
        return self.f.client.delete('/api/v1/lite/data/' + self.aid)

    def test_old_upload_cannot_recreate_deleted_images(self):
        image_id = self.f.post().json['image_id']
        self.assertEqual(self.withdraw().status_code, 200)
        self.assertEqual(self.f.post().status_code, 410)
        self.assertFalse(self.f.store.exists('lite/' + self.aid + '/' + image_id + '.jpg'))

    def test_no_annotations_still_revokes(self):
        self.assertEqual(self.withdraw().status_code, 200)
        self.assertIn('"deleted"', self.f.store.get_blob('lite_labels.jsonl').decode())
        self.assertEqual(self.f.post().status_code, 410)

    def test_incomplete_delete_still_blocks(self):
        self.f.post()
        with patch.object(self.f.store, 'delete', side_effect=OSError('injected')):
            self.assertEqual(self.withdraw().status_code, 503)
        self.assertEqual(self.f.post().status_code, 410)

    def test_unreadable_ledger_blocks_before_inference(self):
        with patch.object(self.f.store, 'read_lines_fresh', side_effect=OSError('injected')), \
             patch.object(fixture.lite, '_SEGMENT') as model:
            self.assertEqual(self.f.post().status_code, 503)
        model.assert_not_called()

    def test_malformed_ledger_blocks_before_inference(self):
        self.f.store.put_blob('lite_labels.jsonl', b'not json\n')
        with patch.object(fixture.lite, '_SEGMENT') as model:
            self.assertEqual(self.f.post().status_code, 503)
        model.assert_not_called()

    def test_withdrawal_during_inference_blocks_blob_writes(self):
        def segment(rgb):
            self.assertEqual(self.withdraw().status_code, 200)
            return self.f.segment(rgb)
        with patch.object(fixture.lite, '_SEGMENT', side_effect=segment), \
             patch.object(self.f.store, 'put_blob', wraps=self.f.store.put_blob) as put:
            self.assertEqual(self.f.post().status_code, 410)
        self.assertFalse(any(str(c.args[0]).endswith('.jpg') for c in put.call_args_list))

    def test_legacy_annotation_denied_after_withdrawal(self):
        image_id=self.f.post().json['image_id']
        self.withdraw()
        r=self.f.client.post('/api/v1/lite/annotation', json={
            'anon_id':self.aid,'image_id':image_id,'research_consent':True,
            'image_w':64,'image_h':64,'polygons':[[[10,10],[39,10],[39,39]]],
        })
        self.assertEqual(r.status_code, 410)

    def test_marker_failure_never_reports_success(self):
        with patch.object(fixture.fw, 'append_jsonl', side_effect=OSError('injected')):
            r=self.withdraw()
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json['error'], 'withdrawal_unconfirmed')

    def test_unindexed_partial_upload_is_also_erased(self):
        # A media write can succeed before its index append fails.
        key = 'lite/' + self.aid + '/0123456789abcdef.jpg'
        self.f.store.put_blob(key, self.f.jpeg)
        r = self.withdraw()
        self.assertEqual(r.status_code, 200)
        self.assertFalse(self.f.store.exists(key))
        self.assertEqual(r.json['objects_removed'], 1)

    def test_actual_failed_index_append_leaves_no_orphan_after_withdrawal(self):
        original = fixture.fw.append_jsonl
        def append(path, row):
            if str(path).endswith('lite_index.jsonl'):
                raise OSError('injected index append failure')
            return original(path, row)
        with patch.object(fixture.fw, 'append_jsonl', side_effect=append):
            self.assertEqual(self.f.post().status_code, 500)
        self.assertEqual(len(self.f.store.list_keys('lite/' + self.aid)), 2)
        r = self.withdraw()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json['objects_removed'], 2)
        self.assertEqual(self.f.store.list_keys('lite/' + self.aid), [])

    def test_completion_marker_failure_is_retryable_and_never_reports_success(self):
        self.f.post()
        original = fixture.fw.append_jsonl
        def append(path, row):
            if row.get('action') == 'deleted':
                raise OSError('injected completion marker failure')
            return original(path, row)
        with patch.object(fixture.fw, 'append_jsonl', side_effect=append):
            self.assertEqual(self.withdraw().status_code, 503)
        self.assertEqual(self.f.post().status_code, 410)
        self.assertEqual(self.withdraw().status_code, 200)

    def test_inventory_is_independent_of_cached_index(self):
        self.f.post()
        with patch.object(fixture.fw, 'read_jsonl', return_value=[]):
            r = self.withdraw()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json['objects_removed'], 2)
        self.assertEqual(r.json['records'], 1)

    def test_silent_delete_failure_cannot_report_success(self):
        image_id = self.f.post().json['image_id']
        with patch.object(self.f.store, 'delete', return_value=False):
            self.assertEqual(self.withdraw().status_code, 503)
        self.assertTrue(self.f.store.exists('lite/' + self.aid + '/' + image_id + '.jpg'))
        self.assertEqual(self.f.post().status_code, 410)

    def test_listing_failure_still_revokes_and_can_be_retried(self):
        image_id = self.f.post().json['image_id']
        with patch.object(self.f.store, 'list_keys', side_effect=OSError('injected listing failure')):
            self.assertEqual(self.withdraw().status_code, 503)
        self.assertEqual(self.f.post().status_code, 410)
        self.assertEqual(self.withdraw().status_code, 200)
        self.assertFalse(self.f.store.exists('lite/' + self.aid + '/' + image_id + '.jpg'))

    def test_untrusted_listing_cannot_delete_another_installation(self):
        own = 'lite/' + self.aid + '/0123456789abcdef.jpg'
        other = 'lite/' + self.aid + '-other/0123456789abcdef.jpg'
        for key in (own, other):
            self.f.store.put_blob(key, self.f.jpeg)
        with patch.object(self.f.store, 'list_keys', return_value=[own, other]), \
                patch.object(self.f.store, 'delete', wraps=self.f.store.delete) as delete:
            self.assertEqual(self.withdraw().status_code, 503)
        delete.assert_not_called()
        self.assertTrue(self.f.store.exists(other))

    def test_new_object_during_cleanup_cannot_report_complete(self):
        self.f.post()
        original = self.f.store.list_keys
        calls = []
        def listing(prefix):
            calls.append(prefix)
            if len(calls) == 2:
                self.f.store.put_blob('lite/' + self.aid + '/fedcba9876543210.jpg', self.f.jpeg)
            return original(prefix)
        with patch.object(self.f.store, 'list_keys', side_effect=listing):
            self.assertEqual(self.withdraw().status_code, 503)
        self.assertEqual(self.withdraw().status_code, 200)

    def test_unknown_object_stops_before_any_deletion(self):
        own = 'lite/' + self.aid + '/0123456789abcdef.jpg'
        self.f.store.put_blob(own, self.f.jpeg)
        self.f.store.put_blob('lite/' + self.aid + '/unknown.bin', b'unknown schema')
        with patch.object(self.f.store, 'delete', wraps=self.f.store.delete) as delete:
            self.assertEqual(self.withdraw().status_code, 503)
        delete.assert_not_called()

if __name__=='__main__': unittest.main()
