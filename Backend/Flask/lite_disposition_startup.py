"""Pinned private disposition factory; no model, HMAC, login or API purchase key.

Startup verifies existing native bindings and accepted closures. New private
component configuration markers may be create-only initialized; startup never
executes a command, revokes a token, destroys a key or removes media.
"""
import base64
import hashlib
from pathlib import Path
import re
import time
from cryptography.hazmat.primitives import serialization
from audit_chain_contract import loads_json_object_strict
from lite_account_deletion import AccountDeletion
from lite_account_lifecycle import AccountLifecycle
from lite_account_research_closure import AccountResearchClosure
from lite_apple_oauth import AppleClientSecret, AppleOAuthProvider, REVOKE_URL, post_apple_form
from lite_attest_state import GCSStateStore, StateUnavailable
from lite_disposition_context import (ClosedAccountContext, GrantRevocationContext,
    RegistryReadContext, ResultReadContext, existing)
from lite_disposition_executor import DispositionExecutor, validate_manifest
from lite_disposition_progress import DispositionProgress, CATALOG
from lite_disposition_worker import AccountDispositionWorker
from lite_fenced_store import FencedPrivacy
from lite_grant_key_custody import AccountGrantCipher, GoogleGrantKeyProvider
from lite_reconsent import Reconsent
from lite_research_media_disposition import ResearchMediaDisposition
from lite_research_history import ResearchMediaHistory
from lite_service_disposition import ServiceResultDisposition
from lite_service_history import ServiceResultHistory
from lite_store_locations import LimitedStateStore, location, overlaps
from lite_subscription_executor import key
from lite_usage_ledger import digest, _hex, _subject

READ = ('lifecycle', 'lifecycle_audit', 'identity', 'identity_audit', 'accounts', 'quota')
WRITE = ('keys', 'grants', 'deletion', 'results', 'progress', 'research', 'research_closure',
         'research_media', 'service_history', 'research_history', 'worker', 'executor')
AUDIT = tuple(n+'_audit' for n in WRITE if n != 'research')
STORES = READ + WRITE + AUDIT + ('operations',)


def exact(v, fields):
    if type(v) is not dict or set(v) != set(fields.split()): raise ValueError('disposition configuration fields differ')
    return v


def text(v, pattern):
    if type(v) is not str or re.fullmatch(pattern, v) is None: raise ValueError('invalid pinned configuration')
    return v


