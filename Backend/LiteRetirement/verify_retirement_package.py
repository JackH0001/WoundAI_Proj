"""Exact package integrity and isolated imports. Never acquires credentials."""
import hashlib
import importlib
import json
from pathlib import Path
import socket
import sys
from unittest.mock import patch

FORBIDDEN = ('app', 'api_lite', 'api_flywheel', 'store', 'lite_native_backend',
    'lite_native_startup', 'lite_native_inference', 'lite_disposition_startup',
    'lite_key_startup', 'onnxruntime', 'torch', 'gunicorn')


def verify(root):
    root = Path(root).resolve()
    manifest_path = root/'build-context-manifest.json'
    if manifest_path.is_symlink(): raise ValueError('unsafe manifest')
    manifest = json.loads(manifest_path.read_text())
    if manifest['schema'] != 'lite.retirement-build-context/1': raise ValueError('wrong context')
    if any(p.is_symlink() for p in root.rglob('*')): raise ValueError('unsafe packaged link')
    expected = set(manifest['files']) | {'build-context-manifest.json'}
    actual = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}
    if expected != actual: raise ValueError('unexpected/missing packaged files')
    for name, meta in manifest['files'].items():
        p = root/name
        if not p.resolve().is_relative_to(root): raise ValueError('unsafe file')
        with p.open('rb') as f: digest = hashlib.file_digest(f, 'sha256').hexdigest()
        if digest != meta['sha256'] or p.stat().st_size != meta['bytes']: raise ValueError('package changed')
    sys.path.insert(0, str(root))
    with patch.object(socket.socket, 'connect', side_effect=AssertionError('import network forbidden')), \
         patch.object(socket.socket, 'connect_ex', side_effect=AssertionError('import network forbidden')):
        module = importlib.import_module('lite_shared_retirement_runner')
    if Path(module.__file__).resolve() != root/'lite_shared_retirement_runner.py':
        raise ValueError('runner imported outside package')
    if any(n in sys.modules for n in FORBIDDEN): raise ValueError('web/model startup imported')
    return dict(passed=True, files=len(manifest['files']), application_started=False,
        cloud_access=False, models_packaged=False, credential_acquisition=False)


if __name__ == '__main__': print(json.dumps(verify(Path(__file__).parent)))
