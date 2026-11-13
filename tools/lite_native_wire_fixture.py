"""Loopback-only Swift/Python contract fixture. Never imported by an App image.

Apple, KMS and inference are synthetic. Native request bodies are forwarded
unchanged through real TCP; a test-only middleware adds synthetic App Attest
assertions, which the normal backend verifies. This is NOT Apple Sandbox/TLS QA.
"""
import base64
import hashlib
import io
import json
from pathlib import Path
import secrets
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Backend/Flask'), str(ROOT / 'engineering/phase2')]
from werkzeug.serving import make_server, WSGIRequestHandler
from werkzeug.wrappers import Request, Response
from test_lite_native_backend import NativeBackendTests, rows


class QuietHandler(WSGIRequestHandler):
    def log_request(self, *args): pass


class WireFixture:
    def __init__(self):
        NativeBackendTests.setUpClass()
        self.token = secrets.token_hex(32)
        self.h = None
        self.events = []
        self.completed = []
        self.revoke_before_service = False
        self.server = make_server('127.0.0.1', 0, self, request_handler=QuietHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self):
        self.thread.start()
        return {'url': f'http://127.0.0.1:{self.server.server_port}', 'token': self.token}

    def close(self):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()
        if self.h: self.h.doCleanups()

    def snapshot(self):
        h = self.h
        confirmed = any(r['path'] == '/api/v1/lite/account/confirm' and r['status'] == 200 for r in self.events)
        quota = h.quota() if confirmed else {}
        return dict(model_calls=h.h.model_calls, used=quota.get('used', 0),
                    attempts=quota.get('attempts', 0),
                    research_unchanged=rows(h.h.privacy.store) == self.privacy,
                    image_unchanged=hashlib.sha256(h.f.jpeg).hexdigest() == self.image_hash,
                    routes=list(self.events))

    def control(self, kind, body):
        if kind == 'reset':
            if self.h: self.h.doCleanups()
            self.h = NativeBackendTests(); self.h.setUp()
            self.privacy = rows(self.h.h.privacy.store)
            self.image_hash = hashlib.sha256(self.h.f.jpeg).hexdigest()
            self.events = []
            self.revoke_before_service = False
            return dict(owner=self.h.owner, now=self.h.h.clock(),
                        jpeg=base64.b64encode(self.h.f.jpeg).decode())
        if not self.h: raise ValueError('reset required')
        h = self.h
        if kind == 'identity':
            c = body['challenge']; sub = 'apple-human-1'
            person = h.backend.identity.events.identity(sub)
            version = h.backend.identity.grants.cipher.provider.version(h.backend.identity.lifecycle.scope, person)
            if version not in h.h.kms.states: h.h.kms.provision(version)
            signed = h.h.identity.token(c, sub=sub); h.h.grant.token = signed
            return dict(identityToken=signed, state=c['state'], user=sub,
                        authorizationCode='synthetic-code-' + c['operation'])
        if kind == 'purchase':
            a = h.h.release.n.a; a.payload['appAccountToken'] = body['token']
            return dict(transaction=a.payload['transactionId'], original=a.payload['originalTransactionId'],
                        token=body['token'], product=a.payload['productId'], environment='Sandbox', signed=a.sign(a.payload))
        if kind == 'advance':
            seconds = body['seconds']
            if type(seconds) is not int or not 0 <= seconds <= 1800: raise ValueError('bounded clock')
            h.h.release.n.a.now += seconds
            h.e.now = h.h.clock()
            return dict(now=h.h.clock())
        if kind == 'restart':
            h.backend = h.h.compose(); h.app = h.backend.create_app(); h.client = h.app.test_client()
            return dict(restarted=True)
        if kind == 'revoke-before-service':
            self.revoke_before_service = True
            return dict(armed=True)
        if kind == 'snapshot': return self.snapshot()
        if kind == 'finish':
            result = self.snapshot(); result['name'] = body['name']; self.completed.append(result)
            return result
        raise ValueError('unknown control')

    def __call__(self, env, start_response):
        request = Request(env)
        if env.get('REMOTE_ADDR') != '127.0.0.1' or not secrets.compare_digest(
                request.headers.get('X-Synthetic-Wire', ''), self.token):
            return Response(status=403)(env, start_response)
        if request.method != 'POST' or request.content_length is None or request.content_length > 2 * 1024 * 1024:
            return Response(status=400)(env, start_response)
        raw = request.get_data(cache=True)
        if request.path.startswith('/fixture/'):
            try:
                result = self.control(request.path.rsplit('/', 1)[1], json.loads(raw))
                return Response(json.dumps(result), content_type='application/json')(env, start_response)
            except Exception as error:
                # No identity tokens, body, credentials or exception text in logs.
                return Response(json.dumps({'fixture_error': type(error).__name__}), status=500,
                                content_type='application/json')(env, start_response)
        if not self.h or not request.path.startswith('/api/v1/lite/'):
            return Response(status=404)(env, start_response)
        path = request.path
        if self.revoke_before_service and path == '/api/v1/lite/service/prepare':
            self.revoke_before_service = False
            session = self.h.backend.identity.status(self.h.owner)
            self.h.backend.identity.logout(self.h.owner, session['operation'])
        purpose = 'withdraw' if '/account/deletion/' in path else 'assert'
        headers = self.h.headers(path, raw, request.content_type, purpose=purpose)
        for k, v in headers.items(): env['HTTP_' + k.upper().replace('-', '_')] = v
        env['wsgi.input'] = io.BytesIO(raw)
        # The actual production Flask router receives the native Swift bytes.
        def capture(status, response_headers, exc_info=None):
            self.events.append(dict(path=path, status=int(status.split()[0]),
                                    body_sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw)))
            return start_response(status, response_headers, exc_info)
        return self.h.app(env, capture)
