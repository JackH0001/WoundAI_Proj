"""Pinned private key-preparation factory; no activation or creation at startup.

Cloud Run must enforce invoker IAM for the separately named native web identity.
This factory is not a public endpoint and never claims to configure/check IAM.
The worker holds no Apple private key, identity HMAC secret, model or user media.
"""
import hashlib
from pathlib import Path
import re
import time
from audit_chain_contract import loads_json_object_strict
from lite_account_lifecycle import AccountLifecycle
from lite_attest_state import GCSStateStore, StateUnavailable
from lite_bucket_policy import validate_bucket_policy
from lite_key_provisioning import AccountKeyProvisioner, CreateOnlyKeyProvider
from lite_store_locations import LimitedStateStore, location, overlaps
from lite_subscription_executor import key
from lite_usage_ledger import digest, _hex, _subject

STORES = ('state', 'audit', 'lifecycle', 'lifecycle_audit')


def exact(value, fields):
    if type(value) is not dict or set(value) != set(fields.split()):
        raise ValueError('key worker configuration fields differ')
    return value


def text(value, pattern):
    if type(value) is not str or re.fullmatch(pattern, value) is None:
        raise ValueError('key worker configuration value invalid')
    return value


def preflight(raw, *, expected_config, expected_source, expected_image):
    _hex(expected_config); _hex(expected_source, 40); _hex(expected_image)
    if (type(raw) is not bytes or not 1 <= len(raw) <= 32768
            or hashlib.sha256(raw).hexdigest() != expected_config):
        raise ValueError('pinned bounded worker configuration required')
    c = loads_json_object_strict(raw.decode('utf-8'))
    exact(c, 'schema enabled deployment binding buckets stores manifest manifest_sha256')
    if c['schema'] != 'lite-key-startup/1' or c['enabled'] is not True:
        raise ValueError('explicit private worker enablement required')
    d = exact(c['deployment'], 'source image project project_number service_account invoker_account keyring')
    if d['source'] != expected_source or d['image'] != expected_image:
        raise ValueError('worker source/image differ from reviewed pins')
    text(d['project'], r'[a-z][a-z0-9-]{4,28}[a-z0-9]')
    text(d['project_number'], r'[1-9][0-9]{5,19}')
    for field in ('service_account', 'invoker_account'):
        text(d[field], r'[a-z][a-z0-9-]{4,28}[a-z0-9]@'+re.escape(d['project'])+r'\.iam\.gserviceaccount\.com')
    if d['service_account'] == d['invoker_account']:
        raise ValueError('worker and web identities must differ')
    text(d['keyring'], r'projects/'+re.escape(d['project'])+r'/locations/asia-east1/keyRings/[A-Za-z0-9_-]+')
    b = exact(c['binding'], 'namespace issuer audience identity_key_sha256')
    _subject(b['namespace']); _hex(b['identity_key_sha256'])
    if b['issuer'] != 'https://appleid.apple.com' or b['audience'] != 'com.woundai.lite':
        raise ValueError('Lite account binding required')
    m = exact(c['manifest'], 'v namespace scope keyring source image operation not_before expires_at max_creates')
    _hex(c['manifest_sha256']); _subject(m['namespace']); _hex(m['operation'])
    if (digest(m) != c['manifest_sha256'] or type(m['v']) is not int or m['v'] != 1
            or m['source'] != expected_source or m['image'] != expected_image
            or m['scope'] != digest(b) or m['keyring'] != d['keyring']
            or any(type(m[k]) is not int for k in ('not_before', 'expires_at', 'max_creates'))
            or not 0 <= m['not_before'] < m['expires_at'] <= m['not_before']+3600
            or not 1 <= m['max_creates'] <= 1000):
        raise ValueError('matching bounded provisioning plan required')
    if type(c['buckets']) is not dict or not 1 <= len(c['buckets']) <= 4:
        raise ValueError('bounded explicit bucket inventory required')
    for name, meta in c['buckets'].items():
        text(name, r'[a-z0-9][a-z0-9-]{1,61}[a-z0-9]')
        exact(meta, 'metageneration'); text(meta['metageneration'], r'[1-9][0-9]*')
    if type(c['stores']) is not dict or set(c['stores']) != set(STORES):
        raise ValueError('four explicit state locations required')
    locations = []
    for name, loc in c['stores'].items():
        exact(loc, 'bucket prefix')
        if loc['bucket'] not in c['buckets']: raise ValueError('unlisted bucket')
        text(loc['prefix'], r'lite-(?:native|key-prepare)/[a-z0-9_-]{1,64}/[a-z0-9_-]{1,64}')
        if name in ('state', 'audit') and not loc['prefix'].startswith('lite-key-prepare/'):
            raise ValueError('worker writes must use dedicated worker namespace')
        locations.append(('gcs', loc['bucket'], loc['prefix']))
    if set(x[1] for x in locations) != set(c['buckets']): raise ValueError('unused bucket')
    if any(overlaps(a, b) for i, a in enumerate(locations) for b in locations[i+1:]):
        raise ValueError('worker/lifecycle state overlaps')
    return c


