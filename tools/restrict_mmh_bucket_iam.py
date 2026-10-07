"""Remove only fresh MMH bucket convenience grants; not a full IAM isolation proof.

Owner access stays. Unknown/conditional bindings stop before any write. Project
roles are not modified, and inherited bucket deletion permission remains a gate.
"""
import argparse
import json
from pathlib import Path
import tempfile
from provision_mmh_foundation import BUCKETS, PROJECT, command, verify_bucket

OWNER = 'projectOwner:' + PROJECT
EDITOR = 'projectEditor:' + PROJECT
VIEWER = 'projectViewer:' + PROJECT
DEFAULTS = {
    'roles/storage.legacyBucketOwner': {OWNER, EDITOR},
    'roles/storage.legacyBucketReader': {VIEWER},
    'roles/storage.legacyObjectOwner': {OWNER, EDITOR},
    'roles/storage.legacyObjectReader': {VIEWER},
}
TARGET = {r: {OWNER} for r in DEFAULTS if r.endswith('Owner')}


def bindings(policy):
    if not isinstance(policy, dict) or not policy.get('etag'):
        raise ValueError('missing policy/etag')
    result = {}
    for b in policy.get('bindings', []):
        role, members = b.get('role'), b.get('members')
        if (set(b) != {'role', 'members'} or role in result or role not in DEFAULTS
                or not isinstance(members, list) or not members
                or len(set(members)) != len(members)):
            raise ValueError('unexpected bucket binding requires review')
        result[role] = set(members)
    if result not in (DEFAULTS, TARGET):
        raise ValueError('policy is not the exact fresh or restricted MMH policy')
    return result


def restrict(run=command, apply=False):
    plans = []
    for name in BUCKETS:
        url = 'gs://' + name
        verify_bucket(run(['storage', 'buckets', 'describe', url, '--raw']), name)
        before = run(['storage', 'buckets', 'get-iam-policy', url])
        current = bindings(before)
        plans.append((url, before, current))
    changed = []
    for url, before, current in plans:
        if not apply or current == TARGET:
            continue
        after = dict(before, bindings=[{'role': r, 'members': sorted(m)} for r, m in TARGET.items()])
        with tempfile.TemporaryDirectory(prefix='mmh-iam-') as tmp:
            path = Path(tmp) / 'policy.json'
            path.write_text(json.dumps(after))
            run(['storage', 'buckets', 'set-iam-policy', url, str(path), '--etag=' + before['etag']])
        if bindings(run(['storage', 'buckets', 'get-iam-policy', url])) != TARGET:
            raise ValueError('policy readback mismatch; stop before data use')
        changed.append(url)
    return {'applied': apply, 'changed': changed, 'project_iam_changed': False,
            'full_isolation_verified': False, 'remaining_gate': 'inherited bucket deletion permissions'}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--apply', action='store_true')
    p.add_argument('--report', type=Path, required=True)
    args = p.parse_args()
    result = restrict(apply=args.apply)
    args.report.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
