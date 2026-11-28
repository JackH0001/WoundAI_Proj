"""Two real HTTP processes: account closure against late native ONNX work.

Only synthetic SQLite/Apple/KMS data, in an isolated immutable Linux image.
Barriers delay execution; they do not replace the production account/CAS logic.
This exercises current-result sealing, not complete erasure or cloud IAM.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import platform
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import numpy as np
from PIL import Image
import lite_native_startup as startup
from lite_native_inference import LiteNativeInference, file_hash
from lite_rotating_grant_keys import SHARED_SCHEME, RotatingGrantCipher
from lite_account_deletion import LINKED_WITHDRAWAL
from lite_usage_ledger import digest, free_grant
from test_lite_rotating_grant_keys import MultiVersionKMS
from test_lite_subscription_grace import rows
import test_lite_native_startup as fixture
from probe_lite_native_replicas import Server, wait_for, append
from verify_native_package import verify


def case_run(mode):
    case = fixture.StartupHTTPTests()
    case.setUpClass()
    servers = []
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
        case.assertIs(type(case.backend.identity.grants.cipher), RotatingGrantCipher)
        case.counter, case.key, case.login_serial = 0, fixture.b64(h.e.key_id), 100
        case.client = case.backend.create_app().test_client()
        case.register()
        case.login()
        subject = h.release.n.accounts.registration(case.owner)['subject']
        work = h.secret_dir.parent
        calls, events = work/'model-calls.jsonl', work/'barriers.jsonl'
        token_file = work/'synthetic-oauth.jwt'
        token_file.write_text(case.confirm_fields['identity_token'])
        h.grant.hook = lambda _: setattr(h.grant, 'token', token_file.read_text())
        held, release, armed = work/'held', work/'release', work/'armed'
        def barrier(stage):
            if mode == stage and armed.exists():
                append(events, dict(stage=stage, pid=os.getpid()))
                held.touch()
                wait_for(release.exists, 25)
        h.config['model']['preprocessing_sha256'] = file_hash(Path('/app/vendor/preprocessing.json'))
        def load_model(base, expected):
            model = LiteNativeInference(package=base, preprocessing_sha256=expected)
            infer = model.raster.infer
            def counted(rgb):
                append(calls, dict(pid=os.getpid()))
                barrier('inference')
                return infer(rgb)
            model.raster.infer = counted
            return model
        h.providers.model = load_model
        # Capture the value/revision at the production CAS boundary. The real
        # store implementation executes unchanged after the worker seals it.
        original_state = h.providers.state
        def state(bucket, prefix):
            store = original_state(bucket, prefix)
            if prefix.endswith('/operations'):
                original_cas = store.compare_exchange
                def delayed(key, revision, value):
                    if value.get('result') is not None:
                        barrier('result_commit')
                    return original_cas(key, revision, value)
                store.compare_exchange = delayed
            return store
        h.providers.state = state
        config = work/'mounted-config.json'
        config.write_bytes(fixture.encoded(h.config))
        env = dict(LITE_NATIVE_CONFIG=str(config), LITE_NATIVE_CONFIG_SHA256=file_hash(config),
            LITE_NATIVE_SOURCE=h.config['deployment']['source'],
            LITE_NATIVE_IMAGE_SHA256=h.config['deployment']['image'],
            LITE_NATIVE_SECRET_DIR=str(h.secret_dir))
        for _ in range(2):
            servers.append(Server(env, h, token_file))
        for server in servers:
            case.assertEqual(server.client.post('/api/v1/lite/service/run', json={}).status_code, 401)
        rng = np.random.default_rng(314159)
        rng.integers(0, 256, (256, 256, 3), dtype=np.uint8)
        rgb = rng.integers(0, 256, (521, 319, 3), dtype=np.uint8)
        image = io.BytesIO()
        Image.fromarray(rgb).save(image, format='JPEG', quality=100, subsampling=0)
        case.f = SimpleNamespace(jpeg=image.getvalue())
        case.image_hash = hashlib.sha256(case.f.jpeg).hexdigest()
        case.client = servers[0].client
        first = case.complete(1).json
        case.assertEqual(first['receipt']['outcome'], 'succeeded')
        case.assertEqual(case.json_request('prepare', 2).status_code, 200)
        ops = case.backend.operations
        prior = {n:ops.load(subject, f'{n:032x}') for n in (1, 2)}
        first_ticket = ops.ticket(subject, prior[1][1])
        privacy_before = rows(h.privacy.store)
        audit_before = {k:rows(s) for k,s in h.all_stores.items() if k.endswith('_audit')}
        armed.touch()
        with ThreadPoolExecutor(max_workers=1) as pool:
            running = pool.submit(case.run_request, 2)
            try:
                wait_for(held.exists)
                case.client = servers[1].client
                intake = case.post('account/deletion/request', operation='d'*32,
                    confirmation=LINKED_WITHDRAWAL)
                case.assertEqual(intake.status_code, 202, intake.json)
                case.assertFalse(intake.json['account_deleted'])
                case.assertFalse(running.done())
                # Signed public intake must not invoke the private cleanup lane.
                case.assertEqual(ops.load(subject, f'{1:032x}'), prior[1])
                case.assertIsNone(ops.load(subject, f'{2:032x}')[1]['result'])
                denied = {}
                for action in ('prepare', 'status'):
                    response = case.json_request(action, 3 if action == 'prepare' else 1)
                    denied[action] = dict(http=response.status_code, response=response.json)
                    case.assertEqual(response.status_code, 401, response.json)
                ledger_before = rows(h.release.ledger.store)
                # Trusted parent process acts as the internal current-result
                # worker. It has no public route and only sees synthetic state.
                sealed = case.backend.progress.results.advance(case.person, limit=2)
                case.assertEqual((sealed['sealed'], sealed['total']), (2, 2))
                case.assertFalse(sealed['account_deleted'])
                seals_before_release = {n:ops.load(subject, f'{n:032x}') for n in (1, 2)}
                case.assertTrue(all(row[1]['v'] == 3 and 'result' not in row[1]
                    for row in seals_before_release.values()))
            finally:
                release.touch()
            late = running.result(timeout=40)
        case.assertEqual(late.status_code, 409 if mode == 'inference' else 410, late.json)
        case.assertNotIn('result', late.json)
        case.assertEqual(rows(h.release.ledger.store), ledger_before)
        case.assertEqual(ops.ledger.receipt(first_ticket), first['receipt'])
        case.assertEqual({n:ops.load(subject, f'{n:032x}') for n in (1, 2)}, seals_before_release)
        case.assertEqual(case.backend.progress.results.advance(case.person, limit=2), sealed)
        before_restart = {k:rows(s) for k,s in h.all_stores.items() if k != 'security' and k != 'budget'}
        servers[0].stop()
        servers[0] = Server(env, h, token_file)
        case.client = servers[0].client
        replay = case.post('account/deletion/request', operation='d'*32, confirmation=LINKED_WITHDRAWAL)
        case.assertEqual(replay.status_code, 202, replay.json)
        status = case.post('account/deletion/status', operation='d'*32)
        case.assertEqual(status.json, replay.json)
        case.assertFalse(status.json['account_deleted'])
        for action in ('run', 'status'):
            response = case.run_request(2) if action == 'run' else case.json_request('status', 1)
            case.assertEqual(response.status_code, 401, response.json)
            denied['restart_'+action] = dict(http=response.status_code, response=response.json)
        case.assertEqual({k:rows(s) for k,s in h.all_stores.items() if k != 'security' and k != 'budget'}, before_restart)
        case.assertEqual(rows(h.privacy.store), privacy_before)
        for name,old in audit_before.items():
            after = dict((k,(rev,data)) for k,rev,data in rows(h.all_stores[name]))
            case.assertTrue(all(after.get(k)==(rev,data) for k,rev,data in old), name)
        model_events = [json.loads(line) for line in calls.read_text().splitlines()]
        case.assertEqual(len(model_events), 2)
        quota = h.release.ledger.snapshot(subject, h.clock()//86400, free_grant(subject,h.clock()))
        case.assertEqual((quota['used'],quota['attempts'],quota['pending']), (1,2,1))
        return dict(mode=mode, model_calls=2, quota=quota, baseline_receipt=first['receipt'],
            original_sha256=hashlib.sha256(case.f.jpeg).hexdigest(), intake=intake.json,
            late_http=late.status_code, late_response=late.json, private_lane_receipt=sealed,
            seals={str(n):row[1] for n,row in seals_before_release.items()},
            denied=denied, restarted_status=status.json, model_events=model_events,
            barrier_events=[json.loads(line) for line in events.read_text().splitlines()],
            ledger_unchanged_by_seal_and_replay=True, previous_audit_rows_retained=True,
            privacy_unchanged=True, intake_did_not_seal=True, restart_state_unchanged=True)
    finally:
        for server in servers:
            server.stop()
        case.doCleanups()


def run():
    package = Path('/app')
    assert platform.system() == 'Linux' and platform.machine() == 'x86_64'
    assert os.getuid() == 1000 and os.statvfs(package).f_flag & os.ST_RDONLY
    assert {p.name for p in Path('/sys/class/net').iterdir()} == {'lo'}
    checked = verify(package)
    manifest = json.loads((package/'build-context-manifest.json').read_bytes())
    assert manifest['git_head'] == os.environ['QA_SOURCE'] and not manifest['working_tree_dirty']
    results = [case_run(mode) for mode in ('inference','result_commit')]
    return dict(schema='lite.closure-race-acceptance/1', passed=True, scenarios=results,
        image_source=manifest['git_head'], packaged_files=checked['files'], real_http=True,
        independent_gunicorn_processes=2, private_worker_process=True, worker_restarted=True,
        uid=os.getuid(), shared_durable_store='SQLite', real_apple=False, real_gcs_kms=False,
        external_network=False, cloud_capacity_tested=False, complete_erasure_tested=False)


if __name__ == '__main__':
    print(json.dumps(run(), sort_keys=True))