def preflight(raw, *, expected_config, expected_source, expected_image, secrets, clock):
    _hex(expected_config); _hex(expected_source, 40); _hex(expected_image)
    if (type(raw) is not bytes or not 1 <= len(raw) <= 65536
            or hashlib.sha256(raw).hexdigest() != expected_config): raise ValueError('bounded pinned configuration required')
    c = loads_json_object_strict(raw.decode())
    exact(c, 'schema enabled deployment binding buckets stores media policies manifest manifest_sha256 signers apple')
    if c['schema'] != 'lite-disposition-startup/1' or c['enabled'] is not True:
        raise ValueError('explicit private disposition enablement required')
    d = exact(c['deployment'], 'source image project project_number service_account keyring')
    if d['source'] != expected_source or d['image'] != expected_image: raise ValueError('source/image pins differ')
    text(d['project'], r'[a-z][a-z0-9-]{4,28}[a-z0-9]'); text(d['project_number'], r'[1-9][0-9]{5,19}')
    text(d['service_account'], r'[a-z][a-z0-9-]{4,28}[a-z0-9]@'+re.escape(d['project'])+r'\.iam\.gserviceaccount\.com')
    text(d['keyring'], r'projects/'+re.escape(d['project'])+r'/locations/asia-east1/keyRings/[A-Za-z0-9_-]+')
    b = exact(c['binding'], 'namespace issuer audience identity_key_sha256')
    _subject(b['namespace']); _hex(b['identity_key_sha256'])
    if b['issuer'] != 'https://appleid.apple.com' or b['audience'] != 'com.woundai.lite': raise ValueError('Lite binding required')
    validate_manifest(c['manifest'], expected_manifest=c['manifest_sha256'], expected_source=expected_source, expected_image=expected_image)
    signers = exact(c['signers'], 'operator drain'); decoded = []
    for role in ('operator', 'drain'):
        value = signers[role]
        if type(value) is not dict or not 1 <= len(value) <= 4: raise ValueError('bounded signing key sets required')
        keys = {}
        for name, data in value.items():
            _subject(name); keys[name] = base64.b64decode(data, validate=True)
            if len(keys[name]) != 32: raise ValueError('Ed25519 public key required')
        decoded.append(keys)
    if set(decoded[0].values()) & set(decoded[1].values()): raise ValueError('independent recovery signer required')
    if type(c['buckets']) is not dict or not 2 <= len(c['buckets']) <= 4: raise ValueError('isolated explicit bucket inventory required')
    for name, meta in c['buckets'].items():
        text(name, r'[a-z0-9][a-z0-9-]{1,61}[a-z0-9]'); exact(meta, 'metadata_sha256'); _hex(meta['metadata_sha256'])
    if type(c['stores']) is not dict or set(c['stores']) != set(STORES): raise ValueError('complete named store map required')
    locations = []
    for name, loc in c['stores'].items():
        exact(loc, 'bucket prefix')
        if loc['bucket'] not in c['buckets']: raise ValueError('unlisted state bucket')
        text(loc['prefix'], r'lite-(?:native|disposition|research)/[a-z0-9_-]{1,64}/[a-z0-9_-]{1,64}')
        locations.append(('gcs', loc['bucket'], loc['prefix']))
    media = exact(c['media'], 'bucket')
    if media['bucket'] not in c['buckets'] or media['bucket'] in {x[1] for x in locations}:
        raise ValueError('separate research media bucket required')
    if {x[1] for x in locations} | {media['bucket']} != set(c['buckets']): raise ValueError('unused bucket')
    if any(overlaps(a,b) for i,a in enumerate(locations) for b in locations[i+1:]): raise ValueError('state overlap')
    policies = exact(c['policies'], 'service research')
    svc = exact(policies['service'], 'v id bucket prefix'); res = exact(policies['research'], 'v id bucket prefixes')
    for p in (svc, res):
        if type(p['v']) is not int or p['v'] != 1: raise ValueError('history policy version')
        text(p['id'], r'[a-z0-9_-]{1,80}')
    if (svc['bucket'] != c['stores']['operations']['bucket'] or svc['prefix'] != c['stores']['operations']['prefix']
            or res['bucket'] != media['bucket'] or res['prefixes'] != ['lite_fenced/v1/', 'lite_epochs/v1/']):
        raise ValueError('history policy scope differs')
    a = exact(c['apple'], 'team key_id file sha256')
    for name in ('team','key_id'): text(a[name], r'[A-Z0-9]{10}')
    text(a['file'], r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}'); _hex(a['sha256'])
    path = Path(secrets).resolve()/a['file']
    if path.is_symlink() or not path.is_file() or not 1 <= path.stat().st_size <= 16384: raise ValueError('bounded mounted OAuth key required')
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != a['sha256']: raise ValueError('OAuth key pin differs')
    secret = AppleClientSecret(team=a['team'], key_id=a['key_id'], client_id=b['audience'],
        private_key=serialization.load_pem_private_key(data, password=None), clock=clock)
    if not callable(clock) or type(clock()) is not int or clock() < 0: raise ValueError('trusted clock required')
    return c, decoded, secret


