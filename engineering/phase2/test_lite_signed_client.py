"""Signed synthetic client shared by the research and isolated native HTTP tests."""
import base64
import hashlib
import io
import json

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from werkzeug.test import EnvironBuilder

from lite_attest_assertion import CATEGORY, VERSION
from lite_attest_http import CHALLENGE_PATH, REGISTER_PATH
from lite_attest_request import client_data
from lite_service_http import PREFIX


def b64(value):
    return base64.b64encode(value).decode('ascii')


class SignedHTTPClient:
    def register(self):
        challenge = self.client.post(CHALLENGE_PATH, json=dict(key_id=self.key, purpose='register'))
        self.assertEqual(challenge.status_code, 200, challenge.json)
        self.assertEqual(challenge.json['challenge'], b64(self.e.challenge.nonce))
        result = self.client.post(REGISTER_PATH, json=dict(
            key_id=self.key, challenge_id=challenge.json['challenge_id'], attestation=b64(self.e.encoded)))
        self.assertEqual(result.status_code, 200, result.json)
        self.owner = result.json['installation']
        return result

    def headers(self, path, raw, content_type, method='POST', purpose='assert'):
        response = self.client.post(CHALLENGE_PATH, json=dict(key_id=self.key, purpose=purpose))
        self.assertEqual(response.status_code, 200, response.json)
        challenge = response.json
        audience = getattr(self, 'audience', 'lite-test')
        self.assertEqual(challenge['audience'], audience)
        self.counter += 1
        rid = '11111111-1111-4111-8111-111111111111'
        data = client_data(
            audience=audience, installation=self.owner, key_id=self.e.key_id,
            challenge=base64.b64decode(challenge['challenge']), request_id=rid,
            method=method, path=path, content_type=content_type, body=raw)
        auth = hashlib.sha256(self.e.r.policy.app_id.encode()).digest() + b'\x80' + self.counter.to_bytes(4, 'big')
        auth += cbor2.dumps({CATEGORY: 2, VERSION: '33'})
        signature = self.e.r.key.sign(hashlib.sha256(auth + hashlib.sha256(data).digest()).digest(),
                                      ec.ECDSA(hashes.SHA256()))
        assertion = cbor2.dumps(dict(signature=signature, authenticatorData=auth))
        return {'X-Lite-Key-ID': self.key, 'X-Lite-Assertion': b64(assertion),
                'X-Lite-Challenge-ID': challenge['challenge_id'], 'X-Lite-Request-ID': rid}

    def post_signed(self, path, raw, content_type='application/json'):
        headers = self.headers(path, raw, content_type)
        return self.client.post(path, data=raw, content_type=content_type, headers=headers)


class SignedServiceClient(SignedHTTPClient):
    def json_request(self, kind, n=1, **extra):
        body = dict(anon_id=self.owner, operation=f'{n:032x}')
        if kind == 'prepare':
            body['image_sha256'] = self.image_hash
        body.update(extra)
        return self.post_signed(PREFIX + kind, json.dumps(body).encode())

    def run_request(self, n=1, raw=None, **fields):
        payload = self.f.jpeg if raw is None else raw
        builder = EnvironBuilder(method='POST', data=dict(
                anon_id=self.owner, operation=f'{n:032x}',
                image=(io.BytesIO(payload), 'synthetic.jpg'), **fields))
        try:
            env = builder.get_environ()
            data = env['wsgi.input'].read()
            content_type = env['CONTENT_TYPE']
        finally:
            builder.close()
        return self.post_signed(PREFIX + 'run', data, content_type)

    def complete(self, n=1):
        self.assertEqual(self.json_request('prepare', n).status_code, 200)
        response = self.run_request(n)
        self.assertEqual(response.status_code, 200, response.json)
        return response
