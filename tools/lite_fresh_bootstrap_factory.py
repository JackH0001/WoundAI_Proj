"""Private bootstrap composition from pinned public configuration, without keys.

The injected GCS client must use the separately authorized operator identity.
No ADC discovery, Apple, KMS, model, IAM or customer route exists here. Provider
fencing is independently signed evidence, never inferred from an empty bucket.
"""
import base64
import copy
from dataclasses import asdict
import hashlib
import time
from audit_chain_contract import loads_json_object_strict
from lite_attest_state import GCSStateStore, StateUnavailable, _encode
from lite_bucket_policy import validate_bucket_policy
from lite_native_backend import overlaps
from lite_native_startup import ALL_STORES, exact, text
from lite_subscription_authority import ApplePolicy, AppleSubscriptionAuthority
from lite_subscription_executor import SignedEvidence, key
from lite_subscription_migration import StableSubscriptionLinks
from lite_subscription_runtime import SubscriptionRuntime
from lite_service_operations import ServiceAccounts
from lite_service_release import ServiceReleaseReader
from lite_usage_ledger import UsageLedger, UsageConflict, digest, _hex
from lite_usage_rollout import UsageRollout
from lite_fresh_bootstrap import FreshBootstrapExecutor, inventory

READ = frozenset(('accounts','links','links_audit','quota','apple_proofs','observer','bootstrap_observer'))
CREATE = frozenset(('subscription_audit','rollout_audit','release_audit','bootstrap_audit','gate_audit','cutover'))
MUTABLE = frozenset(('subscription','rollout','release','bootstrap','gate'))
NAMES = READ | CREATE | MUTABLE
OPERATIONS = frozenset(('bootstrap','bootstrap_audit','bootstrap_observer','gate','gate_audit'))


class RestrictedStore:
    """Defense in depth; effective bucket IAM must enforce the same boundary."""
    def __init__(self, store, mode): self.raw, self.mode = store, mode
    def load(self, k):
        if self.raw is None: raise StateUnavailable('bootstrap storage not bound')
        return self.raw.load(k)
    def compare_exchange(self, k, revision, value):
        if self.raw is None or self.mode == 'read' or (self.mode == 'create' and revision != 0):
            raise StateUnavailable('bootstrap lane does not permit this write')
        return self.raw.compare_exchange(k,revision,value)


class GCSFactory:
    """No credential creation. GET bucket policies before constructing CAS lanes."""
    def __init__(self, *, client, project_number, buckets):
        self.client,self.number,self.buckets=client,project_number,copy.deepcopy(buckets)
        self.checked={}
    def __call__(self, name, location):
        bucket_name=location['bucket']
        if bucket_name not in self.checked:
            if bucket_name not in self.buckets: raise ValueError('unlisted operational bucket')
            b=self.client.bucket(bucket_name);b.reload(timeout=10,retry=None)
            result=validate_bucket_policy(b._properties,name=bucket_name,project_number=self.number,
                                          role='security',location='ASIA-EAST1')
            if result['metageneration']!=self.buckets[bucket_name]['metageneration']:
                raise ValueError('operational bucket policy changed')
            self.checked[bucket_name]=b
        return GCSStateStore(self.checked[bucket_name],prefix=location['prefix'])


class NoApple:
    def __getattr__(self, name): raise StateUnavailable('bootstrap cannot use Apple services')


