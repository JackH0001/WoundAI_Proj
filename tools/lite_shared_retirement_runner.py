"""Private one-step shared KMS retirement runner; never called by the App.

Pinned public input, attached job identity and finite durable provider budget.
It cannot create keys, edit accounts, rewrite backups or invent approvals. The
prepare step writes hash-only review evidence; execute/reconcile reuse the
existing signed CAS dispatcher. Real execution needs separate authorization.
"""
import argparse
import base64
import copy
import hashlib
import os
import re
import time
from types import SimpleNamespace

from lite_account_lifecycle import AccountLifecycle
from lite_attest_state import GCSStateStore, StateUnavailable
from lite_disposition_bootstrap import acquire, google_session, validate_limits
from lite_fresh_bootstrap_factory import GCSFactory
from lite_fresh_bootstrap_runner import checked, read_regular
from lite_key_lifecycle_dispatch import KeyLifecycleDispatch, PinnedKeyLifecycle
from lite_provider_budget import ProviderBudget, validate_policy
from lite_provider_transport import BudgetedSession
from lite_rotating_grant_keys import RotatingGrantCipher, RotatingGrantKeyProvider, shared_settings
from lite_shared_key_inventory import GCSInventoryReader
from lite_shared_key_retirement import SharedKeyRetirement
from lite_store_locations import location, overlaps
from lite_subscription_executor import key
from lite_usage_ledger import digest, _hex

READ = frozenset(('custody','custody_audit','lifecycle','lifecycle_audit'))
CREATE = frozenset(('reviews','dispatch_audit'))
LANES = READ | CREATE | {'dispatch'}


class RestrictedGCS(GCSStateStore):
    """Preserve physical GCS location/type while narrowing a core's store API.

This is not an IAM substitute. Constructor checks may read existing config but
must never initialize a missing live account/custody namespace.
"""
    def __init__(self, raw, mode):
        if not isinstance(raw,GCSStateStore) or mode not in ('read','create','mutable'):
            raise ValueError('explicit GCS lane restriction required')
        super().__init__(raw.bucket,prefix=raw.prefix)
        self.raw,self.mode=raw,mode

    def load(self,k):return self.raw.load(k)

    def compare_exchange(self,k,revision,value):
        if self.mode=='read' or self.mode=='create' and revision!=0:
            raise StateUnavailable('retirement lane is not writable for this operation')
        return self.raw.compare_exchange(k,revision,value)


class SchemaStore(GCSStateStore):
    """No-I/O constructor schema check only; never returned as a live provider."""
    def __init__(self, loc, records):
        super().__init__(SimpleNamespace(name=loc['bucket']),prefix=loc['prefix'])
        self.records=records
    def load(self,k):return (1,copy.deepcopy(self.records[k])) if k in self.records else None
    def compare_exchange(self,*args):raise StateUnavailable('schema validation cannot initialize data')


class NoTransport:
    def request(self,*args,**kw):raise StateUnavailable('preflight cannot call providers')


class RecoveryOnlySession:
    """KMS reads/DEK decrypt only; lifecycle writes use a separate no-retry path."""
    def __init__(self,session,settings):self.session,self.spec=session,settings
    def request(self,method,url,**kw):
        base='https://cloudkms.googleapis.com/v1/'
        if not (method=='GET' and url in {base+v for v in self.spec['read_versions']}
                or method=='POST' and url==base+self.spec['crypto_key']+':decrypt'):
            raise StateUnavailable('retirement recovery transport cannot wrap or change keys')
        return self.session.request(method,url,**kw)


