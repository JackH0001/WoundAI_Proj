"""Bounded GET-only Cloud Run metadata for a reviewed Lite deployment plan.

This collects evidence; it cannot establish effective IAM, stop writers, sign
observations or authorize bootstrap. Missing resources and unavailable history
are never converted to a zero-inflight claim. No credentials are discovered.
"""
import copy
from datetime import datetime
import re
import time

from lite_subscription_executor import canonical
from lite_usage_ledger import digest, _hex

SERVICE_FIELDS = ('name,uid,generation,observedGeneration,reconciling,etag,ingress,'
                  'invokerIamDisabled,defaultUriDisabled,latestReadyRevision,latestCreatedRevision')
JOB_FIELDS = 'name,uid,generation,observedGeneration,reconciling,etag'
EXECUTION_FIELDS = ('name,uid,job,generation,observedGeneration,reconciling,taskCount,'
                    'runningCount,succeededCount,failedCount,cancelledCount,completionTime,deleteTime')


class ObservationUnavailable(ValueError): pass


def require(ok, reason):
    if not ok: raise ObservationUnavailable(reason)


def plan(value, expected):
    _hex(expected)
    require(type(value) is dict and set(value)=={'schema','project','region','services','jobs'}
        and value['schema']=='lite-control-plane-coverage/1' and digest(value)==expected,'pinned coverage required')
    for field,pattern in (('project',r'[a-z][a-z0-9-]{4,28}[a-z0-9]'),('region',r'[a-z]+-[a-z]+[0-9]')):
        require(type(value[field]) is str and re.fullmatch(pattern,value[field]),'invalid coverage identity')
    for name,limit in (('services',8),('jobs',4)):
        rows=value[name]
        require(type(rows) is list and 1<=len(rows)<=limit and all(type(v) is str and
            re.fullmatch(r'woundai-lite-[a-z0-9][a-z0-9-]{0,48}[a-z0-9]',v) for v in rows)
            and len(set(rows))==len(rows),'explicit distinct Lite resource names required')
    return copy.deepcopy(value)


def generation(value):
    require(type(value) is str and re.fullmatch(r'[1-9][0-9]{0,18}',value),'generation unavailable')
    return value


def metadata(value, expected):
    require(type(value) is dict and value.get('name')==expected,'resource identity differs')
    require(type(value.get('uid')) is str and 1<=len(value['uid'])<=128
        and type(value.get('etag')) is str and 1<=len(value['etag'])<=256,'resource revision unavailable')
    generation(value.get('generation'))
    if 'observedGeneration' in value: generation(value['observedGeneration'])
    require(type(value.get('reconciling',False)) is bool,'invalid reconciling status')
    for field in ('invokerIamDisabled','defaultUriDisabled'):
        require(field not in value or type(value[field]) is bool,'invalid service access flag')
    return copy.deepcopy(value)


def execution(value, parent):
    require(type(value) is dict and type(value.get('name')) is str and
        re.fullmatch(re.escape(parent)+r'/executions/[a-z0-9-]{1,80}',value['name'])
        and value.get('job')==parent and type(value.get('uid')) is str and
        1<=len(value['uid'])<=128,'execution identity differs')
    generation(value.get('generation'))
    if 'observedGeneration' in value: generation(value['observedGeneration'])
    require(type(value.get('reconciling',False)) is bool,'execution reconciliation invalid')
    require(type(value.get('taskCount')) is int and 1<=value['taskCount']<=10000,'task count unavailable')
    counts={k:value.get(k,0) for k in ('runningCount','succeededCount','failedCount','cancelledCount')}
    require(all(type(n) is int and 0<=n<=value['taskCount'] for n in counts.values()),'task counter invalid')
    terminal=sum(counts[k] for k in ('succeededCount','failedCount','cancelledCount'))
    require(terminal+counts['runningCount']<=value['taskCount'],'task counters inconsistent')
    ended=value.get('completionTime')
    if ended is not None:
        require(type(ended) is str and len(ended)<=40,'completion timestamp invalid')
        try: timestamp=datetime.fromisoformat(ended.replace('Z','+00:00'))
        except ValueError: raise ObservationUnavailable('completion timestamp invalid') from None
        require(timestamp.tzinfo is not None,'completion timezone unavailable')
    done=(ended is not None and not value.get('reconciling',False) and
          value.get('observedGeneration')==value['generation'] and terminal==value['taskCount']
          and counts['runningCount']==0)
    # Zero runningCount also describes queued or retrying work; it is not drain.
    return dict(name=value['name'],uid=value['uid'],generation=value['generation'],
                observed_generation=value.get('observedGeneration'),reconciling=value.get('reconciling',False),
                tasks=value['taskCount'],counts=counts,completion_time=ended,
                deleted=bool(value.get('deleteTime')),terminal_confirmed=done)


