"""Fixed synthetic build preparation and strict readback; never deploys a service.

Provisioning is explicit. A partial provision must be inspected, not auto-retried.
Builds are submitted separately so an uncertain response cannot duplicate work.
"""
import argparse, hashlib, json, re, tempfile
from pathlib import Path
from provision_mmh_foundation import command, PROJECT, NUMBER, verify_bucket
from restrict_mmh_bucket_iam import bindings, TARGET

REGION='asia-east1'
ACCOUNT='woundai-medical-build'
EMAIL=ACCOUNT+'@'+PROJECT+'.iam.gserviceaccount.com'
MEMBER='serviceAccount:'+EMAIL
BUCKET=ACCOUNT+'-'+NUMBER
REPO=ACCOUNT
REPO_NAME=f'projects/{PROJECT}/locations/{REGION}/repositories/{REPO}'
PROOF=b'WoundAI medical build isolation smoke: synthetic text only.\n'


def exact_policy(row, expected):
    if not isinstance(row,dict):raise ValueError('missing policy')
    seen={}
    for b in row.get('bindings',[]):
        if set(b)!={'role','members'} or b['role'] in seen or not isinstance(b['members'],list):raise ValueError('ambiguous policy')
        seen[b['role']]=set(b['members'])
    if seen!=expected:raise ValueError('unexpected IAM grants')


def project_roles(row):
    if not row.get('etag'):raise ValueError('missing project etag')
    found=[]
    for b in row.get('bindings',[]):
        for m in b.get('members',[]):
            if not isinstance(m,str) or re.fullmatch(r'(?:user|serviceAccount):[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+',m) is None:
                raise ValueError('ambiguous project principal requires review')
            if m.lower()==MEMBER.lower():
                if b.get('condition'):raise ValueError('conditional build grant requires review')
                found.append(b['role'])
    return sorted(found)


def prepare(output, git_head):
    output=Path(output)
    if not re.fullmatch('[0-9a-f]{40}',git_head):raise ValueError('exact git commit required')
    if output.exists():raise ValueError('refuse existing output')
    output.mkdir(parents=True)
    image=f'{REGION}-docker.pkg.dev/{PROJECT}/{REPO}/smoke:{git_head[:12]}'
    (output/'proof.txt').write_bytes(PROOF)
    (output/'Dockerfile').write_text('FROM scratch\nCOPY proof.txt /proof.txt\n')
    sha=hashlib.sha256(PROOF).hexdigest()
    script=f"set -eu\nprintf '%s  proof.txt\\n' '{sha}' | sha256sum -c -\ndocker build -t '{image}' .\ncontainer=$(docker create '{image}' /unused)\ndocker cp \"$container:/proof.txt\" /workspace/readback.txt\ndocker rm \"$container\"\ncmp proof.txt readback.txt\necho MEDICAL_BUILD_SYNTHETIC_VERIFIED\n"
    config={'serviceAccount':f'projects/{PROJECT}/serviceAccounts/{EMAIL}',
            'timeout':'300s','options':{'logging':'CLOUD_LOGGING_ONLY','machineType':'E2_STANDARD_2'},
            'tags':['medical-build-isolation-smoke'],
            'steps':[{'name':'gcr.io/cloud-builders/docker','entrypoint':'sh','args':['-c',script]}],
            'images':[image]}
    (output/'cloudbuild.json').write_text(json.dumps(config,indent=2)+'\n')
    (output/'.gcloudignore').write_text('*\n!Dockerfile\n!proof.txt\n!cloudbuild.json\n!.gcloudignore\n')
    return {'git_head':git_head,'image':image,'proof_sha256':sha,'config':config,
            'source_files':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir()},
            'submit_argv':['gcloud','builds','submit',str(output),'--project='+PROJECT,'--region='+REGION,
                '--config='+str(output/'cloudbuild.json'),'--gcs-source-staging-dir=gs://'+BUCKET+'/source',
                '--ignore-file='+str(output/'.gcloudignore'),'--async','--format=json']}


