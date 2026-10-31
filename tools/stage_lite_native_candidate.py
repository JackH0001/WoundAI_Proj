"""Stage only reviewed native service files; no deployment, secrets or data.

The explicit allowlist is intentionally separate from the legacy Lite image.
AST checks reject newly introduced local dependencies, never auto-copy them.
"""
import argparse
import ast
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from stage_lite_candidate import checked_source, digest, MODEL_SHA256

MODULES=tuple(('audit_chain_contract lite_account_deletion lite_account_identity '
    'lite_account_lifecycle lite_account_service_inventory lite_account_sessions '
    'lite_apple_account_events lite_apple_grants lite_apple_identity_provider lite_apple_oauth '
    'lite_attest_assertion lite_attest_budget lite_attest_enrollment lite_attest_http '
    'lite_attest_receipt lite_attest_registration lite_attest_request lite_attest_state '
    'lite_bucket_policy lite_deletion_identity lite_disposition_progress lite_fenced_manifest '
    'lite_fenced_objects lite_fenced_store lite_grant_key_custody lite_key_readiness lite_native_backend '
    'lite_native_inference lite_native_purchase lite_native_startup lite_privacy_state '
    'lite_raster_tools lite_reconsent lite_service_disposition lite_service_http lite_service_operations '
    'lite_service_recovery lite_service_release lite_service_subscription_links lite_subscription_authority '
    'lite_subscription_executor lite_subscription_migration lite_subscription_runtime '
    'lite_usage_cutover lite_usage_ledger lite_usage_rollout lite_store_locations model_preprocessing store_errors').split())
ASSETS=('certificates/Apple_App_Attestation_Root_CA.pem','certificates/AppleRootCA-G3.pem',
        'privacy/haarcascade_frontalface_default.xml','privacy/README.md')
PROFILE=('Dockerfile','requirements.in','requirements.lock','verify_native_package.py')


def validate_imports(root):
    folder=root/'Backend/Flask';allowed=set(MODULES)
    for name in MODULES:
        tree=ast.parse(checked_source(root,'Backend/Flask/'+name+'.py').read_text())
        for n in ast.walk(tree):
            names=([a.name.split('.')[0] for a in n.names] if isinstance(n,ast.Import)
                else [n.module.split('.')[0]] if isinstance(n,ast.ImportFrom) and n.module else [])
            for dependency in names:
                if (folder/(dependency+'.py')).is_file() and dependency not in allowed:
                    raise ValueError('native dependency outside allowlist: '+name+' -> '+dependency)
    if allowed & {'app','api_lite','api_flywheel','store'}:raise ValueError('legacy app excluded')


def stage(root,models,destination):
    root=Path(root).resolve();models=Path(models).resolve();destination=Path(destination).absolute()
    if destination.exists() or destination.is_symlink():raise ValueError('new output path required')
    if destination.resolve().is_relative_to(root) or destination.resolve().is_relative_to(models):
        raise ValueError('stage outside source/model directories')
    mapping={n+'.py':(root,'Backend/Flask/'+n+'.py') for n in MODULES}
    mapping.update({n:(root,'Backend/Flask/'+n) for n in ASSETS})
    mapping.update({n:(root,'Backend/LiteNative/'+n) for n in PROFILE})
    mapping['vendor/preprocessing.json']=(root,'engineering/phase0/preprocessing.json')
    mapping.update({'models/'+n:(models,n) for n in MODEL_SHA256})
    sources={n:checked_source(base,rel) for n,(base,rel) in mapping.items()}
    validate_imports(root)
    for n,h in MODEL_SHA256.items():
        if digest(sources['models/'+n])!=h:raise ValueError('model digest mismatch: '+n)
    destination.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.lite-native-stage-',dir=destination.parent) as temp:
        context=Path(temp)/'context';context.mkdir();files={}
        for n,p in sorted(sources.items()):
            out=context/n;out.parent.mkdir(parents=True,exist_ok=True);before=digest(p)
            shutil.copyfile(p,out)
            if digest(out)!=before or digest(p)!=before:raise ValueError('source changed while staging')
            files[n]=dict(sha256=before,bytes=out.stat().st_size)
        if any(digest(p)!=files[n]['sha256'] for n,p in sources.items()):raise ValueError('source changed')
        def git(*args):return subprocess.check_output(['git','-C',str(root),*args],text=True).strip()
        m=dict(schema='lite.native-build-context/1',git_head=git('rev-parse','HEAD'),
            working_tree_dirty=bool(git('status','--porcelain')),files=files,release_approved=False,
            scope='local staged source only; no container build or cloud authorization')
        (context/'build-context-manifest.json').write_text(json.dumps(m,sort_keys=True,indent=2)+'\n')
        context.rename(destination)
    return m


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('repo','models','output'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();m=stage(a.repo,a.models,a.output)
    print(json.dumps(dict(files=len(m['files']),dirty=m['working_tree_dirty'],release_approved=False)))
