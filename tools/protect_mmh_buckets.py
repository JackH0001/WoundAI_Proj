"""Reversible control-plane protection for the three MMH foundation buckets.

Only a new project tag, three direct bindings and an exact deny policy are made.
No allow-role changes, object access, account creation or retention lock.
"""
import argparse
import json
import re
import tempfile
from pathlib import Path
from provision_mmh_foundation import PROJECT, NUMBER, BUCKETS, command, verify_bucket
from restrict_mmh_bucket_iam import bindings, TARGET

ADMIN = 'jack.hou@gmail.com'
# Exact spelling read back from this project's existing owner binding.
OWNER_MEMBER = 'user:Jack.Hou@gmail.com'
TAG_SHORT_NAME = 'woundai-mmhps20261007-protection'
TAG_VALUE = 'enabled'
POLICY_ID = 'woundai-mmhps20261007-bucket-protection'
ATTACHMENT = 'cloudresourcemanager.googleapis.com/projects/' + NUMBER
PERMISSIONS = tuple('storage.googleapis.com/buckets.' + x for x in
                    ('delete', 'setIamPolicy', 'update', 'createTagBinding', 'deleteTagBinding'))


def resource(bucket):
    return '//storage.googleapis.com/projects/_/buckets/' + bucket


def policy(key, value):
    if re.fullmatch(r'tagKeys/[0-9]+', key) is None or re.fullmatch(r'tagValues/[0-9]+', value) is None:
        raise ValueError('tag IDs must be canonical numeric resource names')
    return {'displayName': 'MMHPS20261007 bucket control-plane protection', 'rules': [{
        'description': 'Protect only directly tagged MMH buckets; owner operator remains able to administer.',
        'denyRule': {'deniedPrincipals': ['principalSet://goog/public:all'],
                     'exceptionPrincipals': ['principal://goog/subject/' + ADMIN],
                     'deniedPermissions': list(PERMISSIONS),
                     'denialCondition': {'expression': "resource.matchTagId('%s', '%s')" % (key, value)}}}]}


def check_policy(actual, expected):
    if not isinstance(actual, dict) or actual.get('rules') != expected['rules'] or actual.get('displayName') != expected['displayName']:
        raise ValueError('deny policy differs; no automatic overwrite')


def exact_tag(rows, short, parent, prefix):
    if not isinstance(rows, list):
        raise ValueError('tag inventory unavailable')
    found = [r for r in rows if r.get('shortName') == short]
    if len(found) > 1:
        raise ValueError('ambiguous tag')
    if not found:
        return None
    row = found[0]
    if row.get('parent') != parent or re.fullmatch(prefix + '/[0-9]+', row.get('name', '')) is None:
        raise ValueError('tag resource parent or ID mismatch')
    return row['name']


