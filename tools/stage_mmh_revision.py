"""Stage one MMH private revision at zero default traffic; never promote it.

Existing-service updates are separate from the create-only deployment path.
All inputs are revalidated before PATCH; uncertain outcomes are not retried.
Rollback removes the candidate tag and keeps the original revision at 100%.
It does not roll back data written through a candidate's private tagged URL.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import urllib.error
import urllib.request

import create_mmh_private_service as base
from plan_mmh_runtime import canonical, validate

TYPE = 'TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION'
MASK = 'template.containers,template.revision,traffic'
PATCH_PATH = base.NAME + '?updateMask=' + MASK
ROLLBACK_PATH = base.NAME + '?updateMask=traffic'
DEFAULT_PROBE = {'timeoutSeconds': 240, 'periodSeconds': 240,
                 'failureThreshold': 1, 'tcpSocket': {'port': 8080}}


def require(ok, reason):
    base.require(ok, reason)


def revision_name(value):
    prefix = base.NAME + '/revisions/'
    require(isinstance(value, str) and value.startswith(prefix), 'foreign revision')
    short = value[len(prefix):]
    require(re.fullmatch(re.escape(base.SERVICE) + r'-[a-z0-9-]+', short)
            and len(short) <= 63, 'invalid revision name')
    return short


def transition(old, new):
    validate(old); validate(new)
    a, b = copy.deepcopy(old['spec']), copy.deepcopy(new['spec'])
    require(a['image'] != b['image'] and a['source_commit'] != b['source_commit'], 'new immutable source required')
    for s in (a, b):
        for k in ('image', 'source_commit', 'manifest_sha256'): s.pop(k)
        s['environment'].pop('GIT_COMMIT')
    require(a == b, 'only image/source/manifest may change')
    require(a['environment'].get('WOUNDAI_AUDIT_MODE') == 'mmh-unlocked-validation',
            'this updater is only for the reviewed MMH validation environment')


def traffic(old_revision, candidate=None):
    rows = [{'type': TYPE, 'revision': old_revision, 'percent': 100}]
    if candidate:
        rows.append({'type': TYPE, 'revision': candidate, 'percent': 0, 'tag': 'candidate'})
    return rows


def normalized_traffic(rows, observed=False):
    require(isinstance(rows, list) and rows, 'traffic evidence absent')
    clean = []
    allowed = {'type', 'revision', 'percent', 'tag'} | ({'uri'} if observed else set())
    for row in rows:
        require(isinstance(row, dict) and set(row) <= allowed, 'unrecognized traffic fields')
        require(type(row.get('percent', 0)) is int, 'invalid traffic percentage')
        clean.append({k: row.get(k, default) for k, default in
                      [('type', ''), ('revision', ''), ('percent', 0), ('tag', '')]})
    return sorted(clean, key=lambda r: (r['revision'], r['tag']))


def extra_guards(service):
    require(isinstance(service.get('etag'), str) and service['etag'], 'etag required')
    require(isinstance(service.get('uid'), str) and service['uid'], 'service uid required')
    require(not any(service.get(k) for k in ('iapEnabled', 'sshEnabled', 'defaultUriDisabled',
                                            'binaryAuthorization', 'deleteTime', 'expireTime')),
            'unexpected service controls')
    container = service.get('template', {}).get('containers', [{}])[0]
    require(set(container) <= {'image', 'env', 'resources', 'ports', 'startupProbe'},
            'unreviewed container fields would be replaced')
    require(container.get('startupProbe') in (None, DEFAULT_PROBE), 'unreviewed startup probe')


def containers(new, probe):
    require(probe in (None, DEFAULT_PROBE), 'unreviewed startup probe')
    result = base.payload(new)['template']['containers']
    if probe is not None:
        result[0]['startupProbe'] = copy.deepcopy(probe)
    return result


def proposal(old, new, service, policy):
    transition(old, new)
    base.check_ready(old, service, policy)
    extra_guards(service)
    old_revision = revision_name(service['latestReadyRevision'])
    statuses = service.get('trafficStatuses')
    require(isinstance(statuses, list) and len(statuses) == 1, 'baseline traffic ambiguous')
    status = statuses[0]
    require(status.get('revision') == old_revision and status.get('percent') == 100
            and not status.get('tag') and status.get('type') in (TYPE, 'TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST'),
            'baseline observed traffic differs')
    candidate = base.SERVICE + '-c' + new['spec']['source_commit'][:12]
    require(candidate != old_revision, 'candidate already current')
    probe = service['template']['containers'][0].get('startupProbe')
    request = {'name': base.NAME, 'etag': service['etag'],
               'template': {'containers': containers(new, probe), 'revision': candidate},
               'traffic': traffic(old_revision, candidate)}
    spec = {'old_plan': old, 'new_plan': new, 'baseline_uid': service['uid'],
            'baseline_generation': service['generation'], 'old_revision': old_revision,
            'candidate_revision': candidate, 'startup_probe': probe, 'request': request, 'path': PATCH_PATH}
    return {'schema': 'mmh.zero-traffic-update/1', 'spec': spec,
            'sha256': hashlib.sha256(canonical(spec)).hexdigest()}


def verify_proposal(p):
    require(p.get('schema') == 'mmh.zero-traffic-update/1', 'unknown update schema')
    s = p['spec']; transition(s['old_plan'], s['new_plan'])
    require(p['sha256'] == hashlib.sha256(canonical(s)).hexdigest(), 'proposal hash mismatch')
    revision_name(base.NAME + '/revisions/' + s['old_revision'])
    require(s['candidate_revision'] == base.SERVICE + '-c' + s['new_plan']['spec']['source_commit'][:12], 'candidate mismatch')
    req = s['request']
    expected = {'name': base.NAME, 'etag': req.get('etag'),
                'template': {'containers': containers(s['new_plan'], s['startup_probe']),
                             'revision': s['candidate_revision']},
                'traffic': traffic(s['old_revision'], s['candidate_revision'])}
    require(req == expected and isinstance(req['etag'], str) and req['etag']
            and s['path'] == PATCH_PATH and s['baseline_uid'], 'request exceeds image-only stage scope')


def metadata(new, build_id, run=base.cloud, main=base.remote_main):
    report = base.inspect(new, build_id, run, main)
    # Creation-only absence is replaced by an exact live existing-service check.
    checks = [c for c in report['checks'] if c['check'] != 'mmh_service_absent']
    require(report['complete'] and len(report['checks']) == 13 and len(checks) == 12
            and all(c['passed'] is True for c in checks), 'update metadata prerequisites not met')
    return report


def rest(method, path, body=None):
    if method == 'GET':
        return base.rest(method, path, body)
    require(method == 'PATCH' and path in (PATCH_PATH, ROLLBACK_PATH), 'unapproved operation')
    require(isinstance(body, dict) and body.get('name') == base.NAME and body.get('etag'), 'target/etag required')
    req = urllib.request.Request(base.API + path, method='PATCH', data=json.dumps(body).encode(),
        headers={'Authorization': 'Bearer ' + base.access_token(), 'Content-Type': 'application/json',
                 'x-goog-user-project': base.PROJECT})
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError('Cloud Run HTTP ' + str(exc.code)) from None


def save(path, data):
    path.write_text(json.dumps(data, indent=2)+'\n')


def stage(p, build_id, journal, confirm=None, api=rest, run=base.cloud,
          main=base.remote_main, effective=base.live_effective):
    verify_proposal(p)
    require(confirm is None or confirm == p['sha256'], 'exact proposal confirmation required')
    journal = Path(journal); journal.mkdir(parents=True, exist_ok=False)
    s = p['spec']; state = {'proposal_sha256': p['sha256'], 'attempted': False, 'accepted': False, 'state': 'checking'}
    save(journal/'proposal.json', p); save(journal/'result.json', state)
    try:
        new = s['new_plan']
        save(journal/'metadata.json', metadata(new, build_id, run, main))
        inventory = base.inventory_and_grants(new, run)
        require(effective(new, inventory, journal/'effective-iam.json') is True, 'effective IAM not proven')
        metadata(new, build_id, run, main)
        require(base.inventory_and_grants(new, run) == inventory, 'inventory changed during checks')
        service = api('GET', base.NAME); policy = api('GET', base.NAME+':getIamPolicy')
        fresh = proposal(s['old_plan'], new, service, policy)
        require(fresh == p, 'stale service/etag; generate a new proposal and review')
        if confirm is None:
            state['state'] = 'checked_no_update'; save(journal/'result.json', state); return state
        state.update(attempted=True, state='outcome_unknown'); save(journal/'result.json', state)
        operation = api('PATCH', PATCH_PATH, s['request'])
        require(re.fullmatch(re.escape(base.PARENT)+r'/operations/[A-Za-z0-9_-]+', operation.get('name', '')), 'invalid operation')
        state.update(state='submitted_not_accepted', operation=operation['name'])
        save(journal/'result.json', state); return state
    except Exception as exc:
        state.update(state='outcome_unknown' if state['attempted'] else 'blocked', error_type=type(exc).__name__)
        save(journal/'result.json', state); raise


def check_candidate(p, service, policy, rolled_back=False):
    verify_proposal(p); s=p['spec']; extra_guards(service)
    require(service['uid'] == s['baseline_uid'], 'service replaced')
    require(service['template']['containers'][0].get('startupProbe') == s['startup_probe'], 'startup probe changed')
    expected = traffic(s['old_revision'], None if rolled_back else s['candidate_revision'])
    require(normalized_traffic(service.get('traffic')) == normalized_traffic(expected), 'desired traffic changed')
    require(normalized_traffic(service.get('trafficStatuses'), True) == normalized_traffic(expected), 'observed traffic changed')
    require(service.get('latestReadyRevision') == base.NAME+'/revisions/'+s['candidate_revision'], 'candidate revision mismatch')
    # Reuse all existing image/env/IAM/resource checks; traffic was verified above.
    normalized = copy.deepcopy(service)
    normalized['traffic'] = base.payload(s['new_plan'])['traffic']
    base.check_ready(s['new_plan'], normalized, policy)
    require(int(service['generation']) > int(s['baseline_generation']), 'service generation did not advance')
    return {'candidate_ready': True, 'default_traffic_to_previous': 100,
            'candidate_default_traffic': 0, 'candidate_tag_removed': rolled_back,
            'gcs_e2e_passed': False, 'promoted': False}


def status(p, journal, api=rest, run=base.cloud):
    verify_proposal(p); journal=Path(journal)
    state=json.loads((journal/'result.json').read_text())
    require(state.get('proposal_sha256') == p['sha256'] and state.get('attempted') is True, 'journal mismatch')
    state.update(accepted=False, state='verifying'); save(journal/'result.json', state)
    if state.get('operation'):
        op=api('GET',state['operation'])
        require(not op.get('error'), 'operation failed; inspect before recovery')
        if not op.get('done'): return state
    service=api('GET',base.NAME); policy=api('GET',base.NAME+':getIamPolicy')
    base.check_project(run(['projects','get-iam-policy',base.PROJECT]))
    result=check_candidate(p,service,policy)
    save(journal/'service-readback.json',service)
    state.update(accepted=True,state='private_candidate_ready_e2e_pending',**result)
    save(journal/'result.json',state); return state


def rollback_request(p, service, policy):
    # Traffic-only recovery, not a rollback of data or the service template.
    check_candidate(p,service,policy)
    return {'name':base.NAME,'etag':service['etag'],'traffic':traffic(p['spec']['old_revision'])}


def main_cli():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['prepare','check','stage','status'])
    parser.add_argument('--old-plan',type=Path);parser.add_argument('--new-plan',type=Path)
    parser.add_argument('--proposal',type=Path,required=True)
    parser.add_argument('--build-id');parser.add_argument('--journal',type=Path)
    parser.add_argument('--confirm-sha256')
    a=parser.parse_args()
    if a.mode=='prepare':
        require(a.old_plan and a.new_plan and not a.proposal.exists(),'new proposal and both plans required')
        p=proposal(json.loads(a.old_plan.read_text()),json.loads(a.new_plan.read_text()),
                   rest('GET',base.NAME),rest('GET',base.NAME+':getIamPolicy'))
        save(a.proposal,p);print(json.dumps({'proposal_sha256':p['sha256'],'cloud_mutations':False}));return
    p=json.loads(a.proposal.read_text());require(a.journal,'journal required')
    if a.mode=='status': result=status(p,a.journal)
    else:
        require(a.build_id,'build ID required')
        require(a.mode!='stage' or a.confirm_sha256,'stage requires exact confirmation')
        require(a.mode!='check' or not a.confirm_sha256,'check cannot mutate')
        result=stage(p,a.build_id,a.journal,a.confirm_sha256)
    print(json.dumps(result,indent=2))


if __name__=='__main__':main_cli()
