"""Image build-time import/hash gate. Does not connect or activate services."""
import hashlib
import importlib
import json
from pathlib import Path
import sys


def verify(root):
    root=Path(root).resolve()
    manifest=json.loads((root/'build-context-manifest.json').read_text())
    if manifest['schema']!='lite.native-build-context/1':raise ValueError('wrong context')
    expected=set(manifest['files'])|{'build-context-manifest.json'}
    actual={p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}
    if expected!=actual:raise ValueError('unexpected/missing packaged files')
    for name,meta in manifest['files'].items():
        p=root/name
        if p.is_symlink() or not p.resolve().is_relative_to(root):raise ValueError('unsafe file')
        with p.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
        if digest!=meta['sha256'] or p.stat().st_size!=meta['bytes']:raise ValueError('package changed')
    sys.path.insert(0,str(root))
    importlib.import_module('lite_native_startup')
    if any(n in sys.modules for n in ('app','api_lite','api_flywheel','store')):
        raise ValueError('legacy application imported')
    return dict(passed=True,files=len(manifest['files']),application_started=False,cloud_access=False)


if __name__=='__main__':print(json.dumps(verify(Path(__file__).parent)))
