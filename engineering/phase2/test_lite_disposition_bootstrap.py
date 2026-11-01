"""Use the actual Google/requests stack with synthetic HTTP responses only."""
import json
import os
from unittest import TestCase
from unittest.mock import patch

from requests.adapters import HTTPAdapter
from urllib3.response import HTTPResponse
from lite_attest_state import StateUnavailable
import lite_disposition_bootstrap as boot
from lite_disposition_startup import CloudProviders
from lite_provider_budget import BudgetUnavailable
from lite_provider_transport import BudgetedSession, METADATA_ACCOUNT
from test_lite_provider_transport import Adapter, reply


EMAIL = 'worker@synthetic-project.iam.gserviceaccount.com'
TOKEN = dict(token_type='Bearer', access_token='test.only', expires_in=3600)


def metadata(data):
    return reply(data, headers={'Metadata-Flavor': 'Google'})


class BootstrapTests(TestCase):
    def setUp(self):
        self.wall = 1000
        self.mono = 50
        self.config = dict(bootstrap_budget=dict(requests=8, send_bytes=65536, receive_bytes=1024**2, seconds=20),
                           provider_budget=dict(not_before=1000, expires_at=1300, seconds=60),
                           deployment=dict(service_account=EMAIL, project='synthetic-project'))
        self.adapter = Adapter([])
        self.enterContext(patch.object(HTTPAdapter, 'send', self.adapter.send))
        self.enterContext(patch.dict(os.environ, DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA='true'))

    def acquire(self, *responses):
        self.adapter.responses = list(responses)
        return boot.acquire(self.config, clock=lambda: self.wall, monotonic=lambda: self.mono)

    def good(self):
        return self.acquire(metadata(EMAIL.encode()), metadata(TOKEN))

    def test_attached_email_then_token_no_proxy_or_credentials_on_metadata(self):
        credentials, meter = self.good()
        self.assertEqual(credentials.service_account_email, EMAIL)
        self.assertEqual([q.url for q, _ in self.adapter.calls],
                         [METADATA_ACCOUNT + name for name in ('email', 'token')])
        for req, kw in self.adapter.calls:
            self.assertEqual(req.headers['Metadata-Flavor'], 'Google')
            self.assertNotIn('Authorization', req.headers)
            self.assertFalse(kw['proxies'])
        self.assertEqual(meter.snapshot()['providers']['metadata'], 2)
        self.assertNotIn(TOKEN['access_token'], json.dumps(meter.snapshot()))

    def test_wrong_identity_stops_before_token_or_storage(self):
        with self.assertRaisesRegex(BudgetUnavailable, 'identity differs'):
            self.acquire(metadata(b'other@example.invalid'), metadata(TOKEN))
        self.assertEqual(len(self.adapter.calls), 1)

    def test_without_metadata_response_header_stop_before_token(self):
        with self.assertRaises(BudgetUnavailable):
            self.acquire(reply(EMAIL.encode()), metadata(TOKEN))
        self.assertEqual(len(self.adapter.calls), 1)

    def test_one_network_timeout_never_replayed_or_reported_with_token(self):
        with self.assertRaises(BudgetUnavailable) as error:
            self.acquire(metadata(EMAIL.encode()), TimeoutError('secret-example'), metadata(TOKEN))
        self.assertEqual(len(self.adapter.calls), 2)
        self.assertNotIn('secret-example', str(error.exception))

    def test_short_token_denied_even_though_currently_valid(self):
        with self.assertRaises(BudgetUnavailable):
            self.acquire(metadata(EMAIL.encode()), metadata(dict(TOKEN, expires_in=75)))

    def test_redirect_body_is_not_downloaded_to_prepare_a_next_request(self):
        redirect = reply(b'do not read', 302, {'Location': 'https://example.invalid/other'})
        with patch.object(HTTPResponse, 'read', side_effect=AssertionError('unexpected read')) as read:
            with self.assertRaises(BudgetUnavailable):
                self.acquire(redirect)
        read.assert_not_called()
        self.assertEqual(len(self.adapter.calls), 1)

    def test_malformed_tokens_fail_closed(self):
        for bad in [dict(TOKEN, token_type='Basic'), dict(TOKEN, expires_in=True), dict(TOKEN, extra=1),
                    dict(TOKEN, access_token='line\nbreak'), dict(TOKEN, access_token=''),
                    b'{"expires_in":300,"expires_in":3600,"token_type":"Bearer","access_token":"test.only"}']:
            with self.subTest(bad=type(bad)), self.assertRaises(BudgetUnavailable):
                self.acquire(metadata(EMAIL.encode()), metadata(bad))

    def test_oversize_truncation_and_redirect_never_reach_credential_holder(self):
        for bad in [(200, b'', {'Content-Length': '16385', 'Metadata-Flavor': 'Google'}),
                    (200, b'x', {'Content-Length': '12', 'Metadata-Flavor': 'Google'}),
                    reply(b'x', 302, {'Location': 'https://example.invalid/token'})]:
            self.adapter.calls.clear()
            with self.assertRaises(BudgetUnavailable):
                self.acquire(metadata(EMAIL.encode()), bad)
            self.assertEqual(len(self.adapter.calls), 2)

    def test_expired_window_and_unbounded_startup_limit_make_zero_requests(self):
        self.wall = 1300
        with self.assertRaises(BudgetUnavailable): self.good()
        self.wall = 1000
        for field, value in [('requests', True), ('requests', 129), ('send_bytes', 0),
                             ('receive_bytes', 9 * 1024**2), ('seconds', 121)]:
            with self.subTest(field=field):
                with patch.dict(self.config['bootstrap_budget'], {field: value}), self.assertRaises(ValueError):
                    self.good()
        self.assertFalse(self.adapter.calls)

    def test_401_neither_refreshes_nor_replays(self):
        credentials, meter = self.good()
        self.adapter.responses = [reply({}, 401)]
        session = boot.google_session(credentials)
        self.addCleanup(session.close)
        self.assertIsNone(session._auth_request_session)
        transport = BudgetedSession(session, meter, provider='gcs', authorized=True)
        self.assertEqual(transport.request('GET','https://storage.googleapis.com/test').status_code, 401)
        self.assertEqual(len(self.adapter.calls), 3)
        self.assertEqual(self.adapter.calls[-1][0].headers['Authorization'], 'Bearer test.only')

    def test_expired_credential_not_refreshed_even_if_wall_clock_stalls(self):
        credentials, _ = self.good()
        self.mono += 3600
        with self.assertRaises(BudgetUnavailable):
            credentials.before_request(None, 'GET', '', {})
        with self.assertRaises(BudgetUnavailable):
            credentials.refresh(None)
        self.assertEqual(len(self.adapter.calls), 2)

    def test_metadata_transport_refuses_nonliteral_paths_and_options(self):
        _, meter = self.good()
        transport = BudgetedSession(boot.PlainProviderSession(), meter, provider='metadata', response_limit=4096)
        self.addCleanup(transport.close)
        for method, url, kw in [('POST', METADATA_ACCOUNT+'token', {}),
                ('GET', METADATA_ACCOUNT+'token?scopes=x', {}), ('GET', METADATA_ACCOUNT+'token', {'params': {}}),
                ('GET', METADATA_ACCOUNT+'other', {}), ('GET', METADATA_ACCOUNT+'email', {'headers': {}})]:
            with self.assertRaises(BudgetUnavailable):
                transport.request(method, url, **kw)
        self.assertEqual(len(self.adapter.calls), 2)

    def test_bucket_and_journal_share_one_bootstrap_budget(self):
        self.config['bootstrap_budget']['requests'] = 4
        self.adapter.responses = [metadata(EMAIL.encode()), metadata(TOKEN), reply({'kind':'storage#bucket'}),
                                  reply({}, 404)]
        p = CloudProviders()
        p.connect(self.config, clock=lambda: self.wall)
        self.addCleanup(p.end_bootstrap)
        p.identity_checked(self.config)
        bucket = p.bucket('synthetic-bucket')
        bucket.reload(retry=None)
        store = p.state(bucket, 'lite-disposition/test/budget')
        self.assertIsNone(store.load(b'a'*32))
        with self.assertRaises(StateUnavailable): store.load(b'b'*32)
        self.assertEqual(len(self.adapter.calls), 4)
        self.assertEqual(p.bootstrap_allocation.snapshot()['providers'],dict(gcs=2, metadata=2, kms=0, apple=0))
        self.assertIsNone(p.allocation)
        self.assertTrue(all('/test/budget' in req.url or 'synthetic-bucket' in req.url
                            for req, _ in self.adapter.calls[2:]))

    def test_closed_control_cannot_be_reused_to_get_a_new_budget(self):
        self.adapter.responses = [metadata(EMAIL.encode()), metadata(TOKEN)]
        p = CloudProviders()
        p.connect(self.config, clock=lambda: self.wall)
        p.end_bootstrap()
        with self.assertRaises(BudgetUnavailable):
            p.control.request('GET','https://storage.googleapis.com/b')
        with self.assertRaises(StateUnavailable):
            p.connect(self.config, clock=lambda: self.wall)
        self.assertEqual(len(self.adapter.calls), 2)
