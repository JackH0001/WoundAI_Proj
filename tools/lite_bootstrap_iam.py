"""Offline, bounded bootstrap IAM proposal and policy-delta rehearsal.

No credentials, provider calls, deployment, IAM writes or resource deletion.
Effective access (including inherited/group grants) requires separate live
verification. CAS is an application restriction, not a restriction of DELETE.
"""
import copy
from datetime import datetime, timezone
import re

from lite_fresh_bootstrap_factory import READ, CREATE, MUTABLE
from lite_fresh_bootstrap_runner import checked, preflight
from lite_subscription_executor import canonical
from lite_usage_ledger import digest

MODES = {
    'read': ('storage.objects.get',),
    'create': ('storage.objects.get', 'storage.objects.create'),
    'cas': ('storage.objects.get', 'storage.objects.create', 'storage.objects.delete'),
    'metadata': ('storage.buckets.get',),
}
ROLE_IDS = {mode: 'woundaiLiteBootstrap'+mode.title() for mode in MODES}


def timestamp(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def propose(raw, native_raw, *, expected_config, expected_native, expected_source,
            expected_image, allowed_buckets, clock):
    """Compile additive bucket bindings from the *same* pinned runtime inputs.

    Inspect is synthesized for validation only and never executed. The proposal
    is not an approval, and emits no setIamPolicy command or replacement policy.
    """
    c = checked(raw, expected_config, 256*1024)
    request = dict(schema='lite-bootstrap-request/1', plan=digest(c['plan']),
                   mode='inspect', command=None, inner=None)
    c, _, _ = preflight(raw, native_raw, canonical(request),
        expected_config=expected_config, expected_native=expected_native,
        expected_request=digest(request), expected_source=expected_source,
        expected_image=expected_image, clock=clock)
    if (type(allowed_buckets) not in (list, tuple) or not allowed_buckets
            or any(type(v) is not str or not re.fullmatch(r'[a-z0-9][a-z0-9-]{1,61}[a-z0-9]', v)
                   for v in allowed_buckets)
            or len(set(allowed_buckets)) != len(allowed_buckets)
            or set(allowed_buckets) != set(c['buckets'])):
        raise ValueError('independently reviewed exact bucket set required')
    d, budget = c['deployment'], c['provider_budget']
    member = 'serviceAccount:'+d['service_account']
    window = ("request.time >= timestamp('"+timestamp(budget['not_before'])+"') && "
              "request.time < timestamp('"+timestamp(budget['expires_at'])+"')")
    roles = {m: dict(name='projects/'+d['project']+'/roles/'+ROLE_IDS[m],
                    title='Lite bootstrap '+m, stage='GA', includedPermissions=list(v))
             for m, v in MODES.items()}
    rows = []
    for name, loc in sorted(c['locations'].items()):
        mode = 'read' if name in READ else 'create' if name in CREATE else 'cas' if name in MUTABLE else None
        if mode is None: raise ValueError('unclassified bootstrap lane')
        rows.append(dict(lane=name, mode=mode, **loc))
    rows += [dict(lane='budget-'+n, mode='cas' if n == 'control' else 'create', **v)
             for n, v in sorted(c['budget_stores'].items())]
    bindings = {b: [] for b in sorted(allowed_buckets)}
    for r in rows:
        resource = 'projects/_/buckets/'+r['bucket']+'/objects/'+r['prefix']+'/'
        bindings[r['bucket']].append(dict(role=roles[r['mode']]['name'], members=[member],
            condition=dict(title='lite_bootstrap_'+digest(c['plan'])[:12]+'_'+r['lane'].replace('-', '_'),
                expression="("+window+") && resource.name.startsWith('"+resource+"')")))
    for b in bindings:
        bindings[b].append(dict(role=roles['metadata']['name'], members=[member],
            condition=dict(title='lite_bootstrap_'+digest(c['plan'])[:12]+'_metadata',
                expression="("+window+") && resource.name == 'projects/_/buckets/"+b+"'")))
    return dict(schema='lite-bootstrap-iam-proposal/1', draft=True, applied=False,
        source=expected_source, image=expected_image, config=expected_config, native_config=expected_native,
        plan=digest(c['plan']), principal=member, roles=roles, object_lanes=rows, bucket_bindings=bindings,
        policy_version=3, not_before=budget['not_before'], expires_at=budget['expires_at'],
        storage_list_granted=False, effective_iam_verified=False, provider_calls=0,
        caveat='CAS lanes need DELETE; IAM does not restrict DELETE to compare-and-swap. '
               'Inherited/unconditional grants can bypass these conditions. Remove expired bindings explicitly.')


def check_roles(observations, proposal, *, expected_proposal):
    """Reject preexisting named custom roles with broader/different permissions.

    Missing means an explicit 404 response, never an omitted result or 403.
    Creating the returned roles still requires separate authorization/execution.
    """
    if digest(proposal) != expected_proposal: raise ValueError('proposal changed')
    wanted = {r['name']: r for r in proposal['roles'].values()}
    if type(observations) is not dict or set(observations) != set(wanted):
        raise ValueError('complete custom role readback required')
    missing = []
    for name, role in wanted.items():
        row = observations[name]
        if type(row) is not dict or set(row) != {'status', 'body'}: raise ValueError('unknown role observation')
        if row['status'] == 404 and row['body'] is None:
            missing.append(name); continue
        v = row['body']
        if (type(row['status']) is not int or row['status'] != 200 or type(v) is not dict
                or v.get('name') != name or v.get('stage') != 'GA' or v.get('deleted', False) is not False
                or type(v.get('includedPermissions')) is not list
                or sorted(v['includedPermissions']) != sorted(role['includedPermissions'])):
            raise ValueError('existing role differs or readback unavailable')
    return dict(create_only=sorted(missing), update_existing=[], applied=False)


def checked_policy(policy, etag):
    if (type(policy) is not dict or type(etag) is not str or not etag or policy.get('etag') != etag
            or type(policy.get('version', 1)) is not int or policy.get('version', 1) not in (1, 3)
            or type(policy.get('bindings', [])) is not list):
        raise ValueError('fresh complete policy and exact nonempty etag required')
    for b in policy.get('bindings', []):
        if (type(b) is not dict or type(b.get('role')) is not str or type(b.get('members')) is not list
                or any(type(m) is not str for m in b['members'])):
            raise ValueError('unreadable binding')
    return copy.deepcopy(policy)


def add_delta(policy, proposal, *, bucket, expected_proposal, expected_etag):
    """Prepare an additive policy body; caller must retain etag and use CAS."""
    if (digest(proposal) != expected_proposal or proposal.get('schema') != 'lite-bootstrap-iam-proposal/1'
            or proposal.get('draft') is not True or proposal.get('applied') is not False
            or bucket not in proposal['bucket_bindings']):
        raise ValueError('pinned reviewed proposal required')
    out = checked_policy(policy, expected_etag)
    planned = proposal['bucket_bindings'][bucket]
    current = out.setdefault('bindings', [])
    principal = proposal['principal'].lower()
    for binding in current:
        members = {m.lower() for m in binding['members']}
        if members & {'allusers', 'allauthenticatedusers'}:
            raise ValueError('public bucket grant requires independent review')
        if principal in members and binding not in planned:
            raise ValueError('preexisting bootstrap grant differs; no automatic broadening')
    added = [copy.deepcopy(b) for b in planned if b not in current]
    out['version'] = 3
    out['bindings'].extend(added)
    receipt = dict(schema='lite-bootstrap-iam-delta/1', proposal=expected_proposal,
        bucket=bucket, principal=proposal['principal'], prior_policy=digest(policy),
        proposed_policy=digest(out), base_etag=expected_etag, added=copy.deepcopy(added),
        retained_for_principal=copy.deepcopy([b for b in policy.get('bindings', [])
            if principal in {m.lower() for m in b['members']}]), applied=False)
    return out, receipt


def remove_delta(policy, receipt, *, bucket, expected_receipt, expected_etag):
    """Rehearse removal of exact additions from a fresh policy, not old restore.

    A response-lost caller may read back first and remove only exact surviving
    additions. Changed entries stop for review; unrelated entries are retained.
    The returned body is not sent and does not prove effective access is gone.
    """
    if (digest(receipt) != expected_receipt or receipt.get('schema') != 'lite-bootstrap-iam-delta/1'
            or receipt.get('applied') is not False or type(receipt.get('added')) is not list
            or type(receipt.get('retained_for_principal')) is not list or receipt.get('bucket') != bucket):
        raise ValueError('original pinned delta required')
    out = checked_policy(policy, expected_etag)
    current = out.setdefault('bindings', [])
    for b in current:
        if (receipt['principal'].lower() in {m.lower() for m in b['members']}
                and b not in receipt['added'] and b not in receipt['retained_for_principal']):
            raise ValueError('bootstrap principal has a changed grant; inspect before recovery')
    for b in receipt['added']:
        # Detect a same-purpose entry changed to add users/roles/time/paths.
        title = b['condition']['title']
        collisions = [v for v in current if v.get('condition', {}).get('title') == title]
        if any(v != b for v in collisions):
            raise ValueError('owned binding changed; inspect before recovery')
    removed = [b for b in current if b in receipt['added']]
    out['bindings'] = [b for b in current if b not in receipt['added']]
    out['version'] = 3
    return out, dict(removed=len(removed), before=digest(policy), after=digest(out),
                     effective_access_removed=False, applied=False)
