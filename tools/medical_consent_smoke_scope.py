"""Pure scope/identity contract for an isolated synthetic GCS acceptance run.

This is NOT an application audit mode. No production adapter imports this file.
Preparing a manifest is not authorization to execute it.
"""
import hashlib
import json
import re
import time

PROJECT = 'woundai-jackh001'
PROJECT_NUMBER = '421209514056'
REGION = 'ASIA-EAST1'
MEDIA = 'woundai-consent-smoke-20261023-media-' + PROJECT_NUMBER
AUDIT = 'woundai-consent-smoke-20261023-audit-' + PROJECT_NUMBER
PREFIX = 'consent-ops-synthetic/20261023'
EXECUTOR = 'woundai-consent-smoke@' + PROJECT + '.iam.gserviceaccount.com'
BUCKETS = (MEDIA, AUDIT)
BASE_PERMISSIONS = frozenset({'storage.buckets.get', 'storage.buckets.getIamPolicy',
    'storage.objects.get', 'storage.objects.list', 'storage.objects.create'})
FORBIDDEN_PERMISSIONS = frozenset({'storage.buckets.update', 'storage.buckets.delete',
    'storage.buckets.setIamPolicy', 'storage.objects.update', 'storage.objects.setIamPolicy'})
# GCS rejects the entire testIamPermissions request when either object-ACL
# permission is included on a uniform-access bucket. Keep ACL mutation forbidden;
# its inapplicability is established by the mandatory UBLA metadata check instead.
UBLA_UNSUPPORTED_PROBES = frozenset({'storage.objects.getIamPolicy',
                                   'storage.objects.setIamPolicy'})
PROBE_PERMISSIONS = (BASE_PERMISSIONS | FORBIDDEN_PERMISSIONS |
                     {'storage.objects.delete'}) - UBLA_UNSUPPORTED_PROBES
LIMITS = dict(max_seconds=1200, max_writes=1000, max_upload_bytes=16*1024*1024,
              max_sdk_calls=20000, max_listed_per_call=1000, request_timeout=10)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(value): return hashlib.sha256(canonical(value)).hexdigest()


def manifest(source_commit, source_files, run_id, created_at):
    return dict(schema='medical-consent-gcs-smoke/1', source_commit=source_commit,
        source_files=source_files, run_id=run_id, created_at=created_at,
        expires_at=created_at+86400, project=PROJECT, media_bucket=MEDIA,
        audit_bucket=AUDIT, prefix=PREFIX, executor=EXECUTOR, limits=dict(LIMITS),
        soft_delete_seconds=604800, versioning=True, synthetic_only=True,
        audit_is_worm=False, no_policy_changes=True)


def validate_manifest(value, expected_sha256, now=None):
    now = int(time.time() if now is None else now)
    if not isinstance(value, dict) or sha(value) != expected_sha256:
        raise ValueError('reviewed manifest digest mismatch')
    commit, files = value.get('source_commit'), value.get('source_files')
    if (not isinstance(commit, str) or not re.fullmatch('[0-9a-f]{40}', commit)
            or not isinstance(files, dict) or not files
            or any(not isinstance(k, str) or k.startswith('/') or '..' in k.split('/')
                   or not isinstance(v, str) or not re.fullmatch('[0-9a-f]{64}', v)
                   for k, v in files.items())):
        raise ValueError('invalid source pins')
    if (not isinstance(value.get('run_id'), str)
            or not re.fullmatch('smoke-[0-9a-f]{24}', value['run_id'])
            or type(value.get('created_at')) is not int
            or not value['created_at'] <= now < value.get('expires_at', 0)):
        raise ValueError('invalid or expired run')
    if value != manifest(commit, files, value['run_id'], value['created_at']):
        raise ValueError('scope/limits differ from reviewed synthetic profile')
    return value


