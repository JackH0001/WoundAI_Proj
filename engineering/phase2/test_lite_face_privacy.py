"""Fail-closed face guard; synthetic images and local signed-request fixtures only.

This checks availability/admission, not face recall, deidentification or Apple/GCS.
"""
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch, mock_open

import cv2
import numpy as np
import test_lite_raw_depth_http as raw_fixture
import test_lite_service_profile as profile_fixture
import api_lite as lite
from lite_service_profile import service_profile


class DetectorTests(unittest.TestCase):
    def setUp(self):
        self.image = np.zeros((128, 128, 3), np.uint8)

    def test_installed_cascade_runs_on_synthetic_blank(self):
        self.assertEqual(lite._has_face(self.image), (False, '0'))

    def test_missing_cascade_is_unknown(self):
        with patch.object(lite.os.path, 'isfile', return_value=False):
            self.assertEqual(lite._has_face(self.image), (None, 'cascade_missing'))

    def test_classifier_digest_mismatch_is_unknown_before_native_load(self):
        with patch("builtins.open", mock_open(read_data=b"corrupt")), patch.object(cv2, "CascadeClassifier") as classifier:
            self.assertEqual(lite._has_face(self.image), (None, "cascade_digest_mismatch"))
            classifier.assert_not_called()

    def test_empty_classifier_is_unknown(self):
        classifier = Mock(); classifier.empty.return_value = True
        with patch.object(cv2, 'CascadeClassifier', return_value=classifier):
            self.assertEqual(lite._has_face(self.image), (None, 'cascade_empty'))
        classifier.detectMultiScale.assert_not_called()

    def test_detector_errors_are_unknown_without_exception_text(self):
        classifier = Mock(); classifier.empty.return_value = False
        classifier.detectMultiScale.side_effect = RuntimeError('private/internal/path')
        with patch.object(cv2, 'CascadeClassifier', return_value=classifier):
            self.assertEqual(lite._has_face(self.image), (None, 'detector_error'))

    def test_positive_detection_is_distinct_from_no_faces(self):
        classifier = Mock(); classifier.empty.return_value = False
        with patch.object(cv2, 'CascadeClassifier', return_value=classifier):
            for faces, expected in [([], (False, '0')), ([(0, 0, 80, 80)], (True, '1'))]:
                classifier.detectMultiScale.return_value = faces
                self.assertEqual(lite._has_face(self.image), expected)


class SignedPrivacyTests(unittest.TestCase):
    def setUp(self):
        self.f = raw_fixture.RawDepthHTTPTests(); self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        guard = patch.object(lite, 'FACE_REJECT', True)
        guard.start(); self.addCleanup(guard.stop)

    def assert_no_research_media(self):
        store = self.f.h.f.store
        self.assertEqual(list(store.list_keys('lite/')), [])
        self.assertEqual(list(store.list_keys('lite_raw/')), [])
        self.assertEqual(lite._lite_ledger_rows_fresh('lite_index.jsonl'), [])
        self.assertEqual(lite._lite_label_rows_fresh(), [])
        # The bounded attempt ledger and attestation counter may still advance.
        self.assertEqual(lite.quota_snapshot(self.f.h.owner, '127.0.0.1')[0]['used'], 0)

    def test_missing_detector_blocks_signed_rgbd_before_inference_or_storage(self):
        actual_isfile = os.path.isfile
        def missing_cascade(path):
            return False if str(path).endswith('haarcascade_frontalface_default.xml') else actual_isfile(path)
        with patch.object(lite.os.path, 'isfile', side_effect=missing_cascade), patch.object(lite, '_SEGMENT') as model:
            response = self.f.upload()
            self.assertEqual(response.status_code, 503, response.json)
            self.assertEqual(response.json['error'], 'privacy_check_unavailable')
            self.assertNotIn('detail', response.json)
            model.assert_not_called()
        self.assert_no_research_media()
        # A failed privacy check must release its writer so withdrawal can finish.
        self.assertEqual(self.f.delete().status_code, 200)

    def test_detector_runtime_failure_blocks_signed_upload(self):
        with patch.object(cv2, 'CascadeClassifier', side_effect=RuntimeError('private/internal/path')), patch.object(lite, '_SEGMENT') as model:
            response = self.f.upload()
            self.assertEqual(response.status_code, 503, response.json)
            self.assertNotIn('private/internal/path', response.get_data(as_text=True))
            model.assert_not_called()
        self.assert_no_research_media()

    def test_detected_face_blocks_signed_upload_without_saving_rgbd(self):
        with patch.object(lite, '_has_face', return_value=(True, '1')), patch.object(lite, '_SEGMENT') as model:
            response = self.f.upload()
            self.assertEqual(response.status_code, 400, response.json)
            self.assertEqual(response.json['error'], 'face_detected')
            model.assert_not_called()
        self.assert_no_research_media()

    def test_unrecognised_detector_result_is_not_clean_evidence(self):
        with patch.object(lite, '_has_face', return_value=('unknown', 'bad')), patch.object(lite, '_SEGMENT') as model:
            response = self.f.upload()
            self.assertEqual(response.status_code, 503, response.json)
            model.assert_not_called()
        self.assert_no_research_media()

    def test_available_detector_admits_synthetic_rgbd_and_withdraws(self):
        # Do not mock the detector on the success path.
        response = self.f.upload()
        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json['stored'])
        self.assertTrue(list(self.f.h.f.store.list_keys('lite_raw/')))
        self.assertEqual(self.f.delete().status_code, 200)
        self.assertEqual(list(self.f.h.f.store.list_keys('lite_raw/' + self.f.h.owner + '/')), [])


class ProfilePrivacyTests(unittest.TestCase):
    def test_public_profile_accepts_only_default_or_explicit_enabled(self):
        env = profile_fixture.lite_environment('/tmp/lite-synthetic-runtime')
        self.assertEqual(service_profile(env), 'lite')
        self.assertEqual(service_profile(dict(env, LITE_FACE_REJECT='1')), 'lite')
        for value in ('0', 'false', 'False', '', 'true', '1 ', None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                service_profile(dict(env, LITE_FACE_REJECT=value))

    def test_medical_profile_compatibility_is_preserved(self):
        self.assertEqual(service_profile({'LITE_FACE_REJECT': '0'}), 'medical')


if __name__ == '__main__':
    unittest.main()