class ClosedPreparationProvider:
    """Durable quota preparation gate while customer ingress remains CLOSED.

`new_enabled` means prepared for the later signed service release. This provider
does not change Cloud Run traffic or legacy writers. All mutation remains private
and is valid only within an already-claimed bootstrap quota step.
"""
    def __init__(self, *, manifest, store, audit):
        self.manifest=copy.deepcopy(manifest);self.identity=digest(manifest)
        self.store,self.audit=store,audit;self.key=key('bootstrap-quota-gate',manifest['namespace'])
        self.observe_fence=None;self.authorized=None

    def _immutable(self, kind, v):
        _encode(v);k=key('bootstrap-quota-'+kind,digest(v));r=self.audit.load(k)
        if r is None:self.audit.compare_exchange(k,0,v);r=self.audit.load(k)
        if r is None or r[1]!=v:raise StateUnavailable('quota preparation audit unavailable')

    def _read(self, *, confirmed=True):
        row=self.store.load(self.key)
        if row is None:return None
        v=row[1]
        if (type(v) is not dict or set(v)!={'v','plan','epoch','enabled','intent'}
                or type(v['v']) is not int or v['v']!=1 or v['plan']!=self.identity
                or type(v['epoch']) is not int or not 1<=v['epoch']<=64 or type(v['enabled']) is not bool):
            raise StateUnavailable('quota preparation state differs')
        _hex(v['intent']);intent=self.audit.load(key('bootstrap-quota-intent',v['intent']))
        if intent is None or digest(intent[1])!=v['intent'] or intent[1].get('target')!={k:x for k,x in v.items() if k!='intent'}:
            raise StateUnavailable('quota preparation intent absent')
        if confirmed:
            done=self.audit.load(key('bootstrap-quota-confirmed',digest(v)))
            if done is None or done[1]!=v:raise StateUnavailable('quota preparation unconfirmed')
        return row

    def _proof(self, m):
        if m!=self.manifest or not callable(self.observe_fence):raise StateUnavailable('unbound preparation provider')
        return self.observe_fence()

    def _move(self, m, enabled):
        if not callable(self.authorized):raise StateUnavailable('bootstrap claim required')
        authorization=self.authorized()
        proof=self._proof(m);row=self._read(confirmed=False);before=row[1] if row else None
        if before and before['enabled']==enabled:
            # Only a new authorized invocation can confirm a lost gate receipt.
            self._immutable('confirmed',before);return
        target=dict(v=1,plan=self.identity,epoch=before['epoch']+1 if before else 1,enabled=enabled)
        if target['epoch']>64:raise UsageConflict('bounded preparation history exhausted')
        intent=dict(target=target,before=before,proof=proof,authorization=authorization)
        self._immutable('intent',intent);value=dict(target,intent=digest(intent))
        if not self.store.compare_exchange(self.key,row[0] if row else 0,value):
            raise UsageConflict('preparation changed; inspect before recovery')
        observed=self._read(confirmed=False)
        if observed is None or observed[1]!=value:raise StateUnavailable('preparation write unconfirmed')
        self._immutable('confirmed',value)

    def freeze_legacy(self,m):self._move(m,False)
    def enable_new(self,m):self._move(m,True)
    def disable_new(self,m):self._move(m,False)
    def observe(self,m):
        self._proof(m);row=self._read()
        return dict(namespace=m['namespace'],writer_fence_sha256=m['writer_fence_sha256'],
            baseline_sha256=m['baseline_sha256'],account_map_sha256=m['account_map_sha256'],
            legacy_disabled=True,inflight_legacy=0,new_enabled=row[1]['enabled'] if row else False,
            fresh_inventory_sha256=m['fresh_inventory_sha256'],prior_quota_accounts=0,prior_account_mappings=0)


