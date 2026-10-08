"""Synthetic medical image acceptance, inside a network-disabled container.

Runs the packaged app through real Gunicorn HTTP, real account/consent handlers,
and disposable LocalStore. No cloud credentials, real records or API mocks.
This is not GCS, WORM, phone, precision, or clinical approval evidence.
"""
import argparse
import base64
import gc
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time


def verify_source(context, expected):
    manifest = context / 'build-context-manifest.json'
    if hashlib.sha256(manifest.read_bytes()).hexdigest() != expected:
        raise RuntimeError('source manifest mismatch')
    data = json.loads(manifest.read_text())
    if data.get('working_tree_dirty') is not False:
        raise RuntimeError('source was not clean')
    for name, row in data['files'].items():
        path = context / name
        if path.is_symlink() or not path.resolve().is_relative_to(context):
            raise RuntimeError('unsafe source path')
        if (len(path.read_bytes()) != row['bytes'] or
                hashlib.sha256(path.read_bytes()).hexdigest() != row['sha256']):
            raise RuntimeError('packaged source changed')
    return data['git_head']


def exercise(context, runtime, checks):
    import cv2
    import numpy as np
    import requests

    def check(label, condition):
        checks.append({'check': label, 'passed': bool(condition)})
        if not condition:
            raise RuntimeError(label)

    # Only loopback traffic is needed. The caller additionally disables the
    # container network, so neither app nor probe can reach metadata or GCS.
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    origin = 'http://127.0.0.1:%d' % port
    env = {'PATH': os.environ.get('PATH', ''), 'PYTHONDONTWRITEBYTECODE': '1',
           'HOME': str(runtime), 'TMPDIR': str(runtime), 'PYTHONPATH': str(context),
           'WOUNDAI_STORE': 'local', 'WOUNDAI_RUNTIME_DIR': str(runtime),
           'WOUNDAI_FLYWHEEL_DIR': str(runtime / 'flywheel'),
           'WOUNDAI_INSTITUTION_ORG': 'mmhps20261007',
           'JWT_SECRET_KEY': secrets.token_urlsafe(48),
           'FLASK_SECRET_KEY': secrets.token_urlsafe(48),
           'ADMIN_PASSWORD': secrets.token_urlsafe(32),
           'CARE_RECEIPT_SECRET': json.dumps({'active_kid': 'synthetic',
               'keys': {'synthetic': {'secret_b64': secrets.token_urlsafe(48)}}})}
    session = requests.Session()
    session.trust_env = False

    def request(method, path, **kwargs):
        if not path.startswith('/api/'):
            raise RuntimeError('unexpected request path')
        response = session.request(method, origin + path, allow_redirects=False,
                                   timeout=90, **kwargs)
        if 300 <= response.status_code < 400:
            raise RuntimeError('redirect forbidden')
        return response

    def post(path, **kwargs):
        return request('POST', path, **kwargs)

    with (runtime / 'gunicorn.log').open('w') as log:
        server = subprocess.Popen([sys.executable, '-m', 'gunicorn', '--chdir', str(context),
            '--bind', '127.0.0.1:%d' % port, '--workers', '1', '--threads', '8',
            '--timeout', '120', '--graceful-timeout', '10', 'app:app'],
            env=env, cwd=runtime, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 90
            health = None
            while time.monotonic() < deadline and server.poll() is None:
                try:
                    health = request('GET', '/api/health')
                    if health.status_code == 200:
                        break
                except requests.ConnectionError:
                    pass
                time.sleep(0.25)
            check('Gunicorn medical health ready', health is not None and health.status_code == 200)
            check('model availability disclosed', health.json()['services']['segmentation_model'] is True)

            def login(user, password):
                r = post('/api/auth/login', json={'username': user, 'password': password})
                check('login ' + user, r.status_code == 200 and r.json().get('org') == 'mmhps20261007')
                return {'Authorization': 'Bearer ' + r.json()['access_token']}

            admin = login('admin', env['ADMIN_PASSWORD'])
            denied = post('/api/auth/login', json={'username': 'default:admin', 'password': env['ADMIN_PASSWORD']})
            check('foreign organization login rejected', denied.status_code == 401)
            accounts = {}
            for user, role in [('dr_probe', 'physician'), ('ns_probe', 'nurse')]:
                password = secrets.token_urlsafe(32)
                r = post('/api/v1/users', headers=admin,
                         json={'user': user, 'role': role, 'password': password})
                check('create synthetic ' + role, r.status_code == 200)
                accounts[user] = login(user, password)
            dr, nurse = accounts['dr_probe'], accounts['ns_probe']
            check('foreign organization account write rejected', post('/api/v1/users', headers=admin,
                json={'org': 'other', 'user': 'forbidden', 'role': 'nurse',
                      'password': secrets.token_urlsafe(32)}).status_code == 400)

            pixels = np.full((256, 320, 3), 230, np.uint8)
            cv2.ellipse(pixels, (160, 128), (76, 52), 0, 0, 360, (45, 45, 200), -1)
            ok, jpg = cv2.imencode('.jpg', pixels, [cv2.IMWRITE_JPEG_QUALITY, 95])
            check('synthetic image encoded', ok)
            jpeg = jpg.tobytes()

            def classify(receipt=None):
                data = {'seg': 'color'}
                if receipt:
                    data['care_receipt'] = receipt
                r = post('/api/v1/classify', headers=dr, data=data,
                         files={'image': ('synthetic.jpg', jpeg, 'image/jpeg')})
                check('classify HTTP success', r.status_code == 200)
                return r.json()

            no_consent = classify()
            check('no care receipt never claims persistence', no_consent.get('persisted') is False
                  and no_consent.get('image_id') is None)
            code = 'WD-SYNTHETIC-PROBE'
            r = post('/api/v1/consent/care/attest', headers=dr, json={'code': code})
            check('care attestation available', r.status_code == 200 and bool(r.json().get('care_receipt')))
            measured = classify(r.json()['care_receipt'])
            iid = measured.get('image_id')
            check('care receipt stages image with explicit persistence', bool(iid)
                  and measured.get('persisted') is True and measured.get('persistence_reason') == 'staged'
                  and measured.get('image_w') == 320 and measured.get('image_h') == 256)
            tissue = np.zeros((256, 320), np.uint8)
            tissue[90:170, 100:210] = 1
            ok, png = cv2.imencode('.png', tissue)
            check('synthetic tissue layer encoded', ok)
            annotation = {'code': code, 'gt_polygon': [[100,90],[210,90],[210,170],[100,170]],
                'image_id': iid, 'image_w': 320, 'image_h': 256, 'exudate': 2,
                'doctor_verified': True, 'deidentified': True, 'consent_train': True,
                'source': 'phantom', 'route': measured['stage2_segment']['route'],
                'depth_source': 'lidar_local', 'tissue_mask_png': base64.b64encode(png).decode()}
            def annotate(body, headers=dr):
                return post('/api/v1/annotation', headers=headers, json=body)
            check('nurse cannot claim physician annotation', annotate(annotation, nurse).status_code == 403)
            check('research consent required', annotate({**annotation, 'consent_train': False}).status_code == 400)
            r = annotate(annotation)
            check('physician annotation enqueued', r.status_code == 200 and r.json().get('status') == 'enqueued')
            r = annotate(annotation)
            check('exact annotation retry deduplicated', r.status_code == 200 and r.json().get('status') == 'duplicate_skipped')
            store = runtime / 'flywheel'
            check('stored image bytes match submitted JPEG', (store / 'images' / (iid + '.jpg')).read_bytes() == jpeg)
            check('stored tissue bytes match submitted layer', (store / 'tissue_masks' / (iid + '.png')).read_bytes() == png.tobytes())

            raw = np.full((12, 16), 0.30, dtype='<f4').tobytes()
            meta = {'width': 16, 'height': 12, 'format': 'f32_le_meters', 'depth_source': 'lidar',
                    'camera_intrinsics': {'fx': 500., 'fy': 500., 'cx': 8., 'cy': 6., 'ref_width': 16, 'ref_height': 12}}
            def depth(content=raw, metadata=meta, headers=dr):
                return post('/api/v1/depth', headers=headers,
                            data={'image_id': iid, 'meta': json.dumps(metadata)},
                            files={'depth_f32': ('depth.f32', content, 'application/octet-stream')})
            check('nurse raw-depth upload rejected', depth(headers=nurse).status_code == 403)
            check('truncated depth rejected', depth(content=raw[:-4]).status_code == 400)
            invalid = dict(meta); invalid['camera_intrinsics'] = dict(meta['camera_intrinsics']); invalid['camera_intrinsics'].pop('ref_width')
            check('missing calibration reference rejected', depth(metadata=invalid).status_code == 400)
            r = depth()
            check('raw depth accepted with content identity', r.status_code == 200
                  and r.json().get('depth_id') == hashlib.sha256(raw).hexdigest()[:16])
            dp = store / 'depth_maps' / (iid + '.f32')
            check('stored raw depth bytes identical', dp.read_bytes() == raw)
            stored_meta = json.loads(dp.with_suffix('.meta.json').read_text())
            check('stored calibration metadata preserved', all(stored_meta.get(k) == v for k, v in meta.items()))
            check('depth statistics computed from raw bytes', all(stored_meta.get(k) == v
                  for k, v in {'coverage': 1.0, 'min_m': 0.3, 'max_m': 0.3}.items()))
            r = depth()
            check('same raw depth retry not marked replacement', r.status_code == 200 and r.json().get('replaced_previous') is False)
            check('invalid retry cannot overwrite accepted raw depth', depth(content=b'\0' * len(raw)).status_code == 400 and dp.read_bytes() == raw)
            stats = request('GET', '/api/v1/flywheel/stats', headers=dr)
            check('one trainable record after retry', stats.status_code == 200 and stats.json().get('trainable') == 1)
            check('withdrawal accepted', post('/api/v1/consent/withdraw', headers=dr, json={'code': code}).status_code == 200)
            check('withdrawal removes training eligibility', request('GET', '/api/v1/flywheel/stats', headers=dr).json().get('trainable') == 0)
            check('withdrawn annotation rejected', annotate(annotation).status_code == 400)
            check('withdrawn raw depth rejected', depth().status_code == 400)
            check('withdrawn image quarantined', not (store / 'images' / (iid + '.jpg')).exists())
            # Exercise the live institution-token revocation check, not just JWT expiry.
            check('disable physician account', post('/api/v1/users', headers=admin,
                json={'user': 'dr_probe', 'role': 'physician', 'disabled': True}).status_code == 200)
            check('issued token rejected after disabling account', request('GET', '/api/v1/flywheel/stats', headers=dr).status_code in (400,401,403))
        finally:
            server.terminate()
            try:
                server.wait(timeout=20)
            except subprocess.TimeoutExpired:
                server.kill(); server.wait(timeout=10)
            session.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context', type=Path, default=Path('/app'))
    parser.add_argument('--manifest-sha256', required=True)
    args = parser.parse_args()
    context = args.context.resolve()
    checks = []
    result = {'schema': 'medical.runtime-probe/1', 'passed': False,
              'manifest_sha256': args.manifest_sha256,
              'limits': ['synthetic only', 'LocalStore only', 'no clinical accuracy claim',
                         'no GCS/IAM/WORM or real-device validation']}
    started = time.monotonic()
    try:
        result['source_commit'] = verify_source(context, args.manifest_sha256)
        sys.path.insert(0, str(context))
        import numpy as np
        import onnxruntime as ort
        import runtime_golden
        runtime_golden.verify()
        result['canonical_golden'] = True
        result['models'] = {}
        previous_cwd = Path.cwd()
        with tempfile.TemporaryDirectory(prefix='medical-model-probe-') as model_tmp:
            os.chdir(model_tmp)
            try:
                for name in ('student_fp16.onnx', 'a_unet.onnx', 'unetpp.onnx'):
                    options = ort.SessionOptions(); options.intra_op_num_threads = 2
                    model = ort.InferenceSession(str(context / 'models' / name), options, providers=['CPUExecutionProvider'])
                    inp = model.get_inputs()[0]
                    shape = [v if type(v) is int else 1 for v in inp.shape]
                    arrays = model.run(None, {inp.name: np.zeros(shape, np.float32)})
                    if not all(np.isfinite(a).all() for a in arrays):
                        raise RuntimeError('non-finite model output')
                    result['models'][name] = {'finite': True, 'outputs': [list(a.shape) for a in arrays]}
                    del model, arrays; gc.collect()
            finally:
                os.chdir(previous_cwd)
        with tempfile.TemporaryDirectory(prefix='medical-runtime-probe-') as tmp:
            exercise(context, Path(tmp), checks)
        result['temporary_data_removed'] = not Path(tmp).exists()
        result['passed'] = result['temporary_data_removed']
    except Exception as exc:
        # Never serialize response bodies, tokens, passwords or child logs.
        result['error_type'] = type(exc).__name__
    result['checks'] = checks
    result['seconds'] = round(time.monotonic() - started, 3)
    print('MEDICAL_RUNTIME_PROBE=' + json.dumps(result, sort_keys=True), flush=True)
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
