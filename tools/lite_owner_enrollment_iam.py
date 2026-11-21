"""Prepare or recover one conditional GCS bucket-policy change offline."""
import argparse
import re
import sys
import time
from pathlib import Path

from audit_chain_contract import loads_json_object_strict
from lite_bootstrap_iam import add_delta, check_roles, remove_delta, timestamp
from lite_native_schema import text
from lite_operational_materials import _write_exclusive, canonical, read_private, sha
from lite_usage_ledger import digest

PURPOSE = 'owner-enrollment'
ROLE_ID = 'woundaiLiteSecurityObjects'
BUCKET_READ = ['storage.buckets.get']
OBJECT_ACCESS = ['storage.objects.get', 'storage.objects.create', 'storage.objects.delete']


def pinned(raw, expected):
    if sha(raw) != text(expected, r'[0-9a-f]{64}'):
        raise ValueError('reviewed file digest differs')
    return loads_json_object_strict(raw.decode())


def proposal(scope, scope_hash):
    project = text(scope['project'], r'[a-z][a-z0-9-]{4,28}[a-z0-9]')
    number = text(scope['project_number'], r'[1-9][0-9]{5,19}')
    bucket = 'woundai-lite-native-state-' + number
    if (scope['schema'] != 'lite-owner-enrollment-scope/1' or scope['bucket'] != bucket
            or scope['region'] != 'asia-east1' or scope['bucket_permissions'] != BUCKET_READ
            or scope['object_permissions'] != OBJECT_ACCESS):
        raise ValueError('unexpected enrollment scope')
    account = text(scope['service_account'], r'[a-z][a-z0-9-]{4,28}[a-z0-9]@' + re.escape(project)
                   + r'\.iam\.gserviceaccount\.com')
    first, last = scope['starts'], scope['until']
    if (type(first) is not int or type(last) is not int or not 0 <= first < last <= first + 900
            or first // 86400 != (last - 1) // 86400):
        raise ValueError('invalid enrollment window')
    if not isinstance(scope['objects'], list) or len(scope['objects']) != 2:
        raise ValueError('exactly two enrollment objects required')
    security, budget = scope['objects']
    text(security, r'lite-native/[a-z0-9_-]{1,64}/security/[0-9a-f]{64}\.json')
    namespace = security.split('/')[1]
    if budget != ('lite-native/' + namespace + '/owner-enrollment-budget/'
                  + sha(b'woundlite/global-request-budget/1') + '.json'):
        raise ValueError('unexpected enrollment budget')
    resource = 'projects/_/buckets/' + bucket
    expression = (
        f"request.time >= timestamp('{timestamp(first)}') && request.time < timestamp('{timestamp(last)}') && "
        f"((resource.type == 'storage.googleapis.com/Bucket' && resource.name == '{resource}') || "
        f"(resource.type == 'storage.googleapis.com/Object' && "
        f"(resource.name == '{resource}/objects/{security}' || resource.name == '{resource}/objects/{budget}')))"
    )
    name = 'projects/' + project + '/roles/' + ROLE_ID
    role = dict(name=name, includedPermissions=BUCKET_READ + OBJECT_ACCESS)
    member = 'serviceAccount:' + account
    plan = text(scope['plan_sha256'], r'[0-9a-f]{64}')
    binding = dict(role=name, members=[member], condition=dict(title='lite_owner_enrollment_' + plan[:20],
                                                              expression=expression))
    return dict(schema='lite-owner-enrollment-iam-proposal/1', draft=True, applied=False,
                principal=member, roles={ROLE_ID: role}, scope=scope_hash, bucket_bindings={bucket: [binding]})


def draft(scope_raw, scope_hash, observation, etag, *, role=None, original=None, clock=None):
    scope = pinned(scope_raw, scope_hash)
    wanted = proposal(scope, scope_hash)
    bucket = scope['bucket']
    if (observation.get('bucket') != bucket or observation.get('status') != 200
            or observation.get('requested_policy_version') != 3):
        raise ValueError('complete conditional policy readback for the reviewed bucket required')
    policy = observation['body']
    if policy.get('resourceId', 'projects/_/buckets/' + bucket) != 'projects/_/buckets/' + bucket:
        raise ValueError('policy resource differs')
    existing = policy.get('bindings', [])
    if (any('_withcond_' in b['role'] for b in existing)
            or (policy.get('version', 1) != 3 and any('condition' in b for b in existing))):
        raise ValueError('incomplete conditional policy readback')
    binding, = wanted['bucket_bindings'][bucket]
    matching = [b for b in existing
                if b.get('condition', {}).get('title') == binding['condition']['title']]
    if matching not in ([], [binding]):
        raise ValueError('enrollment binding is ambiguous or changed')
    proposal_hash = digest(wanted)
    if (role is None) == (original is None):
        raise ValueError('provide either the existing role observation or the original grant draft')
    if original is None:
        if (time.time() if clock is None else clock()) >= scope['until']:
            raise ValueError('enrollment window expired')
        roles = check_roles({binding['role']: role}, wanted, expected_proposal=proposal_hash)
        if roles['create_only']:
            raise ValueError('existing enrollment role required')
        out, receipt = add_delta(policy, wanted, bucket=bucket, expected_proposal=proposal_hash,
                                 expected_etag=etag, purpose=PURPOSE)
        return dict(policy=out, receipt=receipt)
    grant = pinned(*original)
    receipt = grant['receipt']
    if (receipt['proposal'] != proposal_hash or receipt['principal'] != wanted['principal']
            or receipt['proposed_policy'] != digest(grant['policy'])
            or (receipt['added'], receipt['retained_for_principal']) not in (([binding], []), ([], [binding]))):
        raise ValueError('original grant is for a different enrollment or binding')
    out, recovery = remove_delta(policy, receipt, bucket=bucket, expected_receipt=digest(receipt),
                                 expected_etag=etag, purpose=PURPOSE)
    if not recovery['removed']:
        # A lost write may never have committed. Preserve even a v1 policy on a no-op.
        out = policy
        recovery['after'] = recovery['before']
    return dict(policy=out, recovery=recovery)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('grant', 'recover'))
    for name in ('scope', 'policy', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    for name in ('scope-sha256', 'etag'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--role', type=Path)
    parser.add_argument('--original', type=Path)
    parser.add_argument('--original-sha256')
    args = parser.parse_args(argv)
    grant = args.action == 'grant'
    if grant != bool(args.role) or grant == bool(args.original) or grant == bool(args.original_sha256):
        parser.error('grant requires --role. recover requires --original and --original-sha256.')
    try:
        scope = read_private(args.scope)
        observed = loads_json_object_strict(read_private(args.policy).decode())
        role = loads_json_object_strict(read_private(args.role).decode()) if grant else None
        original = None if grant else (read_private(args.original), args.original_sha256)
        result = canonical(draft(scope, args.scope_sha256, observed, args.etag, role=role, original=original))
        if grant and time.time() >= pinned(scope, args.scope_sha256)['until']:
            raise ValueError('expired before save')
        _write_exclusive(args.output, result)
        if read_private(args.output) != result:
            raise ValueError('private output readback differs')
    except Exception:
        print('No policy was sent. Check the private inputs, reviewed hashes, etag, window and output path.',
              file=sys.stderr)
        return 1
    print('Offline policy draft saved. SHA-256', sha(result))
    return 0


if __name__ == '__main__':
    sys.exit(main())
