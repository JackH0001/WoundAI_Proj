"""Create only the approved MMH empty non-public buckets and identity without added grants.

No deployment, IAM grant, secret, object upload, retention lock or deletion.
Existing resources must match the exact project and bucket settings.
Inherited IAM must be checked separately; non-public does not mean isolated.
"""
import argparse
import json
import subprocess
from pathlib import Path

PROJECT = 'woundai-jackh001'
NUMBER = '421209514056'
REGION = 'ASIA-EAST1'
ORG = 'mmhps20261007'
SA_ID = 'woundai-mmhps20261007-runtime'
SA = SA_ID + '@' + PROJECT + '.iam.gserviceaccount.com'
BUCKETS = tuple('woundai-' + ORG + '-' + kind + '-' + NUMBER
                for kind in ('media', 'security', 'audit'))


def command(args):
    result = subprocess.run(['gcloud', *args, '--project', PROJECT, '--format=json', '--quiet'],
                            capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise RuntimeError('gcloud failed: ' + result.stderr.strip())
    return json.loads(result.stdout) if result.stdout.strip() else None


def verify_bucket(row, name):
    if row.get('name') != name or str(row.get('projectNumber')) != NUMBER:
        raise ValueError('bucket name/project mismatch')
    if row.get('location', '').upper() != REGION or row.get('storageClass') != 'STANDARD':
        raise ValueError('bucket location/class mismatch')
    iam = row.get('iamConfiguration', {})
    if iam.get('uniformBucketLevelAccess', {}).get('enabled') is not True or iam.get('publicAccessPrevention') != 'enforced':
        raise ValueError('bucket access controls do not match')
    if row.get('retentionPolicy') or row.get('lifecycle', {}).get('rule') or row.get('defaultEventBasedHold'):
        raise ValueError('existing retention/lifecycle/hold requires separate review')
    if row.get('versioning', {}).get('enabled') or str(row.get('softDeletePolicy', {}).get('retentionDurationSeconds', 'missing')) != '0':
        raise ValueError('version/soft-delete policy differs from empty foundation')


def verify_identity(row):
    if row.get('email') != SA or row.get('projectId') != PROJECT or row.get('disabled') is True:
        raise ValueError('runtime identity mismatch or disabled')


def provision(run=command, apply=False):
    project = run(['projects', 'describe', PROJECT])
    if project.get('projectId') != PROJECT or str(project.get('projectNumber')) != NUMBER or project.get('lifecycleState') != 'ACTIVE':
        raise ValueError('project identity mismatch')
    buckets = run(['storage', 'buckets', 'list'])
    accounts = run(['iam', 'service-accounts', 'list'])
    if not isinstance(buckets, list) or not isinstance(accounts, list):
        raise ValueError('inventory unavailable')
    existing = {r['name']: r for r in buckets if r.get('name') in BUCKETS}
    identities = [r for r in accounts if r.get('email') == SA]
    if len(identities) > 1 or len(existing) != sum(r.get('name') in BUCKETS for r in buckets):
        raise ValueError('duplicate inventory')
    for name, row in existing.items():
        verify_bucket(run(['storage', 'buckets', 'describe', 'gs://' + name, '--raw']), name)
    for row in identities:
        verify_identity(row)
    result = {'project': PROJECT, 'org': ORG, 'runtime': SA, 'buckets': list(BUCKETS),
              'applied': apply, 'created': [], 'service_deployed': False,
              'accounts_created': False, 'audit_locked': False, 'iam_grants_added': False}
    if not apply:
        return result
    for name in BUCKETS:
        if name not in existing:
            run(['storage', 'buckets', 'create', 'gs://' + name, '--location=' + REGION,
                 '--default-storage-class=STANDARD', '--uniform-bucket-level-access',
                 '--public-access-prevention', '--soft-delete-duration=0'])
            result['created'].append(name)
        row = run(['storage', 'buckets', 'describe', 'gs://' + name, '--raw'])
        verify_bucket(row, name)
    if not identities:
        run(['iam', 'service-accounts', 'create', SA_ID,
             '--display-name=MMHPS20261007 isolated medical validation runtime'])
        result['created'].append(SA)
    verify_identity(run(['iam', 'service-accounts', 'describe', SA]))
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--apply', action='store_true')
    p.add_argument('--report', type=Path, required=True)
    args = p.parse_args()
    result = provision(apply=args.apply)
    args.report.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
