"""Stage a clean committed Lite portal only. No credentials, cloud or deployment.

Read each allowlisted file from Git, compare to the working copy, and publish a
new minimal context atomically. Never recurse through the workspace to copy it.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from Backend.LiteReview.build_provenance import PAYLOAD, MANIFEST, digest, verify


def sources():
    result = {name: name for name in PAYLOAD}
    result['requirements.lock'] = 'Backend/LiteReview/requirements.lock'
    result['Dockerfile'] = 'Backend/LiteReview/Dockerfile'
    return result


def stage(root, destination):
    root = Path(root).resolve(strict=True)
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink() or destination.resolve().is_relative_to(root):
        raise ValueError('new output outside repository required')

    def git(*args):
        return subprocess.check_output(['git', '-C', str(root), *args])

    commit = git('rev-parse', 'HEAD').decode().strip()
    if git('status', '--porcelain'):
        raise ValueError('clean committed source required')
    # Pin to the captured commit, not a moving HEAD; then check again at publish.
    blobs = {}
    for name, relative in sources().items():
        path = root / relative
        if (not path.is_file() or any(p.is_symlink() for p in (path, *path.parents) if p != root)
                or not path.resolve().is_relative_to(root)):
            raise ValueError('missing or unsafe source: ' + relative)
        raw = git('show', commit + ':' + relative)
        if path.read_bytes() != raw:
            raise ValueError('working file differs from committed bytes: ' + relative)
        blobs[name] = raw
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.lite-review-stage-', dir=destination.parent) as temporary:
        context = Path(temporary) / 'context'
        context.mkdir()
        for name, raw in blobs.items():
            path = context / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            path.chmod(0o644)
        manifest = dict(schema='lite.review-build-context/1', source_commit=commit,
            files={n: dict(sha256=digest(context / n), bytes=len(blobs[n])) for n in PAYLOAD},
            recipe_sha256=digest(context / 'Dockerfile'))
        (context / MANIFEST).write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n')
        result = verify(context, staged=True)
        if git('rev-parse', 'HEAD').decode().strip() != commit or git('status', '--porcelain'):
            raise ValueError('source changed while staging')
        context.rename(destination)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(stage(args.repo, args.output)))
