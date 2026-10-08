"""Read-only MMH metadata preflight; never deploy, grant IAM or read secret values.

A green metadata report is NOT deployment authorization or effective IAM proof.
Every run invalidates the prior report before validating inputs or querying cloud.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess

from medical_image_build import resolve
from plan_mmh_runtime import validate, SECRET_NAMES, REGION, SERVICE
from provision_mmh_foundation import PROJECT, NUMBER, SA, BUCKETS, verify_identity

OLD_RUNTIME = NUMBER + '-compute@developer.gserviceaccount.com'
REPO_URL = 'https://github.com/JackH0001/WoundAI_Proj.git'
REMAINING = [
    'fresh effective IAM including impersonation paths and legacy authenticated write baseline',
    'separate exact irreversible audit-lock authorization',
    'reviewed deployment executor and exclusive private service creation',
    'private synthetic GCS save/readback, durable accounts, withdrawal and RGB-D validation',
]


def cloud(args):
    # This adapter is deliberately narrower than the general deployment adapter.
    prefixes = [('projects', 'describe'), ('projects', 'get-iam-policy'),
                ('builds', 'describe'), ('artifacts', 'docker', 'images', 'describe'),
                ('storage', 'buckets', 'describe'), ('iam', 'service-accounts', 'describe'),
                ('run', 'services', 'list'), ('secrets', 'list'),
                ('secrets', 'versions', 'describe')]
    if not any(tuple(args[:len(p)]) == p for p in prefixes):
        raise ValueError('read-only metadata command required')
    result = subprocess.run(['gcloud', *args, '--project='+PROJECT, '--format=json'],
                            capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise RuntimeError('cloud metadata unavailable')  # never echo CLI stderr/token
    return json.loads(result.stdout)


def remote_main():
    result = subprocess.run(['git', 'ls-remote', REPO_URL, 'refs/heads/main'],
                            capture_output=True, text=True, timeout=60, check=True)
    fields = result.stdout.strip().split()
    if len(fields) != 2 or fields[1] != 'refs/heads/main' or re.fullmatch('[0-9a-f]{40}', fields[0]) is None:
        raise ValueError('exact remote main unavailable')
    return fields[0]


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def bucket_metadata(row, name, audit=False):
    require(row.get('name') == name and str(row.get('projectNumber')) == NUMBER,
            'bucket identity mismatch')
    require(row.get('location', '').upper() == REGION.upper() and row.get('storageClass') == 'STANDARD',
            'bucket region/storage class mismatch')
    iam = row.get('iamConfiguration', {})
    require(iam.get('uniformBucketLevelAccess', {}).get('enabled') is True and
            iam.get('publicAccessPrevention') == 'enforced', 'bucket privacy controls mismatch')
    require(not row.get('lifecycle', {}).get('rule') and not row.get('defaultEventBasedHold') and
            not row.get('versioning', {}).get('enabled') and
            str(row.get('softDeletePolicy', {}).get('retentionDurationSeconds', 'missing')) == '0',
            'unreviewed lifecycle/hold/version/soft-delete policy')
    policy = row.get('retentionPolicy', {})
    if audit:
        require(policy.get('isLocked') is True and str(policy.get('retentionPeriod')) == '220903200',
                'exact locked seven-year audit policy absent')
    else:
        require(not policy, 'unexpected media/security retention')


def inspect(plan, build_id, run=cloud, read_main=remote_main):
    validate(plan)  # malformed or modified inputs must fail before cloud calls
    require(isinstance(build_id, str) and re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', build_id),
            'exact build UUID required')
    spec = plan['spec']
    checks = []

    def check(name, action):
        try:
            action()
        except Exception as exc:
            checks.append({'check': name, 'passed': False, 'error_type': type(exc).__name__})
            return False
        checks.append({'check': name, 'passed': True})
        return True

    def project():
        row = run(['projects', 'describe', PROJECT])
        require(row.get('projectId') == PROJECT and str(row.get('projectNumber')) == NUMBER and
                row.get('lifecycleState') == 'ACTIVE', 'project mismatch')

    # Avoid a burst of futile cloud calls if project identity or credentials fail.
    if not check('project_identity', project):
        return result(plan, checks, complete=False)

    check('source_is_current_remote_main', lambda: require(read_main() == spec['source_commit'], 'source not current main'))
    check('immutable_build_and_artifact', lambda: require(
        resolve(build_id, spec['source_commit'], spec['manifest_sha256'], PROJECT, REGION, run=run) == spec['image'],
        'image proof mismatch'))
    check('runtime_identity', lambda: verify_identity(run(['iam', 'service-accounts', 'describe', SA])))
    for name in BUCKETS:
        check('bucket_' + ('audit_locked' if name == BUCKETS[2] else ('media' if name == BUCKETS[0] else 'security')),
              lambda name=name: bucket_metadata(run(['storage', 'buckets', 'describe', 'gs://'+name, '--raw']),
                                                name, audit=name == BUCKETS[2]))

    def old_editor():
        policy = run(['projects', 'get-iam-policy', PROJECT])
        require(isinstance(policy.get('bindings'), list) and bool(policy.get('etag')), 'IAM policy incomplete')
        require(not any(b.get('role') == 'roles/editor' and 'serviceAccount:'+OLD_RUNTIME in b.get('members', [])
                        for b in policy['bindings']), 'old Compute Editor remains')
    check('old_compute_editor_removed', old_editor)

    def absent_service():
        rows = run(['run', 'services', 'list', '--region='+REGION, '--platform=managed'])
        require(isinstance(rows, list), 'service inventory malformed')
        for row in rows:
            name = row.get('metadata', {}).get('name')
            require(isinstance(name, str) and bool(name), 'service identity missing')
            require(name != SERVICE, 'service already exists; creation only')
    check('mmh_service_absent', absent_service)

    # List resource metadata, not secret payloads. Describe only exact versions.
    for key, secret in SECRET_NAMES.items():
        def secret_version(key=key, secret=secret):
            version = spec['secret_versions'][key].rsplit(':', 1)[1]
            row = run(['secrets', 'versions', 'describe', version, '--secret='+secret])
            expected = 'projects/'+NUMBER+'/secrets/'+secret+'/versions/'+version
            require(row.get('name') == expected and row.get('state') == 'ENABLED',
                    'secret version missing, mismatched or disabled')
        check('secret_version_' + key, secret_version)
    return result(plan, checks, complete=True)


def result(plan, checks, complete):
    return {'schema': 'mmh.metadata-preflight/1', 'utc': datetime.now(timezone.utc).isoformat(),
            'spec_sha256': plan['spec_sha256'], 'complete': complete,
            'metadata_passed': complete and all(c['passed'] for c in checks),
            'checks': checks, 'passed': sum(c['passed'] for c in checks), 'total': len(checks),
            'deployable': False, 'cloud_mutations': False, 'secret_payloads_read': False,
            'remaining_evidence': list(REMAINING)}


def write_report(plan_path, build_id, report, run=cloud, read_main=remote_main):
    report = Path(report)
    report.write_text(json.dumps({'complete': False, 'metadata_passed': False,
                                  'deployable': False, 'state': 'in_progress'})+'\n')
    try:
        output = inspect(json.loads(Path(plan_path).read_text()), build_id, run, read_main)
    except Exception as exc:
        output = {'complete': False, 'metadata_passed': False, 'deployable': False,
                  'state': 'failed', 'error_type': type(exc).__name__}
    report.write_text(json.dumps(output, indent=2)+'\n')
    return output


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--plan', type=Path, required=True)
    p.add_argument('--build-id', required=True)
    p.add_argument('--report', type=Path, required=True)
    a = p.parse_args()
    output = write_report(a.plan, a.build_id, a.report)
    print(json.dumps(output, indent=2))
    raise SystemExit(0 if output.get('metadata_passed') else 1)