def validate_environment(env):
    # Never borrow service identity or application deployment configuration.
    if any(k in env for k in ('K_SERVICE', 'CLOUD_RUN_JOB', 'WOUNDAI_AUDIT_MODE',
           'WOUNDAI_GCS_BUCKET', 'WOUNDAI_AUDIT_BUCKET', 'WOUNDAI_SECURITY_BUCKET')):
        raise PermissionError('run from a clean operator environment, not an app/service/Job')


def validate_bucket(name, metadata, bindings, granted):
    if name not in BUCKETS or metadata.get('name') != name:
        raise PermissionError('wrong disposable bucket')
    iam = metadata.get('iamConfiguration') or {}
    if (str(metadata.get('projectNumber')) != PROJECT_NUMBER
            or metadata.get('location') != REGION
            or iam.get('publicAccessPrevention') != 'enforced'
            or (iam.get('uniformBucketLevelAccess') or {}).get('enabled') is not True
            or metadata.get('retentionPolicy') or metadata.get('defaultEventBasedHold')
            or metadata.get('lifecycle') or metadata.get('logging') or metadata.get('website')
            or (metadata.get('versioning') or {}).get('enabled') is not True
            or (metadata.get('softDeletePolicy') or {}).get('retentionDurationSeconds') not in (604800, '604800')):
        raise PermissionError('unexpected bucket policy; never change it automatically')
    role = 'projects/'+PROJECT+'/roles/consentSmoke'+('Media' if name == MEDIA else 'Audit')
    normalized = [dict(role=b['role'], members=sorted(b['members'])) for b in bindings
                  if set(b) == {'role', 'members'}]
    if len(normalized) != len(bindings) or normalized != [dict(role=role, members=['serviceAccount:'+EXECUTOR])]:
        raise PermissionError('unexpected bucket IAM bindings')
    wanted = BASE_PERMISSIONS | ({'storage.objects.delete'} if name == MEDIA else set())
    if set(granted) != wanted:
        raise PermissionError('effective permissions differ; check inherited grants')


def validate_job_candidate(spec):
    """Offline review of a future private formal worker; does not admit a Job.

    Existing MMH/Demo unlocked service modes remain unsupported for Jobs. The
    production GcsStore will separately require a real locked audit epoch.
    """
    if not isinstance(spec, dict) or set(spec) != {'job', 'worker', 'scheduler', 'image',
            'audit_mode', 'media_bucket', 'audit_bucket', 'schedule_paused', 'apply',
            'tasks', 'parallelism', 'max_retries', 'timeout_seconds', 'policy_sha256'}:
        raise ValueError('exact Job candidate fields required')
    if (spec['job'] != 'woundai-medical-consent-cleanup-candidate'
            or spec['worker'] != 'woundai-consent-cleanup@'+PROJECT+'.iam.gserviceaccount.com'
            or spec['scheduler'] != 'woundai-consent-scheduler@'+PROJECT+'.iam.gserviceaccount.com'
            or spec['audit_mode'] != 'locked-seven-year'
            or spec['schedule_paused'] is not True or spec['apply'] is not False
            or type(spec['tasks']) is not int or spec['tasks'] != 1
            or type(spec['parallelism']) is not int or spec['parallelism'] != 1
            or type(spec['max_retries']) is not int or spec['max_retries'] != 0
            or type(spec['timeout_seconds']) is not int or not 1 <= spec['timeout_seconds'] <= 600
            or not re.fullmatch('asia-east1-docker.pkg.dev/'+PROJECT+r'/[a-z0-9_-]+/[a-z0-9_-]+@sha256:[0-9a-f]{64}', str(spec['image']))
            or not re.fullmatch('[0-9a-f]{64}', str(spec['policy_sha256']))
            or any(not re.fullmatch(r'woundai-medical-consent-[a-z0-9-]+', str(spec[k])) for k in ('media_bucket', 'audit_bucket'))
            or spec['media_bucket'] == spec['audit_bucket']):
        raise PermissionError('unsupported Job scope; no validation-service impersonation')
    return dict(status='candidate_structure_only', runtime_identity_verified=False,
                locked_epoch_verified=False, execution_authorized=False)
