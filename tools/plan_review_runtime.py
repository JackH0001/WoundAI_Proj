"""Offline, exact resource plan for the isolated WoundAI review service.

No cloud client, subprocess, apply mode, secret value or authorization is used.
An immutable image and clean-source build manifest can be bound only after the
source/build review. Public invocation is a separate, explicitly approved step.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

from medical_image_build import IMAGE_ROOT, exact_hex

PROJECT = 'woundai-jackh001'
NUMBER = '421209514056'
REGION = 'asia-east1'
SERVICE = 'woundai-mmh-review-demo'
ORG = 'mmh-review-demo'
RUNTIME = 'woundai-mmh-review-runtime@' + PROJECT + '.iam.gserviceaccount.com'
BUCKETS = tuple('woundai-mmh-review-' + kind + '-' + NUMBER for kind in ('media', 'security', 'audit'))
SECRETS = {key: 'woundai-mmh-review-' + suffix for key, suffix in (
    ('ADMIN_PASSWORD', 'admin-password'), ('JWT_SECRET_KEY', 'jwt-secret'),
    ('FLASK_SECRET_KEY', 'flask-secret'), ('CARE_RECEIPT_SECRET', 'care-receipt-secret'))}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def resources():
    """Resource inventory only. It is not a deployable Cloud Run request."""
    objects = ['storage.objects.create', 'storage.objects.get', 'storage.objects.list']
    return {
        'project': PROJECT, 'project_number': NUMBER, 'region': REGION,
        'service': SERVICE, 'org': ORG, 'runtime_service_account': RUNTIME,
        'environment': {
            'WOUNDAI_DEPLOYMENT_ENVIRONMENT': 'synthetic-review',
            'WOUNDAI_AUDIT_MODE': 'synthetic-review-validation',
            'WOUNDAI_STORE': 'gcs', 'WOUNDAI_SERVICE_PROFILE': 'medical',
            'WOUNDAI_INSTITUTION_ORG': ORG, 'WOUNDAI_ENABLE_LITE_API': '0',
            'GOOGLE_CLOUD_PROJECT': PROJECT, 'WOUNDAI_GCS_PREFIX': 'flywheel',
            'WOUNDAI_GCS_BUCKET': BUCKETS[0], 'WOUNDAI_SECURITY_BUCKET': BUCKETS[1],
            'WOUNDAI_AUDIT_BUCKET': BUCKETS[2],
        },
        'buckets': [{'name': name, 'location': REGION, 'storage_class': 'STANDARD',
                     'uniform_bucket_level_access': True, 'public_access_prevention': 'enforced',
                     'retention_seconds': 0, 'retention_locked': False, 'lifecycle_rules': []}
                    for name in BUCKETS],
        'bucket_runtime_grants': [
            {'bucket': BUCKETS[0], 'role_id': 'woundaiReviewMediaObjects',
             'permissions': sorted(objects + ['storage.objects.delete'])},
            {'bucket': BUCKETS[1], 'role_id': 'woundaiReviewSecurityAppend', 'permissions': objects},
            {'bucket': BUCKETS[2], 'role_id': 'woundaiReviewAuditAppend',
             'permissions': sorted(objects + ['storage.buckets.get'])},
        ],
        'secret_runtime_grants': [{'secret': name, 'role': 'roles/secretmanager.secretAccessor'}
                                  for name in SECRETS.values()],
        'limits': {'min_instances': 0, 'max_instances': 1, 'cpu': 2, 'memory': '4Gi',
                   'concurrency': 1, 'timeout_seconds': 120},
        'bootstrap_invocation': 'private', 'iap_enabled': False,
        'release_invoker_binding': {'resource': SERVICE, 'role': 'roles/run.invoker', 'member': 'allUsers',
                                    'separate_approval_required': True,
                                    'after': 'private synthetic acceptance and endpoint-auth verification'},
        'application_auth': 'independent WoundAI accounts; no public registration',
        'audit_claim': 'unlocked validation; not WORM',
        'existing_resources_may_be_modified': False,
        'secret_values_may_be_exported': False,
        'mmh_credentials_may_be_copied': False,
        'new_oauth_clients_required': False,
    }


def generate(image, source_commit, manifest_sha256, versions):
    exact_hex(source_commit, 40); exact_hex(manifest_sha256, 64)
    if not isinstance(image, str) or re.fullmatch(re.escape(IMAGE_ROOT) + r'@sha256:[0-9a-f]{64}', image) is None:
        raise ValueError('immutable digest in the fixed medical image repository required')
    if not isinstance(versions, dict) or set(versions) != set(SECRETS):
        raise ValueError('four independent review secret version numbers required')
    for value in versions.values():
        if not isinstance(value, str) or re.fullmatch(r'[1-9][0-9]*', value) is None:
            raise ValueError('positive exact version numbers only; never latest or secret values')
    spec = resources()
    spec.update(image=image, source_commit=source_commit, manifest_sha256=manifest_sha256,
                secret_versions={key: name + ':' + versions[key] for key, name in SECRETS.items()})
    spec['environment']['GIT_COMMIT'] = source_commit
    return {'schema': 'woundai.review-runtime-plan/1', 'deployable': False, 'apply_authorized': False,
            'spec': spec, 'spec_sha256': hashlib.sha256(canonical(spec)).hexdigest(),
            'required_evidence': [
                'reviewed committed source and matching immutable build manifest/digest',
                'fresh resource absence checks; collision means stop, never adopt or overwrite',
                'fresh effective IAM including inherited roles and impersonation paths',
                'independent enabled exact secret versions; evidence contains metadata only',
                'private GCS revision/mask/depth/withdrawal and restart readback acceptance',
                'every data endpoint rejects unauthenticated and foreign-institution requests',
                'explicit approval for resources/build/deployment and separate public invocation',
                'published client stable origin and real-device sign-in/upload/readback',
            ],
            'not_included': ['cloud operations', 'IAM mutation', 'traffic switch', 'bucket lock',
                             'lifecycle change', 'data deletion', 'MMH changes', 'research approval']}


def validate(plan):
    if not isinstance(plan, dict) or not isinstance(plan.get('spec'), dict):
        raise ValueError('review plan object required')
    spec = plan['spec']; refs = spec.get('secret_versions', {})
    if not isinstance(refs, dict):
        raise ValueError('secret references must be an object')
    versions = {}
    for key, name in SECRETS.items():
        value = refs.get(key)
        if not isinstance(value, str) or not value.startswith(name + ':'):
            raise ValueError('review-only secret references required')
        versions[key] = value[len(name) + 1:]
    expected = generate(spec.get('image'), spec.get('source_commit'), spec.get('manifest_sha256'), versions)
    if plan != expected:
        raise ValueError('plan differs from the exact isolated review specification')
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--source-commit', required=True)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--secret-versions', type=Path, required=True, help='version numbers only')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    plan = generate(args.image, args.source_commit, args.manifest_sha256,
                    json.loads(args.secret_versions.read_text()))
    validate(plan)
    with args.out.open('x') as stream:
        stream.write(json.dumps(plan, indent=2) + '\n')
    print(json.dumps({'spec_sha256': plan['spec_sha256'], 'deployable': False, 'cloud_changes': False}))


if __name__ == '__main__':
    main()
