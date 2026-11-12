"""Read-only, generation-pinned shared-key custody inventory.

This is a scoped observation, never a writer drain or KMS destruction authority.
Soft-deleted payloads cannot be read without restoration; unknown stays unknown.
No credentials are discovered, no bucket/object/key is modified, no DEK is opened.
"""
import base64
import copy
import hashlib
import math
import re
import time
from urllib.parse import quote
from audit_chain_contract import loads_json_object_strict
from lite_attest_state import GCSStateStore, MAX_STATE, StateUnavailable
from lite_grant_key_custody import crc32c
from lite_rotating_grant_keys import RotatingGrantCipher
from lite_subscription_executor import key
from lite_usage_ledger import digest, _hex


class InventoryUnavailable(StateUnavailable): pass


class GCSInventoryReader:
    """Only GETs to the pinned GCS JSON endpoint; injected authenticated session.

    Streamed body bounds and no redirects. The caller is responsible for using
    an independently scoped, read-only identity; this is not an IAM substitute.
    """
    def __init__(self,session):
        if not callable(getattr(session,'request',None)): raise ValueError('explicit read-only session required')
        self.session=session

    def __call__(self,path,query,*,media=False):
        if not re.fullmatch(r'/storage/v1/b/[a-z0-9-]+(?:/o(?:/[A-Za-z0-9%_.~-]+)?)?',path):
            raise ValueError('pinned GCS path required')
        response=None
        try:
            response=self.session.request('GET','https://storage.googleapis.com'+path,params=query,
                timeout=(5,10),allow_redirects=False,stream=True)
            if type(response.status_code) is not int: raise ValueError()
            if response.status_code != 200: return response.status_code,None
            cap=MAX_STATE if media else 262144
            raw=bytearray()
            for chunk in response.iter_content(chunk_size=4096):
                if type(chunk) is not bytes or len(raw)+len(chunk)>cap: raise ValueError()
                raw.extend(chunk)
            if media: return 200,bytes(raw)
            return 200,loads_json_object_strict(bytes(raw).decode('utf-8'))
        except Exception:
            raise InventoryUnavailable('GCS read unavailable or response exceeds bound') from None
        finally:
            if response is not None:
                try: response.close()
                except Exception: pass  # Never leak a transport exception or credentials.


def require(condition,reason):
    if not condition: raise InventoryUnavailable(reason)


def integer(value,*,positive=False):
    require(type(value) is str and re.fullmatch(r'0|[1-9][0-9]{0,24}',value),'invalid provider integer')
    n=int(value); require(not positive or n>0,'invalid provider generation'); return n


