"""Pinned, explicit native Sandbox startup; no migration or activation.

Configuration and mounted keys are fully checked before cloud clients exist.
Production uses only the named Cloud Run identity, GCS and fixed Apple endpoints;
test providers replace transports/trust roots, never account or quota decisions.
"""
import base64
from dataclasses import asdict
import hashlib
from pathlib import Path
import re
import time
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from audit_chain_contract import loads_json_object_strict
from lite_native_backend import compose_native_backend, STORE_NAMES, overlaps
from lite_native_inference import LiteNativeInference
from lite_attest_assertion import AssertionPolicy
from lite_attest_enrollment import RegistrationService
from lite_attest_state import GCSStateStore, StateUnavailable
from lite_attest_budget import RequestBudget
from lite_bucket_policy import validate_bucket_policy
from lite_privacy_state import PrivacyState
from lite_apple_identity_provider import RefreshingAppleIdentityVerifier
from lite_apple_oauth import AppleClientSecret, AppleOAuthProvider
from lite_grant_key_custody import GoogleGrantKeyProvider
from lite_key_readiness import KeyReadinessClient
from lite_subscription_authority import ApplePolicy, AppleSubscriptionAuthority, apple_verifier
from lite_subscription_migration import StableSubscriptionLinks, scope
from lite_subscription_executor import SignedEvidence
from lite_subscription_runtime import SubscriptionRuntime
from lite_service_operations import ServiceAccounts
from lite_usage_ledger import UsageLedger, digest, _hex, _subject
from lite_usage_cutover import validate as validate_usage
from lite_usage_rollout import UsageRollout, validate_baselines
from lite_service_release import ServiceReleaseReader, _validate as validate_release

PEER_NAMES = ('security','budget','privacy','accounts','links','links_audit',
    'subscription','subscription_audit','observer','release','release_audit',
    'quota','rollout','rollout_audit','cutover','apple_proofs')
ALL_STORES = PEER_NAMES + STORE_NAMES
BUNDLE='com.woundai.lite'


def exact(value, fields):
    if type(value) is not dict or set(value)!=set(fields.split()):
        raise ValueError('native configuration fields differ')
    return value


def text(value, pattern):
    if type(value) is not str or re.fullmatch(pattern,value) is None:
        raise ValueError('native configuration value invalid')
    return value


def pinned_file(root, reference, maximum):
    exact(reference,'file sha256');_hex(reference['sha256'])
    name=text(reference['file'],r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}')
    base=Path(root).resolve();p=base/name
    if p.is_symlink() or not p.is_file() or p.stat().st_size>maximum:
        raise ValueError('bounded regular mounted file required')
    raw=p.read_bytes()
    if not 0<len(raw)<=maximum or hashlib.sha256(raw).hexdigest()!=reference['sha256']:
        raise ValueError('mounted file digest mismatch')
    return raw


