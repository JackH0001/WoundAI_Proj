"""Stage an explicit private one-shot runner, with no models or credentials.

This creates local files only. No build, registry push, deployment or activation.
Dependency discovery validates the fixed list; it never expands the payload.
"""
import argparse
import ast
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from stage_lite_candidate import checked_source, digest

from stage_lite_bootstrap_candidate import MODULES as BASE_MODULES, TOOLS as BASE_TOOLS
MODULES = BASE_MODULES + ('lite_shared_key_inventory', 'lite_shared_key_retirement',
    'lite_key_lifecycle_dispatch', 'lite_key_lifecycle_transport')
TOOLS = BASE_TOOLS + ('lite_shared_retirement_runner',)
PROFILE = ('Dockerfile', 'verify_retirement_package.py')
# Reuse the reviewed, hash-pinned worker lock. The runner needs no inference runtime.
DEPENDENCIES = ('requirements.in', 'requirements.lock')


def mapping():
    result = {n+'.py': 'Backend/Flask/'+n+'.py' for n in MODULES}
    result.update({n+'.py': 'tools/'+n+'.py' for n in TOOLS})
    result.update({n: 'Backend/LiteRetirement/'+n for n in PROFILE})
    result.update({n: 'Backend/LiteDisposition/'+n for n in DEPENDENCIES})
    return result


def validate_imports(root):
    allowed = set(MODULES) | set(TOOLS)
    for relative in mapping().values():
        if not relative.endswith('.py'): continue
        tree = ast.parse(checked_source(root, relative).read_text())
        for node in ast.walk(tree):
            deps = ([a.name.split('.')[0] for a in node.names] if isinstance(node, ast.Import)
                else [node.module.split('.')[0]] if isinstance(node, ast.ImportFrom) and node.module else [])
            for dep in deps:
                if any((root/folder/(dep+'.py')).is_file() for folder in ('Backend/Flask', 'tools')) and dep not in allowed:
                    raise ValueError('retirement dependency outside allowlist: '+relative+' -> '+dep)


def stage(root, destination):
    root = Path(root).resolve(); destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink() or destination.resolve().is_relative_to(root):
        raise ValueError('new output outside repository required')
    sources = {n: checked_source(root, p) for n, p in mapping().items()}
    validate_imports(root); destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.lite-retirement-stage-', dir=destination.parent) as tmp:
        context = Path(tmp)/'context'; context.mkdir(); files = {}
        for name, p in sorted(sources.items()):
            out = context/name; before = digest(p); shutil.copyfile(p, out)
            if digest(out) != before or digest(p) != before: raise ValueError('source changed while staging')
            files[name] = dict(sha256=before, bytes=out.stat().st_size)
        if any(digest(p) != files[n]['sha256'] for n, p in sources.items()): raise ValueError('source changed')
        def git(*args): return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()
        manifest = dict(schema='lite.retirement-build-context/1', git_head=git('rev-parse', 'HEAD'),
            working_tree_dirty=bool(git('status', '--porcelain')), files=files, origins=mapping(),
            release_approved=False, scope='private shared-key retirement; no cloud acceptance')
        (context/'build-context-manifest.json').write_text(json.dumps(manifest, sort_keys=True, indent=2)+'\n')
        context.rename(destination)
    return manifest


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', type=Path, required=True); p.add_argument('--output', type=Path, required=True)
    a = p.parse_args(); m = stage(a.repo, a.output)
    print(json.dumps(dict(files=len(m['files']), dirty=m['working_tree_dirty'], release_approved=False)))