class ControlPlaneInventory:
    def __init__(self, *, get, clock=time.monotonic, max_requests=64, max_pages=5, max_items=1000, seconds=120):
        require(callable(get) and callable(clock),'explicit GET-only transport required')
        for v,limit in ((max_requests,128),(max_pages,10),(max_items,2000),(seconds,300)):
            require(type(v) is int and 1<=v<=limit,'bounded readback required')
        self.get,self.clock=get,clock
        self.max_requests,self.max_pages,self.max_items,self.seconds=max_requests,max_pages,max_items,seconds
        self.started=None;self.calls=0

    def _get(self,path,params):
        require(self.calls<self.max_requests and self.clock()-self.started<self.seconds,'request/time limit')
        self.calls+=1
        try: status,body=self.get(path,copy.deepcopy(params))
        except Exception: raise ObservationUnavailable('provider read unavailable') from None
        require(type(status) is int and self.clock()-self.started<self.seconds,'provider response/time invalid')
        if status!=200:return status,None  # never retain provider error or credential text
        try: valid=type(body) is dict and len(canonical(body))<=1024*1024
        except (TypeError,ValueError):valid=False
        require(valid,'response size/type invalid')
        return status,body

    def _resource(self,name,fields):
        status,value=self._get('/v2/'+name,{'fields':fields})
        if status!=200:return status,None
        # Whitelist selected fields even if a transport ignores fields projection.
        return status,metadata({k:v for k,v in value.items() if k in fields.split(',')},name)

    def _executions(self,parent):
        rows=[];names=set();uids=set();tokens=set()
        params=dict(pageSize='100',showDeleted='true',fields='executions('+EXECUTION_FIELDS+'),nextPageToken')
        for _ in range(self.max_pages):
            status,value=self._get('/v2/'+parent+'/executions',params)
            require(status==200,'execution history unavailable (HTTP '+str(status)+')')
            require(type(value.get('executions',[])) is list and len(value.get('executions',[]))<=100,'invalid execution page')
            for v in value.get('executions',[]):
                row=execution(v,parent)
                require(row['name'] not in names and row['uid'] not in uids,'duplicate execution across pages')
                names.add(row['name']);uids.add(row['uid']);rows.append(row)
                require(len(rows)<=self.max_items,'execution item limit')
            token=value.get('nextPageToken')
            if token is None:return sorted(rows,key=lambda r:r['name'])
            require(type(token) is str and 0<len(token)<=4096 and token not in tokens,'invalid/repeated page token')
            tokens.add(token);params['pageToken']=token
        raise ObservationUnavailable('execution page limit')

    def inspect(self,coverage,*,expected_coverage):
        c=plan(coverage,expected_coverage);require(self.started is None,'reader is single use')
        self.started=self.clock();base='projects/'+c['project']+'/locations/'+c['region'];reports=[]
        for kind in ('services','jobs'):
            for ident in c[kind]:
                name=base+'/'+kind+'/'+ident
                report=dict(name=name,kind=kind,status='unavailable',snapshot_complete=False,
                            route_closed=None,inflight=None,history_never_used=False)
                fields=SERVICE_FIELDS if kind=='services' else JOB_FIELDS
                try:
                    status,before=self._resource(name,fields)
                    report['http_status']=status
                    if status!=200:
                        report['status']='absent' if status==404 else 'unavailable'
                        report['reason']='not proof of unused history or stopped work'
                    else:
                        report['metadata']=before
                        if kind=='jobs':
                            one=self._executions(name);two=self._executions(name)
                            require(one==two,'execution set/status changed during observation')
                        after_status,after=self._resource(name,fields)
                        require(after_status==200 and before==after,'resource changed during observation')
                        report.update(status='observed',snapshot_complete=True)
                        if kind=='jobs':
                            report.update(visible_executions=len(one),terminal_confirmed=sum(v['terminal_confirmed'] for v in one),
                                not_confirmed_terminal=sum(not v['terminal_confirmed'] for v in one),execution_snapshot=digest(one))
                        # Even two equal reads do not atomically fence new work.
                except ObservationUnavailable as error:report['reason']=str(error)
                reports.append(report)
        return dict(schema='lite-control-plane-inventory/1',coverage=expected_coverage,resources=reports,
            requests=self.calls,snapshot_complete=all(r['snapshot_complete'] for r in reports),
            writer_fence_verified=False,effective_iam_verified=False,authorizes_bootstrap=False,
            private_payload_reads=0,provider_mutations=0,signatures_created=0)
