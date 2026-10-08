"""Prepare a reviewable one-shot Linux build. Never calls gcloud or mutates cloud.

New build-only identity/bucket, no image push, runtime service, secret or public
endpoint. A human-approved plan must be revalidated before any listed operation.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile

PROJECT='woundai-jackh001'
NUMBER='421209514056'
REGION='asia-east1'
ACCOUNT='woundai-lite-build'
EMAIL=ACCOUNT+'@'+PROJECT+'.iam.gserviceaccount.com'
BUCKET='woundai-lite-build-'+NUMBER
ROLE='woundaiLiteBuildObjects'
PERMISSIONS=['storage.buckets.get','storage.objects.get','storage.objects.list','storage.objects.create','storage.objects.delete']
PYTHON_IMAGE='python:3.11.16-slim-bookworm@sha256:528257d48c1da0dcecc2e725d1ae34498d60c965f1241e39cd6a85a8859bdf84'


def sha(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def verify_context(context, expected):
    context=Path(context)
    if not re.fullmatch(r'[0-9a-f]{64}',expected):raise ValueError('exact manifest SHA-256 required')
    manifest=context/'build-context-manifest.json'
    if manifest.is_symlink() or sha(manifest)!=expected:raise ValueError('manifest mismatch')
    data=json.loads(manifest.read_text())
    if data.get('schema')!='lite.build-context/1' or not data.get('files'):raise ValueError('invalid manifest')
    expected_files=set(data['files'])|{'build-context-manifest.json'}
    actual=set()
    for path in context.rglob('*'):
        if path.is_symlink():raise ValueError('symlink in context')
        if path.is_file():actual.add(path.relative_to(context).as_posix())
    if actual!=expected_files:raise ValueError('unexpected or missing build input')
    for name,row in data['files'].items():
        if not isinstance(name,str) or PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts or '\\' in name:
            raise ValueError('unsafe manifest path')
        path=context/name
        if path.stat().st_size!=row['bytes'] or sha(path)!=row['sha256']:raise ValueError('source digest mismatch: '+name)
    return data


def check_ancestor_policy(document):
    # This only proves no direct/ambiguous ancestor grant in this fresh readback.
    # Resource policies, effective permissions and external impersonation are
    # separate checks; do not label this a full IAM proof.
    if not isinstance(document,list) or not document:raise ValueError('ancestor evidence missing')
    seen=False
    for row in document:
        if row.get('type')=='project' and str(row.get('id')) in (PROJECT,NUMBER):seen=True
        policy=row.get('policy')
        if not isinstance(policy,dict) or not isinstance(policy.get('bindings'),list):raise ValueError('incomplete IAM evidence')
        for binding in policy['bindings']:
            if not isinstance(binding.get('role'),str) or not isinstance(binding.get('members'),list) or not binding['members']:
                raise ValueError('invalid binding')
            for member in binding['members']:
                if not isinstance(member,str):raise ValueError('invalid principal')
                if member.lower()=='serviceaccount:'+EMAIL:raise ValueError('build identity has ancestor role')
                if not re.fullmatch(r'(?:user|serviceAccount):[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+',member):
                    raise ValueError('ambiguous ancestor principal')
    if not seen:raise ValueError('expected numeric project missing')
    return {'scope':'ancestor bindings only','direct_or_ambiguous_grants':False}


def generate(context,expected,probe,fixture,output):
    data=verify_context(context,expected);context=Path(context)
    probe,fixture,output=Path(probe),Path(fixture),Path(output).absolute()
    if any(p.is_symlink() or not p.is_file() for p in (probe,fixture)):raise ValueError('regular verification inputs required')
    if output.exists() or output.is_symlink():raise ValueError('output exists')
    if output.resolve().is_relative_to(context.resolve()):raise ValueError('output cannot be inside context')
    probe_hash,fixture_hash=sha(probe),sha(fixture)
    tag='woundai-lite-validation:'+expected[:16]
    verify_code=("import hashlib,json,pathlib; p=pathlib.Path('context'); m=p/'build-context-manifest.json'; "
        f"assert hashlib.sha256(m.read_bytes()).hexdigest()=='{expected}'; "
        "d=json.loads(m.read_text()); "
        "assert {str(f.relative_to(p)) for f in p.rglob('*') if f.is_file()}==set(d['files'])|{'build-context-manifest.json'}; "
        "assert not any(f.is_symlink() for f in p.rglob('*')); "
        "assert all(hashlib.sha256((p/n).read_bytes()).hexdigest()==r['sha256'] and (p/n).stat().st_size==r['bytes'] for n,r in d['files'].items()); "
        f"assert hashlib.sha256(pathlib.Path('probe.py').read_bytes()).hexdigest()=='{probe_hash}'; "
        f"assert hashlib.sha256(pathlib.Path('fixture.json').read_bytes()).hexdigest()=='{fixture_hash}'")
    config={'serviceAccount':f'projects/{PROJECT}/serviceAccounts/{EMAIL}',
        'logsBucket':'gs://'+BUCKET,'timeout':'1200s',
        'options':{'logging':'GCS_ONLY','machineType':'E2_STANDARD_2'},
        'tags':['lite-validation-no-deploy'],
        'steps':[
            {'name':PYTHON_IMAGE,'entrypoint':'python','args':['-c',verify_code]},
            {'name':'gcr.io/cloud-builders/docker','args':['build','--platform','linux/amd64','--tag',tag,'context']},
            {'name':'gcr.io/cloud-builders/docker','args':['run','--rm','--network=none','--read-only',
                '--tmpfs','/tmp:rw,nosuid,nodev,size=256m','--cap-drop=ALL','--security-opt=no-new-privileges',
                '--memory=4g','--cpus=2','--env','PYTHONDONTWRITEBYTECODE=1',
                '-v','/workspace/probe.py:/verification/probe.py:ro','-v','/workspace/fixture.json:/verification/fixture.json:ro',
                '--entrypoint','python',tag,'-B','/verification/probe.py','--fixture','/verification/fixture.json','--manifest-sha256',expected]},
        ]}
    role={'title':'WoundLite isolated build objects','description':'Read source and write logs only on the dedicated build bucket',
          'stage':'GA','includedPermissions':PERMISSIONS}
    # Structured argv, never eval/shell concatenation. Creation is a separate approval.
    commands=[
        ['gcloud','iam','service-accounts','create',ACCOUNT,'--project',PROJECT,'--display-name','WoundLite isolated build'],
        ['gcloud','storage','buckets','create','gs://'+BUCKET,'--project',PROJECT,'--location',REGION,'--uniform-bucket-level-access','--public-access-prevention','--soft-delete-duration=0'],
        ['gcloud','iam','roles','create',ROLE,'--project',PROJECT,'--file',str(output/'build-role.json')],
        ['gcloud','storage','buckets','add-iam-policy-binding','gs://'+BUCKET,'--project',PROJECT,'--member','serviceAccount:'+EMAIL,'--role',f'projects/{PROJECT}/roles/{ROLE}','--condition=None'],
    ]
    submit=['gcloud','builds','submit',str(output/'submission'),'--project',PROJECT,'--region',REGION,
        '--config',str(output/'submission/cloudbuild.json'),'--gcs-source-staging-dir','gs://'+BUCKET+'/source',
        '--ignore-file',str(output/'submission/.gcloudignore'),'--async','--format=json']
    plan={'schema':'lite.build-plan/1','scope':'one Linux container build and offline probe; no deploy or image push',
          'source_manifest_sha256':expected,'source_git_head':data['git_head'],'source_dirty':data['working_tree_dirty'],
          'probe_sha256':probe_hash,'fixture_sha256':fixture_hash,'cloudbuild':config,'provision_commands':commands,'submit_command':submit,
          'preconditions':['human approval for these named resources/IAM and one build','fresh project number and enabled APIs',
              'all three new names absent; do not reuse resources after ALREADY_EXISTS','fresh ancestors have no direct/ambiguous build-identity grant',
              'read back newly created identity, role and bucket IAM; exact scoped binding','effective denied access to production/demo secrets and data; UNKNOWN is failure',
              'verify all source and verification digests again immediately before upload'],
          'not_authorized':['cloud run deploy','image push','public invocation','runtime buckets','clinical secrets','project-level role binding'],
          'limits':{'build_timeout_seconds':1200,'machine':'E2_STANDARD_2','estimated_build_compute_usd_without_free_tier':0.12,
                    'cost_note':'20 minutes × listed $0.006/min; storage/network/log costs additional, not an enforced billing cap'},
          'rollback':['cancel only the returned build ID','remove only the exact new bucket role binding after evidence download',
                      'disable the dedicated build account; keep artifacts for review; no automatic bucket/role deletion']}
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.lite-build-plan-',dir=output.parent) as temp:
        folder=Path(temp)/'plan';submission=folder/'submission';submission.mkdir(parents=True)
        shutil.copytree(context,submission/'context');verify_context(submission/'context',expected)
        shutil.copyfile(probe,submission/'probe.py');shutil.copyfile(fixture,submission/'fixture.json')
        if sha(submission/'probe.py')!=probe_hash or sha(submission/'fixture.json')!=fixture_hash:raise ValueError('verification input changed')
        (submission/'.gcloudignore').write_text('*\n!context/\n!context/**\n!probe.py\n!fixture.json\n!cloudbuild.json\n!.gcloudignore\n')
        (submission/'cloudbuild.json').write_text(json.dumps(config,indent=2)+'\n')
        (folder/'build-role.json').write_text(json.dumps(role,indent=2)+'\n')
        (folder/'plan.json').write_text(json.dumps(plan,indent=2)+'\n')
        folder.rename(output)
    return plan


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ['context','probe','fixture','output']:p.add_argument('--'+key,type=Path,required=True)
    p.add_argument('--manifest-sha256',required=True)
    a=p.parse_args();plan=generate(a.context,a.manifest_sha256,a.probe,a.fixture,a.output)
    print(json.dumps({'plan':str(a.output/'plan.json'),'source_manifest_sha256':plan['source_manifest_sha256'],'cloud_changes':False}))

if __name__=='__main__':main()
