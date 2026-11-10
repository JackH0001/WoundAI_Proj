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

MODULES = ('audit_chain_contract', 'lite_account_deletion', 'lite_account_identity',
    'lite_account_lifecycle', 'lite_account_service_inventory', 'lite_account_sessions',
    'lite_apple_account_events', 'lite_apple_grants', 'lite_apple_oauth',
    'lite_attest_assertion', 'lite_attest_request', 'lite_attest_state', 'lite_bucket_policy',
    'lite_deletion_identity', 'lite_disposition_bootstrap', 'lite_disposition_context',
    'lite_disposition_progress', 'lite_grant_key_custody', 'lite_key_readiness',
    'lite_native_schema', 'lite_provider_budget', 'lite_provider_transport', 'lite_raster_tools',
    'lite_result_contract', 'lite_rotating_grant_keys', 'lite_service_disposition',
    'lite_service_operations', 'lite_service_recovery', 'lite_service_release',
    'lite_service_subscription_links', 'lite_store_locations', 'lite_subscription_authority',
    'lite_subscription_executor', 'lite_subscription_migration', 'lite_subscription_runtime',
    'lite_usage_cutover', 'lite_usage_ledger', 'lite_usage_rollout')
TOOLS = ('lite_fresh_bootstrap', 'lite_fresh_bootstrap_factory', 'lite_fresh_bootstrap_runner')
PROFILE = ('Dockerfile', 'verify_bootstrap_package.py')
# Reuse the reviewed, hash-pinned worker lock. The runner needs no inference runtime.
DEPENDENCIES = ('requirements.in', 'requirements.lock')


def mapping():
    result = {n+'.py': 'Backend/Flask/'+n+'.py' for n in MODULES}
    result.update({n+'.py': 'tools/'+n+'.py' for n in TOOLS})
    result.update({n: 'Backend/LiteBootstrap/'+n for n in PROFILE})
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
                    raise ValueError('bootstrap dependency outside allowlist: '+relative+' -> '+dep)


def stage(root, destination):
    root = Path(root).resolve(); destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink() or destination.resolve().is_relative_to(root):
        raise ValueError('new output outside repository required')
    sources = {n: checked_source(root, p) for n, p in mapping().items()}
    validate_imports(root); destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.lite-bootstrap-stage-', dir=destination.parent) as tmp:
        context = Path(tmp)/'context'; context.mkdir(); files = {}
        for name, p in sorted(sources.items()):
            out = context/name; before = digest(p); shutil.copyfile(p, out)
            if digest(out) != before or digest(p) != before: raise ValueError('source changed while staging')
            files[name] = dict(sha256=before, bytes=out.stat().st_size)
        if any(digest(p) != files[n]['sha256'] for n, p in sources.items()): raise ValueError('source changed')
        def git(*args): return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()
        manifest = dict(schema='lite.bootstrap-build-context/1', git_head=git('rev-parse', 'HEAD'),
            working_tree_dirty=bool(git('status', '--porcelain')), files=files, origins=mapping(),
            release_approved=False, scope='private one-shot runner; no cloud acceptance')
        (context/'build-context-manifest.json').write_text(json.dumps(manifest, sort_keys=True, indent=2)+'\n')
        context.rename(destination)
    return manifest


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', type=Path, required=True); p.add_argument('--output', type=Path, required=True)
    a = p.parse_args(); m = stage(a.repo, a.output)
    print(json.dumps(dict(files=len(m['files']), dirty=m['working_tree_dirty'], release_approved=False)))
