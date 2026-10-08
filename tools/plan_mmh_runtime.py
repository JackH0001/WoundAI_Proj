"""Offline MMH deployment specification. No cloud calls or apply operation.

The result is deliberately not deployment authorization. Immutable build proof,
fresh effective IAM, independent secrets and irreversible audit-lock approval
must be checked by a future executor before using the reviewed specification.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

from provision_mmh_foundation import PROJECT, NUMBER, ORG, SA, BUCKETS
from medical_image_build import IMAGE_ROOT, exact_hex

REGION = 'asia-east1'
SERVICE = 'woundai-backend-' + ORG
SECRET_NAMES = {
    key: 'woundai-' + ORG + '-' + suffix
    for key, suffix in [('ADMIN_PASSWORD', 'admin-password'),
                        ('JWT_SECRET_KEY', 'jwt-secret'),
                        ('FLASK_SECRET_KEY', 'flask-secret'),
                        ('CARE_RECEIPT_SECRET', 'care-receipt-secret')]
}
OBJECT_PERMS = ('storage.objects.create', 'storage.objects.delete',
                'storage.objects.get', 'storage.objects.list')
APPEND_PERMS = ('storage.objects.create', 'storage.objects.get', 'storage.objects.list')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def generate(image, source_commit, manifest, versions):
    exact_hex(source_commit, 40); exact_hex(manifest, 64)
    if not isinstance(image, str) or re.fullmatch(re.escape(IMAGE_ROOT) + r'@sha256:[0-9a-f]{64}', image) is None:
        raise ValueError('fixed medical repository and immutable digest required')
    if not isinstance(versions, dict) or set(versions) != set(SECRET_NAMES):
        raise ValueError('all four secret versions required')
    for version in versions.values():
        if not isinstance(version, str) or re.fullmatch(r'[1-9][0-9]*', version) is None:
            raise ValueError('exact positive secret version required; no latest alias')
    env = {'WOUNDAI_SERVICE_PROFILE': 'medical', 'WOUNDAI_ENABLE_LITE_API': '0',
           'WOUNDAI_INSTITUTION_ORG': ORG, 'WOUNDAI_STORE': 'gcs',
           'WOUNDAI_GCS_BUCKET': BUCKETS[0], 'WOUNDAI_SECURITY_BUCKET': BUCKETS[1],
           'WOUNDAI_AUDIT_BUCKET': BUCKETS[2], 'WOUNDAI_GCS_PREFIX': 'flywheel',
           'GIT_COMMIT': source_commit}
    secrets = {key: name + ':' + versions[key] for key, name in SECRET_NAMES.items()}
    spec = {'project': PROJECT, 'project_number': NUMBER, 'region': REGION,
            'service': SERVICE, 'runtime_service_account': SA, 'image': image,
            'source_commit': source_commit, 'manifest_sha256': manifest,
            'environment': env, 'secret_versions': secrets,
            'resources': {'min_instances': 0, 'max_instances': 1, 'concurrency': 1,
                          'cpu': 2, 'memory': '4Gi', 'timeout_seconds': 120},
            'invocation': 'private', 'existing_services_may_be_modified': False,
            'audit_requirement': {'bucket': BUCKETS[2], 'retention_seconds': 220903200,
                                  'locked': True, 'separate_irreversible_approval': True},
            'planned_bucket_grants': [
                {'bucket': BUCKETS[0], 'role_id': 'woundaiMmhRuntimeObjects', 'permissions': list(OBJECT_PERMS)},
                {'bucket': BUCKETS[1], 'role_id': 'woundaiMmhSecurityAppend', 'permissions': list(APPEND_PERMS)},
                {'bucket': BUCKETS[2], 'role_id': 'woundaiMmhAuditAppend',
                 'permissions': sorted(APPEND_PERMS + ('storage.buckets.get',))}],
            'planned_secret_grants': [{'secret': s, 'role': 'roles/secretmanager.secretAccessor'}
                                      for s in SECRET_NAMES.values()]}
    return {'schema': 'mmh.runtime-review/1', 'deployable': False,
            'spec': spec, 'spec_sha256': hashlib.sha256(canonical(spec)).hexdigest(),
            'required_evidence': ['reviewed merged source and matching immutable build proof',
                'fresh old-runtime dependency baseline and successful least-privilege migration',
                'runtime effective IAM isolation including impersonation paths',
                'explicit audit-lock authorization and fresh exact retention readback',
                'four independent secrets with enabled exact versions; no values in evidence',
                'service absent before creation; no modification of existing services',
                'private synthetic GCS save/readback, cold-start accounts, withdrawal and RGB-D checks'],
            'not_included': ['cloud mutations', 'public invocation', 'App URL replacement',
                             'real-patient collection', 'IRB approval', 'complete 3D readiness']}


def validate(plan):
    """Refuse edits to institution, buckets, roles, source binding or safeguards."""
    spec = plan.get('spec', {})
    versions = {}
    for key, name in SECRET_NAMES.items():
        ref = spec.get('secret_versions', {}).get(key, '')
        if not isinstance(ref, str) or not ref.startswith(name + ':'):
            raise ValueError('secret is not institution-specific')
        versions[key] = ref[len(name) + 1:]
    expected = generate(spec.get('image'), spec.get('source_commit'), spec.get('manifest_sha256'), versions)
    if plan != expected:
        raise ValueError('review plan differs from exact MMH specification')
    return True


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--image', required=True); p.add_argument('--source-commit', required=True)
    p.add_argument('--manifest-sha256', required=True)
    p.add_argument('--secret-versions', type=Path, required=True, help='JSON of env names to version numbers only')
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    plan = generate(a.image, a.source_commit, a.manifest_sha256, json.loads(a.secret_versions.read_text()))
    validate(plan)
    with a.out.open('x') as f:
        f.write(json.dumps(plan, indent=2) + '\n')
    print(json.dumps({'spec_sha256': plan['spec_sha256'], 'deployable': False, 'cloud_changes': False}))


if __name__ == '__main__':
    main()
