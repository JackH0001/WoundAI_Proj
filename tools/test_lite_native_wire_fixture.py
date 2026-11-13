"""Safety boundary of the opt-in loopback fixture (no cloud or Xcode needed)."""
import json
import unittest
from werkzeug.test import Client
from werkzeug.wrappers import Response
from lite_native_wire_fixture import WireFixture


class NativeWireFixtureTests(unittest.TestCase):
    def setUp(self):
        self.fixture = WireFixture(); self.fixture.start()
        self.addCleanup(self.fixture.close)
        self.client = Client(self.fixture, Response)

    def post(self, path, raw=b'{}', remote='127.0.0.1', token=True):
        headers = {'X-Synthetic-Wire':self.fixture.token} if token else {}
        return self.client.post(path, data=raw, content_type='application/json', headers=headers,
                                environ_overrides={'REMOTE_ADDR':remote})

    def test_remote_or_missing_bearer_cannot_create_fixture_state(self):
        self.assertEqual(self.post('/fixture/reset', remote='192.0.2.1').status_code, 403)
        self.assertEqual(self.post('/fixture/reset', token=False).status_code, 403)
        self.assertIsNone(self.fixture.h)

    def test_bad_method_or_oversized_payload_rejected_before_state(self):
        self.assertEqual(self.client.get('/fixture/reset', headers={'X-Synthetic-Wire':self.fixture.token},
                         environ_overrides={'REMOTE_ADDR':'127.0.0.1'}).status_code, 400)
        self.assertEqual(self.post('/fixture/reset', b'x' * (2 * 1024 * 1024 + 1)).status_code, 400)
        self.assertIsNone(self.fixture.h)

    def test_reset_is_synthetic_and_snapshot_excludes_keys_and_images(self):
        seed = self.post('/fixture/reset'); self.assertEqual(seed.status_code, 200)
        snapshot = self.post('/fixture/snapshot')
        self.assertEqual(snapshot.status_code, 200)
        value = snapshot.json
        self.assertEqual((value['model_calls'], value['used'], value['attempts']), (0, 0, 0))
        self.assertTrue(value['research_unchanged']); self.assertTrue(value['image_unchanged'])
        for forbidden in ('jpeg', 'private', 'refresh_token', 'authorizationCode', self.fixture.token):
            self.assertNotIn(forbidden, snapshot.get_data(as_text=True))
        old = self.fixture.h.h.clock()
        invalid = self.post('/fixture/advance', json.dumps({'seconds':1801}).encode())
        self.assertEqual(invalid.status_code, 500)
        self.assertEqual(self.fixture.h.h.clock(), old)


if __name__ == '__main__': unittest.main()
