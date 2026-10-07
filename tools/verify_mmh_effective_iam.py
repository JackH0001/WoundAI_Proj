"""Read-only IAM v3 checks. Never mint impersonation credentials or perform deletes.

The operator access token is kept in memory only. UNKNOWN/PAB uncertainty is not
accepted as proof. This is a direct-permission check, not an impersonation graph.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time
import urllib.request
from provision_mmh_foundation import PROJECT, NUMBER, BUCKETS, SA
from protect_mmh_buckets import ADMIN, resource

OLD_RUNTIME = NUMBER + '-compute@developer.gserviceaccount.com'
CONTROL_BUCKET = 'woundai-flywheel-jackh001'


def cases(profile='bucket-deny'):
    if profile not in ('bucket-deny', 'least-privilege'):
        raise ValueError('unknown verification profile')
    rows = []
    for b in BUCKETS:
        for p in ('objects.get','objects.list','objects.create','objects.delete',
                  'buckets.delete','buckets.setIamPolicy','buckets.update',
                  'buckets.createTagBinding','buckets.deleteTagBinding'):
            rows.append((OLD_RUNTIME, resource(b), 'storage.' + p, False))
        rows.append((ADMIN, resource(b), 'storage.buckets.delete', True))
    for p in ('storage.buckets.delete', 'storage.objects.list'):
        expected = not (profile == 'least-privilege' and p == 'storage.buckets.delete')
        rows.append((OLD_RUNTIME, resource(CONTROL_BUCKET), p, expected))
    sa_resource = '//iam.googleapis.com/projects/' + PROJECT + '/serviceAccounts/' + SA
    for p in ('iam.serviceAccounts.actAs','iam.serviceAccounts.getAccessToken','iam.serviceAccounts.getOpenIdToken',
              'iam.serviceAccounts.implicitDelegation','iam.serviceAccounts.signBlob','iam.serviceAccounts.signJwt',
              'iam.serviceAccountKeys.create','iam.serviceAccounts.setIamPolicy'):
        rows.append((OLD_RUNTIME, sa_resource, p, False))
    if profile == 'least-privilege':
        for p in ('iam.serviceAccounts.delete', 'iam.serviceAccounts.disable', 'iam.serviceAccounts.update'):
            rows.append((OLD_RUNTIME, sa_resource, p, False))
        # Keep legacy runtime data access working while removing control-plane power.
        for p in ('storage.objects.get', 'storage.objects.create', 'storage.objects.delete'):
            rows.append((OLD_RUNTIME, resource(CONTROL_BUCKET), p, True))
        for p in ('storage.objects.get', 'storage.objects.list', 'storage.objects.create'):
            rows.append((OLD_RUNTIME, resource(CONTROL_BUCKET + '-audit'), p, True))
        for secret in ('woundai-admin-password', 'woundai-jwt-secret'):
            target = '//secretmanager.googleapis.com/projects/' + NUMBER + '/secrets/' + secret
            rows.append((OLD_RUNTIME, target, 'secretmanager.versions.access', True))
    return rows


def assess(response, principal, target, permission, expected_access):
    echoed = response.get('accessTuple', {})
    if any(echoed.get(k) != v for k,v in [('principal',principal),('fullResourceName',target),('permission',permission)]):
        raise ValueError('Troubleshooter response does not match requested identity/resource/permission')
    overall = response.get('overallAccessState')
    allow = response.get('allowPolicyExplanation',{}).get('allowAccessState')
    deny = response.get('denyPolicyExplanation',{}).get('denyAccessState')
    known = allow in ('ALLOW_ACCESS_STATE_GRANTED','ALLOW_ACCESS_STATE_NOT_GRANTED') and deny in ('DENY_ACCESS_STATE_DENIED','DENY_ACCESS_STATE_NOT_DENIED')
    if expected_access:
        ok = known and overall == 'CAN_ACCESS' and allow == 'ALLOW_ACCESS_STATE_GRANTED' and deny == 'DENY_ACCESS_STATE_NOT_DENIED'
    else:
        ok = known and overall == 'CANNOT_ACCESS' and (allow == 'ALLOW_ACCESS_STATE_NOT_GRANTED' or deny == 'DENY_ACCESS_STATE_DENIED')
    return {'principal':principal,'resource':target,'permission':permission,'expected_access':expected_access,
            'overall':overall,'allow':allow,'deny':deny,'passed':ok}


def access_token():
    token = subprocess.run(['gcloud','auth','print-access-token','--project',PROJECT],
                           capture_output=True,text=True,check=True,timeout=60).stdout.strip()
    if not token: raise ValueError('credential unavailable')
    return token


def request_v3(principal, target, permission, token):
    payload={'accessTuple':{'principal':principal,'fullResourceName':target,'permission':permission,
             'conditionContext':{'request':{'receiveTime':datetime.now(timezone.utc).isoformat().replace('+00:00','Z')}}}}
    request=urllib.request.Request('https://policytroubleshooter.googleapis.com/v3/iam:troubleshoot',
            data=json.dumps(payload).encode(),headers={'Authorization':'Bearer '+token,
            'Content-Type':'application/json','x-goog-user-project':PROJECT})
    with urllib.request.urlopen(request,timeout=60) as response: return json.load(response)


def verify_deny_authority():
    target='//cloudresourcemanager.googleapis.com/projects/'+PROJECT
    permission='iam.denypolicies.create'
    result=request_v3(ADMIN,target,permission,access_token())
    return assess(result,ADMIN,target,permission,True)['passed']


def verify(report, profile='bucket-deny'):
    # Invalidate a previous green report even when credential discovery fails.
    report.write_text(json.dumps({'complete':False,'state':'in_progress','profile':profile})+'\n')
    evaluated=[]
    try:
        matrix=cases(profile)
        credential=access_token()
        for index,case in enumerate(matrix):
            if index: time.sleep(7)  # Avoid quota bursts; never cache authorization.
            principal,target,permission,expected=case
            result=request_v3(principal,target,permission,credential)
            evaluated.append((assess(result,*case),result))
    except Exception as error:
        failure={'complete':False,'state':'failed','profile':profile,'completed':[r for r,_ in evaluated],
                 'error_type':type(error).__name__,'http_status':getattr(error,'code',None)}
        report.write_text(json.dumps(failure,indent=2)+'\n')
        raise
    summary={'api':'v3','profile':profile,'complete':True,'cases':[r for r,_ in evaluated],
             'passed':sum(r['passed'] for r,_ in evaluated), 'total':len(evaluated),
             'scope':'direct permissions for named principals; not a complete impersonation-chain proof'}
    report.write_text(json.dumps(summary,indent=2)+'\n')
    report.with_suffix('.raw.json').write_text(json.dumps([r for _,r in evaluated],indent=2)+'\n')
    print(json.dumps({'passed':summary['passed'],'total':summary['total'],
                      'failures':[r for r in summary['cases'] if not r['passed']]},indent=2))
    return summary['passed']==summary['total']


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--profile',choices=('bucket-deny','least-privilege'),default='bucket-deny')
    args=parser.parse_args();raise SystemExit(0 if verify(args.report,args.profile) else 1)
