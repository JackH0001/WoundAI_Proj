"""Offline package verification; hashes identify bytes, not cloud authorization."""
import hashlib
import json
from pathlib import Path
import re

PAYLOAD = ('requirements.lock', 'Backend/LiteReview/__init__.py',
    'Backend/LiteReview/contracts.py', 'Backend/LiteReview/auth.py',
    'Backend/LiteReview/reader.py', 'Backend/LiteReview/storage.py',
    'Backend/LiteReview/web.py', 'Backend/LiteReview/runtime.py',
    'Backend/LiteReview/build_provenance.py',
    'Backend/LiteReview/templates/error.html', 'Backend/LiteReview/templates/review.html',
    'Backend/LiteReview/static/review.css')
MANIFEST = 'build-context-manifest.json'


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verify(root, *, staged=False):
    root = Path(root).resolve(strict=True)
    paths = list(root.rglob('*'))
    if any(p.is_symlink() or not (p.is_dir() or p.is_file()) for p in paths):
        raise ValueError('unsafe package entry')
    expected = set(PAYLOAD) | {MANIFEST} | ({'Dockerfile'} if staged else set())
    if {p.relative_to(root).as_posix() for p in paths if p.is_file()} != expected:
        raise ValueError('unexpected or missing package file')
    manifest = json.loads((root / MANIFEST).read_bytes())
    if (set(manifest) != {'schema', 'source_commit', 'files', 'recipe_sha256'} or
            manifest['schema'] != 'lite.review-build-context/1' or
            not isinstance(manifest['source_commit'], str) or
            not re.fullmatch('[0-9a-f]{40}', manifest['source_commit']) or
            not isinstance(manifest['recipe_sha256'], str) or
            not re.fullmatch('[0-9a-f]{64}', manifest['recipe_sha256']) or
            set(manifest['files']) != set(PAYLOAD)):
        raise ValueError('invalid fixed-source manifest')
    for name in PAYLOAD:
        meta = manifest['files'][name]
        if (set(meta) != {'sha256', 'bytes'} or type(meta['bytes']) is not int or
                meta['bytes'] != (root / name).stat().st_size or
                meta['sha256'] != digest(root / name)):
            raise ValueError('packaged source mismatch: ' + name)
    if staged and digest(root / 'Dockerfile') != manifest['recipe_sha256']:
        raise ValueError('build recipe mismatch')
    return dict(source_commit=manifest['source_commit'], files=len(PAYLOAD),
                manifest_sha256=digest(root / MANIFEST), recipe_sha256=manifest['recipe_sha256'])


if __name__ == '__main__':
    print(json.dumps(verify(Path(__file__).resolve().parents[2])))
