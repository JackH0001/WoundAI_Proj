"""Offline, source-bound Lite candidate deployment plan; never calls cloud APIs.

Uses a new private service, separate mutable media/security buckets and salt.
The build-only approval from an earlier plan cannot authorize these resources.
An operator must verify effective IAM, bind the resulting image digest, and pass
private runtime checks before separately opening access for controlled TestFlight.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile

import plan_lite_cloud_build as build

PROJECT, NUMBER, REGION = build.PROJECT, build.NUMBER, build.REGION
SERVICE = 'woundai-lite-candidate'
RUNTIME = 'woundai-lite-runtime@' + PROJECT + '.iam.gserviceaccount.com'
REPOSITORY = 'woundai-lite-candidate'
IMAGE = REGION + '-docker.pkg.dev/' + PROJECT + '/' + REPOSITORY + '/backend'
MEDIA = 'woundai-lite-media-' + NUMBER
SECURITY = 'woundai-lite-security-' + NUMBER
SECRET = 'woundai-lite-ip-salt'
APP_ID = 'LY2F24ZM68.com.woundai.lite'
AUDIENCE = 'woundlite-research-v1'


def runtime_environment(version, source_sha):
    if type(version) is not str or re.fullmatch(r'[1-9][0-9]{0,8}', version) is None:
        raise ValueError('one explicit numeric candidate build version required')
    if type(source_sha) is not str or re.fullmatch(r'[0-9a-f]{64}', source_sha) is None:
        raise ValueError('source manifest digest required')
    return {
        'WOUNDAI_SERVICE_PROFILE': 'lite', 'WOUNDAI_ENABLE_LITE_API': '1',
        'WOUNDAI_STORE': 'gcs', 'WOUNDAI_GCS_BUCKET': MEDIA,
        'WOUNDAI_GCS_PREFIX': 'lite-public-v1', 'WOUNDAI_RUNTIME_DIR': '/tmp/woundlite',
        'WOUNDAI_LITE_SECURITY_PROJECT': PROJECT, 'WOUNDAI_LITE_SECURITY_PROJECT_NUMBER': NUMBER,
        'WOUNDAI_LITE_SECURITY_BUCKET': SECURITY, 'WOUNDAI_LITE_ATTEST_APP_ID': APP_ID,
        'WOUNDAI_LITE_ATTEST_AUDIENCE': AUDIENCE, 'WOUNDAI_LITE_ATTEST_VERSIONS': version,
        'WOUNDAI_LITE_BUDGET_MINUTE': '60', 'WOUNDAI_LITE_BUDGET_DAY': '2000',
        'LITE_FACE_REJECT': '1', 'LITE_LIMIT_ANON': '5', 'LITE_LIMIT_ATTEMPT_ANON': '30',
        'LITE_LIMIT_ANNOTATION_ANON': '30', 'LITE_LIMIT_IP': '200',
        'SOURCE_MANIFEST_SHA256': source_sha, 'PYTHONDONTWRITEBYTECODE': '1',
    }


def bucket_request(name):
    if name not in (MEDIA, SECURITY): raise ValueError('not a candidate data bucket')
    return {'name': name, 'location': 'ASIA-EAST1', 'storageClass': 'STANDARD',
            'iamConfiguration': {'uniformBucketLevelAccess': {'enabled': True},
                                 'publicAccessPrevention': 'enforced'},
            'softDeletePolicy': {'retentionDurationSeconds': '0'},
            'versioning': {'enabled': False}, 'lifecycle': {'rule': []},
            'defaultEventBasedHold': False}


def role(name, listing):
    permissions = ['storage.buckets.get', 'storage.objects.get', 'storage.objects.create', 'storage.objects.delete']
    if listing: permissions.append('storage.objects.list')
    return {'title': name, 'description': 'Lite candidate scoped bucket access; no IAM or policy edits',
            'stage': 'GA', 'includedPermissions': permissions}


def deploy_argv(image_digest, environment_path):
    if type(image_digest) is not str or re.fullmatch(r'sha256:[0-9a-f]{64}', image_digest) is None:
        raise ValueError('immutable Artifact Registry image digest required; no tags')
    return ['gcloud', 'run', 'deploy', SERVICE, '--project', PROJECT, '--region', REGION,
            '--image', IMAGE + '@' + image_digest, '--service-account', RUNTIME,
            '--env-vars-file', str(environment_path), '--set-secrets', 'LITE_IP_SALT='+SECRET+':1',
            '--cpu', '2', '--memory', '4Gi', '--concurrency', '1', '--timeout', '120',
            '--min', '0', '--min-instances', '0', '--max-instances', '1',
            '--cpu-throttling', '--no-cpu-boost', '--execution-environment', 'gen2',
            '--port', '8080', '--ingress', 'all', '--no-allow-unauthenticated', '--invoker-iam-check',
            '--quiet', '--format=json']


def generate(context, expected, probe, fixture, version, output):
    environment = runtime_environment(version, expected)
    data = build.verify_context(context, expected)
    output = Path(output).absolute()
    if output.exists() or output.is_symlink() or output.resolve().is_relative_to(Path(context).resolve()):
        raise ValueError('new output outside context required')
    tag = IMAGE + ':source-' + expected[:16]
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.lite-deploy-plan-', dir=output.parent) as td:
        folder = Path(td) / 'candidate'; folder.mkdir()
        initial = build.generate(context, expected, probe, fixture, folder/'build-only')
        config = copy.deepcopy(initial['cloudbuild'])
        old_tag = config['steps'][1]['args'][config['steps'][1]['args'].index('--tag')+1]
        for step in config['steps']:
            step['args'] = [tag if x == old_tag else x for x in step['args']]
        # Cloud Build publishes these images only after every step succeeds.
        config['images'] = [tag]; config['tags'] = ['lite-candidate-private-deploy-preparation']
        submission = folder/'submission'; (folder/'build-only/submission').rename(submission)
        shutil.rmtree(folder/'build-only')
        (submission/'cloudbuild.json').write_text(json.dumps(config, indent=2)+'\n')
        roles = {'woundaiLiteMediaObjects': role('WoundLite media objects', True),
                 'woundaiLiteSecurityObjects': role('WoundLite security state', False)}
        documents = {'runtime-env.json': environment, 'media-bucket.json': bucket_request(MEDIA),
                     'security-bucket.json': bucket_request(SECURITY), 'cloudbuild.json': config}
        documents.update({name+'.json': value for name,value in roles.items()})
        for name,value in documents.items(): (folder/name).write_text(json.dumps(value,indent=2)+'\n')
        plan = {
            'schema': 'lite.candidate-deploy-plan/1', 'project': PROJECT, 'project_number': NUMBER, 'region': REGION,
            'scope': 'isolated synthetic-data candidate, initially private IAM; not public research release',
            'source_manifest_sha256': expected, 'source_git_head': data['git_head'],
            'source_dirty': data['working_tree_dirty'], 'probe_sha256': initial['probe_sha256'],
            'fixture_sha256': initial['fixture_sha256'], 'candidate_build_version': version,
            'resources': {'service': SERVICE, 'runtime_account': RUNTIME, 'repository': REPOSITORY,
                          'media_bucket': MEDIA, 'security_bucket': SECURITY, 'ip_salt_secret': SECRET},
            'build': {'reuse_disabled_account': build.EMAIL, 'existing_build_bucket': build.BUCKET,
                      'image_tag': tag, 'one_build_timeout_seconds': 1200,
                      'source_staging_prefix': 'gs://'+build.BUCKET+'/source/lite-candidate-'+expected[:16]},
            'runtime': {'cpu': 2, 'memory_gib': 4, 'minimum_instances': 0, 'maximum_instances_per_revision': 1,
                        'concurrency': 1, 'billing': 'request-based', 'production_attest_only': True,
                        'app_id': APP_ID, 'audience': AUDIENCE, 'salt_version': '1', 'iam_invoker_required': True},
            'resource_bindings': [
                {'resource': MEDIA, 'principal': RUNTIME, 'role': 'projects/'+PROJECT+'/roles/woundaiLiteMediaObjects'},
                {'resource': SECURITY, 'principal': RUNTIME, 'role': 'projects/'+PROJECT+'/roles/woundaiLiteSecurityObjects'},
                {'resource': 'projects/'+NUMBER+'/secrets/'+SECRET, 'principal': RUNTIME, 'role': 'roles/secretmanager.secretAccessor'},
                {'resource': REPOSITORY, 'principal': build.EMAIL, 'role': 'roles/artifactregistry.writer', 'temporary': True},
                {'resource': build.BUCKET, 'principal': build.EMAIL, 'role': 'projects/'+PROJECT+'/roles/'+build.ROLE, 'temporary': True}],
            'preconditions': [
                'explicit approval for named new resources, scoped IAM, one build/image push and private deploy',
                'fresh numeric project, APIs, billing, source hashes; new resource names absent or stop',
                'read back reused disabled build identity, its prior role, and build bucket policy; no blind reuse',
                'fresh ancestor and protected resource policies; ambiguous principals fail closed',
                'effective denial: protected secret access/setIamPolicy, bucket read/write/setIamPolicy, project setIamPolicy, eight impersonation permissions for project and protected-policy-named external accounts',
                'Troubleshooter tuple must match exact principal, resource and permission; UNKNOWN/errors are not denial; secret resource names use project number',
                'create IP salt privately with cryptographic randomness; Secret Manager version 1 only; no secret value in plan/logs',
                'read back exact bucket policy and resource IAM; runtime denied IAM edits, build denied runtime media/security/salt access',
                'one Linux build with golden/real-model/synthetic packet probes before image publication; verify result digest and bind deploy argv',
                'verify new private Cloud Run revision digest/environment/account, min=0, limits, IAM, and healthy model/security configuration'],
            'testflight_open_gate': [
                'private synthetic GCS CAS, generation fencing, retry/restart and withdrawal verification',
                'separate recorded authorization before adding allUsers roles/run.invoker on this service only',
                'bind candidate URL in Lite build '+version+'; genuine production App Attest and synthetic capture/upload/readback/revision/retry/withdrawal acceptance',
                'privacy/research eligibility and Beta review notes before inviting controlled external testers'],
            'not_in_scope': ['existing medical/demo deployments or data/IAM changes', 'clinical/audit bucket retention changes',
                             'real participant recruitment', 'automatic research/security object lifecycle or deletion',
                             'project-wide runtime/build role grants', 'unbounded cloud rebuild retries'],
            'cleanup': ['download logs/evidence; remove exact temporary build bindings and disable build account',
                        'keep private service at min=0 while pending; remove public invoker binding if later open tests fail',
                        'retain candidate security tombstones; no object/bucket purge as a cost shortcut'],
            'cost_limits': {'general_admissions_per_utc_day': 2000, 'separate_withdrawal_admissions_per_utc_day': 2000,
                            'per_installation_daily_measurements': 5,
                            'warning': 'admission limits, maxScale and billing alerts are not a hard cost cap'},
            'execution': 'offline review artifact only; no cloud mutation has occurred',
        }
        plan['artifact_sha256'] = {str(p.relative_to(folder)):build.sha(p) for p in sorted(folder.rglob('*')) if p.is_file()}
        (folder/'plan.json').write_text(json.dumps(plan,indent=2)+'\n');folder.rename(output)
    return plan


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('context','probe','fixture','output'):parser.add_argument('--'+key,type=Path,required=True)
    parser.add_argument('--manifest-sha256',required=True);parser.add_argument('--version',required=True)
    args=parser.parse_args();plan=generate(args.context,args.manifest_sha256,args.probe,args.fixture,args.version,args.output)
    print(json.dumps({'plan':str(args.output/'plan.json'),'plan_sha256':build.sha(args.output/'plan.json'),
                      'source_manifest_sha256':plan['source_manifest_sha256'],'cloud_changes':False},indent=2))


if __name__ == '__main__': main()