def build(c, *, factory, recovery_session, lifecycle_provider, get, clock):
    stores={n:RestrictedGCS(factory(n,c['locations'][n]),
        'read' if n in READ else 'create' if n in CREATE else 'mutable') for n in sorted(LANES)}
    life=AccountLifecycle(store=stores['lifecycle'],audit_store=stores['lifecycle_audit'],
        binding=c['binding'],service_catalog=c['service_catalog'],clock=clock)
    spec=shared_settings(c['shared_key'],c['keyring'])
    provider=RotatingGrantKeyProvider(session=RecoveryOnlySession(recovery_session,spec),keyring=c['keyring'],**spec)
    cipher=RotatingGrantCipher(provider=provider,lifecycle=life,store=stores['custody'],audit_store=stores['custody_audit'])
    public=[base64.b64decode(c['signers'][n],validate=True) for n in ('operator','drain','reviewer')]
    return SharedKeyRetirement(enabled=True,manifest=c['manifest'],expected_manifest=digest(c['manifest']),
        expected_source=c['deployment']['source'],cipher=cipher,get=get,provider=lifecycle_provider,
        reviews=stores['reviews'],dispatch_store=stores['dispatch'],dispatch_audit=stores['dispatch_audit'],
        operator_key=public[0],drain_key=public[1],reviewer_key=public[2],clock=clock)


def preflight(raw,request_raw,*,expected_config,expected_request,expected_source,expected_image,clock):
    c=checked(raw,expected_config,256*1024);r=checked(request_raw,expected_request,32768)
    if (set(c)!={'schema','enabled','deployment','operation_namespace','binding','service_catalog','keyring','shared_key','manifest',
            'signers','locations','buckets','budget_stores','provider_budget','bootstrap_budget'}
            or c['schema']!='lite-shared-retirement-runner/1' or c['enabled'] is not True):
        raise ValueError('explicit pinned private retirement configuration required')
    _hex(expected_source,40);_hex(expected_image)
    if type(c['operation_namespace']) is not str or not re.fullmatch(r'[a-z0-9_-]{1,64}',c['operation_namespace']):
        raise ValueError('stable operation namespace required')
    d=c['deployment']
    if (type(d) is not dict or set(d)!={'project','project_number','service_account','source','image'}
            or d['source']!=expected_source or d['image']!=expected_image
            or not re.fullmatch(r'[a-z][a-z0-9-]{4,61}[a-z0-9]',d['project'])
            or not re.fullmatch(r'[1-9][0-9]{5,19}',d['project_number'])
            or not re.fullmatch(r'[a-z][a-z0-9-]{4,28}[a-z0-9]@'+re.escape(d['project'])+r'\.iam\.gserviceaccount\.com',d['service_account'])
            or not re.fullmatch('projects/'+re.escape(d['project'])+r'/locations/[a-z0-9-]+/keyRings/[A-Za-z0-9_-]{1,63}',c['keyring'])
            or c['binding'].get('audience')!='com.woundai.lite'):
        raise ValueError('pinned Lite project, identity and keyring required')
    if (type(c['locations']) is not dict or set(c['locations'])!=LANES
            or type(c['budget_stores']) is not dict or set(c['budget_stores'])!={'control','audit'}
            or type(c['signers']) is not dict or set(c['signers'])!={'operator','drain','reviewer'}):
        raise ValueError('exact lanes and independent signer set required')
    locations={**c['locations'],**{'budget_'+n:v for n,v in c['budget_stores'].items()}}
    pairs=[]
    for name,loc in locations.items():
        if (type(loc) is not dict or set(loc)!={'bucket','prefix'}
                or not re.fullmatch(r'[a-z0-9][a-z0-9-]{1,61}[a-z0-9]',loc['bucket'])
                or not re.fullmatch(r'[a-z0-9_-]+(?:/[a-z0-9_-]+)+',loc['prefix'])):
            raise ValueError('explicit GCS prefix required')
        pair=('gcs',loc['bucket'],loc['prefix'])
        if any(overlaps(pair,x) for x in pairs):raise ValueError('retirement lanes overlap')
        pairs.append(pair)
        prefix=('lite-retirement-budget/'+c['provider_budget']['namespace']+'/' if name.startswith('budget_')
                else 'lite-retirement/'+c['operation_namespace']+'/')
        if name not in READ and not loc['prefix'].startswith(prefix):
            raise ValueError('writes require dedicated retirement operation prefixes')
    # A mirror is read-only input too; it must not be an operation/budget lane.
    for s in c['manifest']['sources']:
        if s['project_number']!=d['project_number']:raise ValueError('inventory project differs')
        for n,loc in locations.items():
            if n not in READ and overlaps(('gcs',s['bucket'],s['prefix']),('gcs',loc['bucket'],loc['prefix'])):
                raise ValueError('retirement writes alias a recovery source')
    buckets={v['bucket'] for v in locations.values()}
    if (type(c['buckets']) is not dict or set(c['buckets'])!=buckets
            or any(type(v) is not dict or set(v)!={'metageneration'} or type(v['metageneration']) is not str
                or not re.fullmatch(r'[1-9][0-9]*',v['metageneration']) for v in c['buckets'].values())):
        raise ValueError('exact operational bucket metadata generations required')
    p=validate_policy(c['provider_budget'],digest(c['provider_budget']));validate_limits(c['bootstrap_budget'])
    if (p['source']!=expected_source or p['image']!=expected_image or p['manifest']!=digest(c['manifest'])
            or not p['not_before']<=clock()<p['expires_at']):raise ValueError('provider budget binding/window differs')
    if (set(r)!={'schema','manifest','mode','payload'} or r['schema']!='lite-shared-retirement-request/1'
            or r['manifest']!=digest(c['manifest']) or r['mode'] not in ('prepare','plan','inspect','execute','reconcile')
            or type(r['payload']) is not dict):raise ValueError('exact single retirement request required')
    fields=dict(prepare=set(),plan={'review','action'},inspect={'plan'},execute={'plan','command','scope'},reconcile={'plan','drain'})
    if set(r['payload'])!=fields[r['mode']]:raise ValueError('mode payload differs')
    # Seed only the two expected constructor config records in memory. This
    # validates the REAL schemas before credentials; it cannot prove live data.
    scope=digest(c['binding'])
    cfg=dict(v=2 if c['service_catalog'] else 1,binding=c['binding'],max_grants=AccountLifecycle.MAX_GRANTS)
    if c['service_catalog']:cfg['service_catalog']='linked-operations/1'
    mode=dict(v=1,scheme='shared-kms-envelope-1',scope=scope,keyring=c['keyring'],crypto_key=c['shared_key']['crypto_key'])
    seeds={key('account-lifecycle-config',c['binding']['namespace']):cfg,key('account-grant-cipher-config',scope):mode}
    provider=PinnedKeyLifecycle(session=NoTransport(),version=c['manifest']['retiring_version'],protected_version=c['manifest']['protected_version'])
    x=build(c,factory=lambda n,l:SchemaStore(l,seeds),recovery_session=NoTransport(),lifecycle_provider=provider,
        get=lambda *a,**k:(_ for _ in ()).throw(StateUnavailable('schema only')),clock=clock)
    payload=r['payload']
    if r['mode']=='plan':
        _hex(payload['review'])
        if payload['action'] not in ('disable','schedule','restore'):raise ValueError('unsupported lifecycle action')
    if 'plan' in payload:
        plan=payload['plan']
        dispatcher=KeyLifecycleDispatch(enabled=True,provider=provider,manifest=plan,expected_manifest=digest(plan),
            expected_source=expected_source,store=x.state,audit_store=x.audit,
            operator_key=x.operator,drain_key=x.drainer,clock=clock)
        if r['mode']=='execute':
            if not plan['not_before']<=clock()<plan['expires_at']:raise ValueError('dispatch plan window closed')
            dispatcher._signed(payload['command']);x._scope(payload['scope'],plan)
        elif r['mode']=='reconcile':dispatcher._signed(payload['drain'],drain=True)
    return c,r


