"""Bounded metadata-only observations for a reviewed first-install source map.

Not a bootstrap authorization or writer fence. Missing/unsupported/unreadable
history stays unknown, never zero. The caller injects a GET-only transport; this
module cannot discover credentials, retrieve payloads or write provider state.
"""
import copy
import hashlib
import re
import time
from urllib.parse import quote
from lite_usage_ledger import digest, _hex


class InventoryUnavailable(ValueError): pass


def require(ok, reason):
    if not ok: raise InventoryUnavailable(reason)


def unsigned(value, *, positive=False):
    require(type(value) is str and re.fullmatch(r'0|[1-9][0-9]{0,24}',value), 'invalid metadata integer')
    result=int(value)
    require(not positive or result>0, 'invalid metadata generation')
    return result


class MetadataInventory:
    def __init__(self, *, get, clock=time.monotonic, max_requests=128, max_pages=10, max_items=1000, seconds=120):
        require(callable(get) and callable(clock),'explicit read-only transport required')
        for value,limit in ((max_requests,256),(max_pages,20),(max_items,10000),(seconds,300)):
            require(type(value) is int and 1<=value<=limit,'bounded inventory limits required')
        self.get,self.clock=get,clock
        self.max_requests,self.max_pages,self.max_items,self.seconds=max_requests,max_pages,max_items,seconds
        self.calls=0;self.started=None

    def _get(self,path,params):
        require(self.calls<self.max_requests and self.clock()-self.started<self.seconds,'inventory request/time bound')
        self.calls+=1
        try:status,body=self.get(path,copy.deepcopy(params))
        except Exception:raise InventoryUnavailable('metadata transport unavailable') from None
        require(type(status) is int and type(body) is dict,'metadata response malformed')
        require(self.clock()-self.started<self.seconds,'inventory time bound')
        return status,body

    def _bucket(self,source):
        loc=source['location'];path='/storage/v1/b/'+quote(loc['bucket'],safe='')
        status,b=self._get(path,{'fields':'name,projectNumber,metageneration,versioning,softDeletePolicy,hierarchicalNamespace'})
        if status==404:return None
        require(status==200,'bucket GET status '+str(status))
        require(b.get('name')==loc['bucket'] and b.get('projectNumber')==source['project_number'],'bucket identity differs')
        unsigned(b.get('metageneration'),positive=True)
        require(type(b.get('versioning',{})) is dict and type(b.get('softDeletePolicy',{})) is dict,'bucket policy malformed')
        require(type(b.get('versioning',{}).get('enabled',False)) is bool,'versioning state malformed')
        unsigned(b.get('softDeletePolicy',{}).get('retentionDurationSeconds','0'))
        require(type(b.get('hierarchicalNamespace',{})) is dict and b.get('hierarchicalNamespace',{}).get('enabled',False) is False,
                'hierarchical namespace requires separate inventory')
        return {k:b[k] for k in ('name','projectNumber','metageneration','versioning','softDeletePolicy') if k in b}

    def _objects(self,source,soft):
        loc=source['location'];path='/storage/v1/b/'+quote(loc['bucket'],safe='')+'/o'
        # No delimiter, start/endOffset, maxResults over 100, payload GET or ACL.
        params=dict(prefix=loc['prefix']+('/' if loc['match']=='directory' else ''),maxResults='100',
            fields='items(name,generation,size,timeDeleted,softDeleteTime),nextPageToken')
        params['softDeleted' if soft else 'versions']='true'
        seen=set();tokens=set();items=[]
        for _ in range(self.max_pages):
            status,body=self._get(path,params)
            if status!=200:
                return dict(status='unavailable',reason='objects LIST status '+str(status),count=None,bytes=None)
            require(type(body.get('items',[])) is list and len(body.get('items',[]))<=100,'invalid page items')
            for item in body.get('items',[]):
                require(type(item) is dict and type(item.get('name')) is str and item['name'].startswith(params['prefix']),
                        'object outside reviewed prefix')
                require(len(item['name'].encode())<=1024,'object name bound')
                generation=unsigned(item.get('generation'),positive=True);size=unsigned(item.get('size'))
                identity=(item['name'],generation)
                require(identity not in seen,'duplicate object version across pages')
                seen.add(identity)
                require(len(items)<self.max_items,'inventory item bound')
                require(not soft or bool(item.get('softDeleteTime')),'soft-delete metadata missing')
                require(soft or not item.get('softDeleteTime'),'unexpected soft-deleted object')
                items.append(dict(name_sha256=hashlib.sha256(item['name'].encode()).hexdigest(),generation=str(generation),
                                  bytes=size,noncurrent=bool(item.get('timeDeleted')),soft_deleted=soft))
            token=body.get('nextPageToken')
            if token is None:
                items.sort(key=lambda x:(x['name_sha256'],int(x['generation'])))
                return dict(status='complete',count=len(items),bytes=sum(x['bytes'] for x in items),
                            noncurrent=sum(x['noncurrent'] for x in items),snapshot_sha256=digest(items))
            require(type(token) is str and 0<len(token)<=4096 and token not in tokens,'invalid/repeated page token')
            tokens.add(token);params['pageToken']=token
        raise InventoryUnavailable('inventory page bound')

    def inspect(self,sources,*,expected_sources):
        _hex(expected_sources)
        require(self.started is None,'inventory instances are single use')
        require(type(sources) is list and 3<=len(sources)<=64 and digest(sources)==expected_sources,'reviewed source coverage required')
        ids=set();locations=set();roles=set()
        for s in sources:
            require(type(s) is dict and set(s)=={'id','role','location','project_number'},'invalid source schema')
            require(type(s['id']) is str and re.fullmatch(r'[a-z0-9_-]{1,80}',s['id']) and s['id'] not in ids,'invalid source identity')
            require(s['role'] in ('accounts','links','quota'),'invalid source role')
            require(type(s['location']) is dict and set(s['location'])=={'bucket','prefix','match'},'invalid source location')
            loc=s['location'];require(type(loc['bucket']) is str and re.fullmatch(r'[a-z0-9][a-z0-9-]{1,61}[a-z0-9]',loc['bucket']), 'invalid bucket name')
            require(type(loc['prefix']) is str and re.fullmatch(r'[a-z0-9_-]+(?:/[a-z0-9_-]+)+',loc['prefix']), 'explicit prefix required')
            require(loc['match'] in ('directory','filename_prefix') and
                    (loc['match']=='directory' or loc['prefix'].endswith('_')), 'explicit filename-prefix scope required')
            pair=loc['bucket'],loc['prefix']+('/' if loc['match']=='directory' else '')
            require(not any(pair[0]==b and (pair[1].startswith(p) or p.startswith(pair[1])) for b,p in locations),
                    'overlapping source coverage')
            unsigned(s['project_number'],positive=True);ids.add(s['id']);locations.add(pair);roles.add(s['role'])
        require(roles=={'accounts','links','quota'},'missing inventory class')
        self.started=self.clock();reports=[]
        for source in sources:
            report=dict(id=source['id'],role=source['role'],location_sha256=digest(source['location']),complete=False,observed_empty=None)
            try:
                before=self._bucket(source)
                if before is None:report.update(status='bucket_absent',reason='absence does not prove unused history')
                else:
                    report['bucket_before']=before
                    report['versions']=self._objects(source,False)
                    report['soft_deleted']=self._objects(source,True)
                    after=self._bucket(source);report['bucket_after']=after
                    require(before==after,'bucket policy changed during inventory')
                    report['complete']=all(report[k]['status']=='complete' for k in ('versions','soft_deleted'))
                    report['status']='complete' if report['complete'] else 'incomplete'
                    if report['complete']:report['observed_empty']=not(report['versions']['count']+report['soft_deleted']['count'])
            except InventoryUnavailable as error:report.update(status='incomplete',reason=str(error))
            reports.append(report)
        return dict(schema='lite-source-metadata-inventory/1',coverage_sha256=expected_sources,sources=reports,
                    requests=self.calls,complete=all(r['complete'] for r in reports),writer_fence_verified=False,
                    historical_never_used_verified=False,authorizes_bootstrap=False,provider_mutations=0,payload_reads=0)