def provision(run=command, apply=False):
    p=run(['projects','describe',PROJECT])
    if p.get('projectId')!=PROJECT or str(p.get('projectNumber'))!=NUMBER or p.get('parent') or p.get('lifecycleState')!='ACTIVE':raise ValueError('unexpected project or ancestors')
    active=run(['auth','list','--filter=status:ACTIVE'])
    if len(active)!=1 or active[0].get('account')!='jack.hou@gmail.com':raise ValueError('wrong operator')
    iam=run(['projects','get-iam-policy',PROJECT])
    if project_roles(iam):raise ValueError('build identity already has project roles')
    accounts=run(['iam','service-accounts','list']);buckets=run(['storage','buckets','list']);repos=run(['artifacts','repositories','list','--location='+REGION])
    if not all(isinstance(x,list) for x in (accounts,buckets,repos)):raise ValueError('inventory unavailable')
    if any(x.get('email')==EMAIL for x in accounts) or any(x.get('name')==BUCKET for x in buckets) or any(x.get('name')==REPO_NAME for x in repos):raise ValueError('existing or partial provision; inspect before resuming')
    result={'applied':False,'account':EMAIL,'bucket':BUCKET,'repository':REPO_NAME,'service_deployed':False}
    if not apply:return result
    run(['iam','service-accounts','create',ACCOUNT,'--display-name=WoundAI medical build only'])
    sa=run(['iam','service-accounts','describe',EMAIL])
    if sa.get('email')!=EMAIL or sa.get('projectId')!=PROJECT or sa.get('disabled'):raise ValueError('SA readback failed')
    run(['storage','buckets','create','gs://'+BUCKET,'--location='+REGION,'--default-storage-class=STANDARD','--uniform-bucket-level-access','--public-access-prevention','--soft-delete-duration=0'])
    verify_bucket(run(['storage','buckets','describe','gs://'+BUCKET,'--raw']),BUCKET)
    before=run(['storage','buckets','get-iam-policy','gs://'+BUCKET]);bindings(before)
    expected={**TARGET,'roles/storage.objectViewer':{MEMBER}}
    policy=dict(before,bindings=[{'role':r,'members':sorted(m)} for r,m in expected.items()])
    with tempfile.TemporaryDirectory() as td:
        f=Path(td)/'policy.json';f.write_text(json.dumps(policy))
        run(['storage','buckets','set-iam-policy','gs://'+BUCKET,str(f),'--etag='+before['etag']])
    exact_policy(run(['storage','buckets','get-iam-policy','gs://'+BUCKET]),expected)
    run(['artifacts','repositories','create',REPO,'--location='+REGION,'--repository-format=docker','--description=Isolated medical build outputs; no runtime deployment'])
    repo=run(['artifacts','repositories','describe',REPO,'--location='+REGION])
    if repo.get('name')!=REPO_NAME or repo.get('format')!='DOCKER':raise ValueError('repo readback failed')
    exact_policy(run(['artifacts','repositories','get-iam-policy',REPO,'--location='+REGION]),{})
    run(['artifacts','repositories','add-iam-policy-binding',REPO,'--location='+REGION,'--member='+MEMBER,'--role=roles/artifactregistry.writer','--condition=None'])
    exact_policy(run(['artifacts','repositories','get-iam-policy',REPO,'--location='+REGION]),{'roles/artifactregistry.writer':{MEMBER}})
    # Built-in command uses optimistic IAM concurrency; never write a stale whole policy.
    run(['projects','add-iam-policy-binding',PROJECT,'--member='+MEMBER,'--role=roles/logging.logWriter','--condition=None'])
    if project_roles(run(['projects','get-iam-policy',PROJECT]))!=['roles/logging.logWriter']:raise ValueError('unexpected ancestor grant')
    result['applied']=True
    result['effective_iam_verified']=False
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--apply',action='store_true');p.add_argument('--report',type=Path,required=True)
    args=p.parse_args();result=provision(apply=args.apply);args.report.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
