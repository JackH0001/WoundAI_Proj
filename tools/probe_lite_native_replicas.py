"""Offline two-process acceptance against an immutable native container.

Real Gunicorn HTTP, signatures, quota/CAS and packaged ONNX. Apple, KMS,
clock and GCS transport are synthetic; SQLite is shared across processes.
Not a Cloud Run capacity test or a clinical segmentation accuracy test.
"""
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import signal
import socket
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import numpy as np
from PIL import Image
from lite_native_inference import LiteNativeInference, file_hash
import lite_native_startup as startup
from lite_rotating_grant_keys import SHARED_SCHEME, RotatingGrantCipher
import test_lite_native_startup as fixture
from test_lite_rotating_grant_keys import MultiVersionKMS
from test_lite_subscription_grace import rows
from probe_lite_native_image import LocalGunicorn, LoopbackClient
from verify_native_package import verify


def wait_for(predicate, seconds=60):
    end = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() >= end:
            raise TimeoutError('bounded local replica rendezvous')
        time.sleep(.05)


def append(path, value):
    with path.open('a') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(json.dumps(value, sort_keys=True) + '\n')
        f.flush()


class Server:
    def __init__(self, env, h, token_file):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            self.port = listener.getsockname()[1]
            self.pid = os.fork()
            if self.pid == 0:
                try:
                    LocalGunicorn(listener.fileno(), env, h.providers, h.clock).run()
                except SystemExit as exc:
                    os._exit(exc.code if isinstance(exc.code, int) else int(exc.code is not None))
                except BaseException:
                    traceback.print_exc(file=sys.stderr)
                    os._exit(1)
                os._exit(0)
        self.client = LoopbackClient(self.port, token_file)

    def stop(self):
        if self.pid is not None:
            self.client.session.close()
            try:
                os.kill(self.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            _, status = os.waitpid(self.pid, 0)
            self.pid = None
            if status:
                raise RuntimeError(f'Gunicorn wait status {status}')


def run():
    package = Path('/app')
    assert platform.system() == 'Linux' and platform.machine() == 'x86_64'
    assert os.getuid() == 1000 and os.statvfs(package).f_flag & os.ST_RDONLY
    assert {p.name for p in Path('/sys/class/net').iterdir()} == {'lo'}
    checked = verify(package)
    manifest = json.loads((package/'build-context-manifest.json').read_bytes())
    assert manifest['git_head'] == os.environ['QA_SOURCE'] and not manifest['working_tree_dirty']
    case = fixture.StartupHTTPTests()
    case.setUpClass()
    servers = []
    observations = []
    try:
        h = fixture.Fixture(case)
        h.isolate_secret_mounts()
        h.config['schema'] = 'lite-native-startup/3'
        key = h.config['deployment']['keyring'] + '/cryptoKeys/shared-runtime'
        version = key + '/cryptoKeyVersions/1'
        h.config['grant_keys'] = dict(scheme=SHARED_SCHEME, crypto_key=key,
            write_version=version, read_versions=[version])
        h.kms = MultiVersionKMS()
        h.kms.provision(version)
        h.providers.kms = startup.RuntimeKMSSession(h.kms)
        case.h, case.e, case.backend = h, h.e, h.compose()
        assert type(case.backend.identity.grants.cipher) is RotatingGrantCipher
        case.counter, case.key, case.login_serial = 0, fixture.b64(h.e.key_id), 100
        # Bootstrap the signed account and purchase context before fork. External
        # providers use immutable synthetic responses shared by both children.
        case.client = case.backend.create_app().test_client()
        case.register()
        case.login()
        context = case.post('purchase/context', operation='c'*32)
        case.assertEqual(context.status_code, 200, context.json)
        a = h.release.n.a
        a.payload['appAccountToken'] = context.json['app_account_token']
        transaction = a.sign(a.payload)
        work = h.secret_dir.parent
        calls, apple_calls = work/'model-calls.jsonl', work/'apple-calls.jsonl'
        token_file = work/'synthetic-oauth.jwt'
        token_file.write_text(case.confirm_fields['identity_token'])
        h.grant.hook = lambda _: setattr(h.grant, 'token', token_file.read_text())
        fetch = a.client.get_all_subscription_statuses
        def counted_apple(original):
            append(apple_calls, dict(pid=os.getpid(), at=time.monotonic()))
            return fetch(original)
        a.client.get_all_subscription_statuses = counted_apple
        held, release = work/'first-inference-held', work/'release-inference'
        h.config['model']['preprocessing_sha256'] = file_hash(package/'vendor/preprocessing.json')
        def load_model(base, digest):
            model = LiteNativeInference(package=base, preprocessing_sha256=digest)
            infer = model.raster.infer
            def counted(rgb):
                first = False
                try:
                    fd = os.open(held, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                    os.close(fd)
                    first = True
                except FileExistsError:
                    pass
                append(calls, dict(pid=os.getpid(), at=time.monotonic(), first=first))
                if first:
                    wait_for(release.exists, 25)
                return infer(rgb)
            model.raster.infer = counted
            return model
        h.providers.model = load_model
        config = work/'mounted-config.json'
        config.write_bytes(fixture.encoded(h.config))
        env = dict(LITE_NATIVE_CONFIG=str(config), LITE_NATIVE_CONFIG_SHA256=file_hash(config),
            LITE_NATIVE_SOURCE=h.config['deployment']['source'],
            LITE_NATIVE_IMAGE_SHA256=h.config['deployment']['image'],
            LITE_NATIVE_SECRET_DIR=str(h.secret_dir))
        for _ in range(2):
            servers.append(Server(env, h, token_file))
        for server in servers:
            case.assertEqual(server.client.post('/api/v1/lite/segment', json={}).status_code, 404)
            case.assertEqual(server.client.post('/api/v1/lite/service/prepare', json={}).status_code, 401)
        original_privacy = rows(h.privacy.store)
        rng = np.random.default_rng(314159)
        rng.integers(0, 256, (256, 256, 3), dtype=np.uint8)
        rgb = rng.integers(0, 256, (521, 319, 3), dtype=np.uint8)
        image = io.BytesIO()
        Image.fromarray(rgb).save(image, format='JPEG', quality=100, subsampling=0)
        case.f = SimpleNamespace(jpeg=image.getvalue())
        case.image_hash = hashlib.sha256(case.f.jpeg).hexdigest()
        case.client = servers[0].client
        case.assertEqual(case.json_request('prepare', 1).status_code, 200)
        receipts = {}
        with ThreadPoolExecutor(max_workers=1) as pool:
            running = pool.submit(case.run_request, 1)
            try:
                wait_for(held.exists)
                # Request A has passed signature verification before client B
                # consumes the next App Attest counter. Counters are not raced.
                case.client = servers[1].client
                duplicate = case.run_request(1)
                case.assertEqual(duplicate.status_code, 202, duplicate.json)
                second = case.complete(2)
                case.assertEqual(second.json['receipt']['outcome'], 'succeeded')
                case.assertFalse(running.done(), 'the first inference must still be held')
                observations.append(dict(case='overlapping_operation', duplicate_http=202,
                    second_completed_while_first_held=True))
            finally:
                release.touch()
            first = running.result(timeout=40)
            case.assertEqual(first.status_code, 200, first.json)
            case.assertEqual(first.json['receipt']['outcome'], 'succeeded')
        receipts[1], receipts[2] = first.json, second.json
        case.assertEqual(case.quota()['used'], 2)
        denied_free = case.json_request('prepare', 3)
        case.assertEqual(denied_free.status_code, 429, denied_free.json)
        observations.append(dict(case='free_limit', used=2, next_http=429, response=denied_free.json))
        # Simulate loss of the original client response: another process reads
        # the durable receipt and retries the same run, with no new inference.
        case.assertEqual(case.json_request('status', 1).json, first.json)
        case.assertEqual(case.run_request(1).json, first.json)
        count_before = len(calls.read_text().splitlines())
        case.assertEqual(count_before, 2)
        upgraded = case.post('purchase/confirm', operation='c'*32, signed_transaction=transaction)
        case.assertEqual(upgraded.status_code, 200, upgraded.json)
        apple_before = apple_calls.read_text()
        case.client = servers[0].client
        restored = case.post('purchase/confirm', operation='c'*32, signed_transaction=transaction)
        case.assertEqual(restored.json, upgraded.json)
        case.assertEqual(apple_calls.read_text(), apple_before)
        case.assertEqual(case.quota()['used'], 2)
        observations.append(dict(case='upgrade_and_confirmation_replay', used=2,
            confirmation_equal=True, extra_apple_queries=0))
        for n in range(3, 11):
            case.client = servers[n % 2].client
            response = case.complete(n)
            case.assertEqual(response.json['receipt']['outcome'], 'succeeded')
            receipts[n] = response.json
        denied_plus = case.json_request('prepare', 11)
        case.assertEqual(denied_plus.status_code, 429, denied_plus.json)
        quota_before_restart = case.quota()
        case.assertEqual(quota_before_restart['used'], 10)
        case.assertEqual(quota_before_restart['attempts'], 10)
        model_before_restart = calls.read_text()
        apple_before_restart = apple_calls.read_text()
        # A fresh Gunicorn master and worker must use the same receipt/ledger.
        servers[0].stop()
        servers[0] = Server(env, h, token_file)
        case.client = servers[0].client
        for n in range(1, 11):
            case.assertEqual(case.json_request('status', n).json, receipts[n])
            case.assertEqual(case.run_request(n).json, receipts[n])
        case.assertEqual(case.quota(), quota_before_restart)
        case.assertEqual(calls.read_text(), model_before_restart)
        case.assertEqual(apple_calls.read_text(), apple_before_restart)
        model_events = [json.loads(line) for line in calls.read_text().splitlines()]
        case.assertEqual(len(model_events), 10)
        case.assertEqual(len({v['pid'] for v in model_events}), 2)
        case.assertEqual(rows(h.privacy.store), original_privacy)
        case.assertTrue(all(not r['research_stored'] and r['result']['wound_polygons'] for r in receipts.values()))
        observations.append(dict(case='plus_limit_and_restart', used=10, attempts=10,
            next_http=429, response=denied_plus.json, unchanged_receipts=10,
            status_reads=10, run_replays=10, extra_model_calls=0, extra_apple_queries=0))
        return dict(schema='lite.replica-quota-acceptance/1', passed=True,
            image_source=manifest['git_head'], packaged_files=checked['files'],
            independent_gunicorn_processes=2, worker_restarted=True,
            uid=os.getuid(), machine=platform.machine(), source_image_sha256=case.image_hash,
            model_calls=len(model_events), used=10, attempts=10, observations=observations,
            model_events=model_events, receipts=receipts,
            apple_status_queries=len(apple_calls.read_text().splitlines()),
            research_state_unchanged=True, synthetic_original_sha256=hashlib.sha256(case.f.jpeg).hexdigest(),
            production_code_from_image=True, real_http=True, shared_durable_store='SQLite',
            real_apple=False, real_gcs_kms=False, external_network=False,
            cloud_capacity_tested=False, clinical_accuracy_tested=False)
    finally:
        for server in servers:
            server.stop()
        case.doCleanups()


if __name__ == '__main__':
    print(json.dumps(run(), sort_keys=True))