class RetirementProviders:
    """Real GCS/KMS adapters, attached identity only, no ADC/keyfile fallback."""
    def __init__(self):self.transports=[];self.started=False
    def connect(self,c,*,clock):
        if self.started:raise StateUnavailable('one-shot provider already started')
        self.started=True
        if os.environ.get('DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA')!='true':
            raise ValueError('background GCS metadata disabled required')
        from google.cloud.storage import Client
        self.c,self.clock=c,clock
        self.credentials,self.control_meter=acquire(c,clock=clock)
        self.control=BudgetedSession(google_session(self.credentials),self.control_meter,provider='gcs',authorized=True)
        self.transports.append(self.control)
        self.client=Client(project=c['deployment']['project'],credentials=self.credentials,_http=self.control)
    def factory(self):
        return GCSFactory(client=self.client,project_number=self.c['deployment']['project_number'],buckets=self.c['buckets'])
    def activate(self,allocation):
        from google.cloud.storage import Client
        self.control.close()
        self.storage=BudgetedSession(google_session(self.credentials),allocation,provider='gcs',response_limit=256*1024,authorized=True)
        self.recovery=BudgetedSession(google_session(self.credentials),allocation,provider='kms',response_limit=32768,authorized=True)
        self.transports.extend((self.storage,self.recovery))
        self.client=Client(project=self.c['deployment']['project'],credentials=self.credentials,_http=self.storage)
        self.inventory=GCSInventoryReader(session=self.storage)
        def token_clock():
            self.credentials.before_request(None,'GET','',{}) # Both original token clocks; no network/refresh.
            return self.clock()
        self.lifecycle=PinnedKeyLifecycle.with_access_token(version=self.c['manifest']['retiring_version'],
            protected_version=self.c['manifest']['protected_version'],access_token=self.credentials.token,
            expires_at=self.credentials._ends[0],clock=token_clock,allocation=allocation)
        self.transports.append(self.lifecycle)
    def close(self):
        for t in self.transports:t.close()
        if hasattr(self,'credentials'):self.credentials.token=None


