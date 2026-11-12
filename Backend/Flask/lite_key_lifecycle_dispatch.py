"""Private, default-off KMS lifecycle dispatch and uncertain-outcome recovery.

This is a low-level operator component, NOT a complete retirement authority.
Complete inventory, live-account recovery, IAM and writer drain must be checked
by the future retirement composition before enabling any real provider session.
No startup, HTTP route, credential discovery, provisioning or scheduled worker.
"""
import base64
import copy
import math
import re
import time
from datetime import datetime
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from audit_chain_contract import loads_json_object_strict
from lite_attest_state import StateUnavailable
from lite_store_locations import location, overlaps
from lite_subscription_executor import canonical, key
from lite_subscription_runtime import SubscriptionRuntime
from lite_usage_ledger import digest, _hex


class LifecycleRejected(ValueError):pass


class PinnedKeyLifecycle:
    """Exact version only, one session call, no enable/import/create or crypto.

    KMS offers no generation condition on these requests. A matching pre-read
    is not an atomic precondition. Dedicated IAM + independent drain are required.
    An injected session is trusted and may replay internally. Real operations
    must use with_access_token(), which owns the no-replay HTTP transport.
    """
    def __init__(self,*,session,version,protected_version):
        pattern=r'projects/[a-z0-9-]+/locations/[a-z0-9-]+/keyRings/[A-Za-z0-9_-]+/cryptoKeys/[A-Za-z0-9_-]+/cryptoKeyVersions/[1-9][0-9]{0,9}'
        if (not callable(getattr(session,'request',None)) or type(version) is not str
                or not re.fullmatch(pattern,version) or type(protected_version) is not str
                or not re.fullmatch(pattern,protected_version)
                or version.rsplit('/',1)[0]!=protected_version.rsplit('/',1)[0]
                or int(version.rsplit('/',1)[1])>=int(protected_version.rsplit('/',1)[1])):
            raise ValueError('one older version and a protected later version required')
        self.session,self.version,self.protected=session,version,protected_version

    @classmethod
    def with_access_token(cls,*,version,protected_version,access_token,expires_at,clock=time.time):
        from lite_key_lifecycle_transport import SingleAttemptKMSTransport
        transport=SingleAttemptKMSTransport(version=version,access_token=access_token,expires_at=expires_at,clock=clock)
        try:return cls(session=transport,version=version,protected_version=protected_version)
        except Exception:
            transport.close()
            raise

    def close(self):
        close=getattr(self.session,'close',None)
        if close is not None:close()

    def _call(self,method,*,action='',body=None):
        response=None
        try:
            response=self.session.request(method,'https://cloudkms.googleapis.com/v1/'+self.version+action,
                params={'updateMask':'state'} if method=='PATCH' else {},json=body,
                timeout=(5,10),allow_redirects=False,stream=True)
            if type(response.status_code) is not int or response.status_code!=200:raise ValueError()
            raw=bytearray()
            for chunk in response.iter_content(chunk_size=4096):
                if type(chunk) is not bytes or len(raw)+len(chunk)>32768:raise ValueError()
                raw.extend(chunk)
            row=loads_json_object_strict(bytes(raw).decode('utf-8'))
            if (row.get('name')!=self.version or row.get('algorithm')!='GOOGLE_SYMMETRIC_ENCRYPTION'
                    or row.get('protectionLevel')!='SOFTWARE'
                    or row.get('state') not in ('ENABLED','DISABLED','DESTROY_SCHEDULED','DESTROYED')
                    or row.get('reimportEligible',False) is not False
                    or any(row.get(k) for k in ('importJob','importTime','importFailureReason'))):raise ValueError()
            for field in ('destroyTime','destroyEventTime'):
                if row.get(field) is not None:
                    if type(row[field]) is not str or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z',row[field]):raise ValueError()
                    datetime.fromisoformat(row[field].replace('Z','+00:00'))
            if row['state']=='DESTROY_SCHEDULED' and row.get('destroyTime') is None:raise ValueError()
            # Persist only reviewed public metadata, never full provider errors.
            return dict(name=self.version,state=row['state'],algorithm=row['algorithm'],
                protectionLevel=row['protectionLevel'],destroyTime=row.get('destroyTime'),
                destroyEventTime=row.get('destroyEventTime'))
        except Exception:raise StateUnavailable('key lifecycle outcome unavailable; do not retry mutation') from None
        finally:
            if response is not None:
                try:response.close()
                except Exception:pass

    def describe(self):return self._call('GET')

    def dispatch(self,action):
        if action=='disable':return self._call('PATCH',body=dict(name=self.version,state='DISABLED'))
        if action in ('schedule','restore'):
            return self._call('POST',action=':destroy' if action=='schedule' else ':restore',body={})
        raise LifecycleRejected('only reviewed disable/schedule/restore actions supported')


