"""Create-only MMH private service entry. Never provisions IAM/secrets or locks buckets.

Default check is read-only. Creation needs live metadata, exact resource grants,
old-runtime and new-runtime effective IAM, a reviewed plan confirmation, and a
new exclusive journal. Uncertain POST outcomes are never automatically retried.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import time
import urllib.error
import urllib.request

from check_mmh_runtime_preflight import inspect, remote_main
from plan_mmh_runtime import validate, REGION, SERVICE, SECRET_NAMES
from provision_mmh_foundation import PROJECT, NUMBER, SA, BUCKETS
from restrict_mmh_bucket_iam import TARGET
from verify_mmh_effective_iam import cases as legacy_cases, assess, request_v3, access_token

PARENT = 'projects/' + PROJECT + '/locations/' + REGION
NAME = PARENT + '/services/' + SERVICE
API = 'https://run.googleapis.com/v2/'
MEMBER = 'serviceAccount:' + SA
IMPERSONATION = ('iam.serviceAccounts.actAs', 'iam.serviceAccounts.getAccessToken',
                 'iam.serviceAccounts.getOpenIdToken', 'iam.serviceAccounts.implicitDelegation',
                 'iam.serviceAccounts.signBlob', 'iam.serviceAccounts.signJwt',
                 'iam.serviceAccountKeys.create', 'iam.serviceAccounts.setIamPolicy')


def require(ok, message):
    if not ok:
        raise ValueError(message)


def cloud(args):
    # Only metadata/IAM discovery; no secret payload or mutating CLI is allowed.
    prefixes = [('projects', 'describe'), ('projects', 'get-iam-policy'),
                ('builds', 'describe'), ('artifacts', 'docker', 'images', 'describe'),
                ('storage', 'buckets', 'describe'), ('storage', 'buckets', 'list'),
                ('storage', 'buckets', 'get-iam-policy'), ('iam', 'roles', 'describe'),
                ('iam', 'service-accounts', 'describe'), ('iam', 'service-accounts', 'list'),
                ('run', 'services', 'list'), ('secrets', 'versions', 'describe'),
                ('secrets', 'list'), ('secrets', 'get-iam-policy')]
    require(any(tuple(args[:len(p)]) == p for p in prefixes), 'read-only command required')
    r = subprocess.run(['gcloud', *args, '--project='+PROJECT, '--format=json'],
                       capture_output=True, text=True, timeout=120)
    require(r.returncode == 0, 'cloud metadata unavailable')
    return json.loads(r.stdout)


def payload(plan):
    validate(plan)
    s = plan['spec']
    env = [{'name': k, 'value': v} for k, v in sorted(s['environment'].items())]
    for key, ref in sorted(s['secret_versions'].items()):
        secret, version = ref.split(':')
        env.append({'name': key, 'valueSource': {'secretKeyRef': {
            'secret': 'projects/'+NUMBER+'/secrets/'+secret, 'version': version}}})
    # CreateService takes the identifier in ?serviceId=. The resource name is
    # assigned by the API; sending it in the create body is INVALID_ARGUMENT.
    # Read-back identity is still checked against NAME in check_ready().
    return {'ingress': 'INGRESS_TRAFFIC_ALL', 'invokerIamDisabled': False,
            'scaling': {'minInstanceCount': 0, 'maxInstanceCount': 1, 'scalingMode': 'AUTOMATIC'},
            'template': {'serviceAccount': SA, 'timeout': '120s',
                         'maxInstanceRequestConcurrency': 1,
                         'scaling': {'minInstanceCount': 0, 'maxInstanceCount': 1},
                         'containers': [{'image': s['image'], 'env': env,
                             'resources': {'limits': {'cpu': '2', 'memory': '4Gi'}, 'cpuIdle': True},
                             'ports': [{'containerPort': 8080}]}]},
            'traffic': [{'type': 'TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST', 'percent': 100}]}


def exact_bindings(policy, expected):
    require(isinstance(policy, dict), 'policy unavailable')
    found = {}
    for b in policy.get('bindings', []):
        require(set(b) == {'role', 'members'} and b['role'] not in found,
                'conditional/duplicate/unrecognized binding')
        members = b['members']
        require(isinstance(members, list) and len(members) == len(set(members)), 'invalid members')
        found[b['role']] = set(members)
    require(found == expected, 'resource grants differ from reviewed plan')


def check_project(policy):
    require(isinstance(policy.get('bindings'), list) and policy.get('etag'), 'project policy unavailable')
    for b in policy['bindings']:
        require(set(b) == {'role', 'members'}, 'conditional project grant requires review')
        require(isinstance(b['members'], list) and b['members'], 'invalid project binding')
        for member in b['members']:
            require(isinstance(member, str) and re.fullmatch(r'(user|serviceAccount):[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+', member),
                    'group/domain/public/federated/unknown project principal requires review')
            require(member.lower() != MEMBER.lower(), 'runtime project role prohibited')


def inventory_and_grants(plan, run=cloud):
    validate(plan)
    project = run(['projects', 'describe', PROJECT])
    require(project.get('projectId') == PROJECT and str(project.get('projectNumber')) == NUMBER
            and not project.get('parent'), 'project/ancestor scope changed')
    check_project(run(['projects', 'get-iam-policy', PROJECT]))
    for g in plan['spec']['planned_bucket_grants']:
        role_name = 'projects/'+PROJECT+'/roles/'+g['role_id']
        r = run(['iam', 'roles', 'describe', g['role_id']])
        require(r.get('name') == role_name and r.get('deleted', False) is False
                and r.get('stage') == 'GA' and sorted(r.get('includedPermissions', [])) == sorted(g['permissions']),
                'custom role differs from reviewed permission set')
        expected = {**TARGET, role_name: {MEMBER}}
        exact_bindings(run(['storage', 'buckets', 'get-iam-policy', 'gs://'+g['bucket']]), expected)
    for secret in SECRET_NAMES.values():
        exact_bindings(run(['secrets', 'get-iam-policy', secret]), {'roles/secretmanager.secretAccessor': {MEMBER}})
    buckets = run(['storage', 'buckets', 'list'])
    secrets = run(['secrets', 'list'])
    accounts = run(['iam', 'service-accounts', 'list'])
    require(all(isinstance(x, list) and x for x in (buckets, secrets, accounts)), 'complete resource inventory required')
    bnames = [b.get('name') for b in buckets]
    snames = [s.get('name') for s in secrets]
    emails = [s.get('email') for s in accounts]
    require(all(isinstance(b, str) and re.fullmatch('[a-z0-9][a-z0-9._-]+', b) for b in bnames)
            and len(bnames) == len(set(bnames)) and set(BUCKETS) <= set(bnames), 'bucket inventory invalid')
    require(all(isinstance(s, str) and re.fullmatch('projects/'+NUMBER+r'/secrets/[A-Za-z0-9_-]+', s) for s in snames)
            and len(snames) == len(set(snames))
            and {'projects/'+NUMBER+'/secrets/'+n for n in SECRET_NAMES.values()} <= set(snames), 'secret inventory invalid')
    require(all(isinstance(s, str) and re.fullmatch(r'[A-Za-z0-9._-]+@[A-Za-z0-9.-]+', s) for s in emails)
            and len(emails) == len(set(emails)) and SA in emails, 'identity inventory invalid')
    return bnames, snames, emails


def runtime_cases(plan, inventory):
    validate(plan)
    buckets, secrets, accounts = inventory
    grants = {g['bucket']: set(g['permissions']) for g in plan['spec']['planned_bucket_grants']}
    rows = []
    for bucket in buckets:
        for permission in ('storage.objects.get', 'storage.objects.list', 'storage.objects.create',
                           'storage.objects.delete', 'storage.buckets.get', 'storage.buckets.delete',
                           'storage.buckets.update', 'storage.buckets.setIamPolicy'):
            rows.append((SA, '//storage.googleapis.com/projects/_/buckets/'+bucket,
                         permission, permission in grants.get(bucket, set())))
    for secret in secrets:
        own = secret.rsplit('/', 1)[-1] in SECRET_NAMES.values()
        for permission in ('secretmanager.versions.access', 'secretmanager.secrets.setIamPolicy'):
            rows.append((SA, '//secretmanager.googleapis.com/'+secret, permission,
                         own and permission == 'secretmanager.versions.access'))
    for account in accounts:
        for permission in IMPERSONATION:
            rows.append((SA, '//iam.googleapis.com/projects/'+PROJECT+'/serviceAccounts/'+account, permission, False))
    rows.append((SA, '//cloudresourcemanager.googleapis.com/projects/'+PROJECT,
                 'resourcemanager.projects.setIamPolicy', False))
    return rows


def effective_response(case, token):
    """Refresh an expired caller token once for this read-only IAM query."""
    try:
        response = request_v3(*case[:3], token)
    except urllib.error.HTTPError as exc:
        if exc.code != 401:
            raise
        token = access_token()
        # A second 401, failed refresh, or any other error still stops the gate.
        response = request_v3(*case[:3], token)
    return response, token


def live_effective(plan, inventory, report):
    matrix = legacy_cases('least-privilege') + runtime_cases(plan, inventory)
    report.write_text(json.dumps({'complete': False, 'total': len(matrix)})+'\n')
    token = access_token()  # memory only; no impersonation credentials are minted
    results = []
    for i, case in enumerate(matrix):
        if i:
            time.sleep(7)
        response, token = effective_response(case, token)
        row = assess(response, *case)
        results.append(row)
        report.write_text(json.dumps({'complete': False, 'cases': results, 'total': len(matrix)}, indent=2)+'\n')
        require(row['passed'], 'effective permission failed or unknown')
    report.write_text(json.dumps({'complete': True, 'passed': len(results), 'total': len(matrix),
                                 'cases': results}, indent=2)+'\n')
    return True


def rest(method, path, body=None):
    # Only the one fixed create operation and its GET readbacks; no PATCH/DELETE/IAM writes.
    allowed_get = path == NAME or path == NAME+':getIamPolicy' or bool(re.fullmatch(re.escape(PARENT)+r'/operations/[A-Za-z0-9_-]+', path))
    require((method == 'POST' and path == PARENT+'/services?serviceId='+SERVICE)
            or (method == 'GET' and allowed_get), 'unapproved Cloud Run operation')
    token = access_token()
    req = urllib.request.Request(API+path, method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={'Authorization': 'Bearer '+token, 'Content-Type': 'application/json', 'x-goog-user-project': PROJECT})
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            return json.load(response)
    except urllib.error.HTTPError as e:
        # Never echo response bodies, which may contain deployment diagnostics.
        raise RuntimeError('Cloud Run HTTP '+str(e.code)) from None


def check_ready(plan, service, policy):
    desired = payload(plan)
    require(service.get('name') == NAME and service.get('ingress') == desired['ingress']
            and service.get('invokerIamDisabled', False) is False, 'service identity/invoker mismatch')
    require(bool(re.fullmatch(r'[1-9][0-9]*', str(service.get('generation', '')))), 'missing service generation')
    require(not service.get('reconciling') and service.get('terminalCondition', {}).get('state') == 'CONDITION_SUCCEEDED'
            and service.get('generation') == service.get('observedGeneration')
            and service.get('latestReadyRevision') == service.get('latestCreatedRevision')
            and bool(service.get('latestReadyRevision')), 'service not reconciled/ready')
    scale = service.get('scaling', {})
    require(scale.get('minInstanceCount', 0) == 0 and scale.get('maxInstanceCount') == 1
            and scale.get('scalingMode', 'AUTOMATIC') == 'AUTOMATIC'
            and not scale.get('manualInstanceCount'), 'service scaling mismatch')
    t = service.get('template', {}); expected = desired['template']
    for key in ('serviceAccount', 'timeout', 'maxInstanceRequestConcurrency'):
        require(t.get(key) == expected[key], 'revision setting mismatch')
    require(t.get('scaling', {}).get('minInstanceCount', 0) == 0
            and t.get('scaling', {}).get('maxInstanceCount') == 1, 'scaling mismatch')
    require(not t.get('volumes') and not t.get('vpcAccess') and not t.get('healthCheckDisabled'), 'unexpected revision configuration')
    containers = t.get('containers', [])
    require(len(containers) == 1, 'unexpected containers')
    c = containers[0]; e = expected['containers'][0]
    require(c.get('image') == e['image'] and sorted(c.get('env', []), key=lambda x:x['name']) == sorted(e['env'], key=lambda x:x['name']), 'image/environment mismatch')
    require(c.get('resources', {}).get('limits') == e['resources']['limits']
            and c.get('resources', {}).get('cpuIdle', True) is True, 'resource mismatch')
    require(c.get('ports') == e['ports'], 'container port mismatch')
    require(not service.get('buildConfig') and not service.get('multiRegionSettings')
            and not service.get('customAudiences'), 'unexpected service overrides')
    require(not c.get('command') and not c.get('args') and not c.get('volumeMounts'), 'unreviewed execution override')
    require(service.get('traffic') == desired['traffic'], 'traffic mismatch')
    exact_bindings(policy, {})  # no service-level invocation grants, inherits only reviewed project policy
    return {'private_service_ready': True, 'gcs_e2e_passed': False, 'mobile_released': False}


def audit_empty():
    # Full bucket, not merely the current audit prefix; maximum one metadata row.
    url = 'https://storage.googleapis.com/storage/v1/b/'+BUCKETS[2]+'/o?maxResults=1&fields=items(name),nextPageToken'
    req = urllib.request.Request(url, headers={'Authorization': 'Bearer '+access_token()})
    with urllib.request.urlopen(req, timeout=60) as response:
        row = json.load(response)
    require(isinstance(row, dict) and not row.get('items') and not row.get('nextPageToken'), 'audit epoch is not empty')
    return True


def execute(plan, build_id, journal, confirm_sha=None, run=cloud, main=remote_main,
            effective=live_effective, api=rest, empty=audit_empty):
    journal = Path(journal)
    validate(plan)
    creating = confirm_sha is not None
    require(not creating or confirm_sha == plan['spec_sha256'], 'explicit exact plan confirmation required')
    # Exclusive directory prevents accidental reuse of an old green journal or duplicate POST.
    journal.mkdir(parents=True, exist_ok=False)
    state = {'state': 'checking', 'creation_attempted': False, 'deployed': False,
             'spec_sha256': plan['spec_sha256'], 'started_at': datetime.now(timezone.utc).isoformat()}
    def save():
        (journal/'result.json').write_text(json.dumps(state, indent=2)+'\n')
    save()
    try:
        preflight = inspect(plan, build_id, run, main)
        (journal/'metadata.json').write_text(json.dumps(preflight, indent=2)+'\n')
        require(preflight['complete'] and preflight['metadata_passed'], 'metadata prerequisites not met')
        inventory = inventory_and_grants(plan, run)
        require(empty() is True, 'empty epoch not proven')
        require(effective(plan, inventory, journal/'effective-iam.json') is True, 'effective IAM not proven')
        # Effective IAM takes minutes: repeat mutable metadata and grant inventory before POST.
        require(inspect(plan, build_id, run, main)['metadata_passed'], 'metadata changed during IAM checks')
        require(inventory_and_grants(plan, run) == inventory, 'resource inventory changed')
        require(empty() is True, 'audit epoch changed')
        request = payload(plan)
        (journal/'request.json').write_text(json.dumps(request, indent=2)+'\n')
        if not creating:
            state['state'] = 'preflight_passed_no_create'; save(); return state
        state['creation_attempted'] = True; state['state'] = 'create_outcome_unknown'; save()
        operation = api('POST', PARENT+'/services?serviceId='+SERVICE, request)
        name = operation.get('name', '')
        require(bool(re.fullmatch(re.escape(PARENT)+r'/operations/[A-Za-z0-9_-]+', name)), 'unexpected operation name')
        state['operation'] = name; state['state'] = 'submitted_not_accepted'; save()
        return state
    except Exception as exc:
        state['state'] = 'create_outcome_unknown' if state['creation_attempted'] else 'blocked'
        state['error_type'] = type(exc).__name__; save()
        raise


def status(plan, journal, api=rest, run=cloud):
    validate(plan)
    journal = Path(journal)
    state = json.loads((journal/'result.json').read_text())
    require(state.get('spec_sha256') == plan['spec_sha256'] and state.get('creation_attempted') is True,
            'journal does not match an attempted create')
    state.update(state='verifying_readback', private_service_ready=False, deployed=False)
    (journal/'result.json').write_text(json.dumps(state, indent=2)+'\n')
    if state.get('operation'):
        op = api('GET', state['operation'])
        require(not op.get('error'), 'Cloud Run operation failed; preserve state for inspection')
        if not op.get('done'):
            return {'state': 'operation_pending', 'deployed': False}
    service = api('GET', NAME)
    policy = api('GET', NAME+':getIamPolicy')
    check_project(run(['projects', 'get-iam-policy', PROJECT]))
    result = check_ready(plan, service, policy)
    (journal/'service-readback.json').write_text(json.dumps(service, indent=2)+'\n')
    # No general deploy success until authenticated GCS E2E is done.
    state.update(state='private_service_ready_e2e_pending', **result)
    (journal/'result.json').write_text(json.dumps(state, indent=2)+'\n')
    return state


def main_cli():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode', choices=['check', 'create', 'status'])
    p.add_argument('--plan', type=Path, required=True); p.add_argument('--build-id')
    p.add_argument('--journal', type=Path, required=True); p.add_argument('--confirm-plan-sha256')
    a = p.parse_args(); plan = json.loads(a.plan.read_text())
    try:
        if a.mode == 'status':
            require(a.confirm_plan_sha256 is None, 'status cannot create')
            result = status(plan, a.journal)
        else:
            require((a.mode == 'create') == (a.confirm_plan_sha256 is not None), 'create requires exact confirmation; check cannot create')
            result = execute(plan, a.build_id, a.journal, a.confirm_plan_sha256)
        print(json.dumps(result))
    except Exception as exc:
        print(json.dumps({'state': 'blocked_or_requires_inspection', 'error_type': type(exc).__name__}))
        raise SystemExit(1)


if __name__ == '__main__':
    main_cli()