def build(raw, *, expected_config, plan, expected_plan, sources, writers,
          locations, expected_locations, state_factory, clock=lambda:int(time.time())):
    """Wire existing real core APIs. Read public config only; do not initialize.

    Physical locations are independently pinned, including the read-only data
    lanes. A future deployment must also prove IAM and real frozen inventories.
    """
    _hex(expected_config);_hex(expected_locations);_hex(expected_plan)
    if (type(raw) is not bytes or not 0<len(raw)<=256*1024
            or hashlib.sha256(raw).hexdigest()!=expected_config):raise ValueError('pinned config bytes required')
    c=loads_json_object_strict(raw.decode())
    if digest(plan)!=expected_plan or plan.get('config')!=expected_config:
        raise ValueError('pinned bootstrap plan differs')
    inventory(plan['inventory'],sources=sources,writers=writers)
    if c.get('schema')!='lite-native-startup/3' or c.get('enabled') is not True:
        raise ValueError('reviewed shared-key candidate config required')
    d=exact(c['deployment'],'source image project project_number service_account keyring')
    a=exact(c['app'],'bundle app_id team version attest_audience');apple=c['apple']
    if a['bundle']!='com.woundai.lite' or apple['environment']!='Sandbox':raise ValueError('wrong candidate application')
    policy=ApplePolicy(a['bundle'],a['app_id'],apple['product'],'Sandbox');policy.validate()
    controls=exact(c['controls'],'operator_keys observer_keys release usage baselines')
    if (type(locations) is not dict or set(locations)!=NAMES or digest(locations)!=expected_locations
            or set(c['stores'])!=set(ALL_STORES) or controls['usage']!=plan['usage']
            or controls['release']!=plan['release'] or controls['baselines']!=[]):
        raise ValueError('exact bootstrap locations and controls required')
    pairs=[]
    for name,loc in locations.items():
        exact(loc,'bucket prefix');text(loc['bucket'],r'[a-z0-9][a-z0-9-]{1,61}[a-z0-9]')
        text(loc['prefix'],r'[a-z0-9_-]+(?:/[a-z0-9_-]+)+')
        if name not in OPERATIONS and loc!=c['stores'][name]:raise ValueError('native storage location changed')
        if name in OPERATIONS and not loc['prefix'].startswith('lite-bootstrap/'+plan['namespace']+'/'):
            raise ValueError('dedicated operational namespace required')
        pairs.append(('gcs',loc['bucket'],loc['prefix']))
    if any(overlaps(a,b) for i,a in enumerate(pairs) for b in pairs[i+1:]):raise ValueError('bootstrap lanes overlap')
    # Operational lanes must not alias ANY native lane, including ones omitted
    # from this operator's allowlist (identity, grants, deletion, images, etc.).
    for name in OPERATIONS:
        loc=locations[name]
        if any(overlaps(('gcs',loc['bucket'],loc['prefix']),('gcs',v['bucket'],v['prefix'])) for v in c['stores'].values()):
            raise ValueError('operation aliases native data')
    public=[{n:base64.b64decode(k,validate=True) for n,k in controls[field].items()} for field in ('operator_keys','observer_keys')]
    evidence=SignedEvidence(*public,clock=clock)
    # Compose and validate the real core against unbound lanes first. Invalid
    # manifests cannot trigger even a bucket-metadata GET from the callback.
    stores={name:RestrictedStore(None,
                'read' if name in READ else 'create' if name in CREATE else 'mutable') for name in sorted(NAMES)}
    authority=AppleSubscriptionAuthority(policy=policy,verifier=NoApple(),client=NoApple(),proof_store=stores['apple_proofs'],clock=clock)
    links=StableSubscriptionLinks(store=stores['links'],audit_store=stores['links_audit'],authorities=(authority,),
        active_policy=digest(asdict(policy)),legacy_policies=(),authorize=lambda *_:False,writer_fence=lambda _:False,clock=clock)
    accounts=ServiceAccounts(stores['accounts'],subscription_links=links)
    reader=ServiceReleaseReader(store=stores['release'],audit_store=stores['release_audit'],manifest=controls['release'])
    runtime=SubscriptionRuntime(links=links,registry=accounts,state_store=stores['subscription'],audit_store=stores['subscription_audit'],
        observation_store=stores['observer'],evidence=evidence,expected_source=d['source'],expected_image=d['image'],service_release=reader)
    provider=ClosedPreparationProvider(manifest=controls['usage'],store=stores['gate'],audit=stores['gate_audit'])
    ledger=UsageLedger(stores['quota'],namespace=controls['usage']['namespace'],clock=clock)
    rollout=UsageRollout(store=stores['rollout'],audit_store=stores['rollout_audit'],control_store=stores['cutover'],
        ledger=ledger,provider=provider,manifest=controls['usage'],baselines=[],service_release=reader)
    ledger.cutover=rollout.policy()
    executor=FreshBootstrapExecutor(plan=plan,expected_plan=expected_plan,expected_config=expected_config,sources=sources,writers=writers,
        runtime=runtime,rollout=rollout,reader=reader,evidence=evidence,observer=stores['bootstrap_observer'],
        store=stores['bootstrap'],audit=stores['bootstrap_audit'])
    for name in sorted(NAMES):
        stores[name].raw=state_factory(name,locations[name])
    provider.observe_fence=executor._observation
    def quota_claim():
        state=executor.inspect()
        if state is None or state['stage']!='quota' or state['status']!='running':
            raise StateUnavailable('claimed quota step required')
        intent=executor.audit.load(key('fresh-bootstrap-intent',state['intent']))
        evidence.verify(intent[1]['command'],'approval')
        return dict(plan=executor.identity,intent=state['intent'],attempt=state['attempt'])
    provider.authorized=quota_claim
    return executor