class KeyLifecycleDispatch:
    """One durable slot per KMS version; one provider mutation per signed intent.

    Uncertain claims/RPC outcomes cannot be retried or superseded. A separately
    signed drain permits a read-only reconciliation, never automatic rollback.
    Status reports provider observations, not full account/backup destruction.
    """
    immutable=staticmethod(SubscriptionRuntime.immutable)
    transitions={'disable':('ENABLED','DISABLED'), 'schedule':('DISABLED','DESTROY_SCHEDULED'),
                 'restore':('DESTROY_SCHEDULED','DISABLED')}

    def __init__(self,*,enabled=False,provider,manifest,expected_manifest,expected_source,store,audit_store,
                 operator_key,drain_key,clock=time.time):
        if enabled is not True or type(provider) is not PinnedKeyLifecycle or not callable(clock):
            raise ValueError('explicit private lifecycle composition required')
        _hex(expected_source,40);_hex(expected_manifest)
        m=copy.deepcopy(manifest)
        try:
            if (type(m) is not dict or set(m)!={'v','source_commit','version','protected_version','action',
                    'expected_metadata_sha256','not_before','expires_at','review_sha256','state_location_sha256','audit_location_sha256'}
                    or type(m['v']) is not int or m['v']!=1 or digest(m)!=expected_manifest
                    or m['source_commit']!=expected_source or m['version']!=provider.version
                    or m['protected_version']!=provider.protected or m['action'] not in self.transitions
                    or m['state_location_sha256']!=digest(location(store)) or m['audit_location_sha256']!=digest(location(audit_store))
                    or any(type(m[k]) is not int for k in ('not_before','expires_at'))
                    or not 0<=m['not_before']<m['expires_at']<=m['not_before']+900):raise ValueError()
            _hex(m['expected_metadata_sha256']);_hex(m['review_sha256'])
            if (type(operator_key) is not bytes or type(drain_key) is not bytes
                    or len(operator_key)!=32 or len(drain_key)!=32 or operator_key==drain_key):raise ValueError()
        except (KeyError,TypeError,ValueError):raise ValueError('exact lifecycle plan and separate signers required') from None
        if overlaps(location(store),location(audit_store)):raise ValueError('independent dispatch state and audit required')
        self.p,self.m,self.identity,self.store,self.audit,self.clock=provider,m,expected_manifest,store,audit_store,clock
        self.operator,self.drainer=operator_key,drain_key

    def _key(self):return key('key-lifecycle-dispatch',digest(self.p.version))
    def _now(self):
        n=self.clock()
        if type(n) not in (int,float) or not math.isfinite(n) or not 0<=n<2**53:raise StateUnavailable('lifecycle clock unavailable')
        return n
    def _proof(self,j):return key('key-lifecycle-state',digest(j))

    def _signed(self,envelope,*,drain=False):
        try:
            if type(envelope) is not dict or set(envelope)!={'payload','signature'} or len(canonical(envelope))>4096:raise ValueError()
            p=envelope['payload'];kind='key-lifecycle-drain/1' if drain else 'key-lifecycle-command/1'
            fields={'kind','version','manifest','command','expected_state','issued_at','expires_at'}
            if drain:fields.add('prior_dispatch_drained')
            if (type(p) is not dict or set(p)!=fields or p['kind']!=kind or p['version']!=self.p.version
                    or p['manifest']!=self.identity or any(type(p[k]) is not int for k in ('issued_at','expires_at'))
                    or not 0<=p['issued_at']<=self._now()<p['expires_at']<=p['issued_at']+(60 if drain else 300)):
                raise ValueError()
            _hex(p['command']);_hex(p['expected_state'])
            if drain and p['prior_dispatch_drained'] is not True:raise ValueError()
            Ed25519PublicKey.from_public_bytes(self.drainer if drain else self.operator).verify(
                base64.b64decode(envelope['signature'],validate=True),canonical(p))
            return p
        except Exception:raise LifecycleRejected('invalid or expired lifecycle signature') from None

    def _read(self,*,allow_missing_for_reconcile=False):
        row=self.store.load(self._key());anchor=self.audit.load(key('key-lifecycle-started',digest(self.p.version)))
        if anchor is not None and anchor[1]!=dict(version=self.p.version):raise StateUnavailable('lifecycle anchor differs')
        if row is None:
            if anchor is not None and not allow_missing_for_reconcile:raise StateUnavailable('lifecycle checkpoint missing; never reset')
            return None
        j=row[1]
        try:
            if (type(j) is not dict or set(j)!={'v','version','manifest','request','phase','before','after'}
                    or type(j['v']) is not int or j['v']!=1 or j['version']!=self.p.version
                    or j['phase'] not in ('pending','uncertain','observed','reconciled')):raise ValueError()
            _hex(j['manifest']);_hex(j['request'])
            plan=self.audit.load(key('key-lifecycle-plan',j['manifest']))
            request=self.audit.load(key('key-lifecycle-request',j['request']))
            proof=self.audit.load(self._proof(j))
            if (plan is None or digest(plan[1])!=j['manifest'] or plan[1]['version']!=self.p.version
                    or request is None or digest(request[1])!=j['request']
                    or request[1]['payload']['manifest']!=j['manifest'] or proof is None or proof[1]!=j):raise ValueError()
            if digest(j['before'])!=plan[1]['expected_metadata_sha256']:raise ValueError()
            if (j['after'] is None)!=(j['phase'] in ('pending','uncertain')):raise ValueError()
        except (KeyError,TypeError,ValueError):raise StateUnavailable('lifecycle checkpoint or original audit differs') from None
        return row

    @staticmethod
    def _state(row):return digest(None if row is None else row[1])

    def _save(self,row,j,*,claim=False):
        self.immutable(self.audit,self._proof(j),j)
        try:won=self.store.compare_exchange(self._key(),0 if row is None else row[0],j)
        except StateUnavailable:won=False
        if claim and not won:raise StateUnavailable('lifecycle claim uncertain; independent drain required')
        saved=self._read()
        if saved is None or saved[1]!=j:raise StateUnavailable('lifecycle checkpoint unconfirmed')
        return saved

    def inspect(self):
        row=self._read();j=None if row is None else row[1]
        return dict(schema='key-lifecycle-dispatch-status/1',version=self.p.version,state_sha256=self._state(row),
            phase='not_started' if j is None else j['phase'],request=None if j is None else j['request'],
            provider_state=None if j is None or j['after'] is None else j['after']['state'],
            complete_inventory_verified=False,account_deleted=False,physical_backups_erased=False)

    def execute(self,envelope):
        command=self._signed(envelope);now=self._now()
        if not self.m['not_before']<=now<self.m['expires_at']:raise LifecycleRejected('lifecycle execution window closed')
        row=self._read();rh=digest(envelope)
        result=self.audit.load(key('key-lifecycle-finished',rh))
        if result is not None:
            # Old intent replay is observation only, even after another action.
            r=result[1]
            if (type(r) is not dict or set(r)!={'schema','version','state_sha256','phase','request','provider_state',
                    'complete_inventory_verified','account_deleted','physical_backups_erased'}
                    or r['schema']!='key-lifecycle-dispatch-status/1' or r['version']!=self.p.version
                    or r['request']!=rh or r['phase'] not in ('observed','reconciled')
                    or any(r[k] is not False for k in ('complete_inventory_verified','account_deleted','physical_backups_erased'))):
                raise StateUnavailable('original lifecycle result corrupt')
            state=self.audit.load(key('key-lifecycle-state',r['state_sha256']))
            if (state is None or digest(state[1])!=r['state_sha256'] or state[1]['request']!=rh
                    or state[1]['phase']!=r['phase'] or state[1]['after']['state']!=r['provider_state']):
                raise StateUnavailable('original lifecycle result evidence missing')
            return copy.deepcopy(result[1])
        if row is not None and row[1]['phase'] in ('pending','uncertain'):
            raise StateUnavailable('prior lifecycle dispatch requires independent drain')
        if command['expected_state']!=self._state(row):raise LifecycleRejected('lifecycle checkpoint changed')
        before=self.p.describe()
        source,target=self.transitions[self.m['action']]
        if digest(before)!=self.m['expected_metadata_sha256'] or before['state']!=source:
            raise LifecycleRejected('provider state differs from reviewed source')
        self.immutable(self.audit,key('key-lifecycle-plan',self.identity),self.m)
        self.immutable(self.audit,key('key-lifecycle-request-id',digest([self.p.version,command['command']])),dict(request=rh))
        self.immutable(self.audit,key('key-lifecycle-request',rh),copy.deepcopy(envelope))
        self.immutable(self.audit,key('key-lifecycle-started',digest(self.p.version)),dict(version=self.p.version))
        j=dict(v=1,version=self.p.version,manifest=self.identity,request=rh,phase='pending',before=before,after=None)
        self.immutable(self.audit,key('key-lifecycle-prepared',rh),j)
        row=self._save(row,j,claim=True)
        try:
            self._signed(envelope)
            if not self.m['not_before']<=self._now()<self.m['expires_at']:raise LifecycleRejected('lifecycle execution window closed')
            if self._read()!=row:raise StateUnavailable('lifecycle dispatch ownership changed')
            reply=self.p.dispatch(self.m['action'])  # Exactly one mutating request.
            if reply['state']!=target:raise StateUnavailable('lifecycle mutation outcome differs')
            after=self.p.describe()
            if after['state']!=target:raise StateUnavailable('lifecycle readback differs')
            row=self._save(row,dict(j,phase='observed',after=after))
            result=self.inspect();self.immutable(self.audit,key('key-lifecycle-finished',rh),result)
            return result
        except Exception:
            current=self._read()
            if current is not None and current[1]['request']==rh and current[1]['phase']=='pending':
                self._save(current,dict(current[1],phase='uncertain'))
            raise

    def reconcile(self,envelope):
        """Fresh independent drain + GET only. Never retry a provider mutation."""
        p=self._signed(envelope,drain=True);row=self._read(allow_missing_for_reconcile=True)
        original=None if row is None else row[1]
        if row is None:
            prepared=self.audit.load(key('key-lifecycle-prepared',p['command']))
            original=None if prepared is None else prepared[1]
            if original is None or original.get('phase')!='pending':raise LifecycleRejected('original prepared dispatch required')
            proof=self.audit.load(self._proof(original));request=self.audit.load(key('key-lifecycle-request',p['command']))
            if (proof is None or proof[1]!=original or request is None or digest(request[1])!=p['command']
                    or digest(original['before'])!=self.m['expected_metadata_sha256']):
                raise LifecycleRejected('original claim evidence required')
        if original['manifest']!=self.identity or p['expected_state']!=self._state(row) or p['command']!=original['request']:
            raise LifecycleRejected('drain must bind exact original dispatch checkpoint')
        # A received 200 response followed by a lost audit write also comes here.
        self.immutable(self.audit,key('key-lifecycle-drain',digest(envelope)),copy.deepcopy(envelope))
        after=self.p.describe();self._signed(envelope,drain=True)
        row=self._save(row,dict(original,phase='reconciled',after=after))
        result=self.inspect()
        self.immutable(self.audit,key('key-lifecycle-reconciled',digest([row[1]['request'],digest(envelope)])),result)
        if self.audit.load(key('key-lifecycle-finished',row[1]['request'])) is None:
            self.immutable(self.audit,key('key-lifecycle-finished',row[1]['request']),result)
        return result