class DispositionKMSSession:
    """Only reviewed accounts' describe/decrypt/destroy; no wrap/create/restore."""
    def __init__(self, session, keyring, scope, persons):
        self.session = session
        provider = GoogleGrantKeyProvider(session=session, keyring=keyring)
        self.versions = {'https://cloudkms.googleapis.com/v1/'+provider.version(scope, person) for person in persons}
        self.keys = {v.rsplit('/cryptoKeyVersions/',1)[0] for v in self.versions}

    def request(self, method, url, **kw):
        allowed = method == 'GET' and url in self.versions
        allowed |= method == 'POST' and (url in {v+':destroy' for v in self.versions} or url in {k+':decrypt' for k in self.keys})
        if not allowed: raise StateUnavailable('disposition KMS method outside reviewed accounts')
        return self.session.request(method, url, **kw)


def revoke_transport(url, fields):
    if url != REVOKE_URL: raise StateUnavailable('disposition cannot exchange or refresh Apple tokens')
    return post_apple_form(url, fields)


class CloudProviders:
    def connect(self, c):
        from google.auth.compute_engine import Credentials
        from google.auth.transport.requests import AuthorizedSession
        from google.cloud.storage import Client
        d = c['deployment']; self.credentials = Credentials(service_account_email=d['service_account'])
        self.client = Client(project=d['project'], credentials=self.credentials)
        self.kms = DispositionKMSSession(AuthorizedSession(self.credentials), d['keyring'], digest(c['binding']),
            [a['person'] for a in c['manifest']['accounts']])
    def identity_checked(self, c):
        if self.credentials.service_account_email != c['deployment']['service_account']: raise ValueError('runtime identity differs')
    def bucket(self, name): return self.client.bucket(name)
    def state(self, bucket, prefix): return GCSStateStore(bucket, prefix=prefix)
    def oauth(self, secret): return AppleOAuthProvider(client_id='com.woundai.lite', client_secret=secret, post=revoke_transport)