class CreateOnlyKMSSession:
    """Reject destructive, crypto, IAM, rotation, import and foreign-ring calls."""
    def __init__(self, session, keyring):
        self.session, self.base = session, 'https://cloudkms.googleapis.com/v1/'+keyring

    def request(self, method, url, **kw):
        child = re.escape(self.base)+r'/cryptoKeys/g-[a-z2-7]{52}(?:/cryptoKeyVersions/1)?'
        allowed = method == 'GET' and (url == self.base or re.fullmatch(child, url) is not None)
        if method == 'POST' and url == self.base+'/cryptoKeys':
            params = kw.get('params')
            allowed = (type(params) is dict and set(params) == {'cryptoKeyId', 'skipInitialVersionCreation'}
                and type(params['cryptoKeyId']) is str
                and re.fullmatch(r'g-[a-z2-7]{52}', params['cryptoKeyId']) is not None
                and params['skipInitialVersionCreation'] == 'false')
        if not allowed: raise StateUnavailable('key worker provider method outside reviewed scope')
        return self.session.request(method, url, **kw)


class CloudProviders:
    def connect(self, c):
        from google.auth.compute_engine import Credentials
        from google.auth.transport.requests import AuthorizedSession
        from google.cloud.storage import Client
        d = c['deployment']
        self.credentials = Credentials(service_account_email=d['service_account'])
        self.client = Client(project=d['project'], credentials=self.credentials)
        self.kms = CreateOnlyKMSSession(AuthorizedSession(self.credentials), d['keyring'])

    def identity_checked(self, c):
        if self.credentials.service_account_email != c['deployment']['service_account']:
            raise ValueError('key worker runtime identity differs')

    def bucket(self, name): return self.client.bucket(name)
    def state(self, bucket, prefix): return GCSStateStore(bucket, prefix=prefix)


def build_key_worker(raw, *, expected_config, expected_source, expected_image,
                     providers=None, clock=lambda: int(time.time())):
    c = preflight(raw, expected_config=expected_config, expected_source=expected_source, expected_image=expected_image)
    if not callable(clock) or type(clock()) is not int or clock() < 0: raise ValueError('trusted clock required')
    p = CloudProviders() if providers is None else providers
    p.connect(c)
    buckets = {}
    for name, meta in c['buckets'].items():
        b = p.bucket(name); b.reload(timeout=10, retry=None)
        checked = validate_bucket_policy(b._properties, name=name, project_number=c['deployment']['project_number'],
                                        role='security', location='ASIA-EAST1')
        if checked['metageneration'] != meta['metageneration']: raise ValueError('worker bucket policy changed')
        buckets[name] = b
    p.identity_checked(c)
    provider = CreateOnlyKeyProvider(session=p.kms, keyring=c['deployment']['keyring'])
    status, ring = provider.call('GET', provider.keyring)
    if status != 200 or ring.get('name') != provider.keyring:
        raise StateUnavailable('reviewed key ring not confirmed')
    stores = {k: p.state(buckets[v['bucket']], v['prefix']) for k, v in c['stores'].items()}
    locs = [location(s) for s in stores.values()]
    if any(overlaps(a, b) for i, a in enumerate(locs) for b in locs[i+1:]):
        raise ValueError('resolved worker state overlaps')
    # Missing/corrupt native baseline is an operator task. Worker never initializes
    # it, reserves grants, changes membership or reopens a closed account.
    config = dict(v=2, binding=c['binding'], max_grants=AccountLifecycle.MAX_GRANTS,
                  service_catalog='linked-operations/1')
    old = stores['lifecycle'].load(key('account-lifecycle-config', c['binding']['namespace']))
    if old is None or old[1] != config: raise StateUnavailable('reviewed native lifecycle baseline required')
    life = AccountLifecycle(store=LimitedStateStore(stores['lifecycle'], mode='read'),
        audit_store=LimitedStateStore(stores['lifecycle_audit'], mode='read'),
        binding=c['binding'], clock=clock, service_catalog=True)
    return AccountKeyProvisioner(enabled=True, manifest=c['manifest'], expected_manifest=c['manifest_sha256'],
        lifecycle=life, provider=provider, store=stores['state'],
        audit_store=LimitedStateStore(stores['audit'], mode='create'), clock=clock)


def application(environment=None):
    import os
    env = os.environ if environment is None else environment
    names = ('LITE_KEY_CONFIG', 'LITE_KEY_CONFIG_SHA256', 'LITE_KEY_SOURCE', 'LITE_KEY_IMAGE_SHA256')
    if any(type(env.get(k)) is not str or not env[k] for k in names):
        raise ValueError('explicit private key worker deployment required')
    path = Path(env['LITE_KEY_CONFIG'])
    if not path.is_absolute() or path.is_symlink() or not path.is_file() or not 1 <= path.stat().st_size <= 32768:
        raise ValueError('bounded regular worker configuration required')
    try:
        return build_key_worker(path.read_bytes(), expected_config=env['LITE_KEY_CONFIG_SHA256'],
            expected_source=env['LITE_KEY_SOURCE'], expected_image=env['LITE_KEY_IMAGE_SHA256']).create_app()
    except Exception:
        raise RuntimeError('key worker startup refused; preserve configuration and audit') from None
