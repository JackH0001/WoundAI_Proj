"""Offline gate for exposed MMH revisions sharing the account store.

Does not deploy, authorize writes, read accounts, or prove external scripts are
stopped. Run after a separately approved cutover with a fresh read-only snapshot.
The target digest must independently be tied to reviewed CAS-capable source.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re

SERVICE = 'woundai-backend-mmhps20261007'
PROJECT = 'woundai-jackh001'
IMAGE = r'asia-east1-docker\.pkg\.dev/woundai-jackh001/woundai-medical-build/medical@sha256:[0-9a-f]{64}'
STORAGE = {'WOUNDAI_GCS_BUCKET': 'woundai-mmhps20261007-media-421209514056',
           'WOUNDAI_SECURITY_BUCKET': 'woundai-mmhps20261007-security-421209514056',
           'WOUNDAI_AUDIT_BUCKET': 'woundai-mmhps20261007-audit-421209514056',
           'WOUNDAI_GCS_PREFIX': 'flywheel'}
IDENTITY = 'woundai-mmhps20261007-runtime@woundai-jackh001.iam.gserviceaccount.com'


def check(snapshot, target_image, *, now=None):
    if not isinstance(target_image, str) or not re.fullmatch(IMAGE, target_image):
        raise ValueError('exact_reviewed_medical_digest_required')
    now = now or datetime.now(timezone.utc)
    errors = []
    if snapshot.get('project') != PROJECT or snapshot.get('service') != SERVICE:
        errors.append('wrong_project_or_service')
    try:
        observed = datetime.fromisoformat(snapshot['observed_at'].replace('Z', '+00:00'))
        age = (now - observed).total_seconds()
        if observed.tzinfo is None or not 0 <= age <= 900:
            errors.append('snapshot_not_fresh')
    except (ValueError, TypeError, KeyError):
        errors.append('snapshot_not_fresh')
    if snapshot.get('iap_enabled') != 'true':
        errors.append('existing_iap_not_confirmed')
    traffic = snapshot.get('traffic')
    revisions = snapshot.get('revisions')
    if not isinstance(traffic, list) or not traffic or not isinstance(revisions, list):
        errors.append('incomplete_revision_inventory')
    else:
        refs, total, candidate = set(), 0, False
        for item in traffic:
            if not isinstance(item, dict):
                errors.append('invalid_traffic'); continue
            name = item.get('revisionName')
            percent = item.get('percent', 0)
            if (not isinstance(name, str) or not name.startswith(SERVICE + '-') or
                    type(percent) is not int or not 0 <= percent <= 100):
                errors.append('unresolved_traffic'); continue
            total += percent
            refs.add(name)
            if item.get('tag') == 'candidate':candidate = True
        if total != 100:errors.append('default_traffic_not_fully_resolved')
        if not candidate:errors.append('candidate_endpoint_missing')
        by_name = {}
        for item in revisions:
            if not isinstance(item, dict) or not isinstance(item.get('name'), str):
                errors.append('invalid_revision'); continue
            if item['name'] in by_name:errors.append('duplicate_revision')
            by_name[item['name']] = item
        if set(by_name) != refs:errors.append('incomplete_revision_inventory')
        for name in refs:
            item = by_name.get(name, {})
            if item.get('image') != target_image:errors.append('exposed_old_or_unreviewed_image')
            if item.get('identity') != IDENTITY:errors.append('unexpected_runtime_identity')
            storage = item.get('storage', {})
            if not isinstance(storage, dict) or any(storage.get(k) != v for k, v in STORAGE.items()):
                errors.append('account_storage_boundary_changed')
    return {'schema': 'mmh.account-cutover-check/1',
            'exposed_revisions_aligned': not errors,
            'blockers': sorted(set(errors)),
            'deployment_or_write_authorized': False,
            'remaining_checks': ['CAS-capable source and immutable build provenance',
                                 'external account writers stopped and coordinated',
                                 'fresh account replay and verified backup',
                                 'real account disable and stale-token denial on every endpoint']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--target-image', required=True)
    args = parser.parse_args()
    try:result = check(json.loads(args.snapshot.read_text()), args.target_image)
    except (OSError, ValueError, TypeError, AttributeError):
        print(json.dumps({'exposed_revisions_aligned': False, 'blockers': ['invalid_input'],
                          'deployment_or_write_authorized': False}));return 2
    print(json.dumps(result, indent=2))
    return 0 if result['exposed_revisions_aligned'] else 1


if __name__ == '__main__':raise SystemExit(main())