def preflight(raw, *, expected_config, expected_source, expected_image, secrets, clock):
    _hex(expected_config);_hex(expected_source,40);_hex(expected_image)
    if type(raw) is not bytes or not 1<=len(raw)<=256*1024 or hashlib.sha256(raw).hexdigest()!=expected_config:
        raise ValueError('pinned bounded native configuration required')
    c=loads_json_object_strict(raw.decode('utf-8'))
    exact(c,'schema enabled namespace deployment app apple budgets buckets stores controls secrets model'
          + (' key_preparation' if 'key_preparation' in c else ''))
    if c['schema']!='lite-native-startup/1' or c['enabled'] is not True:
        raise ValueError('explicit native Sandbox enablement required')
    _subject(c['namespace'])
    d=exact(c['deployment'],'source image project project_number service_account keyring')
    if d['source']!=expected_source or d['image']!=expected_image:
        raise ValueError('reviewed source/image differ')
    text(d['project'],r'[a-z][a-z0-9-]{4,28}[a-z0-9]')
    text(d['project_number'],r'[1-9][0-9]{5,19}')
    text(d['service_account'],r'[a-z][a-z0-9-]{4,28}[a-z0-9]@'+re.escape(d['project'])+r'\.iam\.gserviceaccount\.com')
    text(d['keyring'],r'projects/'+re.escape(d['project'])+r'/locations/asia-east1/keyRings/[A-Za-z0-9_-]+')
    if 'key_preparation' in c:
        prep=exact(c['key_preparation'],'origin')
        text(prep['origin'],r'https://[a-z0-9][a-z0-9-]+\.a\.run\.app')
    a=exact(c['app'],'bundle app_id team version attest_audience')
    if a['bundle']!=BUNDLE or type(a['app_id']) is not int or a['app_id']<=0:
        raise ValueError('explicit Lite app required')
    text(a['team'],r'[A-Z0-9]{10}');text(a['version'],r'[1-9][0-9]{0,9}')
    text(a['attest_audience'],r'[a-z0-9-]{1,64}')
    apple=exact(c['apple'],'environment product oauth_key_id api_key_id issuer_id roots')
    if apple['environment']!='Sandbox':raise ValueError('candidate accepts Sandbox subscriptions only')
    policy=ApplePolicy(BUNDLE,a['app_id'],apple['product'],'Sandbox');policy.validate()
    for k in ('oauth_key_id','api_key_id'):text(apple[k],r'[A-Z0-9]{10}')
    text(apple['issuer_id'],r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')
    if type(apple['roots']) is not list or not 1<=len(apple['roots'])<=4:
        raise ValueError('bounded pinned Apple roots required')
    budget=exact(c['budgets'],'minute day')
    if (any(type(budget[k]) is not int for k in budget)
            or not 1<=budget['minute']<=budget['day']<=10000000):raise ValueError('bounded budgets required')
    if type(c['buckets']) is not dict or not 1<=len(c['buckets'])<=4:
        raise ValueError('explicit isolated native bucket inventory required')
    for bucket,meta in c['buckets'].items():
        text(bucket,r'[a-z0-9][a-z0-9-]{1,61}[a-z0-9]');exact(meta,'metageneration')
        text(meta['metageneration'],r'[1-9][0-9]*')
    if type(c['stores']) is not dict or set(c['stores'])!=set(ALL_STORES):
        raise ValueError('complete explicit native state locations required')
    locations=[]
    for loc in c['stores'].values():
        exact(loc,'bucket prefix')
        if loc['bucket'] not in c['buckets']:raise ValueError('unlisted bucket')
        text(loc['prefix'],r'lite-native/[a-z0-9_-]{1,64}/[a-z0-9_-]{1,64}')
        locations.append(('gcs',loc['bucket'],loc['prefix']))
    if set(x[1] for x in locations)!=set(c['buckets']):raise ValueError('unused bucket')
    if any(overlaps(x,y) for i,x in enumerate(locations) for y in locations[i+1:]):
        raise ValueError('state prefixes overlap')
    control=exact(c['controls'],'operator_keys observer_keys release usage baselines')
    keys=[]
    for field in ('operator_keys','observer_keys'):
        if type(control[field]) is not dict:raise ValueError('explicit public key maps required')
        keys.append({k:base64.b64decode(v,validate=True) for k,v in control[field].items()})
    evidence=SignedEvidence(*keys,clock=clock)
    release=validate_release(control['release']);usage=validate_usage(control['usage'])
    baselines=validate_baselines(control['baselines'],usage)
    active=digest(asdict(policy))
    if (release['source']!=expected_source or release['image']!=expected_image
            or release['scope']!=digest(scope(policy)) or release['active_policy']!=active
            or set(release['policies'])!={active} or release['usage_plan']!=digest(usage)
            or release['usage_namespace']!=usage['namespace'] or release['fence']!=usage['writer_fence_sha256']):
        raise ValueError('subscription/release/usage pins differ')
    secret_refs=exact(c['secrets'],'identity oauth api')
    blobs={k:pinned_file(secrets,v,16384) for k,v in secret_refs.items()}
    if len({v['file'] for v in secret_refs.values()})!=3 or len(blobs['identity'])!=32:
        raise ValueError('three explicit secret mounts and 32-byte identity key required')
    oauth_key=serialization.load_pem_private_key(blobs['oauth'],password=None)
    api_key=serialization.load_pem_private_key(blobs['api'],password=None)
    # Validate both key roles with the existing P-256 policy before any provider.
    secret=AppleClientSecret(team=a['team'],key_id=apple['oauth_key_id'],client_id=BUNDLE,private_key=oauth_key,clock=clock)
    AppleClientSecret(team=a['team'],key_id=apple['api_key_id'],client_id=BUNDLE,private_key=api_key,clock=clock)
    roots=[x509.load_pem_x509_certificate(pinned_file(secrets,r,32768)).public_bytes(serialization.Encoding.DER)
           for r in apple['roots']]
    if len({hashlib.sha256(r).digest() for r in roots})!=len(roots):raise ValueError('duplicate Apple root')
    exact(c['model'],'preprocessing_sha256');_hex(c['model']['preprocessing_sha256'])
    return c,policy,evidence,secret,blobs,roots


class RuntimeRolloutProvider:
    """The web process has no operational writer-fence or activation capability."""
    def observe(self,manifest):raise StateUnavailable('external operational observer required')
    def freeze_legacy(self,manifest):raise StateUnavailable('operational executor unavailable in web runtime')
    enable_new=freeze_legacy
    disable_new=freeze_legacy


class RuntimeKMSSession:
    def __init__(self,session):self.session=session
    def request(self,method,url,**kw):
        if method!='GET' and not (method=='POST' and url.endswith((':encrypt',':decrypt'))):
            raise StateUnavailable('web runtime cannot destroy or provision keys')
        return self.session.request(method,url,**kw)


class CloudProviders:
    """Concrete transport creation; never scans local ADC or service key files."""
    def connect(self,c):
        from google.auth.compute_engine import Credentials
        from google.auth.transport.requests import AuthorizedSession
        from google.cloud.storage import Client
        d=c['deployment']
        self.credentials=Credentials(service_account_email=d['service_account'])
        self.client=Client(project=d['project'],credentials=self.credentials)
        self.kms=RuntimeKMSSession(AuthorizedSession(self.credentials))
    def bucket(self,name):return self.client.bucket(name)
    def identity_checked(self,c):
        if self.credentials.service_account_email!=c['deployment']['service_account']:
            raise ValueError('runtime service identity differs')
    def state(self,bucket,prefix):return GCSStateStore(bucket,prefix=prefix)
    def apple(self,policy,roots,raw_key,c):
        from appstoreserverlibrary.api_client import AppStoreServerAPIClient
        from appstoreserverlibrary.models.Environment import Environment
        a=c['apple']
        return apple_verifier(policy,roots),AppStoreServerAPIClient(raw_key,a['api_key_id'],a['issuer_id'],BUNDLE,Environment.SANDBOX)
    def identity(self,clock):return RefreshingAppleIdentityVerifier(audience=BUNDLE,clock=clock)
    def oauth(self,secret):return AppleOAuthProvider(client_id=BUNDLE,client_secret=secret)
    def readiness(self,c,scope):
        from google.auth.compute_engine import IDTokenCredentials
        from google.auth.transport.requests import Request,AuthorizedSession
        origin=c['key_preparation']['origin']
        credential=IDTokenCredentials(Request(),target_audience=origin,use_metadata_identity_endpoint=True)
        if credential.service_account_email!=c['deployment']['service_account']:
            raise ValueError('private service caller identity differs')
        return KeyReadinessClient(session=AuthorizedSession(credential),origin=origin,
            scope=scope,keyring=c['deployment']['keyring'])
    def model(self,package,expected):return LiteNativeInference(package=package,preprocessing_sha256=expected)


def build_native_runtime(raw, *, expected_config, expected_source, expected_image,
                         secrets, package, providers=None, clock=lambda:int(time.time())):
    """No IAM, activation, migration, observer publication or cleanup on startup.

    Reading the pinned, already-activated policy is required. Missing policy is
    an operator task, never interpreted as approval to start a fresh deployment.
    """
    c,policy,evidence,secret,blobs,roots=preflight(raw,expected_config=expected_config,
        expected_source=expected_source,expected_image=expected_image,secrets=secrets,clock=clock)
    p=CloudProviders() if providers is None else providers
    model=p.model(package,c['model']['preprocessing_sha256'])
    p.connect(c)
    buckets={}
    for name,meta in c['buckets'].items():
        b=p.bucket(name);b.reload(timeout=10,retry=None)
        checked=validate_bucket_policy(b._properties,name=name,project_number=c['deployment']['project_number'],role='security',location='ASIA-EAST1')
        if checked['metageneration']!=meta['metageneration']:raise ValueError('bucket policy changed since review')
        buckets[name]=b
    p.identity_checked(c)
    stores={k:p.state(buckets[v['bucket']],v['prefix']) for k,v in c['stores'].items()}
    verifier,client=p.apple(policy,roots,blobs['api'],c)
    authority=AppleSubscriptionAuthority(policy=policy,verifier=verifier,client=client,proof_store=stores['apple_proofs'],clock=clock)
    links=StableSubscriptionLinks(store=stores['links'],audit_store=stores['links_audit'],authorities=(authority,),
        active_policy=digest(asdict(policy)),legacy_policies=(),authorize=lambda *_:False,writer_fence=lambda _:False,clock=clock)
    accounts=ServiceAccounts(stores['accounts'],subscription_links=links)
    control=c['controls'];reader=ServiceReleaseReader(store=stores['release'],audit_store=stores['release_audit'],manifest=control['release'])
    runtime=SubscriptionRuntime(links=links,registry=accounts,state_store=stores['subscription'],audit_store=stores['subscription_audit'],
        observation_store=stores['observer'],evidence=evidence,expected_source=expected_source,expected_image=expected_image,service_release=reader)
    ledger=UsageLedger(stores['quota'],namespace=control['usage']['namespace'],clock=clock)
    rollout=UsageRollout(store=stores['rollout'],audit_store=stores['rollout_audit'],control_store=stores['cutover'],
        ledger=ledger,provider=RuntimeRolloutProvider(),manifest=control['usage'],baselines=control['baselines'],service_release=reader)
    ledger.cutover=rollout.policy()
    a=c['app'];security=RegistrationService(stores['security'],policy=AssertionPolicy(a['team']+'.'+BUNDLE,
        frozenset({2,4}),frozenset({a['version']})),audience=a['attest_audience'],environment='production',clock=clock)
    budget=RequestBudget(stores['budget'],minute_limit=c['budgets']['minute'],day_limit=c['budgets']['day'],clock=clock)
    account_scope=digest(dict(namespace=c['namespace'],issuer='https://appleid.apple.com',audience=BUNDLE,
        identity_key_sha256=hashlib.sha256(blobs['identity']).hexdigest()))
    readiness=p.readiness(c,account_scope) if 'key_preparation' in c else None
    return compose_native_backend(enabled=True,runtime=runtime,rollout=rollout,security=security,budget=budget,
        privacy=PrivacyState(stores['privacy']),verifier=p.identity(clock),oauth=p.oauth(secret),
        key_provider=GoogleGrantKeyProvider(session=p.kms,keyring=c['deployment']['keyring']),
        stores={k:stores[k] for k in STORE_NAMES},namespace=c['namespace'],identity_key=blobs['identity'],
        inference=model.raster,model_version=model.version,key_readiness=readiness)


def application(environment=None):
    """Gunicorn factory: missing deployment pins refuse startup; no auto-defaults."""
    import os
    env=os.environ if environment is None else environment
    required=('LITE_NATIVE_CONFIG','LITE_NATIVE_CONFIG_SHA256','LITE_NATIVE_SOURCE',
              'LITE_NATIVE_IMAGE_SHA256','LITE_NATIVE_SECRET_DIR')
    if any(type(env.get(k)) is not str or not env[k] for k in required):
        raise ValueError('explicit native deployment configuration required')
    path=Path(env['LITE_NATIVE_CONFIG'])
    if not path.is_absolute() or path.is_symlink() or not path.is_file() or path.stat().st_size>256*1024:
        raise ValueError('bounded mounted configuration required')
    raw=path.read_bytes()
    try:
        return build_native_runtime(raw,expected_config=env['LITE_NATIVE_CONFIG_SHA256'],
            expected_source=env['LITE_NATIVE_SOURCE'],expected_image=env['LITE_NATIVE_IMAGE_SHA256'],
            secrets=env['LITE_NATIVE_SECRET_DIR'],package=Path(__file__).parent).create_app()
    except Exception:
        # Never emit provider response bodies, mounted key bytes or account rows.
        raise RuntimeError('native candidate startup failed; preserve configuration and audit') from None