def run(raw,request_raw,*,expected_config,expected_request,expected_source,expected_image,providers=None,clock=lambda:int(time.time())):
    c,r=preflight(raw,request_raw,expected_config=expected_config,expected_request=expected_request,
        expected_source=expected_source,expected_image=expected_image,clock=clock)
    p=RetirementProviders() if providers is None else providers
    try:
        p.connect(c,clock=clock);factory=p.factory()
        states={n:factory('budget_'+n,l) for n,l in c['budget_stores'].items()}
        budget=ProviderBudget(policy=c['provider_budget'],expected=digest(c['provider_budget']),
            store=states['control'],audit_store=states['audit'],clock=clock)
        allocation=budget.allocate() # All modes consume an audit allocation, including inspect.
        p.activate(allocation)
        x=build(c,factory=p.factory(),recovery_session=p.recovery,lifecycle_provider=p.lifecycle,get=p.inventory,clock=clock)
        q=r['payload'];m=r['mode']
        if m=='prepare':result=x.prepare()
        elif m=='plan':result=x.plan(q['review'],q['action'])
        elif m=='inspect':result=x._dispatch(q['plan']).inspect()
        elif m=='execute':result=x.execute(q['plan'],q['command'],scope=q['scope'])
        else:result=x.reconcile(q['plan'],q['drain'])
        return dict(schema='lite-shared-retirement-receipt/1',source=expected_source,image=expected_image,
            config=expected_config,request=expected_request,manifest=digest(c['manifest']),mode=m,
            result=result,allocation=allocation.name,provider_attempts=dict(allocation.by_provider),
            audit_preserved=True,account_deleted=False,automatic_retry=False)
    finally:p.close()


def main():
    import json
    a=argparse.ArgumentParser(description=__doc__)
    for n in ('config','request','source','image','config-sha256','request-sha256'):a.add_argument('--'+n,required=True)
    a.add_argument('--validate-only',action='store_true',help='check pinned public inputs only; no credentials or providers')
    v=a.parse_args()
    try:
        raw,request=read_regular(v.config,256*1024),read_regular(v.request,32768)
        args=dict(expected_config=v.config_sha256,expected_request=v.request_sha256,expected_source=v.source,expected_image=v.image)
        if v.validate_only:
            c,r=preflight(raw,request,**args,clock=lambda:int(time.time()))
            result=dict(schema='lite-shared-retirement-preflight/1',passed=True,config=v.config_sha256,
                request=v.request_sha256,mode=r['mode'],provider_access=False,execution_authorized=False)
        else:result=run(raw,request,**args)
    except Exception as e:
        print(json.dumps(dict(status='stopped',error_type=type(e).__name__,automatic_retry=False,
            action='preserve evidence; inspect original state; obtain fresh review before continuation')))
        return 1
    print(json.dumps(result,sort_keys=True));return 0


if __name__=='__main__':raise SystemExit(main())
