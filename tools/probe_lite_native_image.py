"""Run inside the built linux/amd64 image with synthetic tests mounted read-only.

The production factory and model files come from /app. Apple, GCS, KMS and
the clock are synthetic. Only the loopback interface may be present.
"""
import functools
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import signal
import socket
import sys
import traceback
from types import SimpleNamespace
from unittest.mock import patch

from gunicorn.app.base import BaseApplication
import numpy as np
from PIL import Image
import requests

import lite_native_startup as startup
from lite_native_inference import LiteNativeInference, file_hash
import test_lite_native_startup as fixture
from test_lite_subscription_grace import rows
from verify_native_package import verify


class LoopbackClient:
    def __init__(self, port, token_file):
        self.base = f'http://127.0.0.1:{port}'
        self.session = requests.Session()
        self.session.trust_env = False
        self.token_file = token_file

    def post(self, path, *, content_type=None, headers=None, **kwargs):
        if path == '/api/v1/lite/account/confirm':
            # The external OAuth test double returns this synthetic sign-in JWT.
            self.token_file.write_text(json.loads(kwargs['data'])['identity_token'])
        headers = dict(headers or {})
        if content_type:
            headers['Content-Type'] = content_type
        r = self.session.post(self.base + path, headers=headers, timeout=30, allow_redirects=False, **kwargs)
        return SimpleNamespace(status_code=r.status_code, json=r.json(), headers=r.headers)


class LocalGunicorn(BaseApplication):
    def __init__(self, fd, environment, providers, clock):
        self.fd = fd
        self.environment = environment
        self.build = functools.partial(startup.build_native_runtime, providers=providers, clock=clock)
        super().__init__()

    def load_config(self):
        for k, v in dict(bind=f'fd://{self.fd}', workers=1, threads=2, timeout=120,
                         graceful_timeout=5, worker_tmp_dir='/tmp', control_socket_disable=True).items():
            self.cfg.set(k, v)

    def load(self):
        with patch.object(startup, 'build_native_runtime', self.build):
            return startup.application(self.environment)


def run():
    package = Path('/app')
    if (platform.system() != 'Linux' or platform.machine() != 'x86_64' or os.getuid() != 1000
            or not os.statvfs(package).f_flag & os.ST_RDONLY
            or {p.name for p in Path('/sys/class/net').iterdir()} != {'lo'}):
        raise RuntimeError('requires read-only isolated linux/amd64 image with its runtime UID')
    package_status = verify(package)
    manifest = json.loads((package/'build-context-manifest.json').read_bytes())
    if manifest['git_head'] != os.environ['QA_SOURCE'] or manifest['working_tree_dirty']:
        raise ValueError('expected clean, pinned image')

    case = fixture.StartupHTTPTests()
    case.setUpClass()
    pid = None
    try:
        h = fixture.Fixture(case)
        h.isolate_secret_mounts()
        case.h, case.e, case.backend = h, h.e, h.compose()
        case.counter, case.key, case.login_serial = 0, fixture.b64(h.e.key_id), 100
        account = case.backend.identity
        person = account.events.identity('apple-human-1')
        h.kms.provision(account.grants.cipher.provider.version(account.lifecycle.scope, person))
        work = h.secret_dir.parent
        token_file, calls = work/'synthetic-oauth.jwt', work/'model-calls'
        h.grant.hook = lambda _: setattr(h.grant, 'token', token_file.read_text())
        h.config['model']['preprocessing_sha256'] = file_hash(package/'vendor/preprocessing.json')

        def load_model(base, digest):
            model = LiteNativeInference(package=base, preprocessing_sha256=digest)
            infer = model.raster.infer
            def counted(rgb):
                with calls.open('a') as f:
                    f.write('1\n')
                return infer(rgb)
            model.raster.infer = counted
            return model

        h.providers.model = load_model
        config = work/'mounted-config.json'
        config.write_bytes(fixture.encoded(h.config))
        env = dict(LITE_NATIVE_CONFIG=str(config), LITE_NATIVE_CONFIG_SHA256=file_hash(config),
                   LITE_NATIVE_SOURCE=h.config['deployment']['source'], LITE_NATIVE_IMAGE_SHA256=h.config['deployment']['image'],
                   LITE_NATIVE_SECRET_DIR=str(h.secret_dir))
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            port = listener.getsockname()[1]
            pid = os.fork()
            if pid == 0:
                try:
                    LocalGunicorn(listener.fileno(), env, h.providers, h.clock).run()
                except SystemExit as exc:
                    os._exit(exc.code if isinstance(exc.code, int) else int(exc.code is not None))
                except BaseException:
                    traceback.print_exc(file=sys.stderr)
                    os._exit(1)
                os._exit(0)
        case.client = LoopbackClient(port, token_file)
        blocked = case.client.post('/api/v1/lite/segment', json={})
        case.assertEqual(blocked.status_code, 404)
        case.assertEqual(case.client.post('/api/v1/lite/service/prepare', json={}).status_code, 401)
        # The worker and parent open the same temporary SQLite database.
        original_privacy = rows(h.privacy.store)
        case.register()
        case.login()
        rng = np.random.default_rng(314159)
        rng.integers(0, 256, (256, 256, 3), dtype=np.uint8)
        rgb = rng.integers(0, 256, (521, 319, 3), dtype=np.uint8)
        buf = io.BytesIO()
        Image.fromarray(rgb).save(buf, format='JPEG', quality=100, subsampling=0)
        case.f = SimpleNamespace(jpeg=buf.getvalue())
        case.image_hash = hashlib.sha256(case.f.jpeg).hexdigest()
        first = case.complete(1)
        case.assertEqual(first.json['receipt']['outcome'], 'succeeded')
        case.assertFalse(first.json['research_stored'])
        case.assertEqual(case.json_request('status', 1).json, first.json)
        case.assertEqual(case.run_request(1).json, first.json)
        case.assertEqual(case.quota()['used'], 1)
        case.assertEqual(calls.read_text(), '1\n')
        case.assertEqual(rows(h.privacy.store), original_privacy)
        geometry = first.json['result']['wound_polygons']
        case.assertTrue(geometry)
        return dict(passed=True, source=manifest['git_head'], secret_layout=h.config['schema'],
                    packaged_files=package_status['files'],
                    machine=platform.machine(), uid=os.getuid(), server='gunicorn', model_calls=1, used=1,
                    image_sha256=case.image_hash, detected_polygons=len(geometry),
                    original_receipt_replayed=True, research_state_unchanged=True, research_route=404,
                    unsigned_prepare=401, real_tcp=True, external_network=False,
                    real_apple=False, real_gcs_kms=False, clinical_accuracy_tested=False)
    finally:
        if pid:
            if hasattr(case, 'client'):
                case.client.session.close()
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            _, status = os.waitpid(pid, 0)
            if status and sys.exc_info()[0] is None:
                raise RuntimeError(f'Gunicorn exited with wait status {status}')
        case.doCleanups()


if __name__ == '__main__':
    print(json.dumps(run(), sort_keys=True))
