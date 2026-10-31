"""Exact private worker context; no build, deployment, model, key or user data."""
import argparse
import ast
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from stage_lite_candidate import checked_source, digest

MODULES = tuple(('audit_chain_contract lite_account_deletion lite_account_identity '
    'lite_account_lifecycle lite_account_service_inventory lite_account_sessions '
    'lite_apple_account_events lite_apple_grants lite_apple_oauth lite_attest_assertion '
    'lite_attest_request lite_attest_state lite_bucket_policy lite_deletion_identity '
    'lite_disposition_progress lite_grant_key_custody lite_key_provisioning lite_key_readiness '
    'lite_key_startup lite_raster_tools lite_service_disposition lite_service_operations '
    'lite_service_recovery lite_service_release lite_service_subscription_links '
    'lite_store_locations lite_subscription_authority lite_subscription_executor '
    'lite_subscription_migration lite_subscription_runtime lite_usage_cutover '
    'lite_usage_ledger lite_usage_rollout').split())
PROFILE = ('Dockerfile', 'requirements.in', 'requirements.lock', 'verify_key_package.py')


def validate_imports(root):
    folder = root/'Backend/Flask'; allowed = set(MODULES)
    for name in MODULES:
        for n in ast.walk(ast.parse(checked_source(root, 'Backend/Flask/'+name+'.py').read_text())):
            deps = ([a.name.split('.')[0] for a in n.names] if isinstance(n, ast.Import)
                else [n.module.split('.')[0]] if isinstance(n, ast.ImportFrom) and n.module else [])
            for dep in deps:
                if (folder/(dep+'.py')).is_file() and dep not in allowed:
                    raise ValueError('worker dependency outside allowlist: '+name+' -> '+dep)


def stage(root, destination):
    root = Path(root).resolve(); destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink() or destination.resolve().is_relative_to(root):
        raise ValueError('new output outside repository required')
    mapping = {n+'.py': 'Backend/Flask/'+n+'.py' for n in MODULES}
    mapping.update({n: 'Backend/LiteKeyWorker/'+n for n in PROFILE})
    sources = {n: checked_source(root, path) for n, path in mapping.items()}
    validate_imports(root); destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.lite-key-stage-', dir=destination.parent) as tmp:
        context = Path(tmp)/'context'; context.mkdir(); files = {}
        for name, p in sorted(sources.items()):
            out = context/name; before = digest(p); shutil.copyfile(p, out)
            if digest(out) != before or digest(p) != before: raise ValueError('source changed while staging')
            files[name] = dict(sha256=before, bytes=out.stat().st_size)
        if any(digest(p) != files[n]['sha256'] for n, p in sources.items()): raise ValueError('source changed')
        def git(*args): return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()
        manifest = dict(schema='lite.key-build-context/1', git_head=git('rev-parse', 'HEAD'),
            working_tree_dirty=bool(git('status', '--porcelain')), files=files, release_approved=False,
            scope='private worker source only; no container or cloud acceptance')
        (context/'build-context-manifest.json').write_text(json.dumps(manifest, sort_keys=True, indent=2)+'\n')
        context.rename(destination)
    return manifest


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', type=Path, required=True); p.add_argument('--output', type=Path, required=True)
    a = p.parse_args(); m = stage(a.repo, a.output)
    print(json.dumps(dict(files=len(m['files']), dirty=m['working_tree_dirty'], release_approved=False)))
