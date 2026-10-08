"""Prepare an allowlisted medical image build, or resolve verified cloud evidence.

Preparation never submits. Resolution is read-only and prints only an immutable
image reference. Neither operation deploys, grants IAM or reads secret values.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
from medical_build_smoke import PROJECT, REGION, EMAIL, BUCKET, REPO
from stage_lite_candidate import stage
from plan_lite_cloud_build import PYTHON_IMAGE, verify_context

DOCKER = 'gcr.io/cloud-builders/docker@sha256:40c2fb4fcd0ad51376eef166c2e7b2b40a3508d5776e2bf33db3783ab39d0f2e'
IMAGE_ROOT = f'{REGION}-docker.pkg.dev/{PROJECT}/{REPO}/medical'


def exact_hex(value, length):
    if not isinstance(value, str) or re.fullmatch('[0-9a-f]{'+str(length)+'}', value) is None:
        raise ValueError('exact source SHA required')
    return value


def configuration(commit, manifest):
    exact_hex(commit, 40); exact_hex(manifest, 64)
    image = IMAGE_ROOT + ':' + commit + '-' + manifest[:16]
    verify = ("import hashlib,json,pathlib; p=pathlib.Path('context'); "
              "m=p/'build-context-manifest.json'; "
              f"assert hashlib.sha256(m.read_bytes()).hexdigest()=='{manifest}'; "
              "d=json.loads(m.read_text()); "
              f"assert d['git_head']=='{commit}' and d['working_tree_dirty'] is False; "
              "assert {f.relative_to(p).as_posix() for f in p.rglob('*') if f.is_file()}==set(d['files'])|{'build-context-manifest.json'}; "
              "assert not any(f.is_symlink() for f in p.rglob('*')); "
              "assert all(hashlib.sha256((p/n).read_bytes()).hexdigest()==r['sha256'] and (p/n).stat().st_size==r['bytes'] for n,r in d['files'].items())")
    return {'serviceAccount': f'projects/{PROJECT}/serviceAccounts/{EMAIL}',
            'timeout': '1200s', 'options': {'logging': 'CLOUD_LOGGING_ONLY'},
            'tags': ['medical-reviewed-image'],
            'steps': [
                {'name': PYTHON_IMAGE, 'entrypoint': 'python', 'args': ['-c', verify]},
                {'name': DOCKER, 'args': ['build', '--platform', 'linux/amd64', '--tag', image, 'context']},
            ], 'images': [image]}


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def prepare(root, models, output, commit):
    root = Path(root).resolve(); output = Path(output).absolute()
    exact_hex(commit, 40)
    if git(root, 'rev-parse', 'HEAD') != commit or git(root, 'status', '--porcelain', '--untracked-files=all'):
        raise ValueError('reviewed clean source commit required')
    if output.exists() or output.is_symlink() or output.resolve().is_relative_to(root):
        raise ValueError('new output outside repository required')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent, prefix='.medical-build-') as td:
        folder = Path(td)/'plan'; submission = folder/'submission'; submission.mkdir(parents=True)
        context = submission/'context'
        source = stage(root, models, context)  # exact file allowlist and pinned models; no runtime data
        if source['git_head'] != commit or source['working_tree_dirty']:
            raise ValueError('source changed during packaging')
        manifest = hashlib.sha256((context/'build-context-manifest.json').read_bytes()).hexdigest()
        verify_context(context, manifest)
        config = configuration(commit, manifest)
        (submission/'cloudbuild.json').write_text(json.dumps(config, indent=2)+'\n')
        (submission/'.gcloudignore').write_text('*\n!context/\n!context/**\n!cloudbuild.json\n!.gcloudignore\n')
        plan = {'schema': 'medical.build-plan/1', 'git_commit': commit, 'manifest_sha256': manifest,
                'submitted': False, 'deployed': False, 'config': config,
                'submit_argv': ['gcloud', 'builds', 'submit', str(output/'submission'),
                    '--project='+PROJECT, '--region='+REGION,
                    '--config='+str(output/'submission/cloudbuild.json'),
                    '--gcs-source-staging-dir=gs://'+BUCKET+'/source',
                    '--ignore-file='+str(output/'submission/.gcloudignore'), '--async', '--format=json']}
        (folder/'plan.json').write_text(json.dumps(plan, indent=2)+'\n')
        if git(root, 'rev-parse', 'HEAD') != commit or git(root, 'status', '--porcelain', '--untracked-files=all'):
            raise ValueError('source changed before finalizing plan')
        folder.rename(output)
    return plan


def validate_build(build, build_id, commit, manifest):
    expected = configuration(commit, manifest)
    if (build.get('id') != build_id or build.get('projectId') != PROJECT or
            build.get('status') != 'SUCCESS' or build.get('serviceAccount') != expected['serviceAccount']):
        raise ValueError('build identity/status mismatch')
    for key in ('timeout', 'images', 'tags'):
        if build.get(key) != expected[key]: raise ValueError('unexpected build '+key)
    if any(build.get(k) for k in ('secrets', 'availableSecrets', 'substitutions', 'logsBucket')):
        raise ValueError('unexpected build inputs')
    options = dict(build.get('options', {}))
    # Cloud Build adds these server fields to default-pool builds.
    if options.pop('pool', {}) != {}: raise ValueError('unexpected worker pool')
    options.pop('workerRelease', None)
    if options != expected['options']: raise ValueError('unexpected build options')
    actual_steps = build.get('steps', [])
    if len(actual_steps) != len(expected['steps']): raise ValueError('unexpected build steps')
    for actual, wanted in zip(actual_steps, expected['steps']):
        actual = dict(actual)
        for key in ('timing', 'pullTiming', 'status'): actual.pop(key, None)
        if actual != wanted: raise ValueError('build recipe mismatch')
    source = build.get('source', {})
    storage = source.get('storageSource', {})
    if (set(source) != {'storageSource'} or storage.get('bucket') != BUCKET or
            not re.fullmatch(r'source/[A-Za-z0-9._-]+\.tgz', str(storage.get('object', ''))) or
            not re.fullmatch('[1-9][0-9]*', str(storage.get('generation', '')))):
        raise ValueError('unexpected build source')
    if build.get('sourceProvenance', {}).get('resolvedStorageSource') != storage:
        raise ValueError('source generation mismatch')
    images = build.get('results', {}).get('images', [])
    if len(images) != 1 or images[0].get('name') != expected['images'][0]:
        raise ValueError('missing or unexpected image result')
    digest = images[0].get('digest', '')
    if re.fullmatch(r'sha256:[0-9a-f]{64}', digest) is None: raise ValueError('immutable image digest required')
    return IMAGE_ROOT+'@'+digest


def cloud(args):
    result = subprocess.run(['gcloud', *args, '--project='+PROJECT, '--format=json'],
                            capture_output=True, text=True)
    if result.returncode: raise ValueError('cloud evidence unavailable; command exit '+str(result.returncode))
    return json.loads(result.stdout)


def resolve(build_id, commit, manifest, project, region, run=cloud):
    if project != PROJECT or region != REGION: raise ValueError('build resources are configured only for woundai-jackh001 / asia-east1')
    if re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', build_id) is None:
        raise ValueError('exact build UUID required')
    configuration(commit, manifest)  # fail before any cloud request
    build = run(['builds', 'describe', build_id, '--region='+REGION])
    image = validate_build(build, build_id, commit, manifest)
    artifact = run(['artifacts', 'docker', 'images', 'describe', image])
    summary = artifact.get('image_summary', {})
    if summary.get('fully_qualified_digest') != image or summary.get('digest') != image.split('@')[1]:
        raise ValueError('artifact digest readback mismatch')
    return image


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); sub = p.add_subparsers(dest='mode', required=True)
    a = sub.add_parser('prepare')
    for name in ('repo', 'models', 'output', 'git-commit'): a.add_argument('--'+name, required=True)
    a = sub.add_parser('resolve')
    for name in ('build-id', 'git-commit', 'manifest-sha256', 'project', 'region'): a.add_argument('--'+name, required=True)
    args = p.parse_args()
    if args.mode == 'prepare': print(json.dumps(prepare(args.repo, args.models, args.output, args.git_commit), indent=2))
    else: print(resolve(args.build_id, args.git_commit, args.manifest_sha256, args.project, args.region))