def build_disposition(raw, *, expected_config, expected_source, expected_image, secrets, providers=None,
                      clock=lambda:int(time.time())):
    c, signers, secret = preflight(raw, expected_config=expected_config, expected_source=expected_source,
        expected_image=expected_image, secrets=secrets, clock=clock)
    p = CloudProviders() if providers is None else providers
    p.connect(c); buckets = {}
    for name, meta in c['buckets'].items():
        b = p.bucket(name); b.reload(timeout=10, retry=None); v = b._properties
        if (type(v) is not dict or digest(v) != meta['metadata_sha256'] or v.get('kind') != 'storage#bucket'
                or v.get('name') != name or v.get('projectNumber') != c['deployment']['project_number']
                or v.get('location') != 'ASIA-EAST1'
                or v.get('iamConfiguration',{}).get('publicAccessPrevention') != 'enforced'
                or v.get('iamConfiguration',{}).get('uniformBucketLevelAccess',{}).get('enabled') is not True):
            raise ValueError('reviewed private bucket policy changed')
        buckets[name] = b
    p.identity_checked(c)
    s = {n:p.state(buckets[v['bucket']],v['prefix']) for n,v in c['stores'].items()}
    locs = [location(x) for x in s.values()]
    if any(overlaps(a,b) for i,a in enumerate(locs) for b in locs[i+1:]): raise ValueError('resolved storage overlaps')
    if any(loc[0]=='gcs' and loc[1]==c['media']['bucket'] for loc in locs): raise ValueError('resolved state shares research media')
    for n in READ: s[n] = LimitedStateStore(s[n], mode='read')
    for n in AUDIT: s[n] = LimitedStateStore(s[n], mode='create')
    life = AccountLifecycle(store=s['lifecycle'], audit_store=s['lifecycle_audit'], binding=c['binding'], clock=clock, service_catalog=True)
    scope = life.scope; namespace = c['binding']['namespace']
    # Native baseline must already exist. This factory cannot silently start a
    # fresh account/key/progress history after configuration or evidence loss.
    mode = dict(v=1,scheme='account-kms-envelope-1',scope=scope,keyring=c['deployment']['keyring'])
    existing(s['keys'],key('account-grant-cipher-config',scope),mode)
    existing(s['deletion'],key('account-deletion-config',namespace),dict(v=1,scope=scope,lease_seconds=AccountDeletion.LEASE_SECONDS))
    existing(s['progress'],key('disposition-progress-config',scope),dict(v=1,scope=scope,catalog=CATALOG))
    existing(s['accounts'],hashlib.sha256(b'lite/service-accounts/lifecycle/1').digest(),dict(v=2,binding=life.binding,service_catalog='linked-operations/1'))
    for a in c['manifest']['accounts']:
        row = life._read(a['person'])
        if row is None or row[1]['phase'] != 'closing': raise ValueError('already accepted account closure required')
        v = row[1]['closure']; life.progress(a['person'],v['requester'],v['operation'])
    cipher = AccountGrantCipher(provider=GoogleGrantKeyProvider(session=p.kms,keyring=c['deployment']['keyring']),
        lifecycle=life,store=s['keys'],audit_store=s['keys_audit'])
    grants = GrantRevocationContext(lifecycle=life,provider=p.oauth(secret),cipher=cipher,store=s['grants'],audit_store=s['grants_audit'])
    registry = RegistryReadContext(s['accounts'],life)
    ctx = ClosedAccountContext(lifecycle=life,grants=grants,store=s['identity'],audit_store=s['identity_audit'],registry=registry)
    deletion = AccountDeletion(bridge=ctx,store=s['deletion'],audit_store=s['deletion_audit'])
    ops = ResultReadContext(store=s['operations'],accounts=registry,ledger=s['quota'],clock=clock)
    results = ServiceResultDisposition(operations=ops,lifecycle=life,store=s['results'],audit_store=s['results_audit'])
    progress = DispositionProgress(deletion=deletion,results=results,cipher=cipher,store=s['progress'],audit_store=s['progress_audit'])
    deletion.attach_disposition(progress)
    research = Reconsent(FencedPrivacy(s['research'],buckets[c['media']['bucket']]),clock=clock)
    closure = AccountResearchClosure(deletion=deletion,research=research,store=s['research_closure'],audit_store=s['research_closure_audit'])
    media = ResearchMediaDisposition(closure=closure,store=s['research_media'],audit_store=s['research_media_audit'])
    rh = ResearchMediaHistory(media=media,store=s['research_history'],audit_store=s['research_history_audit'],policy=c['policies']['research'])
    sh = ServiceResultHistory(disposition=results,store=s['service_history'],audit_store=s['service_history_audit'],policy=c['policies']['service'])
    worker = AccountDispositionWorker(progress=progress,service_history=sh,research_history=rh,store=s['worker'],audit_store=s['worker_audit'])
    executor = DispositionExecutor(enabled=True,manifest=c['manifest'],expected_manifest=c['manifest_sha256'],
        expected_source=expected_source,expected_image=expected_image,worker=worker,store=s['executor'],audit_store=s['executor_audit'],
        operator_keys=signers[0],drain_keys=signers[1],clock=clock)
    for a in c['manifest']['accounts']: executor.inspect(a['person'])
    return executor


def application(environment=None):
    import os
    env = os.environ if environment is None else environment
    names = ('LITE_DISPOSITION_CONFIG','LITE_DISPOSITION_CONFIG_SHA256','LITE_DISPOSITION_SOURCE',
             'LITE_DISPOSITION_IMAGE_SHA256','LITE_DISPOSITION_SECRETS')
    if any(type(env.get(n)) is not str or not env[n] for n in names): raise ValueError('explicit private disposition configuration required')
    path = Path(env[names[0]])
    if not path.is_absolute() or path.is_symlink() or not path.is_file() or not 1<=path.stat().st_size<=65536:
        raise ValueError('bounded regular configuration required')
    try:
        return build_disposition(path.read_bytes(),expected_config=env[names[1]],expected_source=env[names[2]],
            expected_image=env[names[3]],secrets=env[names[4]]).create_app()
    except Exception: raise RuntimeError('private disposition startup refused; preserve evidence') from None
