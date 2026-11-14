"""Private retirement composition: fresh inventory + real DEK recovery checks.

Default off, no credentials or service route. Explicit independent scope/drain
attestation is still necessary: code cannot discover unlisted/offline backups.
The result never asserts complete account deletion or physical backup erasure.
"""
import base64
import copy
import hashlib
import hmac
import math
import time
from urllib.parse import quote
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from audit_chain_contract import loads_json_object_strict
from lite_attest_state import MAX_STATE, StateUnavailable
from lite_key_lifecycle_dispatch import KeyLifecycleDispatch, PinnedKeyLifecycle
from lite_rotating_grant_keys import RotatingGrantCipher
from lite_shared_key_inventory import SharedKeyInventory
from lite_store_locations import location, overlaps
from lite_subscription_executor import canonical, key
from lite_subscription_runtime import SubscriptionRuntime
from lite_usage_ledger import digest, _hex


class RetirementRejected(StateUnavailable):pass


def need(value,reason):
    if not value:raise RetirementRejected(reason)


class SharedKeyRetirement:
    immutable=staticmethod(SubscriptionRuntime.immutable)
    def __init__(self,*,enabled=False,manifest,expected_manifest,expected_source,cipher,get,provider,
                 reviews,dispatch_store,dispatch_audit,operator_key,drain_key,reviewer_key,clock=time.time):
        if enabled is not True or type(cipher) is not RotatingGrantCipher or type(provider) is not PinnedKeyLifecycle:
            raise ValueError('explicit private retirement composition required')
        _hex(expected_source,40);_hex(expected_manifest)
        m=copy.deepcopy(manifest)
        keys=(operator_key,drain_key,reviewer_key)
        if any(type(k) is not bytes or len(k)!=32 for k in keys) or len(set(keys))!=3:
            raise ValueError('independent operator, dispatcher drain and scope reviewer required')
        pins=dict(custody=location(cipher.store),custody_audit=location(cipher.audit),
            lifecycle=location(cipher.lifecycle.store),lifecycle_audit=location(cipher.lifecycle.audit),
            reviews=location(reviews),dispatch=location(dispatch_store),dispatch_audit=location(dispatch_audit))
        if any(overlaps(v,w) for i,v in enumerate(pins.values()) for w in list(pins.values())[i+1:]):
            raise ValueError('independent pinned state and audit locations required')
        try:
            if (type(m) is not dict or set(m)!={'v','source_commit','scope','sources','recovery_sources','retiring_version',
                    'protected_version','locations_sha256','signers_sha256'} or type(m['v']) is not int or m['v']!=1
                    or digest(m)!=expected_manifest or m['source_commit']!=expected_source or m['scope']!=cipher.scope
                    or m['retiring_version']!=provider.version or m['protected_version']!=provider.protected
                    or m['protected_version']!=cipher.provider.write_version
                    or m['locations_sha256']!={k:digest(v) for k,v in pins.items()}
                    or m['signers_sha256']!=[hashlib.sha256(k).hexdigest() for k in keys]
                    or not callable(get) or not callable(clock)):raise ValueError()
            scanner=SharedKeyInventory(cipher=cipher,sources=m['sources'],expected_sources=digest(m['sources']),get=get)
            recovery=m['recovery_sources'];primary=next(s for s in scanner.sources if s['kind']=='primary')
            if type(recovery) is not list or not recovery or recovery!=sorted(set(recovery)):raise ValueError()
            for ident in recovery:
                selected=[s for s in scanner.sources if s['id']==ident]
                if len(selected)!=1 or selected[0]['kind']!='mirror' or selected[0]['bucket']==primary['bucket']:raise ValueError()
            cipher.provider._name(provider.version);cipher.provider._name(provider.protected)
        except (ValueError,TypeError,KeyError,StopIteration):raise ValueError('pinned retirement scope and recovery copies required') from None
        self.c,self.get,self.p,self.m,self.identity,self.clock=cipher,get,provider,m,expected_manifest,clock
        self.reviews,self.state,self.audit=reviews,dispatch_store,dispatch_audit
        self.operator,self.drainer,self.reviewer=keys

    def _now(self):
        n=self.clock()
        need(type(n) in (int,float) and math.isfinite(n) and 0<=n<2**53,'retirement clock unavailable')
        return n

    def _scan(self,get=None):
        scanner=SharedKeyInventory(cipher=self.c,sources=self.m['sources'],expected_sources=digest(self.m['sources']),get=get or self.get)
        report=scanner.inspect()
        need(report['all_selected_material_observed'],'complete selected physical inventory required; unknown is not zero')
        need(all(not s['retention_guarded'] and not any(r['held'] and r['wrapped_version']==self.p.version
            for r in s['records']) for s in report['sources']),'retention or held dependency requires separate disposition review')
        # Deliberately omit counters; keep every source policy, generation and payload hash.
        return scanner,dict(coverage_sha256=report['coverage_sha256'],sources=report['sources'],
            dependencies=report['observed_wrapped_dependencies'])

    def _payload(self,scanner,s,r):
        slot=r['object_slot'];_hex(slot)
        name=s['prefix']+'/'+slot+'.json'
        need(hashlib.sha256(name.encode()).hexdigest()==r['object_name_sha256'],'physical object locator differs')
        code,raw=scanner._get('/storage/v1/b/'+s['bucket']+'/o/'+quote(name,safe=''),
            dict(alt='media',generation=r['generation'],ifGenerationMatch=r['generation']),media=True)
        need(code==200 and type(raw) is bytes and len(raw)<=MAX_STATE,'reviewed physical generation unavailable')
        scanner.bytes+=len(raw);need(scanner.bytes<=scanner.max_bytes,'recovery byte bound')
        need(hashlib.sha256(raw).hexdigest()==r['payload_sha256'],'physical content differs from inventory')
        value=loads_json_object_strict(raw.decode('utf-8'))
        need(digest(value)==r['record_sha256'],'physical record differs')
        record=value['record'] if r['kind']=='rewrap_proposal' else value
        self.c._validate(record,r['person'])
        need(record['version']==r['wrapped_version'] and digest(record['wrapped_dek'])==r['wrapped_sha256'],
            'wrapped record differs')
        return record

    def _binding(self,*,previous=None):
        deadline=time.monotonic()+300
        def tick():need(time.monotonic()<deadline,'combined recovery review time bound')
        def get(*args,**kw):
            tick();value=self.get(*args,**kw);tick();return value
        scanner,snapshot=self._scan(get);sources={s['id']:s for s in self.m['sources']}
        people=sorted({r['person'] for s in snapshot['sources'] for r in s['records'] if r['person'] is not None})
        need(0<len(people)<=1000,'bounded nonempty physical account inventory required')
        fence=self.c.audit.load(self.c._write_fence_key(self.p.version))
        need(fence is not None,'old writer fence required; does not prove process drain')
        fv=fence[1]
        need(type(fv) is dict and set(fv)=={'v','scope','version','target','rotation'} and type(fv['v']) is int and fv['v']==1
            and fv['scope']==self.c.scope and fv['version']==self.p.version and fv['target']==self.p.protected,'writer fence differs')
        _hex(fv['rotation'])
        need(self.c.provider.write_version==self.p.protected and self.c.provider.describe(self.p.protected)=='ENABLED',
            'protected version must be the enabled write version')
        roots={};current={};account=[];crypto=[0]
        def unwrap(record):
            tick()
            need(crypto[0]<512,'recovery crypto bound; split reviewed scope before execution')
            crypto[0]+=1
            try:plain=self.c.provider.unwrap(record['version'],record['wrapped_dek'],self.c._aad_for(record))
            except Exception:raise RetirementRejected('account recovery failed; no provider mutation') from None
            tick()
            need(type(plain) is bytes and len(plain)==32,'invalid recovered data key')
            return plain
        for person in people:
            tick()
            lifecycle=self.c.lifecycle._read(person);row=self.c._read(person)
            need(lifecycle is not None and row is not None,'physical account lacks current authoritative inventory')
            roots[person]=lifecycle;current[person]=row;c=row[1]
            prim=[r for s in snapshot['sources'] if s['kind']=='primary' for r in s['records']
                if r['person']==person and r['kind']=='current_key' and not r['noncurrent']]
            need(len(prim)==1 and prim[0]['record_sha256']==digest(c),'primary current row differs from physical inventory')
            old=[];backups=[];dek=None;seen=set()
            closing=lifecycle[1]['phase']=='closing'
            if closing:
                need(c['phase']=='closed','closing account key and proposals must be fenced first')
                receipt=self.c.closure_receipt(person,c['closure']);self.c.validate_closure_receipt(person,c['closure'],receipt)
                closure=digest(receipt)
            else:
                need(c['phase']=='ready' and c['version']==self.p.protected,'every active account must use protected version')
                self.c._require_no_destroy_intent(person);dek=unwrap(c);closure=None
            try:
                for s in snapshot['sources']:
                    for r in s['records']:
                        if r['person']!=person or r['wrapped_version'] is None:continue
                        if r['wrapped_version']==self.p.version:
                            old.append(dict(source=s['id'],generation=r['generation'],payload_sha256=r['payload_sha256']))
                            if not closing and previous is None and r['record_sha256'] not in seen:
                                record=self._payload(scanner,sources[s['id']],r)
                                need(record['dek_id']==c['dek_id'],'old copy uses a different account data key')
                                plain=unwrap(record)
                                try:need(hmac.compare_digest(plain,dek),'old and protected data keys differ')
                                finally:del plain
                                seen.add(r['record_sha256'])
                        if (not closing and s['id'] in self.m['recovery_sources'] and r['kind']=='current_key' and not r['noncurrent']):
                            need(r['wrapped_version']==self.p.protected,'recovery copy still depends on old version')
                            record=self._payload(scanner,sources[s['id']],r)
                            need(record['dek_id']==c['dek_id'],'recovery data key identity differs')
                            plain=unwrap(record)
                            try:need(hmac.compare_digest(plain,dek),'recovery data key differs')
                            finally:del plain
                            backups.append(dict(source=s['id'],generation=r['generation'],payload_sha256=r['payload_sha256']))
                if not closing:
                    need(sorted(b['source'] for b in backups)==self.m['recovery_sources'],
                        'each declared recovery source needs one current protected copy per active account')
                account.append(dict(person=person,phase='closed' if closing else 'active',
                    lifecycle_sha256=digest(lifecycle),current_sha256=digest(row),closure_receipt_sha256=closure,
                    old_copies=old,recovery_copies=backups))
            finally:del dek
        # Observe after cryptographic reads; no claim that two scans are atomic.
        _,after=self._scan(get);need(after==snapshot,'inventory changed during recovery; re-review after drain')
        for person in people:
            need(self.c.lifecycle._read(person)==roots[person] and self.c._read(person)==current[person],
                'account changed during recovery review')
        need(self.c.audit.load(self.c._write_fence_key(self.p.version))==fence,'writer fence changed')
        need(self.c.provider.describe(self.p.protected)=='ENABLED','protected version unavailable after recovery')
        tick()
        binding=dict(inventory=snapshot,accounts=account,fence_sha256=digest(fence))
        if previous is not None:need(binding==previous,'reviewed dependency or recovery binding changed')
        return binding

    def prepare(self):
        """Actual selected-source/DEK recovery; no key mutation or global assertion."""
        before=self.p.describe();need(before['state']=='ENABLED','initial cryptographic review requires old version enabled')
        binding=self._binding();need(self.p.describe()==before,'retiring version changed during preparation')
        proof=dict(schema='shared-key-retirement-review/1',manifest=self.identity,binding=binding,before=before)
        self._save_review(proof)
        return dict(review_sha256=digest(proof),accounts=len(binding['accounts']),
            active_accounts=sum(a['phase']=='active' for a in binding['accounts']),
            closed_accounts=sum(a['phase']=='closed' for a in binding['accounts']),
            selected_recovery_verified=True,global_coverage_attested=False,provider_mutations=0,account_deleted=False)

    def _save_review(self,proof):
        # A physical inventory exceeds a single 16KiB state object even for two
        # accounts. Publish a root only after all bounded content-addressed parts.
        raw=canonical(proof);ident=digest(proof);parts=[]
        need(len(raw)<=1024*1024,'review exceeds bounded immutable inventory')
        for i,start in enumerate(range(0,len(raw),8192)):
            part=dict(schema='shared-key-retirement-part/1',review=ident,index=i,
                data=base64.b64encode(raw[start:start+8192]).decode())
            self.immutable(self.reviews,key('shared-key-retirement-part',digest(part)),part);parts.append(digest(part))
        root=dict(schema='shared-key-retirement-root/1',manifest=self.identity,review=ident,bytes=len(raw),parts=parts)
        self.immutable(self.reviews,key('shared-key-retirement-review',ident),root)

    def _review(self,ident):
        _hex(ident);r=self.reviews.load(key('shared-key-retirement-review',ident))
        need(r is not None and type(r[1]) is dict,'original recovery review missing')
        root=r[1]
        need(set(root)=={'schema','manifest','review','bytes','parts'} and root['schema']=='shared-key-retirement-root/1'
            and root['manifest']==self.identity and root['review']==ident and type(root['bytes']) is int
            and 0<root['bytes']<=1024*1024 and type(root['parts']) is list
            and len(root['parts'])==(root['bytes']+8191)//8192,'invalid bounded review root')
        raw=bytearray()
        for i,part_id in enumerate(root['parts']):
            _hex(part_id);row=self.reviews.load(key('shared-key-retirement-part',part_id))
            need(row is not None and type(row[1]) is dict and digest(row[1])==part_id,'original review part missing')
            c=row[1]
            need(set(c)=={'schema','review','index','data'} and c['schema']=='shared-key-retirement-part/1'
                and c['review']==ident and type(c['index']) is int and c['index']==i,'review part order differs')
            try:part=base64.b64decode(c['data'],validate=True)
            except Exception:raise RetirementRejected('invalid review part encoding') from None
            need(len(part)==min(8192,root['bytes']-len(raw)),'review part length differs');raw.extend(part)
        p=loads_json_object_strict(bytes(raw).decode('utf-8'));need(digest(p)==ident,'review content hash differs')
        need(set(p)=={'schema','manifest','binding','before'} and p['schema']=='shared-key-retirement-review/1'
            and p['manifest']==self.identity and p['before']['name']==self.p.version and p['before']['state']=='ENABLED',
            'original review belongs to another retirement')
        return p

    def plan(self,review,action):
        self._review(review);need(action in ('disable','schedule','restore'),'explicit supported lifecycle action required')
        now=int(self._now())
        return dict(v=1,source_commit=self.m['source_commit'],version=self.p.version,protected_version=self.p.protected,
            action=action,expected_metadata_sha256=digest(self.p.describe()),not_before=now,expires_at=now+900,
            review_sha256=review,state_location_sha256=digest(location(self.state)),audit_location_sha256=digest(location(self.audit)))

    def _dispatch(self,plan):
        self._review(plan['review_sha256'])
        return KeyLifecycleDispatch(enabled=True,provider=self.p,manifest=plan,expected_manifest=digest(plan),
            expected_source=self.m['source_commit'],store=self.state,audit_store=self.audit,
            operator_key=self.operator,drain_key=self.drainer,clock=self.clock)

    def _scope(self,envelope,plan):
        try:
            need(type(envelope) is dict and set(envelope)=={'payload','signature'} and len(canonical(envelope))<=4096,'invalid scope proof')
            p=envelope['payload']
            need(type(p) is dict and set(p)=={'kind','manifest','plan','coverage_evidence','drain_evidence','recovery_evidence',
                'all_custody_sources_declared','all_writers_drained','recovery_locations_reviewed','issued_at','expires_at'},'invalid scope proof')
            need(p['kind']=='shared-key-retirement-scope/1' and p['manifest']==self.identity and p['plan']==digest(plan),'scope proof binding differs')
            for k in ('coverage_evidence','drain_evidence','recovery_evidence'):_hex(p[k])
            need(all(p[k] is True for k in ('all_custody_sources_declared','all_writers_drained','recovery_locations_reviewed')),
                'independent coverage, drain and recovery attestation required')
            need(type(p['issued_at']) is int and type(p['expires_at']) is int
                and 0<=p['issued_at']<=self._now()<p['expires_at']<=p['issued_at']+120,'scope proof expired')
            Ed25519PublicKey.from_public_bytes(self.reviewer).verify(base64.b64decode(envelope['signature'],validate=True),canonical(p))
        except Exception:raise RetirementRejected('invalid or expired independent scope attestation') from None
        return p

    def execute(self,plan,command,*,scope):
        """Fresh read/recovery gate before mutation; human-attested scope is explicit."""
        dispatch=self._dispatch(plan);self._scope(scope,plan)
        # Recorded completion replay remains read-only, including while old KMS is disabled.
        rh=digest(command)
        if self.audit.load(key('key-lifecycle-finished',rh)) is not None:return dispatch.execute(command)
        proof=self._review(plan['review_sha256'])
        if plan['action'] in ('disable','schedule'):
            self._binding(previous=proof['binding'])
        # Restore only returns DISABLED. Do not require broken recovery to be repaired first.
        self._scope(scope,plan)
        self.immutable(self.reviews,key('shared-key-retirement-scope',digest(scope)),copy.deepcopy(scope))
        result=dispatch.execute(command)
        self.immutable(self.reviews,key('shared-key-retirement-dispatch',rh),
            dict(manifest=self.identity,review=plan['review_sha256'],scope_sha256=digest(scope),result_sha256=digest(result)))
        return result

    def reconcile(self,plan,drain):
        # Never decrypt the old key, fix inventory, or repeat a mutation in recovery.
        return self._dispatch(plan).reconcile(drain)
