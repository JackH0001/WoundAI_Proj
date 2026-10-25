"""Positive empty-catalogue B1 behavior using real signatures and both Flask hops.

Source is a tripwire: any source read/list/write fails this acceptance. No cloud
credentials; these tests do not claim actual IAP/browser or GCS IAM acceptance.
"""
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from Backend.LiteReview.auth import IapVerifier
from Backend.LiteReview.contracts import Catalogue, IAP_AUDIENCE, ReviewError, require
from Backend.LiteReview.reader import Reader
from Backend.LiteReview.storage import AccessAudit
from Backend.LiteReview.web import portal_app, reader_app
from test_lite_fenced_objects import Bucket

SUBJECT = 'accounts.google.com:123456'


class EmptyHumanEntry(unittest.TestCase):
    def setUp(self):
        from Backend.LiteReview.contracts import AUDIT
        self.key = ec.generate_private_key(ec.SECP256R1())
        public = self.key.public_key().public_bytes(serialization.Encoding.PEM,
                                                   serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        session = Mock()
        session.get.return_value.status_code = 200
        session.get.return_value.json.return_value = {'test': public}
        self.verify = IapVerifier(session, {SUBJECT: ['lite.review.metadata']})
        self.source = Mock()
        for method in ('state', 'read', 'check'):
            getattr(self.source, method).side_effect = AssertionError('no source access in B1')
        self.bucket = Bucket(); self.bucket.name = AUDIT
        catalogue = Catalogue(dict(schema='lite-review-catalogue/1', captured_at=int(time.time()), records=[]))
        self.reader = Reader(catalogue, self.source, AccessAudit(self.bucket), b'k'*32)
        self.reader_client = reader_app(self.reader, self.verify,
            lambda token: require(token == 'Bearer synthetic-service', 'unauthorized', 401)).test_client()
        self.hops = []
        def hop(path, params, iap, image):
            self.hops.append(path)
            result = self.reader_client.get(path, query_string=params,
                headers={'Authorization': 'Bearer synthetic-service', 'X-Lite-Review-IAP': iap})
            if result.status_code != 200:
                raise ReviewError(result.json['error'], result.status_code)
            return result.json
        self.portal = portal_app(self.verify, hop).test_client()

    def token(self, subject=SUBJECT):
        return jwt.encode(dict(iss='https://cloud.google.com/iap', aud=IAP_AUDIENCE,
            sub=subject, iat=int(time.time())-1, exp=int(time.time())+300), self.key,
            algorithm='ES256', headers={'kid': 'test'})

    def get(self, path='/'):
        return self.portal.get(path, headers={'X-Goog-IAP-JWT-Assertion': self.token()})

    def test_empty_page_passes_both_hops_and_audits_without_source_calls(self):
        import json
        result = self.get()
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.hops, ['/v1/records'])
        self.assertEqual(self.source.mock_calls, [])
        self.assertEqual(len(self.bucket.live), 1)
        receipt = json.loads(next(iter(self.bucket.live.values()))[1])
        self.assertEqual(receipt['actor'], SUBJECT)
        self.assertEqual(receipt['action'], 'list')
        self.assertEqual(receipt['records'], [])
        self.assertIn('no-store', result.headers['Cache-Control'])

    def test_audit_write_failure_denies_even_an_empty_page(self):
        self.bucket.upload_error = RuntimeError('no audit permission')
        result = self.get()
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.json['error'], 'review_audit_unavailable')
        self.assertEqual(self.source.mock_calls, [])

    def test_metadata_only_owner_cannot_preview_even_guessed_record(self):
        result = self.get('/records/' + 'a'*64 + '/image')
        self.assertEqual(result.status_code, 403)
        self.assertEqual(self.hops, [])
        self.assertEqual(self.source.mock_calls, [])
        self.assertEqual(self.bucket.live, {})

    def test_missing_wrong_and_unsigned_human_never_invoke_reader(self):
        for headers in ({}, {'X-Goog-IAP-JWT-Assertion': self.token('accounts.google.com:999')},
                        {'X-Goog-Authenticated-User-Email': 'jack.hou@gmail.com'}):
            with self.subTest(headers=list(headers)):
                self.assertEqual(self.portal.get('/', headers=headers).status_code, 401)
        self.assertEqual(self.hops, [])
        self.assertEqual(self.source.mock_calls, [])
        self.assertEqual(self.bucket.live, {})

    def test_empty_catalogue_cannot_be_bypassed_by_guessed_detail_or_filter(self):
        self.assertEqual(self.get('/records/' + 'a'*64).status_code, 404)
        self.assertEqual(self.get('/?bucket=mmh').status_code, 400)
        self.assertEqual(self.source.mock_calls, [])
        self.assertEqual(self.bucket.live, {})

    def test_reader_rechecks_human_even_with_valid_service(self):
        result = self.reader_client.get('/v1/records', headers={
            'Authorization': 'Bearer synthetic-service', 'X-Lite-Review-IAP': self.token('accounts.google.com:999')})
        self.assertEqual(result.status_code, 401)
        self.assertEqual(self.bucket.live, {})
        self.assertEqual(self.source.mock_calls, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