class SharedKeyInventory:
    def __init__(self,*,cipher,sources,expected_sources,get,clock=time.monotonic,
                 max_operations=2000,max_objects=1000,max_pages=20,max_bytes=16*1024*1024,seconds=300):
        if (not isinstance(cipher,RotatingGrantCipher) or not isinstance(cipher.store,GCSStateStore)
                or not callable(get) or not callable(clock)): raise ValueError('explicit custody and GCS reader required')
        _hex(expected_sources)
        if type(sources) is not list or not 1<=len(sources)<=8 or digest(sources)!=expected_sources:
            raise ValueError('fixed source coverage required')
        ids=set(); locations=[]; primary=[]
        for s in sources:
            if (type(s) is not dict or set(s)!={'id','kind','bucket','prefix','project_number'}
                    or type(s['id']) is not str or not re.fullmatch(r'[a-z0-9_-]{1,80}',s['id']) or s['id'] in ids
                    or s['kind'] not in ('primary','mirror')
                    or type(s['bucket']) is not str or not re.fullmatch(r'[a-z0-9][a-z0-9-]{1,61}[a-z0-9]',s['bucket'])
                    or type(s['prefix']) is not str or not re.fullmatch(r'[a-z0-9_-]+(?:/[a-z0-9_-]+)+',s['prefix'])):
                raise ValueError('explicit bounded custody source required')
            integer(s['project_number'],positive=True)
            loc=s['bucket'],s['prefix']+'/'
            if any(loc[0]==b and (loc[1].startswith(p) or p.startswith(loc[1])) for b,p in locations):
                raise ValueError('overlapping sources are not independent coverage')
            ids.add(s['id']);locations.append(loc)
            if s['kind']=='primary':primary.append(s)
        if (len(primary)!=1 or primary[0]['bucket']!=cipher.store.bucket.name
                or primary[0]['prefix']!=cipher.store.prefix): raise ValueError('exact primary custody location required')
        for n,limit in ((max_operations,10000),(max_objects,10000),(max_pages,100),(max_bytes,16*1024*1024),(seconds,600)):
            if type(n) is not int or not 1<=n<=limit: raise ValueError('finite inventory limits required')
        self.cipher,self.sources,self.coverage,self.get,self.clock=cipher,copy.deepcopy(sources),expected_sources,get,clock
        self.max_operations,self.max_objects,self.max_pages,self.max_bytes,self.seconds=max_operations,max_objects,max_pages,max_bytes,seconds
        self.operations=self.objects=self.bytes=0; self.started=None

    def _tick(self):
        now=self.clock()
        require(type(now) in (int,float) and math.isfinite(now) and self.started<=now<self.started+self.seconds,
                'inventory clock or time bound')
        require(self.operations<self.max_operations,'inventory operation bound');self.operations+=1

    def _get(self,path,query,*,media=False):
        self._tick()
        try: code,body=self.get(path,copy.deepcopy(query),media=media)
        except Exception: raise InventoryUnavailable('provider read unavailable') from None
        require(type(code) is int,'invalid provider status')
        now=self.clock()
        require(type(now) in (int,float) and math.isfinite(now)
                and self.started<=now<self.started+self.seconds,'inventory time bound')
        return code,body

    def _proof(self,k,expected):
        self._tick()
        try: row=self.cipher.audit.load(k)
        except Exception: raise InventoryUnavailable('original custody audit unavailable') from None
        require(row is not None and row[1]==expected,'original custody audit differs')

    def _bucket(self,s):
        code,b=self._get('/storage/v1/b/'+s['bucket'],dict(fields=
            'name,projectNumber,metageneration,versioning,softDeletePolicy,hierarchicalNamespace,retentionPolicy,defaultEventBasedHold,lifecycle'))
        require(code==200 and type(b) is dict,'bucket unavailable; not proof of absence')
        require(b.get('name')==s['bucket'] and b.get('projectNumber')==s['project_number'],'bucket identity differs')
        integer(b.get('metageneration'),positive=True)
        require(type(b.get('versioning',{})) is dict and type(b.get('versioning',{}).get('enabled',False)) is bool,'invalid versioning policy')
        require(type(b.get('softDeletePolicy',{})) is dict,'invalid soft-delete policy')
        integer(b.get('softDeletePolicy',{}).get('retentionDurationSeconds','0'))
        require(type(b.get('hierarchicalNamespace',{})) is dict and b.get('hierarchicalNamespace',{}).get('enabled',False) is False,
                'hierarchical namespace requires separate inventory')
        return b

    def _list(self,s,*,soft):
        prefix=s['prefix']+'/'
        query=dict(prefix=prefix,maxResults='100',fields=
            'items(name,generation,size,crc32c,timeDeleted,softDeleteTime,temporaryHold,eventBasedHold,retention),nextPageToken')
        query['softDeleted' if soft else 'versions']='true'
        tokens=set(); seen=set(); result=[]
        for _ in range(self.max_pages):
            code,b=self._get('/storage/v1/b/'+s['bucket']+'/o',query)
            if soft and code==400:return None  # Even policy=0 does not prove historical absence.
            require(code==200 and type(b) is dict,'version listing unavailable')
            require(type(b.get('items',[])) is list and len(b.get('items',[]))<=100,'invalid list page')
            require(not b.get('prefixes'),'truncated directory listing cannot prove coverage')
            for item in b.get('items',[]):
                require(type(item) is dict and type(item.get('name')) is str,'invalid object metadata')
                require(re.fullmatch(re.escape(prefix)+r'[0-9a-f]{64}\.json',item['name']),'unknown object in custody prefix')
                g=integer(item.get('generation'),positive=True); n=integer(item.get('size'),positive=True)
                require(n<=MAX_STATE,'custody object exceeds bound')
                crc=item.get('crc32c')
                try: require(type(crc) is str and len(base64.b64decode(crc,validate=True))==4,'invalid checksum')
                except Exception: raise InventoryUnavailable('invalid checksum') from None
                ident=item['name'],g
                require(ident not in seen,'duplicate object generation');seen.add(ident)
                require(bool(item.get('softDeleteTime'))==soft,'soft-delete metadata differs')
                for field in ('timeDeleted','softDeleteTime'):
                    require(field not in item or (type(item[field]) is str and len(item[field])>0),
                            'invalid deletion metadata')
                for field in ('temporaryHold','eventBasedHold'):
                    require(type(item.get(field,False)) is bool,'invalid object hold')
                require(self.objects<self.max_objects,'inventory object bound');self.objects+=1
                result.append(copy.deepcopy(item))
            token=b.get('nextPageToken')
            if token is None:
                result.sort(key=lambda x:(x['name'],int(x['generation'])))
                return result
            require(type(token) is str and 0<len(token)<=4096 and token not in tokens,'invalid or repeated page token')
            tokens.add(token);query['pageToken']=token
        raise InventoryUnavailable('inventory page bound')

    def _classify(self,raw,name):
        try:
            c=loads_json_object_strict(raw.decode('utf-8')); scope=self.cipher.scope
            slot=bytes.fromhex(name.rsplit('/',1)[1][:-5]); expected=None; person=None; record=None
            if c==self.cipher.mode:
                expected=key('account-grant-cipher-config',scope);kind='mode'
            elif c.get('v')==2 and 'wrapped_dek' in c:
                person=c['person'];_hex(person)
                candidates=[self.cipher._key(person),key('shared-grant-key-initial-proposal',digest([scope,person]))]
                require(slot in candidates,'key row stored in wrong slot')
                expected=slot;record=c;kind='current_key' if slot==candidates[0] else 'initial_proposal'
            elif set(c)=={'v','operation','source_sha256','record'} and type(c['v']) is int and c['v']==1:
                record=c['record'];person=record['person'];_hex(person);_hex(c['source_sha256'])
                operation=digest([scope,person,c['source_sha256'],record['version']])
                require(c['operation']==operation,'rewrap operation differs')
                expected=key('shared-grant-rewrap-proposal',operation);kind='rewrap_proposal'
                self._proof(key('shared-grant-rewrap-intent',operation),dict(v=1,scope=scope,person=person,
                    source=c['source_sha256'],target=record['version']))
            elif set(c)=={'schema','scope','person','closure','proposal','previous_sha256'} and c['schema']=='shared-proposal-tombstone/1':
                person=c['person'];_hex(person);_hex(c['closure']);_hex(c['proposal'])
                if c['previous_sha256'] is not None:_hex(c['previous_sha256'])
                require(c['scope']==scope,'tombstone scope differs')
                expected=bytes.fromhex(c['proposal']);kind='proposal_tombstone'
                self._proof(key('shared-grant-proposal-erased',digest(c)),c)
            elif set(c)=={'v','scope','person','versions'} and type(c['v']) is int and c['v']==1:
                person=c['person'];_hex(person);require(c['scope']==scope,'dependency scope differs')
                versions=c['versions']
                require(type(versions) is list and 1<=len(versions)<=64 and versions==sorted(set(versions)),'invalid dependencies')
                for v in versions: self.cipher.provider._name(v)
                expected=key('shared-grant-key-dependencies',digest([scope,person]));kind='dependency_reservation'
                self._proof(key('shared-grant-key-dependencies',digest(c)),c)
            elif set(c)=={'v','scope','person','phase','closure','proposals'} and type(c['v']) is int and c['v']==1:
                person=c['person'];_hex(person);require(c['scope']==scope,'index scope differs')
                require(c['phase'] in ('open','closed'),'invalid proposal phase')
                if c['phase']=='closed':_hex(c['closure'])
                else:require(c['closure'] is None,'open index has closure')
                ps=c['proposals'];require(type(ps) is list and 1<=len(ps)<=256 and ps==sorted(set(ps)),'invalid proposal index')
                for p in ps:_hex(p)
                expected=key('shared-grant-proposal-index',digest([scope,person]));kind='proposal_index'
                self._proof(key('shared-grant-proposal-index-proof',digest(c)),c)
            else: raise InventoryUnavailable('unrecognized custody format; never skip')
            require(slot==expected,'custody slot differs from content')
            if record is not None:
                # Existing validator checks the original hash-only audit. One
                # bounded logical audit read, no KMS decrypt or plaintext DEK.
                self._tick();self.cipher._validate(record,person)
            return dict(kind=kind,person=person,record_sha256=digest(c),
                wrapped_version=record['version'] if record is not None and record['wrapped_dek'] is not None else None,
                wrapped_sha256=digest(record['wrapped_dek']) if record is not None and record['wrapped_dek'] is not None else None)
        except InventoryUnavailable:raise
        except Exception: raise InventoryUnavailable('custody content or audit invalid') from None

    def _payload(self,s,item):
        n=int(item['size']);require(self.bytes+n<=self.max_bytes,'payload byte budget')
        self.bytes+=n
        code,raw=self._get('/storage/v1/b/'+s['bucket']+'/o/'+quote(item['name'],safe=''),
            dict(alt='media',generation=item['generation'],ifGenerationMatch=item['generation']),media=True)
        require(code==200 and type(raw) is bytes and len(raw)==n,'pinned generation unavailable or size differs')
        expected=int.from_bytes(base64.b64decode(item['crc32c']),'big')
        require(crc32c(raw)==expected,'pinned generation checksum differs')
        classified=self._classify(raw,item['name'])
        return dict(classified,object_name_sha256=hashlib.sha256(item['name'].encode()).hexdigest(),
            object_slot=item['name'].rsplit('/',1)[1][:-5],
            generation=item['generation'],payload_sha256=hashlib.sha256(raw).hexdigest(),
            noncurrent=bool(item.get('timeDeleted')),held=bool(item.get('temporaryHold') or item.get('eventBasedHold') or item.get('retention')))

    def inspect(self):
        require(self.started is None,'single-use inventory required')
        self.started=self.clock()
        require(type(self.started) in (int,float) and math.isfinite(self.started) and self.started>=0,'invalid inventory clock')
        reports=[]
        for s in self.sources:
            report=dict(id=s['id'],kind=s['kind'],source_sha256=digest(s),status='incomplete',
                material_inventory_complete=False,stable_listing=False,records=[],soft_count=None)
            try:
                before=self._bucket(s); active=self._list(s,soft=False); soft=self._list(s,soft=True)
                for item in active:report['records'].append(self._payload(s,item))
                if s['kind']=='primary':
                    anchor=self.cipher.store._name(key('account-grant-cipher-config',self.cipher.scope))
                    require(any(x['name']==anchor and not x.get('timeDeleted') for x in active),'current mode anchor omitted')
                after_active=self._list(s,soft=False); after_soft=self._list(s,soft=True);after=self._bucket(s)
                require(before==after,'bucket policy changed')
                require(active==after_active and soft==after_soft,'object inventory changed; repeat after real drain')
                report.update(stable_listing=True,policy_sha256=digest(before),active_snapshot_sha256=digest(active),
                    retention_guarded=bool(before.get('retentionPolicy') or before.get('defaultEventBasedHold')),
                    soft_count=None if soft is None else len(soft),soft_snapshot_sha256=None if soft is None else digest(soft))
                report['material_inventory_complete']=soft==[]
                report['status']='scoped_material_observed' if soft==[] else 'soft_payloads_unreadable' if soft else 'soft_inventory_unknown'
            except InventoryUnavailable as error:report['reason']=str(error)
            reports.append(report)
        dependencies={}
        for s in reports:
            for r in s['records']:
                if r['wrapped_version'] is not None:
                    counts=dependencies.setdefault(r['wrapped_version'],dict(current=0,historical=0,proposals=0))
                    counts['historical' if r['noncurrent'] else 'proposals' if r['kind']!='current_key' else 'current']+=1
        return dict(schema='lite-shared-key-inventory/1',coverage_sha256=self.coverage,sources=reports,
            observed_wrapped_dependencies=dependencies,logical_read_operations=self.operations,listed_entries=self.objects,
            payload_bytes=self.bytes,all_selected_material_observed=all(s['material_inventory_complete'] for s in reports),
            complete_global_inventory_verified=False,external_backups_verified=False,all_writers_drained=False,
            kms_destruction_authorized=False,provider_mutations=0,kms_calls=0,plaintext_deks_read=0)
