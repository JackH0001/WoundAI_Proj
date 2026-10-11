"""Image-only MMH candidate replacement that preserves the existing IAP boundary.

Separate from the pre-IAP creator/updater. No IAM, OAuth, secret or IAP writes.
Unknown outcomes require readback, never automatic PATCH retry. Rollback only
restores previous traffic/tag routing, never data or the service template.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import urllib.request

import stage_mmh_revision as old
from plan_mmh_runtime import canonical

base = old.base
UID = 'ed1bb40f-1c67-4e1a-844f-39a61146769c'
IAP = 'projects/421209514056/iap_web/cloud_run-asia-east1/services/' + base.SERVICE
ANCESTORS = ['projects/421209514056/iap_web', 'projects/421209514056/iap_web/cloud_run-asia-east1']
WEB = '421209514056-c62hg9vv5p7g2l96n2efig269kcsads6.apps.googleusercontent.com'
AGENT = 'serviceAccount:service-421209514056@gcp-sa-iap.iam.gserviceaccount.com'
USER = 'user:jack.hou@gmail.com'
require = base.require
# Only API output metadata can vary outside the narrowly allowed image update.
OUTPUT = {'etag','generation','observedGeneration','updateTime','terminalCondition','conditions',
          'latestReadyRevision','latestCreatedRevision','reconciling','lastModifier'}


def digest(x): return hashlib.sha256(canonical(x)).hexdigest()


def policy(p, bindings):
    require(isinstance(p, dict) and set(p) <= {'etag','version','bindings'} and p.get('etag'), 'policy shape/etag')
    require(p.get('version', 1) in (1,3), 'policy version')
    b = copy.deepcopy(p.get('bindings', []))
    for row in b:
        if isinstance(row, dict) and isinstance(row.get('members'), list):
            row['members'] = [USER if isinstance(m,str) and m.casefold()==USER else m for m in row['members']]
    require(b == bindings, 'unreviewed access binding')


def entry(boundary):
    require(set(boundary) == {'service','run_policy','project_policy','project_iap_settings','iap'}, 'boundary shape')
    s=boundary['service']
    require(s.get('name')==base.NAME and s.get('uid')==UID and s.get('iapEnabled') is True,
            'IAP/service identity changed')
    policy(boundary['run_policy'], [{'role':'roles/run.invoker','members':[AGENT]}])
    base.check_project(boundary['project_policy'])
    require(boundary['project_iap_settings']=={'name':'projects/421209514056'}, 'project IAP settings changed')
    require(set(boundary['iap']) == set(ANCESTORS+[IAP]), 'IAP hierarchy incomplete')
    for name in ANCESTORS:
        row=boundary['iap'][name]
        require(set(row)=={'settings','policy'} and row['settings']=={'name':name}, 'ancestor settings changed')
        policy(row['policy'], [])
    row=boundary['iap'][IAP]
    require(set(row)=={'settings','policy'}, 'IAP target shape')
    policy(row['policy'], [{'role':'roles/iap.httpsResourceAccessor','members':[USER]}])
    settings=copy.deepcopy(row['settings'])
    for section, key in [('accessSettings','allowedDomainsSettings'),('applicationSettings','attributePropagationSettings')]:
        parent=settings.get(section,{})
        if parent.get(key)=={'enable':False}:
            del parent[key]
            if not parent: settings.pop(section,None)
    oauth=settings.get('accessSettings',{}).get('oauthSettings',{})
    sha=oauth.get('clientSecretSha256')
    require(isinstance(sha,str) and re.fullmatch('[0-9a-f]{64}',sha), 'OAuth digest missing')
    require(settings=={'name':IAP,'accessSettings':{'oauthSettings':{'clientId':WEB,'clientSecretSha256':sha}}},
            'OAuth audience/secret/programmatic settings changed')


def protected(s):
    value=copy.deepcopy(s)
    for k in OUTPUT | {'traffic','trafficStatuses'}: value.pop(k,None)
    t=value['template'];t.pop('revision',None)
    c=t['containers'][0];c.pop('image',None)
    c['env']=[e for e in c['env'] if e.get('name')!='GIT_COMMIT']
    return value


def configuration(plan, boundary):
    entry(boundary)
    s=copy.deepcopy(boundary['service'])
    # Validate exact IAP policy above before reusing the pre-IAP checks for
    # image/env/resources/readiness. The old checker itself remains unchanged.
    s['traffic']=base.payload(plan)['traffic']
    base.check_ready(plan,s,{'bindings':[]})
    s['iapEnabled']=False
    old.extra_guards(s)


def routing(rows): return old.normalized_traffic(rows, True)


def propose(before, after, boundary):
    old.transition(before, after); configuration(before,boundary)
    s=boundary['service']; rows=routing(s.get('traffic'))
    require(rows==routing(s.get('trafficStatuses')), 'observed traffic differs')
    require(len(rows)==2, 'expected default and candidate routing')
    default=[r for r in rows if r['percent']==100 and r['tag']=='']
    tagged=[r for r in rows if r['percent']==0 and r['tag']=='candidate']
    require(len(default)==len(tagged)==1 and all(r['type']==old.TYPE for r in rows), 'unexpected routing')
    for row in rows: old.revision_name(base.NAME+'/revisions/'+row['revision'])
    require(s['latestReadyRevision']==base.NAME+'/revisions/'+tagged[0]['revision'], 'candidate not latest ready')
    candidate=base.SERVICE+'-c'+after['spec']['source_commit'][:12]
    require(candidate not in [r['revision'] for r in rows], 'revision already exists in baseline')
    req={'name':base.NAME,'etag':s['etag'], 'template':{'containers':old.containers(after,s['template']['containers'][0].get('startupProbe')),
          'revision':candidate}, 'traffic':old.traffic(default[0]['revision'],candidate)}
    spec={'old_plan':before,'new_plan':after,'boundary':copy.deepcopy(boundary),'request':req,'path':old.PATCH_PATH}
    return {'schema':'mmh.iap-image-update/1','spec':spec,'sha256':digest(spec)}


def verify(p):
    require(p.get('schema')=='mmh.iap-image-update/1' and p.get('sha256')==digest(p['spec']), 'proposal integrity')
    s=p['spec']
    require(p==propose(s['old_plan'],s['new_plan'],s['boundary']), 'proposal exceeds reviewed operation')


def read_boundary():
    token=base.access_token()
    def iap(path, body=None):
        req=urllib.request.Request('https://iap.googleapis.com/v1/'+path,
            data=None if body is None else json.dumps(body).encode(),
            headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','x-goog-user-project':base.PROJECT})
        with urllib.request.urlopen(req,timeout=60) as response: return json.load(response)
    value={'service':base.rest('GET',base.NAME),'run_policy':base.rest('GET',base.NAME+':getIamPolicy'),
           'project_policy':base.cloud(['projects','get-iam-policy',base.PROJECT]),
           'project_iap_settings':iap('projects/421209514056:iapSettings'), 'iap':{}}
    for name in ANCESTORS+[IAP]:
        value['iap'][name]={'settings':iap(name+':iapSettings'),'policy':iap(name+':getIamPolicy',{})}
    entry(value)
    return value


def unchanged_access(before, after):
    entry(after)
    for key in ('run_policy','project_policy','project_iap_settings','iap'):
        require(after[key]==before[key], key+' changed')
    require(protected(after['service'])==protected(before['service']), 'protected service configuration changed')


def check_after(p, live, rollback=False):
    verify(p);s=p['spec'];before=s['boundary'];unchanged_access(before,live)
    configuration(s['new_plan'],live)
    service=live['service']
    require(int(service['generation'])>int(before['service']['generation']), 'generation did not advance')
    require(service['latestReadyRevision']==base.NAME+'/revisions/'+s['request']['template']['revision'], 'wrong revision')
    expected=before['service']['traffic'] if rollback else s['request']['traffic']
    require(routing(service.get('traffic'))==routing(expected) and routing(service.get('trafficStatuses'))==routing(expected), 'routing mismatch')
    return {'iap_boundary_preserved':True,'candidate_ready':not rollback,'default_traffic_changed':False,
            'previous_tag_restored':rollback,'upload_verified':False}


def stage(p, build_id, journal, confirm=None, read=read_boundary, api=old.rest,
          metadata=old.metadata, inventory=base.inventory_and_grants, effective=base.live_effective):
    verify(p); require(confirm is None or confirm==p['sha256'], 'exact confirmation required')
    folder=Path(journal);folder.mkdir(parents=True,exist_ok=False)
    state={'attempted':False,'accepted':False,'state':'checking','proposal_sha256':p['sha256']}
    old.save(folder/'proposal.json',p);old.save(folder/'result.json',state)
    try:
        plan=p['spec']['new_plan'];old.save(folder/'metadata.json',metadata(plan,build_id))
        items=inventory(plan)
        require(effective(plan,items,folder/'effective-iam.json') is True, 'effective IAM unknown or denied')
        metadata(plan,build_id); require(inventory(plan)==items,'inventory changed')
        # After the lengthy IAM checks, re-read all etags and settings just
        # before PATCH; no cached access-boundary evidence can authorize it.
        require(read()==p['spec']['boundary'], 'stale service/access boundary; prepare again')
        if confirm is None:
            state['state']='checked_no_update'
        else:
            state.update(attempted=True,state='outcome_unknown');old.save(folder/'result.json',state)
            op=api('PATCH',p['spec']['path'],p['spec']['request'])
            require(re.fullmatch(re.escape(base.PARENT)+r'/operations/[A-Za-z0-9_-]+',op.get('name','')), 'invalid operation')
            state.update(operation=op['name'],state='submitted_not_accepted')
        old.save(folder/'result.json',state);return state
    except Exception:
        state['state']='outcome_unknown' if state['attempted'] else 'blocked'
        old.save(folder/'result.json',state);raise


def status(p,journal,read=read_boundary,api=old.rest):
    verify(p);folder=Path(journal);state=json.loads((folder/'result.json').read_text())
    require(state.get('attempted') is True and state.get('proposal_sha256')==p['sha256'], 'journal mismatch')
    state.update(accepted=False,state='verifying');old.save(folder/'result.json',state)
    require(state.get('operation'), 'unknown PATCH outcome: manual readback required, do not retry')
    op=api('GET',state['operation']);require(not op.get('error'),'operation failed')
    if not op.get('done'):return state
    live=read();checks=check_after(p,live);old.save(folder/'boundary-after.json',live)
    state.update(accepted=True,state='private_candidate_ready_e2e_pending',**checks)
    old.save(folder/'result.json',state);return state


def rollback_request(p,live):
    check_after(p,live)
    return {'name':base.NAME,'etag':live['service']['etag'],'traffic':copy.deepcopy(p['spec']['boundary']['service']['traffic'])}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['prepare','check','stage','status'])
    parser.add_argument('--old-plan',type=Path);parser.add_argument('--new-plan',type=Path)
    parser.add_argument('--proposal',type=Path,required=True);parser.add_argument('--journal',type=Path)
    parser.add_argument('--build-id');parser.add_argument('--confirm-sha256');a=parser.parse_args()
    if a.mode=='prepare':
        require(a.old_plan and a.new_plan and not a.proposal.exists(),'fresh proposal and plans required')
        p=propose(json.loads(a.old_plan.read_text()),json.loads(a.new_plan.read_text()),read_boundary())
        old.save(a.proposal,p);print(json.dumps({'sha256':p['sha256'],'mutated':False}))
    else:
        p=json.loads(a.proposal.read_text());require(a.journal,'journal required')
        if a.mode=='status':result=status(p,a.journal)
        else:
            require(a.build_id and ((a.mode=='stage' and a.confirm_sha256) or (a.mode=='check' and not a.confirm_sha256)),'mode/confirmation mismatch')
            result=stage(p,a.build_id,a.journal,a.confirm_sha256)
        print(json.dumps(result))