def protect(run=command, apply=False, authority_check=None):
    p = run(['projects', 'describe', PROJECT])
    if p.get('projectId') != PROJECT or str(p.get('projectNumber')) != NUMBER or p.get('lifecycleState') != 'ACTIVE':
        raise ValueError('project mismatch')
    active = run(['auth', 'list', '--filter=status:ACTIVE'])
    if len(active) != 1 or active[0].get('account') != ADMIN:
        raise ValueError('only verified owner operator may apply this policy')
    iam = run(['projects', 'get-iam-policy', PROJECT])
    if not any(b.get('role') == 'roles/owner' and OWNER_MEMBER in b.get('members', []) and not b.get('condition')
               for b in iam.get('bindings', [])):
        raise ValueError('exception principal is not an unconditional project owner')
    if apply:
        if authority_check is None or authority_check() is not True:
            raise PermissionError('fresh effective deny-admin permission proof required before mutation')
    inventory = run(['storage', 'buckets', 'list'])
    if not isinstance(inventory, list) or not set(BUCKETS).issubset({b.get('name') for b in inventory}):
        raise ValueError('bucket inventory incomplete')
    for b in BUCKETS:
        verify_bucket(run(['storage', 'buckets', 'describe', 'gs://' + b, '--raw']), b)
        if bindings(run(['storage', 'buckets', 'get-iam-policy', 'gs://' + b])) != TARGET:
            raise ValueError('restrict fresh bucket convenience grants first')
    parent = 'projects/' + NUMBER
    key = exact_tag(run(['resource-manager', 'tags', 'keys', 'list', '--parent=' + parent]), TAG_SHORT_NAME, parent, 'tagKeys')
    if not key and apply:
        row = run(['resource-manager', 'tags', 'keys', 'create', TAG_SHORT_NAME, '--parent=' + parent])
        key = exact_tag([row], TAG_SHORT_NAME, parent, 'tagKeys')
        if not key: raise ValueError('tag creation readback missing')
    value = None
    if key:
        value = exact_tag(run(['resource-manager', 'tags', 'values', 'list', '--parent=' + key]), TAG_VALUE, key, 'tagValues')
        if not value and apply:
            row = run(['resource-manager', 'tags', 'values', 'create', TAG_VALUE, '--parent=' + key])
            value = exact_tag([row], TAG_VALUE, key, 'tagValues')
            if not value: raise ValueError('tag value creation readback missing')
    if not key or not value:
        return {'applied': False, 'state': 'plan_requires_new_tag', 'buckets': list(BUCKETS)}
    # Reject reuse of this tag value on any other current project bucket.
    tagged = set()
    for row in inventory:
        b = row['name']
        location = row.get('location', '').lower()
        if not location: raise ValueError('bucket location missing')
        rows = run(['resource-manager', 'tags', 'bindings', 'list', '--parent=' + resource(b), '--location=' + location])
        if not isinstance(rows, list): raise ValueError('tag bindings unavailable')
        if any(r.get('tagValue') == value for r in rows): tagged.add(b)
    if tagged - set(BUCKETS): raise ValueError('MMH protection tag is attached outside MMH buckets')
    # Never attach this tag to the project: it would propagate outside the intended buckets.
    rows = run(['resource-manager', 'tags', 'bindings', 'list', '--parent=//cloudresourcemanager.googleapis.com/projects/' + NUMBER])
    if not isinstance(rows, list) or any(r.get('tagValue') == value for r in rows):
        raise ValueError('unexpected project tag binding')
    expected = policy(key, value)
    listed = run(['iam', 'policies', 'list', '--attachment-point=' + ATTACHMENT, '--kind=denypolicies'])
    if not isinstance(listed, dict) or set(listed) - {'policies'}:
        raise ValueError('deny policy inventory incomplete')
    matching = [r for r in listed.get('policies', []) if r.get('name', '').endswith('/' + POLICY_ID)]
    if len(matching) > 1: raise ValueError('ambiguous deny policy')
    if matching:
        actual = run(['iam', 'policies', 'get', POLICY_ID, '--attachment-point=' + ATTACHMENT, '--kind=denypolicies'])
        check_policy(actual, expected)
    if apply:
        for b in BUCKETS:
            if b not in tagged:
                run(['resource-manager', 'tags', 'bindings', 'create', '--parent=' + resource(b), '--location=asia-east1', '--tag-value=' + value])
            rows = run(['resource-manager', 'tags', 'bindings', 'list', '--parent=' + resource(b), '--location=asia-east1'])
            if not any(r.get('tagValue') == value for r in rows): raise ValueError('tag binding readback failed')
        if not matching:
            with tempfile.TemporaryDirectory(prefix='mmh-deny-') as tmp:
                file = Path(tmp) / 'policy.json';file.write_text(json.dumps(expected))
                run(['iam', 'policies', 'create', POLICY_ID, '--attachment-point=' + ATTACHMENT, '--kind=denypolicies', '--policy-file=' + str(file)])
        check_policy(run(['iam', 'policies', 'get', POLICY_ID, '--attachment-point=' + ATTACHMENT, '--kind=denypolicies']), expected)
    return {'applied': apply, 'tag_key': key, 'tag_value': value, 'policy_id': POLICY_ID,
            'buckets': list(BUCKETS), 'effective_access_verified': False,
            'next': 'Policy Troubleshooter v3 verification required; unknown is not success'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    from verify_mmh_effective_iam import verify_deny_authority
    result = protect(apply=args.apply, authority_check=verify_deny_authority)
    args.report.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
