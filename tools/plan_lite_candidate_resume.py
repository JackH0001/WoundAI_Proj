"""Validate already-provisioned Lite resources for a reviewed resume plan.

Offline only. This does not authorize execution or replace fresh effective IAM
checks, source review, immutable image verification, or private acceptance.
"""
import hashlib
import json
import plan_lite_candidate_deploy as candidate
import plan_lite_cloud_build as build


def validate_state(state):
    """Reject changed identity/storage/IAM instead of recreating resources."""
    project,number=candidate.PROJECT,candidate.NUMBER
    runtime,worker=candidate.RUNTIME,build.EMAIL
    if state['cloud-inventory']['lite-service'] != {'present':False}:
        raise ValueError('candidate service already exists; inspect before deploying')
    for key,email,disabled in [('runtime',runtime,False),('build-account',worker,True)]:
        row=state[key]
        if row.get('email')!=email or row.get('projectId')!=project or bool(row.get('disabled'))!=disabled:
            raise ValueError('identity state changed: '+key)
    build.check_ancestor_policy(state['ancestors'])
    for row in state['ancestors']:
        for binding in row['policy']['bindings']:
            if 'serviceAccount:'+runtime in binding['members']:
                raise ValueError('runtime has ancestor grant')
    for kind,listing in [('media',True),('security',False)]:
        name='woundai-lite-'+kind+'-'+number
        bucket=state[kind+'-bucket'];iam=bucket.get('iamConfiguration',{})
        if (bucket.get('name')!=name or str(bucket.get('projectNumber'))!=number
                or bucket.get('location')!='ASIA-EAST1' or bucket.get('storageClass')!='STANDARD'
                or iam.get('publicAccessPrevention')!='enforced'
                or iam.get('uniformBucketLevelAccess',{}).get('enabled') is not True
                or bucket.get('retentionPolicy') or bucket.get('defaultEventBasedHold')
                or bucket.get('versioning',{}).get('enabled')
                or bucket.get('lifecycle',{}).get('rule')
                or bucket.get('softDeletePolicy',{}).get('retentionDurationSeconds')!='0'):
            raise ValueError('bucket configuration changed: '+kind)
        role_id='woundaiLite'+kind.capitalize()+'Objects'
        role=state[kind+'-role']
        if (role.get('name')!='projects/'+project+'/roles/'+role_id or role.get('deleted')
                or role.get('stage')!='GA'
                or sorted(role.get('includedPermissions',[]))!=sorted(candidate.role(role_id,listing)['includedPermissions'])):
            raise ValueError('runtime role changed: '+kind)
        expected='projects/'+project+'/roles/'+role_id
        bindings=state[kind+'-iam'].get('bindings',[])
        direct=[b for b in bindings if 'serviceAccount:'+runtime in b.get('members',[])]
        if len(direct)!=1 or direct[0].get('role')!=expected or direct[0].get('condition'):
            raise ValueError('runtime bucket binding changed: '+kind)
        for binding in bindings:
            if binding.get('condition'):raise ValueError('unexpected conditional bucket grant')
            if binding['role']==expected:
                if binding['members']!=['serviceAccount:'+runtime]:raise ValueError('unexpected role member')
            elif binding['role'] in ('roles/storage.legacyBucketOwner','roles/storage.legacyObjectOwner'):
                if set(binding['members'])!={'projectEditor:'+project,'projectOwner:'+project}:raise ValueError('legacy owner changed')
            elif binding['role'] in ('roles/storage.legacyBucketReader','roles/storage.legacyObjectReader'):
                if binding['members']!=['projectViewer:'+project]:raise ValueError('legacy reader changed')
            else:raise ValueError('unexpected bucket role')
    br=state['build-role']
    if (br.get('name')!='projects/'+project+'/roles/'+build.ROLE or br.get('deleted')
            or br.get('stage')!='GA' or sorted(br.get('includedPermissions',[]))!=sorted(build.PERMISSIONS)):
        raise ValueError('build role changed')
    if any('serviceAccount:'+worker in b.get('members',[]) for b in state['build-bucket-iam'].get('bindings',[])):
        raise ValueError('temporary build bucket grant already exists')
    if state['repository-iam'].get('bindings'):raise ValueError('repository grants changed')
    salt=state['salt-metadata']
    if salt.get('name')!='projects/'+number+'/secrets/'+candidate.SECRET:
        raise ValueError('wrong salt resource')
    if salt.get('replication')!={'userManaged':{'replicas':[{'location':candidate.REGION}]}}:
        raise ValueError('salt replication changed')
    bindings=state['salt-iam'].get('bindings',[])
    if bindings!=[{'role':'roles/secretmanager.secretAccessor','members':['serviceAccount:'+runtime]}]:
        raise ValueError('salt binding changed')
    return {'existing_resources_validated':True,'create_resources':False,
            'legacy_project_administrator_access_retained':True,
            'effective_isolation_proven':False,'authorized_to_execute':False}


def snapshot_hash(state):
    return hashlib.sha256(json.dumps(state,sort_keys=True,separators=(',',':')).encode()).hexdigest()
